from pathlib import Path
from urllib.parse import urlparse

from accounts.permissions import IsSystemManager
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.ai_runtime import ensure_ai_profiles, get_ai_profile
from core.business_config import (
    baseline_documents,
    checksum,
    clear_config_cache,
    config_version,
    validate_documents,
)
from core.models import (
    AIModelProfile,
    SystemConfigAudit,
    SystemConfigDocument,
    SystemConfigRelease,
)


class SystemConfigPermission(IsSystemManager):
    pass


class SystemConfigDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemConfigDocument
        fields = ["id", "release", "key", "content", "checksum", "updated_at"]
        read_only_fields = ["id", "checksum", "updated_at"]

    def validate(self, attrs):
        release = attrs.get("release") or self.instance.release
        if release.status != SystemConfigRelease.Status.DRAFT:
            raise serializers.ValidationError("只能修改草稿配置版本。")
        content = attrs.get("content", getattr(self.instance, "content", None))
        if not isinstance(content, dict):
            raise serializers.ValidationError("配置内容必须是 JSON 对象。")
        return attrs

    def create(self, validated_data):
        validated_data["checksum"] = checksum(validated_data["content"])
        instance = super().create(validated_data)
        SystemConfigAudit.objects.create(
            release=instance.release,
            namespace=instance.key,
            action="created",
            after=instance.content,
            actor=self.context["request"].user,
        )
        return instance

    def update(self, instance, validated_data):
        before = instance.content
        validated_data["checksum"] = checksum(validated_data.get("content", before))
        instance = super().update(instance, validated_data)
        SystemConfigAudit.objects.create(
            release=instance.release,
            namespace=instance.key,
            action="updated",
            before=before,
            after=instance.content,
            actor=self.context["request"].user,
        )
        return instance


class SystemConfigReleaseSerializer(serializers.ModelSerializer):
    document_count = serializers.IntegerField(source="documents.count", read_only=True)
    created_by = serializers.CharField(source="created_by.username", read_only=True)
    published_by = serializers.CharField(source="published_by.username", read_only=True)

    class Meta:
        model = SystemConfigRelease
        fields = [
            "id",
            "version",
            "schema_version",
            "status",
            "checksum",
            "document_count",
            "created_by",
            "published_by",
            "published_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "checksum",
            "document_count",
            "created_by",
            "published_by",
            "published_at",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data):
        request = self.context["request"]
        release = SystemConfigRelease.objects.create(created_by=request.user, **validated_data)
        for key, content in baseline_documents().items():
            SystemConfigDocument.objects.create(
                release=release, key=key, content=content, checksum=checksum(content)
            )
        SystemConfigAudit.objects.create(
            release=release,
            action="baseline_created",
            after={"documents": release.documents.count()},
            actor=request.user,
        )
        return release


