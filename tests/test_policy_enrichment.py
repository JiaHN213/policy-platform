from datetime import date, timedelta

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from policies import enrichment as ai
from policies.models import (
    AIReviewControl,
    DocumentSnapshot,
    Opportunity,
    OpportunityBatch,
    Policy,
    PolicyEnrichment,
    PolicyRelation,
    PublicationEvent,
)
from policies.tasks import dispatch_enrichment
from rest_framework.test import APIClient


@pytest.fixture
def setup_ai(db, settings):
    settings.AI_BASE_URL = "https://api.deepseek.com"
    settings.AI_MODEL = "deepseek-flash"
    settings.AI_API_KEY = "test-not-a-real-key"
    settings.LOCAL_WORKER = True
    AIReviewControl.objects.create(enabled=True)
    old = Policy.objects.create(
        title="水务资金办法",
        body="水务资金办法支持污水处理项目。",
        issuer="测试",
        publication_date=date(2026, 1, 1),
        source_key="old",
        content_hash="old",
        source_grade="L1",
        status="published",
        geographic_level="national",
        document_type="policy",
        source_url="https://www.gov.cn/zhengce/old.html",
    )
    new = Policy.objects.create(
        title="水务资金申报通知",
        body="依据《水务资金办法》开展项目申报。本次支持污水处理项目。",
        issuer="测试",
        publication_date=date(2026, 2, 1),
        source_key="new",
        content_hash="new",
        source_grade="L1",
        status="published",
        geographic_level="national",
        document_type="opportunity",
        source_url="https://www.gov.cn/zhengce/new.html",
    )
    return old, new


def mock_model(old, new):
    def generate(instruction, data, schema):
        if schema is ai.Metadata:
            body = data["body"]
            return ai.Metadata(
                points=[ai.Point(text="支持污水处理项目。", quote=body)],
                keywords=[ai.Keyword(term="污水", category="industry")],
            )
        if schema is ai.ReviewOutput:
            return ai.ReviewOutput(
                decision="include",
                confidence=0.96,
                reason="原文明确支持污水处理项目。",
                document_type="opportunity",
                source_grade="L1",
                geographic_level="national",
                province="",
                city="",
                validity_status="unverified",
                business_domains=["urban_sewage"],
                direction_tags=[],
                evidence=[ai.ReviewEvidence(field="scope", quote="污水处理项目")],
                facts=[ai.Fact(category="measure", value="支持污水处理项目", quote="污水处理项目")],
                warnings=[],
            )
        raise AssertionError("单文件 AI 审核不应再调用关系发现模型")

    return generate


def automatic_output(policy, decision="include"):
    return {
        "metadata": {
            "summary": "文件明确支持污水处理项目。",
            "summary_evidence": [
                {"text": "文件明确支持污水处理项目。", "quote": "支持污水处理项目"}
            ],
            "structured_keywords": [
                {
                    "term": "污水处理",
                    "category": "industry",
                    "start": policy.body.index("污水处理"),
                    "end": policy.body.index("污水处理") + 4,
                }
            ],
        },
        "review": {
            "decision": decision,
            "confidence": 0.97,
            "reason": "原文明确说明水务适用范围。",
            "document_type": "policy",
            "source_grade": "L1",
            "geographic_level": "city",
            "province": "广西壮族自治区",
            "city": "南宁市",
            "validity_status": "unverified",
            "business_domains": ["urban_sewage"],
            "direction_tags": ["digital"],
            "evidence": [{"field": "scope", "quote": "支持污水处理项目"}],
            "facts": [{"category": "measure", "value": "支持项目", "quote": "支持污水处理项目"}],
            "warnings": [],
        },
    }


