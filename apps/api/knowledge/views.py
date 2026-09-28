import uuid

from accounts.services import access_decision
from core.models import AuditRecord
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_field
from policies.models import Policy, PolicyRelation
from policies.taxonomy import RelationKind
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    KnowledgeBuild,
    KnowledgeLintIssue,
    KnowledgePage,
    KnowledgePageSource,
    RelationReviewCandidate,
)
from .obsidian import export_vault, import_vault_edits
from .services import lint_all
from .tasks import enqueue_sync


class KnowledgePermission(permissions.BasePermission):
    message = "政策知识库仅供内部人员使用；客户可通过政策搜索查看已发布政策。"

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_staff and all(
            access_decision(request.user, capability)["allowed"]
            for capability in ["policy_search", "policy_detail"]
        )


class KnowledgeAdminPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.is_staff
            and request.user.has_perm("policies.change_policy")
        )


class RelationReviewCandidateSerializer(serializers.ModelSerializer):
    from_title = serializers.CharField(source="from_policy.title", read_only=True)
    to_title = serializers.CharField(source="to_policy.title", read_only=True)
    from_source_url = serializers.CharField(source="from_policy.source_url", read_only=True)
    to_source_url = serializers.CharField(source="to_policy.source_url", read_only=True)
    evidence_title = serializers.CharField(
        source="evidence_policy.title", read_only=True, allow_null=True
    )

    class Meta:
        model = RelationReviewCandidate
        fields = [
            "id",
            "scan",
            "from_policy",
            "to_policy",
            "from_title",
            "to_title",
            "from_source_url",
            "to_source_url",
            "proposed_kind",
            "evidence_policy",
            "evidence_title",
            "evidence_quote",
            "confidence",
            "model_reason",
            "rejection_reason",
            "source_versions",
            "status",
            "reviewed_by",
            "reviewed_at",
            "relation",
            "created_at",
        ]
        read_only_fields = fields


class RelationReviewApprovalSerializer(serializers.Serializer):
    from_policy = serializers.UUIDField()
    to_policy = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=RelationKind.choices)
    evidence_policy = serializers.UUIDField()
    evidence_quote = serializers.CharField(min_length=5, max_length=5000)


class RelationReviewCandidateViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [KnowledgeAdminPermission]
    serializer_class = RelationReviewCandidateSerializer
    queryset = RelationReviewCandidate.objects.select_related(
        "scan", "from_policy", "to_policy", "evidence_policy", "reviewed_by", "relation"
    )

    def get_queryset(self):
        status_value = self.request.query_params.get("status", "pending")
        if status_value not in RelationReviewCandidate.Status.values and status_value != "all":
            raise serializers.ValidationError("未知的关系复审状态。")
        from .progress import scoped_candidates

        scope = self.request.query_params.get("scope", "all")
        if scope not in {"all", "current", "historical"}:
            raise serializers.ValidationError("未知的关系复审范围。")
        queryset = scoped_candidates(self.queryset, scope)
        return queryset if status_value == "all" else queryset.filter(status=status_value)

    @transaction.atomic
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        candidate = get_object_or_404(
            RelationReviewCandidate.objects.select_for_update(), pk=pk
        )
        payload = RelationReviewApprovalSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        values = payload.validated_data
        pair = {candidate.from_policy_id, candidate.to_policy_id}
        if {values["from_policy"], values["to_policy"]} != pair:
            raise serializers.ValidationError("人工复审只能调整当前两份政策之间的关系方向。")
        policies = {
            policy.pk: policy
            for policy in Policy.objects.select_for_update()
            .filter(pk__in=pair)
            .order_by("id")
        }
        if len(policies) != 2 or any(
            policy.status != "published" or policy.source_grade not in Policy.FORMAL_SOURCE_GRADES
            for policy in policies.values()
        ):
            raise serializers.ValidationError("关系两端必须是当前已发布的L1至L3政策。")
        evidence = policies.get(values["evidence_policy"])
        if evidence is None:
            raise serializers.ValidationError("关系证据必须来自关系两端之一。")
        quote = values["evidence_quote"].strip()
        if quote not in evidence.body:
            raise serializers.ValidationError("证据须逐字存在于所选政策的当前正文中。")
        relation, _ = PolicyRelation.objects.update_or_create(
            from_policy_id=values["from_policy"],
            to_policy_id=values["to_policy"],
            kind=values["kind"],
            defaults={
                "evidence_policy": evidence,
                "evidence_version": evidence.version,
                "evidence_quote": quote,
                "verification_status": "verified",
                "verified_by": request.user,
                "verified_at": timezone.now(),
                "discovery": {
                    "method": "human_override",
                    "source": "wiki_rejected_candidate",
                    "candidate_id": str(candidate.pk),
                    "scan_id": str(candidate.scan_id),
                    "original_kind": candidate.proposed_kind,
                    "original_rejection_reason": candidate.rejection_reason,
                    "locked": True,
                },
            },
        )
        candidate.status = RelationReviewCandidate.Status.APPROVED
        candidate.reviewed_by = request.user
        candidate.reviewed_at = timezone.now()
        candidate.relation = relation
        candidate.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "relation", "updated_at"]
        )
        AuditRecord.objects.create(
            actor=request.user,
            action="knowledge.relation_candidate.approved",
            object_id=candidate.pk,
            details={"relation_id": str(relation.pk), "kind": relation.kind},
        )
        from .relations import apply_derived_validity

        apply_derived_validity()
        return Response(RelationReviewCandidateSerializer(candidate).data)

    @transaction.atomic
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        candidate = get_object_or_404(
            RelationReviewCandidate.objects.select_for_update(), pk=pk
        )
        candidate.status = RelationReviewCandidate.Status.REJECTED
        candidate.reviewed_by = request.user
        candidate.reviewed_at = timezone.now()
        candidate.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        AuditRecord.objects.create(
            actor=request.user,
            action="knowledge.relation_candidate.rejected",
            object_id=candidate.pk,
            details={"rejection_reason": candidate.rejection_reason},
        )
        return Response(RelationReviewCandidateSerializer(candidate).data)


