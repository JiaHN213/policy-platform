from accounts.services import access_decision
from django.db import connection
from drf_spectacular.utils import extend_schema
from policies.models import Policy
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from subscriptions.delivery import visible_notifications
from subscriptions.models import Subscription

from core.business_config import config_version, get_config


class HealthView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(responses=dict)
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return Response({"status": "ok", "database": "connected"})
        except Exception:
            return Response({"status": "unavailable", "database": "unavailable"}, status=503)


class OverviewView(APIView):
    @extend_schema(responses=dict)
    def get(self, request):
        if not access_decision(request.user, "policy_detail")["allowed"]:
            raise PermissionDenied()
        policies = Policy.objects.filter(
            status="published", source_grade__in=Policy.FORMAL_SOURCE_GRADES
        )
        if not request.user.is_staff:
            policies = policies.filter(is_demo=False)
        return Response(
            {
                "policies": policies.count(),
                "subscriptions": Subscription.objects.filter(
                    user=request.user, active=True
                ).count(),
                "unread": visible_notifications(request.user).filter(read_at__isnull=True).count(),
            }
        )


class TaxonomyView(APIView):
    @extend_schema(responses=dict)
    def get(self, request):
        policies = Policy.objects.filter(
            status="published", source_grade__in=Policy.FORMAL_SOURCE_GRADES
        )
        if not request.user.is_staff:
            policies = policies.filter(is_demo=False)
        scope = get_config("business_scope")
        configured_taxonomies = get_config("system_taxonomies")
        return Response(
            {
                "version": config_version(),
                "industries": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["industries"]
                    if item.get("enabled", True)
                ],
                "business_domains": [
                    {"value": item["code"], "label": item["label"]}
                    for item in scope["business_domains"]
                    if item.get("enabled", True)
                ],
                "direction_tags": [
                    {"value": item["code"], "label": item["label"]}
                    for item in scope["direction_tags"]
                    if item.get("enabled", True)
                ],
                "opportunity_levels": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["opportunity_levels"]
                    if item.get("enabled", True)
                ],
                "document_roles": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["document_roles"]
                    if item.get("enabled", True)
                ],
                "acquisition_methods": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["acquisition_methods"]
                    if item.get("enabled", True)
                ],
                "validity_statuses": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["validity_statuses"]
                    if item.get("enabled", True)
                ],
                "opportunity_categories": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["opportunity_categories"]
                    if item.get("enabled", True)
                ],
                "opportunity_statuses": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["opportunity_statuses"]
                    if item.get("enabled", True)
                ],
                "verification_statuses": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["verification_statuses"]
                    if item.get("enabled", True)
                ],
                "relation_kinds": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["relation_kinds"]
                    if item.get("enabled", True)
                ],
                "source_grades": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["source_grades"]
                    if item.get("enabled", True)
                ],
                "geographic_levels": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["geographic_levels"]
                    if item.get("enabled", True)
                ],
                "document_types": [
                    {"value": item["code"], "label": item["label"]}
                    for item in configured_taxonomies["document_types"]
                    if item.get("enabled", True)
                ],
                "topics": ["水务", "环保", "人工智能＋"],
                "regions": sorted(set(policies.values_list("region", flat=True))),
            }
        )