@pytest.mark.django_db
def test_candidate_ai_result_auto_publishes_and_can_be_corrected_in_place(setup_ai, monkeypatch):
    _, template = setup_ai
    candidate = Policy.objects.create(
        title="南宁市污水处理办法",
        body="本办法支持污水处理项目和数字化管理。",
        issuer="南宁市人民政府",
        publication_date=date(2026, 3, 1),
        source_url="https://www.nanning.gov.cn/zcwj/test.html",
        source_key="candidate-ai",
        content_hash="candidate-ai",
        status="candidate",
    )
    monkeypatch.setattr(ai, "run_graph", lambda policy: automatic_output(policy))
    job = PolicyEnrichment.objects.create(policy=candidate, policy_version=1)
    ai.process_one(job.pk)
    candidate.refresh_from_db()
    job.refresh_from_db()
    assert candidate.status == "published"
    assert candidate.summary_method == "ai"
    assert candidate.source_grade == "L1"
    assert candidate.document_type == "policy"
    assert candidate.business_domains == ["urban_sewage"]
    assert job.result["finalization"]["status"] == "published"
    assert PublicationEvent.objects.filter(policy=candidate).exists()

    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_superuser("override-admin"))
    response = client.post(
        f"/api/v1/admin/policies/{candidate.pk}/correct",
        {"version": candidate.version, "summary": "人工修正后的摘要。"},
        format="json",
    )
    assert response.status_code == 200
    candidate.refresh_from_db()
    job.refresh_from_db()
    assert candidate.status == "published" and candidate.version == 2
    assert candidate.summary == "人工修正后的摘要。"
    assert candidate.scope_evidence["ai_review"]["human_correction"]["fields"] == ["summary"]
    assert job.policy_version == 2 and job.status == "succeeded"
    assert template.pk


@pytest.mark.django_db
def test_ai_publishes_and_classifies_opportunity_without_manual_entry(setup_ai, monkeypatch):
    policy = Policy.objects.create(
        title="南宁市污水处理项目申报通知",
        body="支持污水处理项目申报，申报期内提交项目材料。2026年第一批从2026年3月1日至2026年4月30日18:00受理。",
        issuer="南宁市人民政府",
        publication_date=date(2026, 3, 3),
        source_url="https://www.nanning.gov.cn/zcwj/opportunity.html",
        source_key="automatic-opportunity",
        content_hash="automatic-opportunity",
        status="candidate",
    )
    output = automatic_output(policy)
    output["review"].update(
        {
            "document_type": "opportunity",
            "opportunity_category": "pilot",
            "opportunity_status": "open",
            "opportunity_batch_name": "2026年第一批",
            "opportunity_batch_status": "open",
            "opportunity_starts_at": "2026-03-01",
            "opportunity_deadline_at": "2026-04-30T18:00:00+08:00",
            "opportunities": [{
                "name": "污水处理项目申报", "category": "pilot", "status": "open",
                "evidence": [{"field": "opportunity", "quote": "支持污水处理项目申报"}],
                "batches": [{"name": "2026年第一批", "status": "open",
                             "starts_at": "2026-03-01", "deadline_at": "2026-04-30T18:00:00+08:00"}],
            }],
        }
    )
    monkeypatch.setattr(ai, "run_graph", lambda candidate: output)
    job = PolicyEnrichment.objects.create(policy=policy, policy_version=1)

    ai.process_one(job.pk)

    policy.refresh_from_db()
    opportunity = Opportunity.objects.get(policy=policy)
    batch = OpportunityBatch.objects.get(opportunity=opportunity)
    assert policy.status == "published" and policy.document_type == "opportunity"
    assert opportunity.category == "pilot" and opportunity.status == "open"
    assert opportunity.verification_status == "verified"
    assert batch.name == "2026年第一批" and batch.status == "open"
    assert batch.starts_at is not None and batch.deadline_at is not None


@pytest.mark.django_db
def test_incomplete_attachment_blocks_ai_publication(setup_ai, monkeypatch):
    candidate = Policy.objects.create(
        title="污水处理附件办法",
        body="本办法支持污水处理项目。",
        issuer="南宁市人民政府",
        publication_date=date(2026, 3, 2),
        source_url="https://www.nanning.gov.cn/zcwj/attachment.html",
        source_key="candidate-attachment",
        content_hash="candidate-attachment",
        status="candidate",
    )
    DocumentSnapshot.objects.create(
        policy=candidate,
        url="https://www.nanning.gov.cn/annex.xls",
        sha256="a" * 64,
        object_key="test/annex.xls",
        content_type="application/vnd.ms-excel",
        size_bytes=10,
        parse_status="pending",
    )
    monkeypatch.setattr(ai, "run_graph", lambda policy: automatic_output(policy))
    job = PolicyEnrichment.objects.create(policy=candidate, policy_version=1)
    ai.process_one(job.pk)
    candidate.refresh_from_db()
    job.refresh_from_db()
    assert candidate.status == "candidate"
    assert job.result["finalization"]["status"] == "blocked"
    assert not PublicationEvent.objects.filter(policy=candidate).exists()

    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_superuser("attachment-admin"))
    response = client.post(
        f"/api/v1/admin/policies/{candidate.pk}/publish",
        {
            "version": candidate.version,
            "document_type": candidate.document_type,
            "source_grade": candidate.source_grade,
            "geographic_level": candidate.geographic_level,
            "province": candidate.province,
            "city": candidate.city,
            "allow_incomplete_attachments": True,
        },
        format="json",
    )
    assert response.status_code == 200
    candidate.refresh_from_db()
    assert candidate.status == "published"
    assert candidate.scope_evidence["publication_override"]["reason"] == "incomplete_attachments"


