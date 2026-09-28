from datetime import date

import pytest
from django.contrib.auth import get_user_model
from knowledge import services as knowledge_services
from knowledge import synthesis as knowledge_synthesis
from knowledge.models import KnowledgeBuild, KnowledgePage
from knowledge.obsidian import (
    BODY_END,
    BODY_START,
    SUMMARY_END,
    SUMMARY_START,
    export_vault,
    import_vault_edits,
)
from knowledge.services import lint_all, page_specs, sync_all
from knowledge.synthesis import SynthesisResult, WikiDocument, WikiParagraph, WikiSection
from knowledge.tasks import enqueue_sync, process_build
from policies.models import Policy, PolicyRelation
from policies.services import fingerprint
from rest_framework.test import APIClient


@pytest.fixture
def knowledge_catalog(db):
    admin = get_user_model().objects.create_superuser("knowledge-admin")
    reader = get_user_model().objects.create_user("knowledge-reader")
    body_a = "本办法支持城镇污水处理设施建设，推动污水管网更新改造。"
    body_b = "本通知实施城镇污水处理设施建设办法，并组织项目申报。"
    a = Policy.objects.create(
        title="城镇污水处理设施建设办法",
        issuer="测试机关",
        publication_date=date(2026, 1, 1),
        region="全国",
        geographic_level="national",
        source_grade="L1",
        industry="water_environment",
        business_domains=["urban_sewage", "water_infrastructure"],
        direction_tags=["equipment_renewal"],
        body=body_a,
        summary="支持城镇污水处理设施和管网更新。",
        summary_evidence=[{"text": "支持设施建设", "quote": "城镇污水处理设施建设"}],
        source_url="https://www.gov.cn/test/wiki-a",
        source_key=fingerprint("wiki-a"),
        content_hash=fingerprint(body_a),
        status="published",
        document_type="policy",
    )
    b = Policy.objects.create(
        title="城镇污水处理项目申报通知",
        issuer="测试机关",
        publication_date=date(2026, 2, 1),
        region="全国",
        geographic_level="national",
        source_grade="L1",
        industry="water_environment",
        business_domains=["urban_sewage"],
        direction_tags=["equipment_renewal"],
        body=body_b,
        summary="实施建设办法并组织项目申报。",
        summary_evidence=[{"text": "组织项目申报", "quote": "组织项目申报"}],
        source_url="https://www.gov.cn/test/wiki-b",
        source_key=fingerprint("wiki-b"),
        content_hash=fingerprint(body_b),
        status="published",
        document_type="opportunity",
    )
    PolicyRelation.objects.create(
        from_policy=b,
        to_policy=a,
        kind="implements",
        evidence_policy=b,
        evidence_version=b.version,
        evidence_quote="实施城镇污水处理设施建设办法",
        verification_status="verified",
        discovery={"method": "wiki_llm", "versions": {str(a.pk): 1, str(b.pk): 1}},
    )
    return admin, reader, a, b


@pytest.mark.django_db
def test_sync_builds_policy_topic_region_and_chain_pages(knowledge_catalog):
    admin, _, a, _ = knowledge_catalog
    result = sync_all()
    assert result["by_type"] == {"policy": 2, "topic": 3, "region": 1, "chain": 1}
    assert result["lint"]["issues"] == 0
    policy_page = KnowledgePage.objects.get(key=f"policy:{a.pk}")
    assert policy_page.current_revision.number == 1
    assert policy_page.page_sources.count() == 1
    assert policy_page.current_revision.citations[0]["quote"] in a.body
    client = APIClient()
    client.force_authenticate(admin)
    response = client.get("/api/v1/knowledge/pages?page_type=chain")
    assert response.status_code == 200
    assert response.data["count"] == 1
    detail = client.get(f"/api/v1/knowledge/pages/{policy_page.slug}")
    assert detail.status_code == 200
    assert detail.data["revision"] == 1
    assert detail.data["citations"][0]["policy_id"] == str(a.pk)


@pytest.mark.django_db
def test_lint_hides_stale_page_until_incremental_rebuild(knowledge_catalog):
    admin, _, a, _ = knowledge_catalog
    sync_all()
    page = KnowledgePage.objects.get(key=f"policy:{a.pk}")
    a.version = 2
    a.save(update_fields=["version", "updated_at"])
    client = APIClient()
    client.force_authenticate(admin)
    assert client.get(f"/api/v1/knowledge/pages/{page.slug}").status_code == 404
    assert lint_all()["issues"] > 0
    page.refresh_from_db()
    assert page.status == "stale"
    assert client.get(f"/api/v1/knowledge/pages/{page.slug}").status_code == 404
    result = sync_all()
    page.refresh_from_db()
    assert page.status == "published"
    assert page.current_revision.number == 2
    assert result["lint"]["issues"] == 0


