from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from knowledge.models import KnowledgePage
from policies.catalog import CatalogAdminPermission
from policies.models import Policy
from policies.taxonomy import OpportunityLevel, RelationKind
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import EvaluationResult, EvaluationRun, EvaluationSample
from .services import (
    create_sample,
    evaluate,
    label_sample,
    prediction,
    prediction_hash,
    seed_samples,
    stale_reason,
)


class SampleSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    labeled_by_name = serializers.CharField(
        source="labeled_by.username", read_only=True, default=""
    )

    class Meta:
        model = EvaluationSample
        fields = [
            "id",
            "kind",
            "kind_label",
            "title",
            "policy",
            "related_policy",
            "page",
            "relation_kind",
            "origin",
            "status",
            "status_label",
            "gold",
            "label_version",
            "labeled_at",
            "labeled_by_name",
            "created_at",
        ]
        read_only_fields = fields


class SampleDetailSerializer(SampleSerializer):
    stale_reason = serializers.SerializerMethodField()
    prediction = serializers.SerializerMethodField()
    prediction_hash = serializers.SerializerMethodField()

    class Meta(SampleSerializer.Meta):
        fields = SampleSerializer.Meta.fields + [
            "snapshot",
            "stale_reason",
            "prediction",
            "prediction_hash",
        ]
        read_only_fields = fields

    def get_stale_reason(self, obj) -> str:
        return stale_reason(obj)

    def get_prediction(self, obj) -> dict | None:
        # The hash must identify exactly the prediction returned in this response.
        if not hasattr(self, "_predictions"):
            self._predictions = {}
        if obj.pk not in self._predictions:
            self._predictions[obj.pk] = None if stale_reason(obj) else prediction(obj)
        return self._predictions[obj.pk]

    def get_prediction_hash(self, obj) -> str:
        return prediction_hash(self.get_prediction(obj))


class LabelRequest(serializers.Serializer):
    prediction_hash = serializers.CharField(min_length=64, max_length=64)
    label_version = serializers.IntegerField(min_value=0)
    notes = serializers.CharField(min_length=5, max_length=3000)
    opportunity_level = serializers.ChoiceField(choices=OpportunityLevel.choices, required=False)
    verdict = serializers.BooleanField(required=False)
    evidence_supported = serializers.BooleanField()
    evidence_policy_id = serializers.UUIDField(required=False, allow_null=True)
    evidence_quote = serializers.CharField(required=False, allow_blank=True, max_length=10000)


class CreateSampleRequest(serializers.Serializer):
    kind = serializers.ChoiceField(choices=EvaluationSample.Kind.choices)
    policy_id = serializers.UUIDField(required=False)
    related_policy_id = serializers.UUIDField(required=False)
    page_id = serializers.UUIDField(required=False)
    relation_kind = serializers.ChoiceField(choices=RelationKind.choices, required=False)


class CreateSampleResponse(serializers.Serializer):
    created = serializers.BooleanField()
    sample = SampleSerializer()


class SampleViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [CatalogAdminPermission]
    queryset = EvaluationSample.objects.select_related(
        "policy", "related_policy", "page__current_revision", "labeled_by"
    )
    serializer_class = SampleSerializer

    def get_serializer_class(self):
        return SampleDetailSerializer if self.action in {"retrieve", "label"} else SampleSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        for name, choices in (
            ("status", ["pending", "labeled", "retired"]),
            ("kind", EvaluationSample.Kind.values),
        ):
            value = self.request.query_params.get(name)
            if value:
                if value not in choices:
                    raise serializers.ValidationError("请选择有效的样本类型和标注状态。")
                qs = qs.filter(**{name: value})
        if self.request.query_params.get("q"):
            qs = qs.filter(title__icontains=self.request.query_params["q"])
        return qs

    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response(
            {
                "counts": list(
                    EvaluationSample.objects.values("kind", "status").annotate(count=Count("pk"))
                )
            }
        )

    @action(detail=False, methods=["post"])
    def seed(self, request):
        limit = serializers.IntegerField(min_value=6, max_value=60).run_validation(
            request.data.get("limit", 30)
        )
        return Response({"created": seed_samples(request.user, limit)})

    @extend_schema(request=CreateSampleRequest, responses=CreateSampleResponse)
    @action(detail=False, methods=["post"], url_path="add")
    def add_sample(self, request):
        form = CreateSampleRequest(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        kwargs = {"kind": data["kind"], "relation_kind": data.get("relation_kind", "")}
        for field, model in (
            ("policy", Policy),
            ("related_policy", Policy),
            ("page", KnowledgePage),
        ):
            if data.get(f"{field}_id"):
                kwargs[field] = get_object_or_404(model, pk=data[f"{field}_id"])
        sample, created = create_sample(**kwargs)
        return Response(
            {"created": created, "sample": SampleSerializer(sample).data},
            status=201 if created else 200,
        )

    @extend_schema(request=LabelRequest, responses=SampleDetailSerializer)
    @action(detail=True, methods=["post"])
    def label(self, request, pk=None):
        sample = self.get_object()
        form = LabelRequest(data=request.data)
        form.is_valid(raise_exception=True)
        updated = label_sample(sample.pk, request.user, form.validated_data)
        return Response(SampleDetailSerializer(updated).data)

    @action(detail=True, methods=["post"])
    def retire(self, request, pk=None):
        from core.models import AuditRecord
        from django.db import transaction

        with transaction.atomic():
            sample = get_object_or_404(self.get_queryset().select_for_update(of=("self",)), pk=pk)
            sample.status = "retired"
            sample.save(update_fields=["status", "updated_at"])
            AuditRecord.objects.create(
                actor=request.user, action="quality.sample.retired", object_id=sample.pk
            )
        return Response(SampleSerializer(sample).data)

    @action(detail=False, methods=["get"])
    def policies(self, request):
        q = request.query_params.get("q", "").strip()
        qs = Policy.objects.filter(is_demo=False)
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(document_number__icontains=q))
        return Response(
            {"items": list(qs.order_by("-publication_date", "id").values("id", "title")[:30])}
        )


class RunSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvaluationRun
        fields = ["id", "status", "protocol_version", "config_version", "metrics", "created_at"]
        read_only_fields = fields


class ResultSerializer(serializers.ModelSerializer):
    sample_title = serializers.CharField(source="sample.title", read_only=True)
    kind = serializers.CharField(source="sample.kind", read_only=True)

    class Meta:
        model = EvaluationResult
        fields = [
            "id",
            "sample",
            "sample_title",
            "kind",
            "label_version",
            "status",
            "reason",
            "gold",
            "prediction",
            "correct",
        ]
        read_only_fields = fields


class RunViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [CatalogAdminPermission]
    queryset = EvaluationRun.objects.all()
    serializer_class = RunSerializer

    @action(detail=False, methods=["post"])
    def evaluate(self, request):
        return Response(RunSerializer(evaluate(request.user)).data, status=201)

    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        run = self.get_object()
        qs = run.results.select_related("sample").order_by("correct", "sample_id")
        if request.query_params.get("errors_only") == "true":
            qs = qs.filter(Q(correct=False) | ~Q(status="evaluated"))
        return self.get_paginated_response(
            ResultSerializer(self.paginate_queryset(qs), many=True).data
        )