@pytest.mark.django_db
def test_enrichment_saves_grounded_summary_without_building_relations(setup_ai, monkeypatch):
    old, new = setup_ai
    monkeypatch.setattr(ai, "model_json", mock_model(old, new))
    job = PolicyEnrichment.objects.create(policy=new, policy_version=1)
    ai.process_one(job.pk)
    job.refresh_from_db()
    new.refresh_from_db()
    assert job.status == "succeeded", job.error_code
    assert new.summary_method == "ai"
    assert new.summary == "支持污水处理项目。"
    assert new.summary_evidence[0]["quote"] in new.body
    assert new.structured_keywords[0]["start"] == new.body.index("污水")
    assert "relations" not in job.result
    assert not PolicyRelation.objects.exists()
    ai.process_one(job.pk)
    assert job.attempts == 1
    PolicyEnrichment.objects.filter(pk=job.pk).update(status="queued")
    ai.process_one(job.pk)
    assert not PolicyRelation.objects.exists()


@pytest.mark.django_db
def test_hallucinated_summary_quote_uses_extractive_fallback(setup_ai, monkeypatch):
    _, new = setup_ai
    monkeypatch.setattr(
        ai,
        "model_json",
        lambda *args: ai.Metadata(
            points=[ai.Point(text="虚构补贴", quote="不存在的政策补贴金额")], keywords=[]
        ),
    )
    output = ai.summarize(new)
    assert output["summary_evidence"]
    assert all(item["quote"] in new.body for item in output["summary_evidence"])
    assert {item["code"] for item in output["grounding_warnings"]} >= {
        "INVALID_SUMMARY_QUOTE_SKIPPED",
        "EXTRACTIVE_SUMMARY_FALLBACK",
    }


def test_grounded_quote_restores_original_formatting():
    source = "支持污水处理项目，最高补助 100 万元。"
    assert ai.grounded_quote("支持污水处理项目，最高补助100万元", source) == source[:-1]
    assert not ai.grounded_quote("支持污水处理项目最高补助100万元", source)


@pytest.mark.django_db
def test_opportunity_type_without_specific_evidence_does_not_create_formal_opportunity(setup_ai, monkeypatch):
    old, new = setup_ai
    monkeypatch.setattr(ai, "model_json", mock_model(old, new))
    result = ai.review_policy(new, {"summary_evidence": []})
    assert result["opportunity_level"] == "SUPPORT_SIGNAL"
    assert result["opportunities"] == []


@pytest.mark.django_db
def test_summary_wrong_percentage_cannot_pass_with_real_quote(setup_ai, monkeypatch):
    _, new = setup_ai
    new.body = "污水处理项目补贴比例为1.5%。"
    monkeypatch.setattr(ai, "model_json", lambda *args, **kwargs: ai.Metadata(
        points=[ai.Point(text="补贴比例为15%。", quote=new.body)], keywords=[]
    ))
    result = ai.summarize(new)
    assert "15%" not in result["summary"]
    assert result["grounding_diagnostics"]


@pytest.mark.django_db
def test_mixed_grounded_summary_keeps_valid_items_and_reports_skips(setup_ai, monkeypatch):
    _, new = setup_ai

    monkeypatch.setattr(
        ai,
        "model_json",
        lambda *args: ai.Metadata(
            points=[
                ai.Point(text="原文明确支持项目。", quote="支持污水处理项目"),
                ai.Point(text="模型改写的无效事实。", quote="不存在的补贴金额"),
            ],
            keywords=[
                ai.Keyword(term="污水处理", category="industry"),
                ai.Keyword(term="不存在的关键词", category="support"),
            ],
        ),
    )

    output = ai.summarize(new)

    assert output["summary"] == "原文明确支持项目。"
    assert output["summary_evidence"] == [
        {"text": "原文明确支持项目。", "quote": "支持污水处理项目"}
    ]
    assert [item["term"] for item in output["structured_keywords"]] == ["污水处理"]
    assert output["grounding_warnings"] == [
        {"code": "INVALID_SUMMARY_QUOTE_SKIPPED", "count": 1},
        {"code": "INVALID_KEYWORD_SKIPPED", "count": 1},
    ]


