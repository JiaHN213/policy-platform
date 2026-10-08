import uuid

from core.models import AuditRecord
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle
from rest_framework.views import APIView

from .permissions import can_manage_system
from .services import DEFAULT_CAPABILITIES, access_decision


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, max_length=256, trim_whitespace=False)


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, max_length=256, trim_whitespace=False)
    password_confirm = serializers.CharField(write_only=True, max_length=256, trim_whitespace=False)

    class Meta:
        model = get_user_model()
        fields = ["username", "password", "password_confirm"]

    def validate_username(self, value):
        value = get_user_model().normalize_username(value)
        if get_user_model().objects.filter(username=value).exists():
            raise serializers.ValidationError("该用户名已被使用，请换一个。")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError("两次输入的密码不一致。")
        user = get_user_model()(username=attrs["username"])
        try:
            validate_password(attrs["password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return attrs


class RegisterThrottle(SimpleRateThrottle):
    scope = "register"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


@method_decorator(csrf_protect, name="dispatch")
class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterThrottle]

    @extend_schema(request=RegisterSerializer, responses=dict)
    def post(self, request):
        if request.user.is_authenticated:
            raise ValidationError("请先退出当前账号再注册。")
        data = RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                # Explicitly select fields: clients cannot grant themselves staff privileges.
                user = get_user_model().objects.create_user(
                    username=data.validated_data["username"],
                    password=data.validated_data["password"],
                    is_staff=False,
                    is_superuser=False,
                    is_active=True,
                )
                AuditRecord.objects.create(
                    actor=user,
                    action="auth.register",
                    object_id=uuid.uuid4(),
                    details={"user_id": user.pk},
                )
        except IntegrityError as exc:
            raise ValidationError("该用户名已被使用，请换一个。") from exc
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return Response({"id": user.id, "username": user.username}, status=201)


class LoginThrottle(AnonRateThrottle):
    scope = "login"


class CsrfView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(responses=dict)
    def get(self, request):
        return Response({"csrf_token": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginThrottle]

    @extend_schema(request=LoginSerializer, responses=dict)
    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate(request, **data.validated_data)
        if user is None:
            # Do not record passwords or distinguish whether an account exists.
            AuditRecord.objects.create(action="auth.login.failed", object_id=uuid.uuid4())
            raise AuthenticationFailed("用户名或密码错误。")
        login(request, user)
        return Response({"id": user.id, "username": user.username})


class LogoutView(APIView):
    @extend_schema(request=None, responses=dict)
    def post(self, request):
        logout(request)
        return Response({"ok": True})


class MeView(APIView):
    @extend_schema(responses=dict)
    def get(self, request):
        user = request.user
        return Response(
            {
                "id": user.id,
                "username": user.username,
                "is_staff": user.is_staff,
                "can_manage_system": can_manage_system(user),
                "permission_version": user.permission_version,
                "capabilities": {c: access_decision(user, c) for c in DEFAULT_CAPABILITIES},
            }
        )
