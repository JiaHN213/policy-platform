from accounts.services import access_decision
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .catalog import (
    OpportunitySerializer,
    RelationSerializer,
    policy_relations,
    visible_opportunities,
)
from .models import (
    DocumentSnapshot,
    Evidence,
    Policy,
    PolicyEnrichment,
    PolicyFieldProvenance,
    PolicySource,
)
from .pipeline import policy_pipeline_state, policy_pipeline_timeline
from .services import mark_as_lead, publish_policy, withdraw_policy
from .taxonomy import ValidityStatus


class PolicySerializer(serializers.ModelSerializer):
    def to_representation(self, obj):
        data = super().to_representation(obj)
        request = self.context.get("request")
        internal = bool(request and request.user.is_staff and getattr(request, "path", "").startswith("/api/v1/admin/"))
        if not internal:
            for key in ("extraction_version", "scope_evidence", "ai_enrichment", "pipeline", "support_signals"):
                data.pop(key, None)
            for source in data.get("sources", []):
                source.pop("match_method", None)
                source.pop("match_confidence", None)
        return data

    class Meta:
        model = Policy
        fields = [
            "id",
            "title",
            "document_number",
            "issuer",
            "publication_date",
            "region",
            "geographic_level",
            "province",
            "city",
            "source_grade",
            "topics",
            "industry",
            "business_domains",
            "direction_tags",
            "scope_evidence",
            "document_type",
            "document_role",
            "opportunity_level",
            "support_signals",
            "validity_status",
            "summary",
            "summary_method",
            "summary_evidence",
            "structured_keywords",
            "extraction_version",
            "source_url",
            "status",
            "version",
            "published_at",
            "is_demo",
        ]


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentSnapshot
        fields = ["id", "url", "content_type", "size_bytes", "parse_status"]


class DistinctSourceListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        from .provenance import source_location

        rows = super().to_representation(data)
        unique = {}
        for row in sorted(rows, key=lambda item: not item["is_primary"]):
            key = source_location(row["resolved_url"] or row["url"])
            unique.setdefault(key, row)
        return list(unique.values())


class PolicySourceSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    source_grade_label = serializers.CharField(source="get_source_grade_display", read_only=True)

    class Meta:
        model = PolicySource
        list_serializer_class = DistinctSourceListSerializer
        fields = [
            "id",
            "url",
            "resolved_url",
            "publisher",
            "publication_date",
            "source_grade",
            "source_grade_label",
            "role",
            "role_label",
            "is_primary",
            "match_method",
            "match_confidence",
        ]