@pytest.mark.django_db
def test_knowledge_read_requires_active_internal_staff(knowledge_catalog):
    from accounts.models import Entitlement

    _, reader, a, _ = knowledge_catalog
    sync_all()
    page = KnowledgePage.objects.get(key=f"policy:{a.pk}")
    urls = ["/api/v1/knowledge/pages", f"/api/v1/knowledge/pages/{page.slug}"]
    client = APIClient()
    for capability in ("policy_search", "policy_detail"):
        Entitlement.objects.create(user=reader, capability=capability, allowed=True)
    client.force_authenticate(reader)
    for url in urls:
        assert client.get(url).status_code == 403
    reader.is_staff = True
    reader.save(update_fields=["is_staff"])
    for url in urls:
        assert client.get(url).status_code == 200
    assert client.post("/api/v1/admin/knowledge/pages/lint").status_code == 403
    reader.is_active = False
    reader.save(update_fields=["is_active"])
    for url in urls:
        assert client.get(url).status_code == 403


@pytest.mark.django_db
def test_admin_can_queue_process_lint_and_export_knowledge(knowledge_catalog, monkeypatch):
    admin, reader, _, _ = knowledge_catalog
    build, created = enqueue_sync(admin)
    assert created and build.status == "queued"
    process_build(build.pk)
    build.refresh_from_db()
    assert build.status == "succeeded"
    assert build.result["pages"] == 7
    client = APIClient()
    client.force_authenticate(reader)
    assert client.post("/api/v1/admin/knowledge/builds/sync").status_code == 403
    client.force_authenticate(admin)
    assert client.post("/api/v1/admin/knowledge/pages/lint").status_code == 200
    monkeypatch.setattr(
        "knowledge.views.export_vault",
        lambda: {"pages": 7, "generated_at": "2026-09-20T00:00:00Z", "vault": "test"},
    )
    export = client.post("/api/v1/admin/knowledge/pages/export-obsidian")
    assert export.status_code == 200 and export.data["pages"] == 7
    monkeypatch.setattr(
        "knowledge.views.import_vault_edits",
        lambda actor: {"imported": 1, "unchanged": 6, "conflicts": [], "items": []},
    )
    imported = client.post("/api/v1/admin/knowledge/pages/import-obsidian")
    assert imported.status_code == 200 and imported.data["imported"] == 1
    response = client.post("/api/v1/admin/knowledge/builds/sync")
    assert response.status_code == 202
    assert KnowledgeBuild.objects.filter(status="queued").exists()


@pytest.mark.django_db
def test_relation_audit_continuation_is_queued(knowledge_catalog, monkeypatch):
    admin, _, _, _ = knowledge_catalog
    build = KnowledgeBuild.objects.create(requested_by=admin)
    monkeypatch.setattr(
        "knowledge.tasks.sync_all",
        lambda: {
            "input_hash": "a" * 64,
            "relation_audit": {"pending": 12},
        },
    )

    process_build(build.pk)

    build.refresh_from_db()
    continuation = KnowledgeBuild.objects.exclude(pk=build.pk).get()
    assert build.status == "succeeded"
    assert continuation.status == "queued"
    assert continuation.result["pending_relation_scans"] == 12


@pytest.mark.django_db
def test_nonformal_and_demo_policies_never_enter_knowledge(knowledge_catalog):
    _, _, _, b = knowledge_catalog
    b.source_grade = "L4"
    b.save(update_fields=["source_grade", "updated_at"])
    result = sync_all()
    assert result["by_type"]["policy"] == 1
    assert not KnowledgePage.objects.filter(key=f"policy:{b.pk}").exists()


@pytest.mark.django_db
def test_changed_page_uses_llm_synthesis_and_records_real_model(knowledge_catalog, monkeypatch):
    knowledge_catalog

    monkeypatch.setattr(knowledge_services, "llm_synthesis_enabled", lambda: True)

    def fake_synthesis(spec, previous_body=""):
        assert spec.title
        return SynthesisResult(
            body=f"# {spec.title}\n\n## 综合判断\n\n这是模型综合页面 [1]",
            abstract="模型综合摘要",
            model="test-model",
            prompt_version="wiki-llm-grounded-v2",
        )

    monkeypatch.setattr(knowledge_services, "synthesize", fake_synthesis)
    result = sync_all()
    page = KnowledgePage.objects.get(page_type="chain")
    policy_page = KnowledgePage.objects.filter(page_type="policy").first()
    assert result["generation_mode"] == "hybrid"
    assert result["llm_revisions"] == 4
    assert page.current_revision.model == "test-model"
    assert page.current_revision.prompt_version == "wiki-llm-grounded-v2"
    assert "模型综合页面" in page.current_revision.body
    assert policy_page.current_revision.model == ""


