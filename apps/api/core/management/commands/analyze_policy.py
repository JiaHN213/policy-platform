import uuid

from analysis.runtime import policy_graph
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from policies.models import Policy


class Command(BaseCommand):
    help = "运营侧试运行真实 LangGraph 分析，需要本地配置模型密钥。输出待复核结果，不发布。"

    def add_arguments(self, parser):
        parser.add_argument("policy_id")
        parser.add_argument("--username", required=True)
        parser.add_argument("--question", default="这项政策与水务、环保、人工智能＋有什么关系？")

    def handle(self, *args, **options):
        policy = Policy.objects.get(pk=options["policy_id"])
        actor = get_user_model().objects.get(username=options["username"])
        run_id = str(uuid.uuid4())
        with policy_graph() as graph:
            result = graph.invoke(
                {
                    "actor_id": actor.pk,
                    "policy_id": str(policy.pk),
                    "input_version": policy.version,
                    "question": options["question"],
                },
                {"configurable": {"thread_id": run_id}, "recursion_limit": 15},
            )
        self.stdout.write(f"run_id={run_id}; status={result['status']}")
        self.stdout.write(str(result.get("result", {})))