@pytest.mark.django_db
def test_review_normalizes_unproven_values_instead_of_failing(setup_ai, monkeypatch):
    _, new = setup_ai
    new.geographic_level = "city"
    new.province = "广西壮族自治区"
    new.city = "南宁市"
    new.business_domains = ["urban_sewage"]
    new.save()

    monkeypatch.setattr(
        ai,
        "model_json",
        lambda *args: ai.ReviewOutput(
            decision="include",
            confidence=0.91,
            reason="原文涉及污水处理项目。",
            document_type="policy",
            source_grade="L1",
            geographic_level="national",
            province="",
            city="",
            validity_status="模型自创状态",
            business_domains=["not_a_domain"],
            direction_tags=["not_a_tag"],
            evidence=[ai.ReviewEvidence(field="scope", quote="污水处理项目")],
            facts=[ai.Fact(category="measure", value="虚构", quote="不存在的引句")],
            warnings=[],
        ),
    )

    output = ai.review_policy(new, {"summary_evidence": []})

    assert output["decision"] == "include"
    assert output["validity_status"] == "unverified"
    assert output["business_domains"] == ["urban_sewage"]
    assert output["direction_tags"] == []
    assert output["geographic_level"] == "city"
    assert output["province"] == "广西壮族自治区" and output["city"] == "南宁市"
    assert output["facts"] == []
    assert "VALIDITY_STATUS_NORMALIZED" in output["warnings"]
    assert "INVALID_REVIEW_QUOTE_SKIPPED" in output["warnings"]


@pytest.mark.django_db
def test_review_accepts_grounded_summary_quote_outside_compact_excerpt(setup_ai, monkeypatch):
    _, policy = setup_ai
    quote = "本文件由市人民政府办公厅负责解释"
    policy.body = "开头说明。" + ("甲" * 9000) + quote + ("乙" * 9000) + "结尾说明。"
    policy.save(update_fields=["body"])
    assert quote not in ai.opportunity_material(policy.body)

    monkeypatch.setattr(
        ai,
        "model_json",
        lambda *args: ai.ReviewOutput(
            decision="exclude",
            confidence=0.9,
            reason="原文没有水务政策机会。",
            document_type="policy",
            source_grade="L1",
            geographic_level="national",
            province="",
            city="",
            validity_status="unverified",
            business_domains=[],
            direction_tags=[],
            evidence=[ai.ReviewEvidence(field="document_type", quote=quote)],
            facts=[],
            warnings=[],
        ),
    )

    output = ai.review_policy(
        policy,
        {"summary_evidence": [{"text": "解释机关", "quote": quote}]},
    )

    assert output["evidence"][0]["quote"] == quote
    assert "INVALID_REVIEW_QUOTE_SKIPPED" not in output["warnings"]


@pytest.mark.django_db
def test_historical_target_year_is_grounded_as_expired(setup_ai, monkeypatch):
    _, policy = setup_ai
    policy.title = "2010年污水处理设施建设实施方案"
    policy.publication_date = date(2009, 6, 1)
    policy.body = "本方案用于推进污水处理设施建设，到2010年底完成重点治理任务。"
    policy.save(update_fields=["title", "publication_date", "body"])

    monkeypatch.setattr(
        ai,
        "model_json",
        lambda *args: ai.ReviewOutput(
            decision="include",
            confidence=0.94,
            reason="原文明确涉及污水处理设施建设。",
            document_type="policy",
            source_grade="L1",
            geographic_level="national",
            province="",
            city="",
            validity_status="unverified",
            business_domains=["urban_sewage"],
            direction_tags=[],
            evidence=[ai.ReviewEvidence(field="scope", quote="污水处理设施")],
            facts=[],
            warnings=[],
        ),
    )

    output = ai.review_policy(policy, {"summary_evidence": []})

    assert output["validity_status"] == "expired"
    validity = next(item for item in output["evidence"] if item["field"] == "validity")
    assert "2010年底" in validity["quote"]
    assert validity["quote"] in policy.body
    assert "HISTORICAL_TARGET_PERIOD_EXPIRED" in output["warnings"]


