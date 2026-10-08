from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from policies.models import Opportunity, OpportunityBatch, Policy
from rest_framework.test import APIClient
from subscriptions.models import Notification, Subscription
from subscriptions.services import create_deadline_reminders, match_subscription


@pytest.fixture
def opportunity_policy():
    policy = Policy.objects.create(
        title="南宁市城镇污水处理项目申报通知",
        issuer="南宁市住房和城乡建设局",
        publication_date=date.today(),
        region="南宁市",
        geographic_level="city",
        province="广西壮族自治区",
        city="南宁市",
        source_grade="L1",
        topics=["水务", "环保"],
        industry="water_environment",
        business_domains=["urban_sewage"],
        direction_tags=["smart_water"],
        document_type="opportunity",
        body="支持水务企业申报城镇污水处理数字化改造项目。",
        validity_status="effective",
        source_url="https://www.nanning.gov.cn/example.htm",
        source_key="structured-subscription-policy",
        content_hash="structured-subscription-content",
        status="published",
        published_at=timezone.now(),
    )
    opportunity = Opportunity.objects.create(
        policy=policy,
        title="城镇污水处理数字化改造项目",
        category="pilot",
        status="open",
        acquisition_method="APPLICATION",
        eligible_subjects=["水务企业"],
        verification_status="verified",
        competent_authorities=["南宁市住房和城乡建设局"],
        evidence_policy=policy,
        evidence_version=policy.version,
        evidence_quote="支持水务企业申报城镇污水处理数字化改造项目。",
    )
    OpportunityBatch.objects.create(
        opportunity=opportunity,
        name="2026年度申报批次",
        verification_status="verified",
        status="open",
        deadline_at=timezone.now() + timedelta(days=5),
        evidence_policy=policy,
        evidence_version=policy.version,
        evidence_quote="支持水务企业申报城镇污水处理数字化改造项目。",
    )
    return policy


@pytest.mark.django_db
def test_structured_subscription_preview_and_match(opportunity_policy):
    user = get_user_model().objects.create_superuser("subscription-admin")
    client = APIClient()
    client.force_authenticate(user)
    payload = {
        "name": "南宁污水项目机会",
        "target_view": "opportunity",
        "business_domain": "urban_sewage",
        "opportunity_category": "pilot",
        "opportunity_status": "open",
        "eligible_keywords": "水务企业",
        "deadline_within_days": 10,
    }
    response = client.post("/api/v1/subscriptions/preview", payload, format="json")
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert "业务领域：城镇污水处理" in response.data["items"][0]["reasons"]
    subscription = Subscription(user=user, idempotency_key="preview", **payload)
    result = match_subscription(subscription, opportunity_policy)
    assert result.matched
    assert result.opportunity_titles == ["城镇污水处理数字化改造项目"]


@pytest.mark.django_db
def test_deadline_reminder_is_sent_once(opportunity_policy):
    user = get_user_model().objects.create_superuser("reminder-admin")
    Subscription.objects.create(
        user=user,
        name="十天内截止机会",
        target_view="opportunity",
        deadline_within_days=10,
        idempotency_key="deadline",
    )
    assert create_deadline_reminders() == 1
    assert create_deadline_reminders() == 0
    notification = Notification.objects.get()
    assert notification.title.startswith("10天内截止：")
    assert "将在 10 天内截止" in notification.reasons[0]
