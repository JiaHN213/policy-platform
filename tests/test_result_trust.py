from datetime import date

import pytest
from accounts.models import User
from enterprises.conditions import assess_conditions, compare_condition
from enterprises.grounding import belongs_to_company
from enterprises.research import CompanyDraft, validate_draft
from knowledge.relations import apply_derived_validity
from policies.field_provenance import record_field_values
from policies.models import (
    DocumentSnapshot,
    Evidence,
    Opportunity,
    Policy,
    PolicyEnrichment,
    PolicyRelation,
    PublicationEvent,
)
from policies.readiness import evidence_readiness
from subscriptions.models import Notification, Subscription
from subscriptions.services import deliver_event


def make_policy(key, **kwargs):
    return Policy.objects.create(**(dict(title="水务管理办法", issuer="政府", publication_date=date(2025, 1, 1),
                                        body="原办法同时废止。", source_key=key, content_hash=key,
                                        status="published", source_grade="L1", validity_status="effective",
                                        extraction_version=1, source_url="https://example.gov.cn/" + key) | kwargs))


def draft_result(field, value, quote, text=None, inputs=None):
    name = "测试水务有限公司"
    source = {"id": 1, "text": text or name + quote, "url": "https://example.com/about", "title": "企业介绍",
              "retrieved_at": "2026-09-30", "material": "页面正文", "source_type": "公开网页"}
    draft = CompanyDraft.model_validate({"candidates": [{"name": name, "identity_evidence": {"source_id": 1, "quote": name},
                                                       "fields": [{"field": field, "value": value, "evidence": [{"source_id": 1, "quote": quote}]}]}]})
    return validate_draft(draft, [source], inputs or {})


def test_quote_exists_but_does_not_support_city_or_tag():
    result = draft_result("city", "南宁", "企业位于柳州市")
    assert "city" not in result["candidates"][0]["data"]
    assert result["warnings"]
    assert "city" not in draft_result("city", "南宁", "企业位于柳州，服务南宁市场")["candidates"][0]["data"]
    assert "direction_tags" not in draft_result("direction_tags", ["ai"], "主营污水处理") ["candidates"][0]["data"]


def test_related_company_facts_cannot_be_attributed_to_target():
    result = draft_result("capabilities", ["高新技术企业"], "其他环保有限公司为高新技术企业", "测试水务有限公司介绍。其他环保有限公司为高新技术企业")
    assert "capabilities" not in result["candidates"][0]["data"]
    result = draft_result("capabilities", ["高新技术企业"], "子公司其他环保有限公司为高新技术企业", "测试水务有限公司的子公司其他环保有限公司为高新技术企业")
    assert "capabilities" not in result["candidates"][0]["data"]


@pytest.mark.parametrize("mode", ["website", "search", "text", "file"])
def test_investor_in_previous_sentence_does_not_remove_own_business(mode):
    name = "测试水务有限公司"
    quote = "公司主要从事城乡供水、污水处理、污泥处理处置。"
    text = name + "是由其他投资有限公司等投资人重组的水务环保运营公司。" + quote
    source = {"id": 1, "text": text, "url": "https://example.com/about", "title": "企业介绍",
              "retrieved_at": "2026-10-03", "material": "网页正文", "source_type": "公开网页"}
    draft = CompanyDraft.model_validate({"candidates": [{"name": name, "identity_evidence": {"source_id": 1, "quote": name},
        "fields": [{"field": "business_summary", "value": quote, "evidence": [{"source_id": 1, "quote": quote}]},
                   {"field": "business_domains", "value": ["water_supply", "urban_sewage"], "evidence": [{"source_id": 1, "quote": quote}]}]}]})
    result = validate_draft(draft, [source], {"name": name, "source_mode": mode})
    candidate = result["candidates"][0]
    assert candidate["data"]["business_summary"] == quote
    assert "urban_sewage" in candidate["data"]["business_domains"]
    assert candidate["evidence"]["business_summary"][0]["start_offset"] == text.index(quote)
    assert not result["warnings"]


