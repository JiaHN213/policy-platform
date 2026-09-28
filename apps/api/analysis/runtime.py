from contextlib import contextmanager

from django.conf import settings
from django.contrib.auth import get_user_model
from langgraph.checkpoint.postgres import PostgresSaver
from policies.models import Evidence, Policy
from rest_framework.exceptions import PermissionDenied

from .gateway import generate
from .graph import build_graph


def authorize(actor_id, policy_id, input_version):
    actor = get_user_model().objects.get(pk=actor_id)
    # This milestone exposes analysis only to staff via a local management command.
    if not actor.is_active or not actor.has_perm("policies.view_policy"):
        raise PermissionDenied("分析权限已失效。")
    if not Policy.objects.filter(
        pk=policy_id,
        status="published",
        version=input_version,
        source_grade__in=Policy.FORMAL_SOURCE_GRADES,
    ).exists():
        raise PermissionDenied("政策已撤下或版本已变化。")


@contextmanager
def policy_graph():
    with PostgresSaver.from_conn_string(settings.CHECKPOINT_DATABASE_URL) as saver:
        yield build_graph(
            saver,
            authorize,
            lambda policy_id, version: [
                str(i)
                for i in Evidence.objects.filter(
                    policy_id=policy_id,
                    policy_version=version,
                    policy__source_grade__in=Policy.FORMAL_SOURCE_GRADES,
                ).values_list("id", flat=True)
            ],
            lambda ids: {
                str(e.id): e.text
                for e in Evidence.objects.filter(
                    pk__in=ids,
                    policy__source_grade__in=Policy.FORMAL_SOURCE_GRADES,
                    policy__status="published",
                )
            },
            generate,
        )
