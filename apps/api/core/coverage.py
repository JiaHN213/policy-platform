from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from ingestion.models import Source
from policies.models import PolicyEnrichment
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView


class SourceCoverageView(APIView):
    permission_classes = [IsAdminUser]
    @extend_schema(responses=dict)
    def get(self, request):
        now = timezone.now()
        items = []
        for source in Source.objects.filter(verification_status="verified").order_by("name"):
            last = source.runs.order_by("-created_at").first()
            rejected = PolicyEnrichment.objects.filter(policy_id=OuterRef("policy_id"), policy_version=OuterRef("policy__version"), status="succeeded", result__review__decision="exclude")
            rows = source.discovereditem_set.annotate(ai_excluded=Exists(rejected)).values("created_at", "status", "policy__status", "policy__published_at", "ai_excluded")
            pending = overdue = finished = within = 0
            for row in rows.iterator():
                if row["policy__status"] == "published" and row["policy__published_at"]:
                    if row["policy__published_at"] >= row["created_at"]:
                        finished += 1
                        within += int(row["policy__published_at"] - row["created_at"] <= timedelta(hours=24))
                elif row["status"] != "excluded" and not row["ai_excluded"] and row["policy__status"] != "withdrawn":
                    pending += 1
                    overdue += int(now - row["created_at"] > timedelta(hours=24))
            state = "已暂停" if not source.enabled else "检查失败，需处理" if last and last.status == "failed" else "检查处理中" if last and last.status in {"queued", "running"} else "最近检查已完成" if last and last.status == "succeeded" else "尚无成功检查记录"
            items.append({"name": source.name, "url": source.url, "state": state, "enabled": source.enabled,
                          "last_success_at": source.last_success_at, "next_check_at": source.next_check_at,
                          "pending_links": pending, "overdue_24h_links": overdue,
                          "published_samples": finished, "published_within_24h": within})
        return Response({"items": items, "measured_at": now, "notice": "仅覆盖列出的已接入来源。待处理按政策链接统计；24小时为发现至发布的建设目标，不是服务承诺。历史导入可能影响样本统计，检查失败不代表没有新增。"})