@pytest.mark.parametrize("text,quote", [
    ("测试水务有限公司介绍。其他环保有限公司为高新技术企业。", "为高新技术企业"),
    ("测试水务有限公司介绍。其他环保有限公司成立于2000年。公司主营污水处理。", "公司主营污水处理"),
    ("测试水务有限公司介绍。子公司已取得高新技术企业资质。", "已取得高新技术企业资质"),
    ("测试水务有限公司介绍。其他环保有限公司\n\n公司主营污水处理。", "公司主营污水处理"),
    ("测试水务有限公司主营污水处理。其他环保有限公司主营污水处理。", "主营污水处理"),
])
def test_local_foreign_subject_and_repeated_ambiguous_quotes_still_rejected(text, quote):
    from enterprises.research import Evidence
    item = Evidence(source_id=1, quote=quote)
    assert not belongs_to_company(item, [{"id": 1, "text": text}], "测试水务有限公司", {"source_mode": "website", "name": "测试水务有限公司"})


def test_normalized_original_is_attributed_before_validation():
    name = "测试水务有限公司"
    quote = "主营污水处理设施运营"
    source = {"id": 1, "text": name + "，主营污水处理\n设施运营。", "url": "https://example.com/about", "title": "企业介绍",
              "retrieved_at": "2026-10-03", "material": "网页正文", "source_type": "公开网页"}
    draft = CompanyDraft.model_validate({"candidates": [{"name": name, "identity_evidence": {"source_id": 1, "quote": name},
        "fields": [{"field": "business_summary", "value": quote, "evidence": [{"source_id": 1, "quote": quote}]}]}]})
    candidate = validate_draft(draft, [source], {"name": name, "source_mode": "website"})["candidates"][0]
    assert candidate["data"]["business_summary"] == "主营污水处理\n设施运营"


def test_exclusion_warnings_distinguish_wrong_subject_and_missing_quote():
    result = draft_result("capabilities", ["高新技术企业"], "其他环保有限公司为高新技术企业", "测试水务有限公司介绍。其他环保有限公司为高新技术企业")
    assert any("其他企业" in warning for warning in result["warnings"])
    result = draft_result("capabilities", ["高新技术企业"], "不存在的原文引文", "测试水务有限公司主营供水。")
    assert any("未能在指定资料中找到" in warning for warning in result["warnings"])


@pytest.mark.parametrize("value", ["", [], "未知", "未提供", "N/A"])
def test_unknown_placeholders_are_omitted_without_error_noise(value):
    result = draft_result("city", value, "主营污水处理")
    assert not result["candidates"][0]["data"] and not result["warnings"]


@pytest.mark.parametrize("mode", ["website", "search", "text", "file"])
def test_operating_region_is_not_a_subscription_preference(mode):
    result = draft_result("interest_regions", ["南宁"], "业务覆盖南宁、柳州等城市", inputs={"source_mode": mode, "name": "测试水务有限公司"})
    assert "interest_regions" not in result["candidates"][0]["data"]


def test_explicit_private_introduction_preferences_can_be_proposed():
    result = draft_result("interest_regions", ["南宁"], "希望关注南宁地区的政策", inputs={"source_mode": "text", "name": "测试水务有限公司"})
    assert result["candidates"][0]["data"]["interest_regions"] == ["南宁"]


def test_unsupported_summary_uses_original_and_negative_capability_is_omitted():
    result = draft_result("business_summary", "主营人工智能", "主营污水处理设施运营")
    assert result["candidates"][0]["data"]["business_summary"] == "主营污水处理设施运营"
    assert not draft_result("capabilities", ["高新技术企业"], "尚未取得高新技术企业资格")["candidates"][0]["data"]


def test_registered_city_is_not_inferred_from_office_city():
    assert compare_condition("企业注册地必须为南宁市", {"city": "南宁"})[0] == "unknown"
    assert compare_condition("企业注册地必须为南宁市", {"registered_city": "柳州"})[0] == "conflict"
    assert compare_condition("企业注册地必须为南宁市", {"registered_city": "南宁"})[0] == "consistent"
    assert compare_condition("企业注册地为南宁市或柳州市", {"registered_city": "桂林"})[0] == "unknown"


