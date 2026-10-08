"""In-app daily digests and immediate changes, with per-user delivery identities."""
import hashlib
from datetime import timedelta

from accounts.models import User
from accounts.services import access_decision
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from policies.catalog import batch_state, visible_batches
from policies.models import PublicationEvent

from .models import Notification, NotificationPreference, PendingDelivery, Subscription


def preferences(user_id):
    legacy = list(Subscription.objects.filter(user_id=user_id, active=True, deleted_at__isnull=True,
                  deadline_within_days__isnull=False).values_list("deadline_within_days", flat=True))
    return NotificationPreference.objects.get_or_create(user_id=user_id, defaults={
        "deadline_enabled": bool(legacy), "deadline_days": sorted(set(legacy), reverse=True) or [7, 1],
    })[0]


def enqueue_or_notify(user_id, event, reasons):
    important = event.kind == "policy.validity_changed.v1" or event.kind.startswith(("opportunity.changed.", "opportunity.deadline."))
    important = important or bool(set(event.payload.get("changed_fields", [])) & {"validity_status", "validity_evidence"})
    if not important and preferences(user_id).update_mode == "daily":
        return int(PendingDelivery.objects.get_or_create(user_id=user_id, event=event, defaults={"reasons": reasons})[1])
    key = event.payload.get("delivery_key", "")
    lookup = {"user_id": user_id, "delivery_key": key} if key else {"user_id": user_id, "event": event}
    defaults = {"event": event, "title": event.payload.get("title", event.policy.title)[:500],
                "reasons": reasons, "kind": "deadline" if event.kind.startswith("opportunity.deadline.") else "important" if important else "update"}
    return int(Notification.objects.get_or_create(**lookup, defaults=defaults)[1])


def visible_notifications(user):
    from enterprises.watching import visible_recommendations
    formal = Q(event__policy__status="published", event__policy__source_grade__in=["L1", "L2", "L3"])
    digest = Q(entries__event__policy__status="published", entries__event__policy__source_grade__in=["L1", "L2", "L3"])
    if not user.is_staff:
        formal &= Q(event__policy__is_demo=False)
        digest &= Q(entries__event__policy__is_demo=False)
    recommendations = visible_recommendations(user)
    return Notification.objects.filter(user=user).filter(formal | digest |
        Q(recommendation__in=recommendations) | Q(entries__recommendation__in=recommendations)).distinct()


def deliver_digests(now=None):
    from .services import match_subscription

    now = now or timezone.now()
    local = timezone.localtime(now)
    count = 0
    owners = PendingDelivery.objects.filter(handled_at__isnull=True).values_list("user_id", flat=True).distinct()
    for user_id in list(owners):
        with transaction.atomic():
            user = User.objects.select_for_update(no_key=True).get(pk=user_id)
            preference = preferences(user_id)
            instant = preference.update_mode == "instant"
            if not instant and local.hour < preference.digest_hour:
                continue
            if not instant and Notification.objects.filter(user=user, digest_date=local.date()).exists():
                continue
            cutoff = now if instant else local.replace(hour=preference.digest_hour, minute=0, second=0, microsecond=0)
            pending = list(PendingDelivery.objects.select_for_update(of=("self",)).filter(user=user, handled_at__isnull=True, created_at__lte=cutoff).select_related("event__policy", "recommendation__watch__profile", "recommendation__watch__project", "recommendation__watch__user", "recommendation__policy"))
            if not pending:
                continue
            subs = list(Subscription.objects.filter(user=user, active=True, deleted_at__isnull=True))
            valid = []
            permitted = user.is_active and access_decision(user, "policy_subscription")["allowed"] and access_decision(user, "policy_detail")["allowed"]
            for item in pending:
                if item.recommendation_id:
                    from enterprises.watching import recommendation_is_current
                    if permitted and recommendation_is_current(item.recommendation):
                        valid.append(item)
                    else:
                        item.handled_at = now
                        item.save(update_fields=["handled_at"])
                    continue
                if not item.event_id:
                    item.handled_at = now
                    item.save(update_fields=["handled_at"])
                    continue
                policy = item.event.policy
                allowed_ids = set(item.event.payload.get("subscription_ids", []))
                matches = [s for s in subs if str(s.pk) in allowed_ids and match_subscription(s, policy).matched] if permitted else []
                if policy.status == "published" and policy.source_grade in {"L1", "L2", "L3"} and (not policy.is_demo or user.is_staff) and matches:
                    valid.append(item)
                else:
                    item.handled_at = now
                    item.save(update_fields=["handled_at"])
            if instant:
                for item in valid:
                    if item.recommendation_id:
                        from enterprises.watching import deliver_recommendation
                        count += deliver_recommendation(item.recommendation)
                    else:
                        count += enqueue_or_notify(user_id, item.event, item.reasons)
                    item.handled_at = now
                    item.save(update_fields=["handled_at"])
            elif valid:
                policy_count = len({item.recommendation.policy_id if item.recommendation_id else item.event.policy_id for item in valid})
                notification = Notification.objects.create(user=user, kind="digest", digest_date=local.date(),
                    title=f"每日政策汇总 · {policy_count} 份政策", reasons=["按你的有效订阅汇总；重要变化单独提醒。"])
                PendingDelivery.objects.filter(pk__in=[p.pk for p in valid]).update(notification=notification, handled_at=now)
                count += 1
    return count