class KnowledgeQuery(serializers.Serializer):
    q = serializers.CharField(max_length=200, required=False, allow_blank=True)
    page_type = serializers.ChoiceField(
        choices=KnowledgePage.PageType.choices, required=False
    )


class KnowledgePageListSerializer(serializers.ModelSerializer):
    revision = serializers.IntegerField(source="current_revision.number", read_only=True)

    class Meta:
        model = KnowledgePage
        fields = [
            "id",
            "slug",
            "page_type",
            "title",
            "abstract",
            "status",
            "source_count",
            "revision",
            "built_at",
        ]


class KnowledgePageDetailSerializer(KnowledgePageListSerializer):
    body = serializers.CharField(source="current_revision.body", read_only=True)
    citations = serializers.JSONField(source="current_revision.citations", read_only=True)
    source_versions = serializers.JSONField(
        source="current_revision.source_versions", read_only=True
    )
    model = serializers.CharField(source="current_revision.model", read_only=True)
    prompt_version = serializers.CharField(
        source="current_revision.prompt_version", read_only=True
    )
    sources = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_sources(self, obj):
        return [
            {
                "id": str(source.policy_id),
                "title": source.policy.title,
                "version": source.policy_version,
                "source_url": source.policy.source_url,
            }
            for source in obj.page_sources.select_related("policy").all()
        ]

    class Meta(KnowledgePageListSerializer.Meta):
        fields = KnowledgePageListSerializer.Meta.fields + [
            "body",
            "citations",
            "source_versions",
            "model",
            "prompt_version",
            "sources",
        ]


class KnowledgePageViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [KnowledgePermission]
    serializer_class = KnowledgePageListSerializer
    lookup_field = "slug"
    queryset = KnowledgePage.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        query = KnowledgeQuery(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        invalid_sources = KnowledgePageSource.objects.filter(page_id=OuterRef("pk")).filter(
            Q(policy_version__lt=F("policy__version"))
            | Q(policy_version__gt=F("policy__version"))
            | ~Q(policy__status="published")
            | ~Q(policy__source_grade__in=["L1", "L2", "L3"])
        )
        qs = (
            KnowledgePage.objects.filter(status="published", current_revision__isnull=False)
            .annotate(has_invalid_source=Exists(invalid_sources))
            .filter(has_invalid_source=False)
        )
        if params.get("page_type"):
            qs = qs.filter(page_type=params["page_type"])
        if params.get("q"):
            qs = qs.filter(
                Q(title__icontains=params["q"]) | Q(abstract__icontains=params["q"])
            )
        return qs.select_related("current_revision")

    def get_serializer_class(self):
        return (
            KnowledgePageDetailSerializer
            if self.action == "retrieve"
            else KnowledgePageListSerializer
        )


class KnowledgeBuildSerializer(serializers.ModelSerializer):
    requested_by = serializers.CharField(source="requested_by.username", read_only=True)

    class Meta:
        model = KnowledgeBuild
        fields = [
            "id",
            "kind",
            "status",
            "attempts",
            "input_hash",
            "result",
            "error_code",
            "requested_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class KnowledgeBuildViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [KnowledgeAdminPermission]
    serializer_class = KnowledgeBuildSerializer
    queryset = KnowledgeBuild.objects.select_related("requested_by").all()

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="progress")
    def progress(self, request):
        from .progress import relation_progress

        return Response(relation_progress())

    @extend_schema(request=None, responses=KnowledgeBuildSerializer)
    @action(detail=False, methods=["post"], url_path="sync")
    def sync(self, request):
        build, created = enqueue_sync(request.user, force=True)
        AuditRecord.objects.create(
            actor=request.user,
            action="knowledge.sync.requested",
            object_id=build.pk,
            details={"created": created, "input_hash": build.input_hash},
        )
        return Response(
            self.get_serializer(build).data,
            status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
        )

    @extend_schema(request=None, responses=KnowledgeBuildSerializer)
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        build = get_object_or_404(KnowledgeBuild, pk=pk)
        if build.status != "failed":
            raise serializers.ValidationError("仅可重试失败的知识库构建任务。")
        build.status = "queued"
        build.attempts = 0
        build.error_code = ""
        build.lease_until = None
        build.requested_by = request.user
        build.save()
        return Response(self.get_serializer(build).data)


class KnowledgeAdminPageViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [KnowledgeAdminPermission]
    serializer_class = KnowledgePageDetailSerializer
    queryset = KnowledgePage.objects.select_related("current_revision").all()

    @extend_schema(request=None, responses=dict)
    @action(detail=False, methods=["post"], url_path="lint")
    def lint(self, request):
        result = lint_all()
        AuditRecord.objects.create(
            actor=request.user,
            action="knowledge.lint.completed",
            object_id=(
                KnowledgePage.objects.values_list("pk", flat=True).first() or uuid.uuid4()
            ),
            details=result,
        )
        return Response(result)

    @extend_schema(request=None, responses=dict)
    @action(detail=False, methods=["post"], url_path="export-obsidian")
    def export_obsidian(self, request):
        lint = lint_all()
        result = export_vault()
        result["lint"] = lint
        AuditRecord.objects.create(
            actor=request.user,
            action="knowledge.obsidian.exported",
            object_id=(
                KnowledgePage.objects.values_list("pk", flat=True).first() or uuid.uuid4()
            ),
            details=result,
        )
        return Response(result)

    @extend_schema(request=None, responses=dict)
    @action(detail=False, methods=["post"], url_path="import-obsidian")
    def import_obsidian(self, request):
        result = import_vault_edits(request.user)
        AuditRecord.objects.create(
            actor=request.user,
            action="knowledge.obsidian.imported",
            object_id=(
                KnowledgePage.objects.values_list("pk", flat=True).first() or uuid.uuid4()
            ),
            details={
                "imported": result["imported"],
                "unchanged": result["unchanged"],
                "conflicts": len(result["conflicts"]),
            },
        )
        return Response(result)

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"], url_path="issues")
    def issues(self, request, pk=None):
        page = self.get_object()
        issues = KnowledgeLintIssue.objects.filter(page=page, resolved_at__isnull=True)
        return Response(
            [
                {
                    "id": str(issue.pk),
                    "code": issue.code,
                    "severity": issue.severity,
                    "message": issue.message,
                    "citation_index": issue.citation_index,
                }
                for issue in issues
            ]
        )
