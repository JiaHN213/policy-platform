from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework import status as http_status
from rest_framework.response import Response
from rest_framework.views import APIView

from .catalog import CatalogAdminPermission
from .opensearch import OpenSearchUnavailable, status, sync_index
from .search_tasks import sync_opensearch


class SearchIndexAction(serializers.Serializer):
    rebuild = serializers.BooleanField(default=False)


class SearchIndexView(APIView):
    permission_classes = [CatalogAdminPermission]

    @extend_schema(responses=dict)
    def get(self, request):
        return Response(status())

    @extend_schema(request=SearchIndexAction, responses=dict)
    def post(self, request):
        serializer = SearchIndexAction(data=request.data)
        serializer.is_valid(raise_exception=True)
        rebuild = serializer.validated_data["rebuild"]
        if settings.LOCAL_WORKER:
            try:
                return Response(sync_index(rebuild=rebuild))
            except OpenSearchUnavailable as exc:
                return Response(
                    {**status(), "message": str(exc)},
                    status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        sync_opensearch.delay(rebuild=rebuild)
        return Response(
            {**status(check_cluster=False), "queued": True, "rebuild": rebuild},
            status=http_status.HTTP_202_ACCEPTED,
        )
