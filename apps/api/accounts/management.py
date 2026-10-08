"""Explicit account scope: authorization remains tied to the real signed-in actor."""
import uuid

from core.errors import Conflict
from core.models import AuditRecord
from django.contrib.auth import get_user_model, logout, update_session_auth_hash
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Membership, Organization
from .permissions import IsSystemManager, can_manage_system


def account_user(request):
    if not hasattr(request, "_account_owner"):
        value = request.query_params.get("user_id")
        if not value:
            request._account_owner = request.user
        else:
            pk = serializers.IntegerField(min_value=1).run_validation(value)
            if pk != request.user.pk and not can_manage_system(request.user):
                raise PermissionDenied("你只能管理自己账号下的资料。")
            request._account_owner = get_object_or_404(get_user_model(), pk=pk)
    return request._account_owner


class AccountScopeMixin:
    @property
    def data_user(self):
        return account_user(self.request)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["data_user"] = self.data_user
        return context

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        owner = getattr(request, "_account_owner", None)
        if owner and owner.pk != request.user.pk and request.method in {"POST", "PATCH", "PUT", "DELETE"} and response.status_code < 400:
            AuditRecord.objects.create(actor=request.user, action="account.data.managed", object_id=uuid.uuid4(), details={"user_id": owner.pk, "method": request.method, "path": request.path})
        return response


def role_of(user):
    # Account status is separate from its assigned role; re-enabling an admin
    # must not accidentally downgrade it because has_perm rejects inactive users.
    if user.is_staff and (user.is_superuser or user.user_permissions.filter(content_type__app_label="accounts", codename="manage_system").exists() or user.groups.filter(permissions__content_type__app_label="accounts", permissions__codename="manage_system").exists()):
        return "admin"
    return "staff" if user.is_staff else "customer"