@pytest.mark.django_db
def test_old_publication_date_alone_does_not_infer_repeal(setup_ai):
    _, policy = setup_ai
    policy.publication_date = date(2009, 6, 1)
    policy.body = "根据2009年工作会议精神制定本办法，支持污水处理设施建设，公布之日起施行。"
    policy.save(update_fields=["publication_date", "body"])

    assert ai.infer_policy_validity(policy, today=date(2026, 9, 19)) is None


@pytest.mark.django_db
def test_past_background_target_does_not_expire_open_ended_policy(setup_ai):
    _, policy = setup_ai
    policy.title = "关于加强水务企业安全生产工作的若干意见"
    policy.publication_date = date(2009, 6, 1)
    policy.body = "到2010年实现阶段性治理目标，同时建立长期安全生产管理制度。"
    policy.save(update_fields=["title", "publication_date", "body"])

    assert ai.infer_policy_validity(policy, today=date(2026, 9, 19)) is None


@pytest.mark.django_db
def test_changed_policy_is_not_overwritten(setup_ai, monkeypatch):
    old, new = setup_ai
    generate = mock_model(old, new)

    def change_during_model(instruction, data, schema):
        result = generate(instruction, data, schema)
        if schema is ai.ReviewOutput:
            Policy.objects.filter(pk=new.pk).update(version=2)
        return result

    monkeypatch.setattr(ai, "model_json", change_during_model)
    job = PolicyEnrichment.objects.create(policy=new, policy_version=1)
    ai.process_one(job.pk)
    job.refresh_from_db()
    assert job.status == "failed" and job.error_code == "SOURCE_CHANGED"
    assert not PolicyRelation.objects.exists()


@pytest.mark.django_db
def test_source_exclusion_and_admin_configuration_permissions(setup_ai, settings):
    old, new = setup_ai
    old.source_grade = "L4"
    old.save()
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user("customer"))
    assert client.get("/api/v1/admin/policy-enrichments/configuration").status_code == 403
    client.force_authenticate(get_user_model().objects.create_superuser("ai-admin"))
    config = client.get("/api/v1/admin/policy-enrichments/configuration")
    assert config.status_code == 200 and "test-not-a-real-key" not in str(config.data)
    payload = {"policy": str(new.pk), "version": 1}
    assert client.post("/api/v1/admin/policy-enrichments/enqueue", payload).status_code == 202
    assert client.post("/api/v1/admin/policy-enrichments/enqueue", payload).status_code == 202
    assert PolicyEnrichment.objects.count() == 1
    assert (
        client.post(
            "/api/v1/admin/policy-enrichments/enqueue", {"policy": str(old.pk), "version": 1}
        ).status_code
        == 404
    )
    settings.AI_API_KEY = ""
    assert client.post("/api/v1/admin/policy-enrichments/enqueue", payload).status_code == 400