@pytest.mark.django_db
def test_attachment_gap_prevents_priority_without_hiding_policy():
    from accounts.models import Organization
    from enterprises.matching import match_policies
    from enterprises.models import EnterpriseProfile

    user = User.objects.create_user("trust-customer")
    profile = EnterpriseProfile.objects.create(organization=Organization.objects.create(name="企业"), data={"business_domains": ["urban_sewage"], "capabilities": ["高新技术企业"]})
    policy = make_policy("attachment", body="企业须具备高新技术企业。", business_domains=["urban_sewage"])
    opportunity = Opportunity.objects.create(policy=policy, title="补贴", category="fiscal", status="open", requirements=[policy.body], evidence_policy=policy, evidence_version=1, evidence_quote=policy.body, verification_status="verified")
    assert assess_conditions(policy, [opportunity], profile.data, None, evidence_readiness(policy))["status"] == "consistent"
    snapshot = DocumentSnapshot.objects.create(policy=policy, url="https://example.gov.cn/conditions.pdf", parse_status="failed", content_type="application/pdf", size_bytes=0)
    result = match_policies(user, profile, view="opportunities")["items"][0]
    assert result["level"] == "medium"
    assert result["conditions"]["status"] == "unknown"
    assert result["recommendation_group"] == "needs_verification"
    assert result["evidence_readiness"]["unresolved_count"] == 1
    snapshot.parse_status = "parsed"
    snapshot.save()
    assert evidence_readiness(policy)["status"] == "available"


@pytest.mark.django_db
def test_conflict_in_one_opportunity_does_not_hide_another():
    policy = make_policy("conditions", body="企业注册地必须为南宁市。企业须具备高新技术企业。")
    opportunities = [Opportunity.objects.create(policy=policy, title=str(i), category="fiscal", status="open", requirements=[condition], evidence_policy=policy, evidence_version=1, evidence_quote=condition, verification_status="verified") for i, condition in enumerate(["企业注册地必须为南宁市。", "企业须具备高新技术企业。"]) ]
    result = assess_conditions(policy, opportunities, {"registered_city": "柳州", "capabilities": ["高新技术企业"]}, None, evidence_readiness(policy))
    assert result["status"] == "consistent"
    assert [g["status"] for g in result["opportunities"]] == ["conflict", "consistent"]
    opportunities[1].status = "not_started"
    assert assess_conditions(policy, opportunities, {"registered_city": "柳州", "capabilities": ["高新技术企业"]}, None, evidence_readiness(policy))["status"] == "unknown"


def make_relation(old, new, kind="repeals"):
    return PolicyRelation.objects.create(from_policy=new, to_policy=old, kind=kind, evidence_policy=new, evidence_version=new.version, evidence_quote=new.body, verification_status="verified")


@pytest.mark.django_db
def test_validity_is_versioned_idempotent_and_notifies_previous_subscriber():
    old, new = make_policy("old"), make_policy("new")
    user = User.objects.create_user("validity-customer")
    Subscription.objects.create(user=user, name="现行政策", validity_status="effective", idempotency_key="validity")
    Evidence.objects.create(policy=old, policy_version=1, text=old.body, quote_hash="original")
    PolicyEnrichment.objects.create(policy=old, policy_version=1, status="succeeded")
    relation = make_relation(old, new)
    assert apply_derived_validity() == 1
    old.refresh_from_db()
    assert (old.version, old.extraction_version, old.validity_status) == (2, 2, "repealed")
    assert old.evidence.filter(policy_version=2).exists()
    assert old.enrichments.filter(policy_version=2, status="succeeded").exists()
    event = PublicationEvent.objects.get(policy=old)
    assert event.consumptions.count() == 4
    assert deliver_event(event.pk) == 1
    assert deliver_event(event.pk) == 0
    assert Notification.objects.get().event_id == event.pk
    assert apply_derived_validity() == 0
    relation.verification_status = "rejected"
    relation.save()
    assert apply_derived_validity() == 1
    old.refresh_from_db()
    assert (old.version, old.validity_status) == (3, "effective")
    assert PublicationEvent.objects.filter(policy=old).count() == 2


@pytest.mark.django_db
def test_partial_revision_and_locked_validity_are_not_overwritten():
    old, new = make_policy("locked"), make_policy("partial", body="废止原办法第三条。")
    make_relation(old, new)
    assert apply_derived_validity() == 0
    new.body = "原办法同时废止。"
    new.save()
    PolicyRelation.objects.update(evidence_quote=new.body)
    record_field_values(old, ["validity_status"], source_type="human_correction", locked=True)
    assert apply_derived_validity() == 0
