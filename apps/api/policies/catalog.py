from datetime import timedelta

from accounts.services import access_decision
from core.models import AuditRecord
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import permissions, serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import get_object_or_404

from .models import Opportunity, OpportunityBatch, Policy, PolicyRelation


def formal_policies(user):
    qs = Policy.objects.filter(status="published", source_grade__in=Policy.FORMAL_SOURCE_GRADES)
    return qs if user.is_staff else qs.filter(is_demo=False)


def verified_records(model, user):
    return model.objects.filter(
        verification_status="verified",
        evidence_policy__in=formal_policies(user),
        evidence_version=F("evidence_policy__version"),
    )


def visible_opportunities(user):
    return verified_records(Opportunity, user).filter(policy__in=formal_policies(user))


def visible_batches(user):
    return verified_records(OpportunityBatch, user).filter(
        opportunity__in=visible_opportunities(user)
    )


def batch_state(batch, now=None):
    now = now or timezone.now()
    status = batch.status
    if status in {"open", "not_started"}:
        if batch.deadline_at and now >= batch.deadline_at:
            status = "closed"
        elif batch.starts_at and now < batch.starts_at:
            status = "not_started"
        elif batch.starts_at and now >= batch.starts_at:
            status = "open"
    days = getattr(settings, "OPPORTUNITY_CLOSING_SOON_DAYS", 7)
    soon = bool(
        status == "open"
        and batch.deadline_at
        and now < batch.deadline_at <= now + timedelta(days=days)
    )
    return status, soon


class VerifiedSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        def value(key):
            return attrs.get(key, getattr(self.instance, key, None))

        proof = value("evidence_policy")
        # Recheck and lock the evidence source within the admin write transaction.
        proof = Policy.objects.select_for_update().get(pk=proof.pk)
        if value("evidence_version") != proof.version:
            raise serializers.ValidationError("证据版本已变化，请重新核实。")
        quote = value("evidence_quote")
        if not quote or len(quote.strip()) < 5 or quote not in proof.body:
            raise serializers.ValidationError("证据须逐字引用当前正文，且至少5个字符。")
        related = []
        if self.Meta.model is Opportunity:
            related = [value("policy")]
        elif self.Meta.model is OpportunityBatch:
            opportunity = value("opportunity")
            related = [opportunity.policy]
            start, end = value("starts_at"), value("deadline_at")
            if start and end and end < start:
                raise serializers.ValidationError("截止时间不能早于开始时间。")
            if value("status") == "ongoing" and end:
                raise serializers.ValidationError("常态化受理不应设置固定截止时间。")
            if (
                value("verification_status") == "verified"
                and not visible_opportunities(self.context["request"].user)
                .filter(pk=opportunity.pk)
                .exists()
            ):
                raise serializers.ValidationError("请先核验所属政策机会。")
        else:
            related = [value("from_policy"), value("to_policy")]
            if related[0].pk == related[1].pk:
                raise serializers.ValidationError("关系两端不能是同一文件。")
            if proof.pk not in {p.pk for p in related}:
                raise serializers.ValidationError("关系证据必须来自关系两端之一。")
        if value("verification_status") == "verified":
            ids = {proof.pk, *(p.pk for p in related)}
            records = list(Policy.objects.select_for_update().filter(pk__in=ids).order_by("id"))
            if any(
                p.status != "published" or p.source_grade not in Policy.FORMAL_SOURCE_GRADES
                for p in records
            ):
                raise serializers.ValidationError("核验通过必须以已发布 L1–L3 文件为依据。")
        return attrs


PROOF_FIELDS = [
    "verification_status",
    "evidence_policy",
    "evidence_version",
    "evidence_quote",
    "verified_by",
    "verified_at",
]
READ_ONLY = ["id", "created_at", "verified_by", "verified_at"]


class BatchSerializer(VerifiedSerializer):
    current_status = serializers.SerializerMethodField()
    closing_soon = serializers.SerializerMethodField()

    @extend_schema_field(serializers.CharField())
    def get_current_status(self, obj):
        return batch_state(obj)[0]

    @extend_schema_field(serializers.BooleanField())
    def get_closing_soon(self, obj):
        return batch_state(obj)[1]

    class Meta:
        model = OpportunityBatch
        fields = [
            "id",
            "opportunity",
            "name",
            "status",
            "starts_at",
            "deadline_at",
            "current_status",
            "closing_soon",
            "created_at",
        ] + PROOF_FIELDS
        read_only_fields = READ_ONLY