@pytest.mark.django_db
def test_admin_policy_list_filters_current_ai_status(setup_ai):
    old, new = setup_ai
    PolicyEnrichment.objects.create(
        policy=new, policy_version=new.version, status="failed", error_code="MODEL_TIMEOUT"
    )
    # A stale job for a previous version must not determine the current status.
    PolicyEnrichment.objects.create(policy=old, policy_version=old.version, status="succeeded")
    old.version = 2
    old.save(update_fields=["version"])
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_superuser("filter-admin"))

    failed = client.get("/api/v1/admin/policies?status=all&ai_status=failed")
    assert failed.status_code == 200
    assert [row["id"] for row in failed.data["items"]] == [str(new.pk)]

    not_started = client.get("/api/v1/admin/policies?status=all&ai_status=not_started")
    assert not_started.status_code == 200
    assert str(old.pk) in [row["id"] for row in not_started.data["items"]]

    PolicyEnrichment.objects.filter(policy=new, policy_version=new.version).update(
        status="succeeded",
        result={"review": {"decision": "exclude"}},
        error_code="",
    )
    new.status = "candidate"
    new.save(update_fields=["status"])
    excluded = client.get("/api/v1/admin/policies?status=all&ai_status=excluded")
    assert excluded.status_code == 200
    assert [row["id"] for row in excluded.data["items"]] == [str(new.pk)]
    workflow_excluded = client.get("/api/v1/admin/policies?workflow_status=ai_excluded")
    assert [row["id"] for row in workflow_excluded.data["items"]] == [str(new.pk)]

    actionable = Policy.objects.create(
        title="AI审核后待处理政策",
        body="供水设施改造政策正文。",
        issuer="测试",
        publication_date=date(2026, 3, 1),
        source_key="actionable",
        content_hash="actionable",
        source_grade="L1",
        status="candidate",
        geographic_level="national",
        document_type="policy",
        source_url="https://www.gov.cn/zhengce/actionable.html",
    )
    PolicyEnrichment.objects.create(
        policy=actionable,
        policy_version=actionable.version,
        status="succeeded",
        result={"review": {"decision": "include"}},
    )
    needs_action = client.get("/api/v1/admin/policies?workflow_status=needs_action")
    assert [row["id"] for row in needs_action.data["items"]] == [str(actionable.pk)]
    assert client.get("/api/v1/admin/policies?workflow_status=unknown").status_code == 400

    summary = client.get("/api/v1/admin/policies/summary")
    assert summary.status_code == 200
    assert summary.data == {
        "total": 3,
        "stages": {"AI_PROCESSING": 0, "WAITING_AI": 0, "AI_REVIEWING": 0,
                   "NEEDS_ACTION": 1, "EXCLUDED": 1, "PUBLISHED": 1, "WITHDRAWN": 0},
        "needs_action": 1,
        "ai_pending": 0,
        "ai_failed": 0,
        "ai_excluded": 1,
        "published": 1,
        "withdrawn": 0,
    }
    assert client.get("/api/v1/admin/policies?ai_status=unknown").status_code == 400


@pytest.mark.django_db
def test_automation_defaults_paused_and_admin_can_toggle(db, settings):
    settings.AI_BASE_URL = "https://api.deepseek.com"
    settings.AI_MODEL = "deepseek-flash"
    settings.AI_API_KEY = "test-not-a-real-key"
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_superuser("automation-admin"))

    configuration = client.get("/api/v1/admin/policy-enrichments/configuration")
    assert configuration.status_code == 200
    assert configuration.data["automation_enabled"] is False

    started = client.post("/api/v1/admin/policy-enrichments/start-automation")
    assert started.status_code == 200 and started.data["automation_enabled"] is True
    assert AIReviewControl.objects.get(singleton_key="default").enabled is True

    stopped = client.post("/api/v1/admin/policy-enrichments/stop-automation")
    assert stopped.status_code == 200 and stopped.data["automation_enabled"] is False


@pytest.mark.django_db
def test_admin_configures_separate_ai_models_without_exposing_keys(db, settings):
    settings.AI_BASE_URL = "http://127.0.0.1:11434"
    settings.AI_MODEL = "qwen3.5:4b"
    settings.AI_API_KEY = "environment-secret"
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_superuser("model-admin"))

    response = client.get("/api/v1/admin/ai-models")
    assert response.status_code == 200
    assert {item["purpose"] for item in response.data["items"]} == {
        "review",
        "search",
        "search_summary",
        "wiki_synthesis",
        "wiki_relations",
        "enterprise",
        "enterprise_match",
    }
    assert "environment-secret" not in str(response.data)

    review = next(item for item in response.data["items"] if item["purpose"] == "review")
    updated = client.patch(
        f"/api/v1/admin/ai-models/{review['id']}",
        {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "new-review-secret",
            "enabled": True,
        },
        format="json",
    )
    assert updated.status_code == 200
    assert updated.data["model"] == "deepseek-chat"
    assert updated.data["has_api_key"] is True
    assert "new-review-secret" not in str(updated.data)

    configuration = client.get("/api/v1/admin/policy-enrichments/configuration")
    assert configuration.status_code == 200
    assert configuration.data["model"] == "deepseek-chat"


