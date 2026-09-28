import logging

from celery import shared_task

from .opensearch import OpenSearchUnavailable, configured, sync_index

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=270, time_limit=300)
def sync_opensearch(rebuild=False):
    if not configured():
        return {"enabled": False}
    try:
        return sync_index(rebuild=rebuild)
    except OpenSearchUnavailable as exc:
        logger.warning("OpenSearch sync unavailable: %s", exc)
        return {"enabled": True, "available": False, "error": str(exc)}

