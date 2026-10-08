from accounts.models import User
from accounts.permissions import IsSystemManager
from core.errors import Conflict
from core.models import AuditRecord
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import EnterpriseProfile, ScopedSettings
from .quota import statistics, window
from .scoped_settings import (
    ENTERPRISE_FIELDS,
    USER_FIELDS,
    recommendation_configuration,
    research_configuration,
)


class ScopedInput(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    overrides = serializers.DictField()

    def validate_overrides(self, values):
        fields = {item["key"]: item for item in self.context["fields"]}
        if values.keys() - fields.keys():
            raise serializers.ValidationError("包含不属于当前账号或企业的设置，请刷新后重试。")
        cleaned = {}
        for key, value in values.items():
            if value is None:
                continue
            item = fields[key]
            validator = serializers.BooleanField() if item["kind"] == "boolean" else serializers.IntegerField(min_value=item["min"], max_value=item["max"])
            try:
                cleaned[key] = validator.run_validation(value)
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({item["label"]: exc.detail}) from exc
        return cleaned


class ScopedSettingsView(APIView):
    permission_classes = [IsSystemManager]
    scope = "user"

    def target(self, pk, *, lock=False):
        model = User if self.scope == "user" else EnterpriseProfile
        query = model.objects.all()
        if lock:
            query = query.select_for_update()
        return get_object_or_404(query, pk=pk)

    def payload(self, target):
        filters = {self.scope if self.scope == "user" else "profile": target}
        row = ScopedSettings.objects.filter(**filters).first()
        base = research_configuration()
        default_rec = recommendation_configuration()
        fields = USER_FIELDS if self.scope == "user" else ENTERPRISE_FIELDS
        defaults = {item["key"]: getattr(default_rec, item["key"].removeprefix("recommendation_")) if item["key"].startswith("recommendation_") else getattr(base, item["key"]) for item in fields}
        if self.scope == "enterprise":
            from .agent import enabled_for

            defaults["agent_enabled"] = enabled_for(base, target)
            defaults["recommendation_enabled"] = default_rec.enabled and (default_rec.all_organizations or str(target.organization_id) in default_rec.organizations)
        values = row.overrides if row else {}
        payload = {"revision": row.revision if row else 0, "fields": fields, "defaults": defaults, "overrides": values, "effective": defaults | values}
        if self.scope == "enterprise":
            effective_rec = recommendation_configuration(target)
            effective_research = research_configuration(profile=target)
            payload["effective"]["agent_enabled"] = effective_research.research_allowed and enabled_for(effective_research, target)
            payload["effective"]["recommendation_enabled"] = effective_rec.enabled and (effective_rec.all_organizations or str(target.organization_id) in effective_rec.organizations)
        if self.scope == "user":
            payload["usage"] = statistics(window(target))
        return payload

    @extend_schema(responses=dict)
    def get(self, request, pk):
        return Response(self.payload(self.target(pk)))

    @extend_schema(request=ScopedInput, responses=dict)
    def patch(self, request, pk):
        fields = USER_FIELDS if self.scope == "user" else ENTERPRISE_FIELDS
        data = ScopedInput(data=request.data, context={"fields": fields})
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            target = self.target(pk, lock=True)
            filters = {self.scope if self.scope == "user" else "profile": target}
            row = ScopedSettings.objects.filter(**filters).first()
            if data.validated_data["revision"] != (row.revision if row else 0):
                raise Conflict("设置已被更新，请刷新后重新修改。")
            previous = row.overrides if row else {}
            if row:
                row.overrides = data.validated_data["overrides"]
                row.revision += 1
                row.save()
            else:
                row = ScopedSettings.objects.create(**filters, overrides=data.validated_data["overrides"])
            AuditRecord.objects.create(actor=request.user, action="enterprise.scoped_settings.updated", object_id=row.pk,
                                      details={"scope": self.scope, "target_id": str(target.pk), "previous": previous, "current": row.overrides, "revision": row.revision})
            return Response(self.payload(target))


class EnterpriseSettingsView(ScopedSettingsView):
    scope = "enterprise"