@pytest.mark.django_db
def test_llm_failure_keeps_previous_revision_and_hides_stale_page(knowledge_catalog, monkeypatch):
    _, _, a, _ = knowledge_catalog
    sync_all()
    page = KnowledgePage.objects.get(page_type="chain")
    old_revision = page.current_revision_id
    a.summary = "来源变化后的摘要"
    a.version += 1
    a.save(update_fields=["summary", "version", "updated_at"])
    monkeypatch.setattr(knowledge_services, "llm_synthesis_enabled", lambda: True)

    def fail(spec, previous_body=""):
        if spec.page_type == "chain":
            raise ValueError("MODEL_INVALID_OUTPUT")
        return SynthesisResult(
            body=f"# {spec.title}\n\n## 综合判断\n\n更新内容 [1]",
            abstract="更新摘要",
            model="test-model",
            prompt_version="wiki-llm-grounded-v2",
        )

    monkeypatch.setattr(knowledge_services, "synthesize", fail)
    result = sync_all()
    page.refresh_from_db()
    assert page.status == "stale"
    assert page.current_revision_id == old_revision
    assert result["failed_pages"] == [
        {
            "key": page.key,
            "title": page.title,
            "reason": "模型连续返回不完整或不符合格式要求的内容。旧修订未被覆盖。",
        }
    ]


@pytest.mark.django_db
def test_synthesis_graph_requires_valid_citation_ids(knowledge_catalog, monkeypatch, settings):
    settings.AI_BASE_URL = "http://localhost:11434/v1"
    settings.AI_MODEL = "test-model"
    settings.AI_API_KEY = "configured"
    spec = next(item for item in page_specs() if item.page_type == "policy")

    def valid_model(*args, **kwargs):
        return WikiDocument(
            abstract="带证据的综合摘要",
            sections=[
                WikiSection(
                    heading="核心内容",
                    paragraphs=[WikiParagraph(text="政策支持设施建设。", citation_ids=[1])],
                ),
                WikiSection(
                    heading="关联说明",
                    paragraphs=[WikiParagraph(text="关系以数据库记录为准。", citation_ids=[1])],
                ),
            ],
        )

    monkeypatch.setattr(knowledge_synthesis, "model_json", valid_model)
    result = knowledge_synthesis.synthesize(spec)
    assert result.model == "test-model"
    assert "政策支持设施建设。 [1]" in result.body

    def invalid_model(*args, **kwargs):
        document = valid_model()
        document.sections[0].paragraphs[0].citation_ids = [999]
        return document

    monkeypatch.setattr(knowledge_synthesis, "model_json", invalid_model)
    with pytest.raises(ValueError, match="WIKI_INVALID_CITATION_REFERENCE"):
        knowledge_synthesis.synthesize(spec)


@pytest.mark.django_db
def test_obsidian_export_uses_chinese_folders_and_editable_policy_names(
    knowledge_catalog, tmp_path
):
    _, _, policy, _ = knowledge_catalog
    sync_all()
    result = export_vault(tmp_path / "policy-vault")
    assert result["pages"] == 7
    index = (tmp_path / "policy-vault" / "00-首页.md").read_text(encoding="utf-8")
    assert "按地域和文件类型分类" in index
    policy_file = next((tmp_path / "policy-vault" / "01-政策文件").rglob("*.md"))
    assert policy.title in policy_file.name and "policy-" not in policy_file.name
    content = policy_file.read_text(encoding="utf-8")
    assert "read_only: false" in content
    assert BODY_START in content and SUMMARY_START in content
    assert "引用与原文依据" in content
    assert "[[" in content


@pytest.mark.django_db
def test_obsidian_policy_edit_imports_with_version_and_audit(knowledge_catalog, tmp_path):
    admin, _, a, _ = knowledge_catalog
    sync_all()
    root = tmp_path / "policy-vault"
    export_vault(root)
    policy_file = next(path for path in (root / "01-政策文件").rglob("*.md") if str(a.pk)[:8] in path.name)
    content = policy_file.read_text(encoding="utf-8")
    content = content.replace(
        f"{SUMMARY_START}\n{a.summary}\n{SUMMARY_END}",
        f"{SUMMARY_START}\n人工修正后的政策摘要。\n{SUMMARY_END}",
    ).replace(
        f"{BODY_START}\n{a.body}\n{BODY_END}",
        f"{BODY_START}\n{a.body}\n补充核对后的正文内容。\n{BODY_END}",
    )
    policy_file.write_text(content, encoding="utf-8")

    result = import_vault_edits(admin, root)
    a.refresh_from_db()
    assert result["imported"] == 1 and not result["conflicts"]
    assert a.version == 2
    assert a.summary == "人工修正后的政策摘要。"
    assert a.body.endswith("补充核对后的正文内容。")
    assert a.enrichments.filter(policy_version=2, status="queued").exists()
    assert {"summary", "body"} <= set(
        a.scope_evidence["obsidian_correction"]["changed_fields"]
    )