class UserOutput(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    enterprise_count = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ["id", "username", "first_name", "last_name", "email", "is_active", "is_superuser", "role", "date_joined", "last_login", "permission_version", "enterprise_count"]
        read_only_fields = fields

    def get_role(self, obj) -> str:
        return role_of(obj)

    def get_enterprise_count(self, obj) -> int:
        return Membership.objects.filter(user=obj, active=True, organization__enterpriseprofile__isnull=False).count()


class UserInput(serializers.Serializer):
    username = serializers.CharField(max_length=150, required=False)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(required=False, write_only=True, max_length=256, trim_whitespace=False)
    role = serializers.ChoiceField(choices=["customer", "staff", "admin"], required=False)
    is_active = serializers.BooleanField(required=False)
    permission_version = serializers.IntegerField(min_value=1, required=False)

    def validate_username(self, value):
        field = get_user_model()._meta.get_field("username")
        value = get_user_model().normalize_username(value)
        try:
            field.run_validators(value)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        return value


class PrivateAccountInput(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(required=False, write_only=True, max_length=256, trim_whitespace=False)
    current_password = serializers.CharField(required=False, write_only=True, trim_whitespace=False)
    permission_version = serializers.IntegerField(min_value=1, required=False)


class DeleteAccountInput(serializers.Serializer):
    confirm_username = serializers.CharField()


class CloseAccountInput(DeleteAccountInput):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)


def audit_user(actor, target, action):
    AuditRecord.objects.create(actor=actor, action=action, object_id=uuid.uuid5(uuid.NAMESPACE_URL, f"account:{target.pk}"), details={"user_id": target.pk})


def guard_deletion(user):
    from enterprises.models import ResearchRun, WatchRun
    if ResearchRun.objects.filter(user=user, status__in=["queued", "running", "waiting"]).exists() or WatchRun.objects.filter(watch__user=user, status__in=["queued", "running"]).exists():
        raise Conflict("该账号还有资料整理或匹配任务，请等待任务结束或停止任务后再删除；也可以先停用账号。")


def delete_account(user):
    # Delete sole-owned organizations; shared organizations remain with other members.
    organization_ids = list(Organization.objects.filter(membership__user=user).values_list("pk", flat=True))
    private = Organization.objects.filter(pk__in=organization_ids).annotate(member_count=Count("membership")).filter(member_count=1)
    from enterprises.models import EnterpriseProfile
    from enterprises.views import delete_profile_data
    for profile in EnterpriseProfile.objects.filter(organization__in=private):
        delete_profile_data(profile)
    from subscriptions.models import PendingDelivery
    PendingDelivery.objects.filter(user=user).delete()
    user.delete()


def save_user(actor, target, values):
    password = values.pop("password", None)
    role = values.pop("role", None)
    expected = values.pop("permission_version", None)
    if expected is not None and expected != target.permission_version:
        raise Conflict("账号资料已更新，请刷新后重试。")
    if target.pk == actor.pk and (values.get("is_active") is False or (role and role != role_of(target))):
        raise ValidationError("不能在当前登录会话中停用自己或更改自己的管理角色。")
    if target.is_superuser and (not actor.is_superuser or role and role != "admin"):
        raise PermissionDenied("超级管理员账号只能由超级管理员维护，且不能在此降级。")
    for key, value in values.items():
        setattr(target, key, value)
    if password is not None:
        try:
            validate_password(password, user=target)
        except DjangoValidationError as exc:
            raise ValidationError({"password": exc.messages}) from exc
        target.set_password(password)
    target.permission_version += 1
    if role:
        target.is_staff = role != "customer"
    try:
        with transaction.atomic():
            target.save()
    except IntegrityError as exc:
        raise ValidationError("该用户名已被使用。") from exc
    if role:
        perm = Permission.objects.get(content_type__app_label="accounts", codename="manage_system")
        if role == "admin":
            target.user_permissions.add(perm)
        else:
            target.user_permissions.remove(perm)
            target.groups.remove(*target.groups.filter(permissions=perm))
    audit_user(actor, target, "account.updated")
    return target


def lock_administration(actor):
    # Serialize role/disable/delete changes so two admins cannot demote each other
    # concurrently and leave the installation without an administrator.
    list(get_user_model().objects.filter(is_staff=True).order_by("pk").select_for_update())
    if not can_manage_system(get_user_model().objects.get(pk=actor.pk)):
        raise PermissionDenied("你的管理权限已变化，请重新登录。")


class UserManagementViewSet(viewsets.ModelViewSet):
    permission_classes = [IsSystemManager]
    queryset = get_user_model().objects.all().order_by("id")
    serializer_class = UserOutput
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get("q", "").strip()
        return qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)) if q else qs

    @extend_schema(request=UserInput, responses=UserOutput)
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        lock_administration(request.user)
        payload = UserInput(data=request.data)
        payload.is_valid(raise_exception=True)
        values = payload.validated_data
        if not values.get("username") or not values.get("password"):
            raise ValidationError("请填写用户名和初始密码。")
        target = get_user_model()(username=values["username"])
        save_user(request.user, target, values)
        return Response(UserOutput(get_user_model().objects.get(pk=target.pk)).data, status=201)

    @extend_schema(request=UserInput, responses=UserOutput)
    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        lock_administration(request.user)
        target = get_object_or_404(self.get_queryset().select_for_update(), pk=kwargs["pk"])
        payload = UserInput(data=request.data)
        payload.is_valid(raise_exception=True)
        save_user(request.user, target, payload.validated_data)
        if target.pk == request.user.pk and "password" in request.data:
            update_session_auth_hash(request, target)
        return Response(UserOutput(get_user_model().objects.get(pk=target.pk)).data)

    @extend_schema(request=DeleteAccountInput, responses={204: None})
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        lock_administration(request.user)
        target = get_object_or_404(self.get_queryset().select_for_update(), pk=kwargs["pk"])
        if target.pk == request.user.pk or target.is_superuser:
            raise ValidationError("不能删除当前登录账号或超级管理员账号。")
        if request.data.get("confirm_username") != target.username:
            raise ValidationError("请输入要删除的用户名进行确认。")
        guard_deletion(target)
        audit_user(request.user, target, "account.deleted")
        delete_account(target)
        return Response(status=204)


class PrivateAccountView(APIView):
    serializer_class = UserOutput

    @extend_schema(responses=UserOutput)
    def get(self, request):
        return Response(UserOutput(request.user).data)

    @extend_schema(request=PrivateAccountInput, responses=UserOutput)
    @transaction.atomic
    def patch(self, request):
        if set(request.data) - {"first_name", "last_name", "email", "password", "current_password", "permission_version"}:
            raise ValidationError("只能修改个人姓名、邮箱和密码。")
        target = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if "password" in request.data and not target.check_password(request.data.get("current_password", "")):
            raise ValidationError("当前密码不正确。")
        payload = UserInput(data=request.data)
        payload.is_valid(raise_exception=True)
        save_user(request.user, target, payload.validated_data)
        if "password" in request.data:
            update_session_auth_hash(request, target)
        return Response(UserOutput(target).data)

    @extend_schema(request=CloseAccountInput, responses={204: None})
    @transaction.atomic
    def delete(self, request):
        target = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if target.is_staff or target.is_superuser:
            raise ValidationError("管理账号不能自行注销，请由其他管理员处理。")
        if not target.check_password(request.data.get("current_password", "")) or request.data.get("confirm_username") != target.username:
            raise ValidationError("请核对用户名和当前密码。")
        guard_deletion(target)
        audit_user(request.user, target, "account.closed")
        delete_account(target)
        logout(request)
        return Response(status=204)