class SystemConfigReleaseViewSet(viewsets.ModelViewSet):
    permission_classes = [SystemConfigPermission]
    serializer_class = SystemConfigReleaseSerializer
    queryset = SystemConfigRelease.objects.select_related("created_by", "published_by").all()
    http_method_names = ["get", "post", "head", "options"]

    def _documents(self, release):
        return {item.key: item.content for item in release.documents.all()}

    @action(detail=True, methods=["post"])
    def validate(self, request, pk=None):
        release = self.get_object()
        errors = validate_documents(self._documents(release))
        return Response({"valid": not errors, "errors": errors, "documents": release.documents.count()})

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        with transaction.atomic():
            release = SystemConfigRelease.objects.select_for_update().get(pk=pk)
            if release.status != SystemConfigRelease.Status.DRAFT:
                raise serializers.ValidationError("只能发布草稿配置版本。")
            documents = self._documents(release)
            errors = validate_documents(documents)
            if errors:
                raise serializers.ValidationError({"配置校验失败": errors})
            package_checksum = checksum(documents)
            SystemConfigRelease.objects.filter(
                status=SystemConfigRelease.Status.PUBLISHED
            ).update(status=SystemConfigRelease.Status.ARCHIVED)
            release.status = SystemConfigRelease.Status.PUBLISHED
            release.checksum = package_checksum
            release.published_by = request.user
            release.published_at = timezone.now()
            release.save()
            clear_config_cache()
            SystemConfigAudit.objects.create(
                release=release,
                action="published",
                after={"checksum": package_checksum},
                actor=request.user,
            )
        return Response(self.get_serializer(release).data)

    @action(detail=True, methods=["post"])
    def clone(self, request, pk=None):
        source = self.get_object()
        version = str(request.data.get("version", "")).strip()
        if not version:
            raise serializers.ValidationError("请提供新版本号。")
        with transaction.atomic():
            target = SystemConfigRelease.objects.create(
                version=version, schema_version=source.schema_version, created_by=request.user
            )
            SystemConfigDocument.objects.bulk_create(
                [
                    SystemConfigDocument(
                        release=target,
                        key=item.key,
                        content=item.content,
                        checksum=item.checksum,
                    )
                    for item in source.documents.all()
                ]
            )
            SystemConfigAudit.objects.create(
                release=target,
                action="cloned",
                after={"source_release": str(source.pk)},
                actor=request.user,
            )
        return Response(self.get_serializer(target).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        release = self.get_object()
        return Response(
            {
                "manifest": {
                    "schema_version": release.schema_version,
                    "release_version": release.version,
                    "checksum": release.checksum,
                },
                "documents": self._documents(release),
            }
        )

    @action(detail=False, methods=["post"], url_path="import")
    def import_package(self, request):
        package = request.data
        package_manifest = package.get("manifest", {})
        documents = package.get("documents", {})
        version = str(package_manifest.get("release_version", "")).strip()
        if not version or not isinstance(documents, dict):
            raise serializers.ValidationError("导入包必须包含版本号和 documents 对象。")
        errors = validate_documents(documents)
        if errors:
            raise serializers.ValidationError({"配置校验失败": errors})
        with transaction.atomic():
            release = SystemConfigRelease.objects.create(
                version=version,
                schema_version=str(package_manifest.get("schema_version", "1.0")),
                created_by=request.user,
            )
            SystemConfigDocument.objects.bulk_create(
                [
                    SystemConfigDocument(
                        release=release,
                        key=key,
                        content=content,
                        checksum=checksum(content),
                    )
                    for key, content in documents.items()
                ]
            )
            SystemConfigAudit.objects.create(
                release=release,
                action="imported",
                after={"documents": len(documents)},
                actor=request.user,
            )
        return Response(self.get_serializer(release).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def rollback(self, request, pk=None):
        with transaction.atomic():
            release = SystemConfigRelease.objects.select_for_update().get(pk=pk)
            if release.status != SystemConfigRelease.Status.ARCHIVED:
                raise serializers.ValidationError("只能回滚到历史已发布配置。")
            errors = validate_documents(self._documents(release))
            if errors:
                raise serializers.ValidationError({"配置校验失败": errors})
            SystemConfigRelease.objects.filter(
                status=SystemConfigRelease.Status.PUBLISHED
            ).update(status=SystemConfigRelease.Status.ARCHIVED)
            release.status = SystemConfigRelease.Status.PUBLISHED
            release.published_by = request.user
            release.published_at = timezone.now()
            release.save()
            clear_config_cache()
            SystemConfigAudit.objects.create(
                release=release,
                action="rolled_back",
                after={"checksum": release.checksum},
                actor=request.user,
            )
        return Response(self.get_serializer(release).data)

    @action(detail=False, methods=["get"], url_path="current")
    def current(self, request):
        release = self.queryset.filter(status=SystemConfigRelease.Status.PUBLISHED).first()
        return Response(
            self.get_serializer(release).data if release else {"version": config_version(), "status": "baseline"}
        )


class SystemConfigDocumentViewSet(viewsets.ModelViewSet):
    permission_classes = [SystemConfigPermission]
    serializer_class = SystemConfigDocumentSerializer
    queryset = SystemConfigDocument.objects.select_related("release").all()
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        release = self.request.query_params.get("release")
        return qs.filter(release_id=release) if release else qs

    def perform_destroy(self, instance):
        if instance.release.status != SystemConfigRelease.Status.DRAFT:
            raise serializers.ValidationError("只能删除草稿配置。")
        SystemConfigAudit.objects.create(
            release=instance.release,
            namespace=instance.key,
            action="deleted",
            before=instance.content,
            actor=self.request.user,
        )
        instance.delete()


class AIModelProfileSerializer(serializers.ModelSerializer):
    purpose_label = serializers.CharField(source="get_purpose_display", read_only=True)
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_api_key = serializers.SerializerMethodField()
    configured = serializers.SerializerMethodField()

    class Meta:
        model = AIModelProfile
        fields = [
            "id",
            "purpose",
            "purpose_label",
            "enabled",
            "base_url",
            "model",
            "concurrency",
            "thinking", "context_tokens", "max_output_tokens",
            "input_price", "output_price", "currency",
            "api_key",
            "has_api_key",
            "configured",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "purpose",
            "purpose_label",
            "has_api_key",
            "configured",
            "updated_at",
        ]

    @extend_schema_field(serializers.BooleanField())
    def get_has_api_key(self, obj):
        return bool(obj.api_key)

    @extend_schema_field(serializers.BooleanField())
    def get_configured(self, obj):
        return get_ai_profile(obj.purpose).configured

    def validate(self, attrs):
        instance = self.instance
        base_url = attrs.get("base_url", getattr(instance, "base_url", ""))
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise serializers.ValidationError({"base_url": "请输入正确的模型服务地址。"})
        if parsed.username or parsed.password:
            raise serializers.ValidationError({"base_url": "模型服务地址不能包含用户名或密码。"})
        if (Path("/.dockerenv").exists() and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                and parsed.port == 11434):
            raise serializers.ValidationError({"base_url": "应用正在 Docker 中运行。本机 Ollama 请使用 http://host.docker.internal:11434/v1；localhost 指向应用容器，无法连接电脑上的模型。"})
        if "api_key" in attrs and not attrs["api_key"]:
            attrs.pop("api_key")
        context = attrs.get("context_tokens", getattr(instance, "context_tokens", None))
        output = attrs.get("max_output_tokens", getattr(instance, "max_output_tokens", None))
        if context and output and output >= context:
            raise serializers.ValidationError({"max_output_tokens": "输出上限必须小于上下文长度，需为政策原文和指令留出空间。"})
        return attrs

    def update(self, instance, validated_data):
        instance.updated_by = self.context["request"].user
        instance = super().update(instance, validated_data)
        return instance


class AIModelProfileViewSet(viewsets.ModelViewSet):
    permission_classes = [SystemConfigPermission]
    serializer_class = AIModelProfileSerializer
    queryset = AIModelProfile.objects.all()
    http_method_names = ["get", "patch", "head", "options"]

    def list(self, request, *args, **kwargs):
        ensure_ai_profiles()
        return super().list(request, *args, **kwargs)
