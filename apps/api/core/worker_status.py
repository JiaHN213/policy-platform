"""Read-only worker discovery. No task arguments, credentials or control actions."""

from config.celery import app
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .config_views import SystemConfigPermission


def worker_status():
    groups = [
        ("ingestion", "采集与解析", settings.CELERY_INGESTION_QUEUE),
        ("analysis", "AI 与业务处理", app.conf.task_default_queue),
        ("notifications", "订阅与通知", settings.CELERY_NOTIFICATION_QUEUE),
    ]
    cache_key = "worker-status:" + ":".join(queue for _, _, queue in groups)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        with app.connection_for_read(
            connect_timeout=2,
            transport_options={
                **app.conf.broker_transport_options,
                "socket_connect_timeout": 2,
                "socket_timeout": 2,
                "retry_on_timeout": False,
            },
        ) as connection:
            connection.ensure_connection(max_retries=0)
            replies = app.control.inspect(connection=connection, timeout=1.5).active_queues() or {}
    except Exception:
        replies = {}
    # An absent reply may be a timeout, not proof that a process is stopped.
    consumers = {}
    for node, queues in replies.items():
        if isinstance(queues, list):
            for queue in queues:
                if isinstance(queue, dict) and isinstance(queue.get("name"), str):
                    consumers.setdefault(queue["name"], set()).add(node)
    items = []
    for key, label, queue in groups:
        nodes = consumers.get(queue, set())
        shared = any(
            other_key != key and (other_queue == queue or nodes & consumers.get(other_queue, set()))
            for other_key, _, other_queue in groups
        )
        items.append(
            {
                "key": key,
                "label": label,
                "consumer_count": len(nodes),
                "shared": shared,
                "state": "responding" if nodes else "unconfirmed",
                "message": "已发现处理服务；与其他环节共用服务"
                if nodes and shared
                else "已发现独立处理服务"
                if nodes
                else "暂未收到处理服务响应，请确认 Docker 后台服务已启动，或稍后重查。",
            }
        )
    result = {
        "checked_at": timezone.now().isoformat(),
        "items": items,
        "notice": "这是当前服务响应快照，短暂缓存 10 秒。收到响应不代表任务已完成、AI 模型可用或外部网站可访问；未收到响应也可能是繁忙或通信超时。",
    }
    cache.set(cache_key, result, timeout=10)
    return result


class WorkerGroupSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    consumer_count = serializers.IntegerField()
    shared = serializers.BooleanField()
    state = serializers.CharField()
    message = serializers.CharField()


class WorkerStatusSerializer(serializers.Serializer):
    checked_at = serializers.DateTimeField()
    items = WorkerGroupSerializer(many=True)
    notice = serializers.CharField()


class WorkerStatusView(APIView):
    permission_classes = [SystemConfigPermission]

    @extend_schema(responses=WorkerStatusSerializer)
    def get(self, request):
        return Response(worker_status())
