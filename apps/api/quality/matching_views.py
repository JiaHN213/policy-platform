from core.config_views import SystemConfigPermission
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from enterprises.views import profiles_for
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .matching import accessible_studies, create_study, label_case, metrics, observe_runtime
from .models import MatchingCase, MatchingObservation, MatchingStudy


class StudyInput(serializers.Serializer):
    profile = serializers.UUIDField()
    project = serializers.UUIDField(allow_null=True, default=None)
    view = serializers.ChoiceField(choices=["policies", "opportunities"], default="opportunities")
    limit = serializers.IntegerField(min_value=4, max_value=40, default=20)


class LabelInput(serializers.Serializer):
    verdict = serializers.ChoiceField(choices=["relevant", "irrelevant", "unsure"])
    evidence_supported = serializers.BooleanField(allow_null=True, default=None)
    notes = serializers.CharField(min_length=5, max_length=2000)
    quote = serializers.CharField(max_length=5000, allow_blank=True, default="")
    label_version = serializers.IntegerField(min_value=0)


class CaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchingCase
        fields = [
            "id",
            "policy",
            "snapshot",
            "predicted",
            "prediction",
            "verdict",
            "evidence_supported",
            "notes",
            "quote",
            "label_version",
            "labeled_at",
        ]
        read_only_fields = fields


class StudySerializer(serializers.ModelSerializer):
    metrics = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DictField())
    def get_metrics(self, obj):
        return metrics(obj)

    class Meta:
        model = MatchingStudy
        fields = [
            "id",
            "profile",
            "project",
            "view",
            "snapshot",
            "selection",
            "metrics",
            "created_at",
        ]
        read_only_fields = fields


class StudyDetailSerializer(StudySerializer):
    cases = CaseSerializer(many=True, read_only=True)

    class Meta(StudySerializer.Meta):
        fields = StudySerializer.Meta.fields + ["cases"]
        read_only_fields = fields


class MatchingStudyViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminUser]
    queryset = MatchingStudy.objects.none()
    serializer_class = StudySerializer

    def get_queryset(self):
        result = accessible_studies(self.request.user).prefetch_related("cases")
        if self.request.query_params.get("profile"):
            profile = serializers.UUIDField().run_validation(self.request.query_params["profile"])
            result = result.filter(profile_id=profile)
        return result

    @extend_schema(parameters=[OpenApiParameter("profile", type=str)])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def get_serializer_class(self):
        return StudyDetailSerializer if self.action in {"retrieve", "sample"} else StudySerializer

    @extend_schema(request=StudyInput, responses=StudyDetailSerializer)
    @action(detail=False, methods=["post"])
    def sample(self, request):
        data = StudyInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        profile = get_object_or_404(profiles_for(request.user), pk=values["profile"])
        project = (
            get_object_or_404(profile.projects, pk=values["project"]) if values["project"] else None
        )
        study = create_study(request.user, profile, project, values["view"], values["limit"])
        return Response(StudyDetailSerializer(study).data, status=201)

    @extend_schema(request=LabelInput, responses=CaseSerializer)
    @action(detail=True, methods=["post"], url_path=r"label/(?P<case_id>[0-9a-f-]{36})")
    def label(self, request, pk=None, case_id=None):
        study = self.get_object()
        get_object_or_404(study.cases, pk=case_id)
        data = LabelInput(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(CaseSerializer(label_case(request.user, case_id, data.validated_data)).data)


class ObservationSerializer(serializers.Serializer):
    current = serializers.DictField()
    history = serializers.ListField(child=serializers.DictField())


class MatchingObservationView(APIView):
    permission_classes = [SystemConfigPermission]

    @extend_schema(responses=ObservationSerializer)
    def get(self, request):
        return Response(
            {
                "current": observe_runtime(),
                "history": list(MatchingObservation.objects.values("hour", "metrics")[:48]),
            }
        )