def deadline_reminders(now=None):
    from .services import deliver_event, match_subscription

    now = now or timezone.now()
    count = 0
    user_ids = Subscription.objects.filter(active=True, deleted_at__isnull=True, user__is_active=True).values_list("user_id", flat=True).distinct()
    for user_id in list(user_ids):
        with transaction.atomic():
            user = User.objects.select_for_update(no_key=True).get(pk=user_id)
            preference = preferences(user_id)
            if not preference.deadline_enabled or not preference.deadline_days:
                continue
            if not access_decision(user, "policy_subscription")["allowed"] or not access_decision(user, "policy_detail")["allowed"]:
                continue
            nodes = sorted(preference.deadline_days)
            subs = list(Subscription.objects.filter(user=user, active=True, deleted_at__isnull=True))
            batches = visible_batches(user).filter(deadline_at__gt=now, deadline_at__lte=now + timedelta(days=max(nodes))).select_related("opportunity__policy").prefetch_related("opportunity__policy__opportunities__batches")
            for batch in batches:
                policy = batch.opportunity.policy
                if policy.validity_status in {"expired", "repealed", "replaced", "consultation", "not_effective"} or batch.opportunity.status not in {"open", "ongoing"} or batch_state(batch, now)[0] not in {"open", "ongoing"}:
                    continue
                node = next(n for n in nodes if batch.deadline_at <= now + timedelta(days=n))
                matching = [s for s in subs if match_subscription(s, policy, specific_batch_id=batch.pk, now=now).matched]
                if not matching:
                    continue
                digest = hashlib.sha256(f"{user_id}:{batch.pk}:{batch.deadline_at.isoformat()}:{node}".encode()).hexdigest()
                key = "deadline:" + digest
                if Notification.objects.filter(user=user, delivery_key=key).exists():
                    continue
                kind = "opportunity.deadline." + digest[:32]
                event = PublicationEvent.objects.filter(policy=policy, kind=kind).first()
                if event is None:
                    event = PublicationEvent.objects.create(policy=policy, policy_version=policy.version, kind=kind, payload={
                        "title": f"{node}天内截止：{batch.opportunity.title}"[:500], "delivery_key": key,
                        "opportunity_batch_id": str(batch.pk), "deadline_at": batch.deadline_at.isoformat(),
                        "subscription_ids": [str(s.pk) for s in matching],
                    })
                count += deliver_event(event.pk)
    return count