class OpportunitySerializer(VerifiedSerializer):
    batches = serializers.SerializerMethodField()
    policy_title = serializers.CharField(source="policy.title", read_only=True)
    policy_version = serializers.IntegerField(source="policy.version", read_only=True)

    @extend_schema_field(BatchSerializer(many=True))
    def get_batches(self, obj):
        user = self.context["request"].user
        batches = obj.batches.all() if user.is_staff else visible_batches(user).filter(opportunity=obj)
        return BatchSerializer(
            batches, many=True, context=self.context
        ).data

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "policy",
            "policy_title",
            "policy_version",
            "title",
            "category",
            "status",
            "acquisition_method",
            "eligible_subjects",
            "eligible_projects",
            "eligible_products",
            "support_content",
            "support_method",
            "amount",
            "amount_unit",
            "percentage",
            "max_amount",
            "min_amount",
            "calculation_basis",
            "requirements",
            "exclusion_conditions",
            "prerequisites",
            "regions",
            "competent_authorities",
            "acceptance_authorities",
            "recommendation_authorities",
            "application_channels",
            "missing_information",
            "evidence_details",
            "created_at",
            "batches",
        ] + PROOF_FIELDS
        read_only_fields = READ_ONLY


class RelationSerializer(VerifiedSerializer):
    from_title = serializers.CharField(source="from_policy.title", read_only=True)
    to_title = serializers.CharField(source="to_policy.title", read_only=True)

    class Meta:
        model = PolicyRelation
        fields = [
            "id",
            "from_policy",
            "to_policy",
            "from_title",
            "to_title",
            "kind",
            "discovery",
            "created_at",
        ] + PROOF_FIELDS
        read_only_fields = READ_ONLY + ["discovery"]


class CatalogAdminPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.is_staff
            and request.user.has_perm("policies.change_policy")
        )


class CatalogAdminViewSet(viewsets.ModelViewSet):
    permission_classes = [CatalogAdminPermission]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        query = self.request.query_params.get("q", "").strip()[:200]
        if query and qs.model is Opportunity:
            qs = qs.filter(title__icontains=query)
        return qs

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        get_object_or_404(self.queryset.select_for_update(), pk=kwargs["pk"])
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        self.save_record(serializer)

    def perform_update(self, serializer):
        self.save_record(serializer)

    def save_record(self, serializer):
        verified = (
            serializer.validated_data.get(
                "verification_status",
                getattr(serializer.instance, "verification_status", "pending"),
            )
            == "verified"
        )
        extra = {
            "verified_by": self.request.user if verified else None,
            "verified_at": timezone.now() if verified else None,
        }
        if serializer.Meta.model is PolicyRelation:
            previous = dict(getattr(serializer.instance, "discovery", {}) or {})
            extra["discovery"] = {
                **previous,
                "method": "human_override",
                "locked": True,
                "actor_id": str(self.request.user.pk),
                "corrected_at": timezone.now().isoformat(),
            }
        obj = serializer.save(**extra)
        AuditRecord.objects.create(
            actor=self.request.user,
            action=f"catalog.{obj._meta.model_name}.save",
            object_id=obj.pk,
            details={"verification_status": obj.verification_status},
        )


class OpportunityAdminViewSet(CatalogAdminViewSet):
    queryset = Opportunity.objects.select_related("policy").prefetch_related("batches").all()
    serializer_class = OpportunitySerializer


class BatchAdminViewSet(CatalogAdminViewSet):
    queryset = OpportunityBatch.objects.all()
    serializer_class = BatchSerializer


class RelationAdminViewSet(CatalogAdminViewSet):
    queryset = PolicyRelation.objects.all()
    serializer_class = RelationSerializer


class OpportunityViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Opportunity.objects.none()
    serializer_class = OpportunitySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        if not access_decision(self.request.user, "policy_detail")["allowed"]:
            raise PermissionDenied()
        if (
            self.action == "list"
            and not access_decision(self.request.user, "policy_search")["allowed"]
        ):
            raise PermissionDenied()
        return visible_opportunities(self.request.user)


def policy_relations(policy, user):
    formal = formal_policies(user)
    return (
        verified_records(PolicyRelation, user)
        .filter(from_policy__in=formal, to_policy__in=formal)
        .filter(Q(from_policy=policy) | Q(to_policy=policy))
    )