@pytest.mark.django_db
@pytest.mark.parametrize("concurrency", [1, 3])
def test_dispatch_respects_pause_and_configured_capacity(setup_ai, monkeypatch, settings, concurrency):
    settings.AI_REVIEW_CONCURRENCY = concurrency
    old, new = setup_ai
    calls = []
    monkeypatch.setattr("policies.tasks.process_one", lambda job_id: calls.append(job_id))
    old_job = PolicyEnrichment.objects.create(policy=old, policy_version=old.version)
    new_job = PolicyEnrichment.objects.create(policy=new, policy_version=new.version)
    PolicyEnrichment.objects.filter(pk=old_job.pk).update(
        created_at=timezone.now() - timedelta(days=1)
    )

    AIReviewControl.objects.filter(singleton_key="default").update(enabled=False)
    dispatch_enrichment()
    assert calls == []

    AIReviewControl.objects.filter(singleton_key="default").update(enabled=True)
    dispatch_enrichment()
    assert calls == ([old_job.pk] if concurrency == 1 else [old_job.pk, new_job.pk])
    assert PolicyEnrichment.objects.filter(pk=new_job.pk).exists()


@pytest.mark.django_db
def test_dispatch_missing_config_leases_and_new_versions(setup_ai, settings, monkeypatch):
    old, new = setup_ai
    settings.AI_API_KEY = ""
    dispatch_enrichment()
    assert not PolicyEnrichment.objects.exists()
    settings.AI_API_KEY = "test"
    monkeypatch.setattr(ai, "model_json", mock_model(old, new))
    job = PolicyEnrichment.objects.create(
        policy=new,
        policy_version=1,
        status="running",
        attempts=1,
        lease_until=timezone.now() + timedelta(minutes=5),
    )
    ai.process_one(job.pk)
    job.refresh_from_db()
    assert job.attempts == 1 and job.status == "running"
    new.version = 2
    new.save()
    dispatch_enrichment()
    assert PolicyEnrichment.objects.filter(policy=new, policy_version=2).exists()


@pytest.mark.django_db
def test_full_text_chunks_and_summary_synthesis(setup_ai, monkeypatch):
    _, new = setup_ai
    new.body = "开头条款" + "水" * 12500 + "最后条款支持污水项目。"
    seen = []

    def model(instruction, data, schema):
        if "body" in data:
            seen.append(data["body"])
            quote = data["body"][-12:]
        else:
            quote = data["points"][-1]["quote"]
        return ai.Metadata(points=[ai.Point(text="分段归纳", quote=quote)], keywords=[])

    monkeypatch.setattr(ai, "model_json", model)
    output = ai.summarize(new)
    assert len(seen) == 3 and new.body[-12:] in seen[-1]
    assert output["summary_evidence"][0]["quote"] == new.body[-12:]


@pytest.mark.django_db
def test_deepseek_http_contract(setup_ai, monkeypatch):
    original = httpx.Client

    def respond(request):
        import json

        body = json.loads(request.content)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert body["response_format"] == {"type": "json_object"}
        assert body["model"] == "deepseek-flash"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"points":[{"text":"模型摘要","quote":"逐字引用原文"}],"keywords":[]}'
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(
        ai.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    assert (
        ai.model_json("JSON摘要", {"body": "逐字引用原文"}, ai.Metadata).points[0].text
        == "模型摘要"
    )


@pytest.mark.django_db
def test_ollama_native_contract_disables_thinking(setup_ai, settings, monkeypatch):
    settings.AI_BASE_URL = "http://localhost:11434/v1"
    settings.AI_MODEL = "qwen3.5:9b"
    original = httpx.Client

    def respond(request):
        import json

        body = json.loads(request.content)
        assert str(request.url) == "http://localhost:11434/api/chat"
        assert body["think"] is False and body["stream"] is False
        assert body["format"]["type"] == "object"
        assert body["options"]["temperature"] == 0
        assert body["options"]["num_predict"] == 1800
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"points":[{"text":"本地摘要","quote":"逐字引用原文"}],"keywords":[]}'
                }
            },
        )

    monkeypatch.setattr(
        ai.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = ai.model_json("JSON摘要", {"body": "逐字引用原文"}, ai.Metadata)
    assert result.points[0].text == "本地摘要"


@pytest.mark.django_db
def test_model_json_retries_truncated_structured_output(setup_ai, settings, monkeypatch):
    settings.AI_BASE_URL = "http://localhost:11434/v1"
    original = httpx.Client
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        content = (
            '{"points":['
            if calls == 1
            else '{"points":[{"text":"完整摘要","quote":"逐字引用原文"}],"keywords":[]}'
        )
        return httpx.Response(200, json={"message": {"content": content}})

    monkeypatch.setattr(
        ai.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = ai.model_json("JSON摘要", {"body": "逐字引用原文"}, ai.Metadata)
    assert calls == 2
    assert result.points[0].text == "完整摘要"