class PolicyDetailSerializer(PolicySerializer):
    evidence_readiness = serializers.SerializerMethodField()
    attachments = serializers.SerializerMethodField()
    sources = PolicySourceSerializer(many=True, read_only=True)
    relations = serializers.SerializerMethodField()
    opportunities = serializers.SerializerMethodField()
    ai_enrichment = serializers.SerializerMethodField()
    pipeline = serializers.SerializerMethodField()

    @extend_schema_field(dict)
    def get_evidence_readiness(self, obj):
        from .readiness import evidence_readiness

        return evidence_readiness(obj)

    @extend_schema_field(dict)
    def get_pipeline(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_staff or not getattr(request, "path", "").startswith("/api/v1/admin/"):
            return None
        return policy_pipeline_state(obj)

    @extend_schema_field(dict)
    def get_ai_enrichment(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_staff or not getattr(request, "path", "").startswith("/api/v1/admin/"):
            return None
        job = obj.enrichments.filter(policy_version=obj.version).order_by("-updated_at").first()
        if not job:
            return {"status": "not_started", "review": None}
        return {
            "id": str(job.pk),
            "status": job.status,
            "attempts": job.attempts,
            "error_code": job.error_code,
            "model": job.model,
            "prompt_version": job.prompt_version,
            "review": job.result.get("review") if job.result else None,
            "finalization": job.result.get("finalization") if job.result else None,
        }

    @extend_schema_field(RelationSerializer(many=True))
    def get_relations(self, obj):
        return RelationSerializer(
            policy_relations(obj, self.context["request"].user), many=True
        ).data

    @extend_schema_field(OpportunitySerializer(many=True))
    def get_opportunities(self, obj):
        return OpportunitySerializer(
            visible_opportunities(self.context["request"].user).filter(policy=obj),
            many=True,
            context=self.context,
        ).data

    @extend_schema_field(AttachmentSerializer(many=True))
    def get_attachments(self, obj):
        return AttachmentSerializer(
            obj.snapshots.exclude(content_type__istartswith="text/html").order_by(
                "created_at", "id"
            ),
            many=True,
        ).data

    class Meta(PolicySerializer.Meta):
        fields = PolicySerializer.Meta.fields + [
            "evidence_readiness",
            "body",
            "sources",
            "attachments",
            "relations",
            "opportunities",
            "validity_evidence",
            "ai_enrichment",
            "pipeline",
        ]


class EvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evidence
        fields = ["id", "policy", "policy_version", "text", "location", "quote_hash"]


class PolicyViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PolicySerializer
    queryset = Policy.objects.filter(
        status=Policy.Status.PUBLISHED, source_grade__in=Policy.FORMAL_SOURCE_GRADES
    ).prefetch_related("sources")

    def get_serializer_class(self):
        return PolicyDetailSerializer if self.action == "retrieve" else PolicySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()
        if not access_decision(self.request.user, "policy_detail")["allowed"]:
            raise PermissionDenied("当前账号无政策查看权限。")
        if (
            self.action == "list"
            and not access_decision(self.request.user, "policy_search")["allowed"]
        ):
            raise PermissionDenied("当前账号无政策检索权限。")
        qs = self.queryset
        for field, choices in [
            ("geographic_level", Policy.GeographicLevel.values),
            ("source_grade", Policy.SourceGrade.values),
        ]:
            value = self.request.query_params.get(field, "")
            if value:
                if value not in choices:
                    raise serializers.ValidationError(f"未知的 {field}。")
                qs = qs.filter(**{field: value})
        for field in ("province", "city"):
            value = self.request.query_params.get(field, "").strip()
            if value:
                qs = qs.filter(**{field: value})
        if not self.request.user.is_staff:
            qs = qs.filter(is_demo=False)
        document_type = self.request.query_params.get("document_type", "")
        if document_type:
            if document_type not in Policy.DocumentType.values:
                raise serializers.ValidationError("未知的文件类型。")
            qs = qs.filter(document_type=document_type)
        industry = self.request.query_params.get("industry", "").strip()
        if industry:
            qs = qs.filter(industry=industry)
        validity_status = self.request.query_params.get("validity_status", "")
        if validity_status:
            if validity_status not in ValidityStatus.values:
                raise serializers.ValidationError("未知的政策效力状态。")
            qs = qs.filter(validity_status=validity_status)
        for parameter, field in [
            ("business_domain", "business_domains"),
            ("direction_tag", "direction_tags"),
        ]:
            value = self.request.query_params.get(parameter, "").strip()
            if value:
                ids = [
                    policy.id
                    for policy in qs.only("id", field)
                    if value in getattr(policy, field)
                ]
                qs = qs.filter(pk__in=ids)
        query = self.request.query_params.get("q", "").strip()[:200]
        if query:
            qs = qs.filter(
                Q(title__icontains=query)
                | Q(body__icontains=query)
                | Q(document_number__icontains=query)
            )
        region = self.request.query_params.get("region", "")
        if region:
            qs = qs.filter(region=region)
        topic = self.request.query_params.get("topic", "")
        if topic:
            # Portable baseline; OpenSearch adapter replaces this for indexed search.
            ids = [p.id for p in qs.only("id", "topics") if topic in p.topics]
            qs = qs.filter(pk__in=ids)
        return qs

    @extend_schema(
        parameters=[
            OpenApiParameter("q", str),
            OpenApiParameter("topic", str),
            OpenApiParameter("region", str),
            OpenApiParameter("geographic_level", enum=Policy.GeographicLevel.values),
            OpenApiParameter("source_grade", enum=Policy.SourceGrade.values),
            OpenApiParameter("province", str),
            OpenApiParameter("city", str),
            OpenApiParameter("document_type", enum=Policy.DocumentType.values),
            OpenApiParameter("industry", str),
            OpenApiParameter("business_domain", str),
            OpenApiParameter("direction_tag", str),
            OpenApiParameter("validity_status", enum=ValidityStatus.values),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @action(detail=True, methods=["get"], serializer_class=EvidenceSerializer)
    def evidence(self, request, pk=None):
        policy = self.get_object()
        return Response(
            EvidenceSerializer(
                policy.evidence.filter(policy_version=policy.version), many=True
            ).data
        )


class DecisionSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)


class MetadataDecisionSerializer(DecisionSerializer):
    validity_status = serializers.ChoiceField(choices=ValidityStatus.choices)
    validity_evidence = serializers.CharField(allow_blank=True, max_length=5000)


PUBLICATION_DOCUMENT_TYPES = [
    choice for choice in Policy.DocumentType.choices if choice[0] != "unclassified"
]


class PublicationDecisionSerializer(DecisionSerializer):
    source_grade = serializers.ChoiceField(choices=Policy.SourceGrade.choices, required=False)
    geographic_level = serializers.ChoiceField(
        choices=Policy.GeographicLevel.choices, required=False
    )
    province = serializers.CharField(max_length=100, allow_blank=True, required=False)
    city = serializers.CharField(max_length=100, allow_blank=True, required=False)
    document_type = serializers.ChoiceField(
        choices=PUBLICATION_DOCUMENT_TYPES,
        required=False,
    )
    allow_incomplete_attachments = serializers.BooleanField(default=False, required=False)


class PolicyCorrectionSerializer(DecisionSerializer):
    summary = serializers.CharField(max_length=10000, required=False)
    document_type = serializers.ChoiceField(choices=PUBLICATION_DOCUMENT_TYPES, required=False)
    source_grade = serializers.ChoiceField(choices=Policy.SourceGrade.choices, required=False)
    geographic_level = serializers.ChoiceField(
        choices=Policy.GeographicLevel.choices, required=False
    )
    province = serializers.CharField(max_length=100, allow_blank=True, required=False)
    city = serializers.CharField(max_length=100, allow_blank=True, required=False)
    validity_status = serializers.ChoiceField(choices=ValidityStatus.choices, required=False)
    validity_evidence = serializers.CharField(max_length=5000, allow_blank=True, required=False)
    business_domains = serializers.ListField(
        child=serializers.CharField(max_length=80), required=False
    )
    direction_tags = serializers.ListField(
        child=serializers.CharField(max_length=80), required=False
    )
    document_role = serializers.CharField(max_length=32, required=False)
    opportunity_level = serializers.CharField(max_length=24, required=False)
    support_signals = serializers.ListField(
        child=serializers.CharField(max_length=500), required=False
    )


class FieldUnlockSerializer(DecisionSerializer):
    field_name = serializers.CharField(max_length=80)


class PolicyFieldProvenanceSerializer(serializers.ModelSerializer):
    source_label = serializers.CharField(source="get_source_type_display", read_only=True)
    actor_name = serializers.CharField(source="actor.username", read_only=True, allow_null=True)

    class Meta:
        model = PolicyFieldProvenance
        fields = [
            "id",
            "policy_version",
            "field_name",
            "value_snapshot",
            "source_type",
            "source_label",
            "evidence_quote",
            "evidence_location",
            "model",
            "prompt_version",
            "config_version",
            "locked",
            "actor_name",
            "created_at",
        ]


class PolicyReviewSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    stages = serializers.DictField(child=serializers.IntegerField())
    needs_action = serializers.IntegerField()
    ai_pending = serializers.IntegerField()
    ai_failed = serializers.IntegerField()
    ai_excluded = serializers.IntegerField()
    published = serializers.IntegerField()
    withdrawn = serializers.IntegerField()


def policy_workflow_queryset(queryset=None):
    """Annotate mutually exclusive operator workflow states for the current policy version."""
    policies = queryset if queryset is not None else Policy.objects.all()
    current_jobs = PolicyEnrichment.objects.filter(
        policy_id=OuterRef("pk"), policy_version=OuterRef("version")
    )
    return policies.annotate(
        has_current_ai=Exists(current_jobs),
        ai_waiting=Exists(current_jobs.filter(status__in=["queued", "running"])),
        ai_queued=Exists(current_jobs.filter(status="queued")),
        ai_running=Exists(current_jobs.filter(status="running")),
        ai_failed=Exists(current_jobs.filter(status="failed")),
        ai_succeeded=Exists(current_jobs.filter(status="succeeded")),
        ai_excluded=Exists(
            current_jobs.filter(status="succeeded", result__review__decision="exclude")
        ),
    )


def filter_policy_stage(queryset, stage):
    policies = policy_workflow_queryset(queryset)
    candidate = policies.filter(status="candidate").exclude(source_grade="L4")
    if stage == "AI_PROCESSING":
        return candidate.filter(Q(has_current_ai=False) | Q(ai_waiting=True))
    if stage == "WAITING_AI":
        return candidate.filter(Q(has_current_ai=False) | Q(ai_queued=True))
    if stage == "AI_REVIEWING":
        return candidate.filter(ai_running=True)
    if stage == "NEEDS_ACTION":
        return candidate.filter(
            Q(ai_failed=True) | Q(ai_succeeded=True, ai_excluded=False)
        )
    if stage == "EXCLUDED":
        return policies.filter(status="candidate").filter(
            Q(source_grade="L4") | Q(ai_excluded=True)
        )
    if stage == "PUBLISHED":
        return policies.filter(status="published")
    if stage == "WITHDRAWN":
        return policies.filter(status="withdrawn")
    return policies


def filter_policy_workflow(queryset, workflow_status):
    policies = policy_workflow_queryset(queryset)
    candidate = policies.filter(status="candidate").exclude(source_grade="L4")
    if workflow_status == "needs_action":
        return candidate.filter(ai_succeeded=True, ai_excluded=False)
    if workflow_status == "ai_pending":
        return candidate.filter(Q(has_current_ai=False) | Q(ai_waiting=True))
    if workflow_status == "ai_failed":
        return candidate.filter(ai_failed=True)
    if workflow_status == "ai_excluded":
        return policies.filter(status="candidate").filter(
            Q(source_grade="L4") | Q(ai_excluded=True)
        )
    if workflow_status in {"published", "withdrawn"}:
        return policies.filter(status=workflow_status)
    return policies


class PolicyReviewViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAdminUser]
    serializer_class = PolicyDetailSerializer
    queryset = Policy.objects.prefetch_related("enrichments", "snapshots", "sources").all()

    def get_queryset(self):
        stage = self.request.query_params.get("stage", "")
        allowed_stages = {
            "all",
            "AI_PROCESSING",
            "WAITING_AI",
            "AI_REVIEWING",
            "NEEDS_ACTION",
            "EXCLUDED",
            "PUBLISHED",
            "WITHDRAWN",
        }
        if stage and stage not in allowed_stages:
            raise serializers.ValidationError("未知的统一处理阶段。")
        workflow_status = self.request.query_params.get("workflow_status", "")
        allowed_workflow_statuses = {
            "all",
            "needs_action",
            "ai_pending",
            "ai_failed",
            "ai_excluded",
            "published",
            "withdrawn",
        }
        if workflow_status and workflow_status not in allowed_workflow_statuses:
            raise serializers.ValidationError("未知的处理状态。")
        if stage:
            qs = filter_policy_stage(self.queryset, stage)
        elif workflow_status:
            qs = filter_policy_workflow(self.queryset, workflow_status)
        else:
            status = self.request.query_params.get("status", "candidate")
            qs = self.queryset if status == "all" else self.queryset.filter(status=status)
        ai_status = self.request.query_params.get("ai_status", "")
        allowed_ai_statuses = {
            "not_started",
            "queued",
            "running",
            "succeeded",
            "failed",
            "excluded",
        }
        if ai_status and ai_status not in allowed_ai_statuses:
            raise serializers.ValidationError("未知的 AI 审核状态。")
        current_jobs = PolicyEnrichment.objects.filter(
            policy_id=OuterRef("pk"), policy_version=OuterRef("version")
        )
        if ai_status == "not_started":
            qs = qs.annotate(has_current_ai=Exists(current_jobs)).filter(has_current_ai=False)
        elif ai_status == "excluded":
            qs = qs.annotate(
                ai_excluded=Exists(
                    current_jobs.filter(status="succeeded", result__review__decision="exclude")
                )
            ).filter(ai_excluded=True)
        elif ai_status:
            qs = qs.annotate(has_matching_ai=Exists(current_jobs.filter(status=ai_status))).filter(
                has_matching_ai=True
            )
        query = self.request.query_params.get("q", "").strip()[:200]
        return (
            qs.filter(Q(title__icontains=query) | Q(document_number__icontains=query))
            if query
            else qs
        )

    @extend_schema(
        parameters=[
            OpenApiParameter("status", enum=["all", "candidate", "published", "withdrawn"]),
            OpenApiParameter(
                "workflow_status",
                enum=[
                    "all",
                    "needs_action",
                    "ai_pending",
                    "ai_failed",
                    "ai_excluded",
                    "published",
                    "withdrawn",
                ],
            ),
            OpenApiParameter(
                "ai_status",
                enum=["not_started", "queued", "running", "succeeded", "failed", "excluded"],
            ),
            OpenApiParameter("q", str),
            OpenApiParameter(
                "stage",
                enum=[
                    "all",
                    "AI_PROCESSING",
                    "WAITING_AI",
                    "AI_REVIEWING",
                    "NEEDS_ACTION",
                    "EXCLUDED",
                    "PUBLISHED",
                    "WITHDRAWN",
                ],
            ),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=None, responses=PolicyReviewSummarySerializer)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        policies = policy_workflow_queryset()
        candidate = policies.filter(status="candidate").exclude(source_grade="L4")
        ai_processing = candidate.filter(Q(has_current_ai=False) | Q(ai_waiting=True)).count()
        stage_counts = {
            "AI_PROCESSING": ai_processing,
            "WAITING_AI": candidate.filter(Q(has_current_ai=False) | Q(ai_queued=True)).count(),
            "AI_REVIEWING": candidate.filter(ai_running=True).count(),
            "NEEDS_ACTION": candidate.filter(
                Q(ai_failed=True) | Q(ai_succeeded=True, ai_excluded=False)
            ).count(),
            "EXCLUDED": policies.filter(status="candidate")
            .filter(Q(source_grade="L4") | Q(ai_excluded=True))
            .count(),
            "PUBLISHED": policies.filter(status="published").count(),
            "WITHDRAWN": policies.filter(status="withdrawn").count(),
        }
        payload = {
            "total": policies.count(),
            "stages": stage_counts,
            "needs_action": candidate.filter(ai_succeeded=True, ai_excluded=False).count(),
            "ai_pending": ai_processing,
            "ai_failed": candidate.filter(ai_failed=True).count(),
            "ai_excluded": policies.filter(status="candidate")
            .filter(Q(source_grade="L4") | Q(ai_excluded=True))
            .count(),
            "published": policies.filter(status="published").count(),
            "withdrawn": policies.filter(status="withdrawn").count(),
        }
        return Response(payload)

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["get"], url_path="pipeline-timeline")
    def pipeline_timeline(self, request, pk=None):
        policy = get_object_or_404(
            self.queryset.prefetch_related("enrichments", "snapshots"), pk=pk
        )
        return Response(policy_pipeline_timeline(policy))

    @extend_schema(request=MetadataDecisionSerializer, responses=PolicySerializer)
    @action(detail=True, methods=["post"], url_path="metadata")
    def metadata(self, request, pk=None):
        from .services import update_validity

        get_object_or_404(Policy, pk=pk)
        payload = MetadataDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return Response(
            PolicySerializer(update_validity(pk, request.user, **payload.validated_data)).data
        )

    @extend_schema(request=DecisionSerializer, responses=PolicySerializer)
    @action(detail=True, methods=["post"], url_path="mark-lead")
    def mark_lead(self, request, pk=None):
        get_object_or_404(Policy, pk=pk)
        payload = DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return Response(
            PolicySerializer(mark_as_lead(pk, request.user, payload.validated_data["version"])).data
        )

    @extend_schema(request=PublicationDecisionSerializer, responses=PolicySerializer)
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        get_object_or_404(Policy, pk=pk)
        payload = PublicationDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        policy = publish_policy(
            pk,
            request.user,
            payload.validated_data["version"],
            payload.validated_data.get("document_type"),
            {
                k: v
                for k, v in payload.validated_data.items()
                if k in {"source_grade", "geographic_level", "province", "city"}
            },
            payload.validated_data["allow_incomplete_attachments"],
        )
        return Response(PolicySerializer(policy).data)

    @extend_schema(request=PolicyCorrectionSerializer, responses=PolicyDetailSerializer)
    @action(detail=True, methods=["post"])
    def correct(self, request, pk=None):
        from .services import correct_policy

        get_object_or_404(Policy, pk=pk)
        payload = PolicyCorrectionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        values = dict(payload.validated_data)
        version = values.pop("version")
        policy = correct_policy(pk, request.user, version, values)
        return Response(PolicyDetailSerializer(policy, context={"request": request}).data)

    @extend_schema(request=None, responses=dict)
    @action(detail=True, methods=["get"], url_path="field-provenance")
    def field_provenance(self, request, pk=None):
        policy = get_object_or_404(Policy, pk=pk)
        records = list(policy.field_provenance.select_related("actor").all())
        current = {}
        for record in records:
            current.setdefault(record.field_name, record)
        serializer = PolicyFieldProvenanceSerializer
        return Response(
            {
                "policy": str(policy.pk),
                "version": policy.version,
                "current": serializer(current.values(), many=True).data,
                "history": serializer(records, many=True).data,
            }
        )

    @extend_schema(request=FieldUnlockSerializer, responses=dict)
    @action(detail=True, methods=["post"], url_path="unlock-field")
    def unlock_field(self, request, pk=None):
        from .services import unlock_policy_field_lock

        payload = FieldUnlockSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        policy = unlock_policy_field_lock(
            pk,
            request.user,
            payload.validated_data["version"],
            payload.validated_data["field_name"],
        )
        return Response({"policy": str(policy.pk), "version": policy.version, "unlocked": True})

    @extend_schema(request=DecisionSerializer, responses=PolicySerializer)
    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        get_object_or_404(Policy, pk=pk)
        payload = DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        policy = withdraw_policy(pk, request.user, payload.validated_data["version"])
        return Response(PolicySerializer(policy).data)
