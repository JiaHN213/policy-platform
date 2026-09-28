import io
import json
import zipfile
from datetime import datetime, time, timedelta

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from ingestion import nanning
from ingestion import tasks as ingestion_tasks
from ingestion.documents import extract_docx_text
from ingestion.gov_library import SourceUnavailable
from ingestion.models import CrawlThrottle, DiscoveredItem, Source, SourceCheckRun
from ingestion.nanning_importer import fetch_npc_document, import_record
from ingestion.tasks import check_source, queue_check
from policies.business_scope import SEARCH_TERMS, assess_scope
from policies.models import Policy, PublicationEvent
from policies.services import publish_policy
from rest_framework.test import APIClient


@pytest.mark.parametrize(
    "title,body,decision",
    [
        ("人工智能设备更新补贴", "支持汽车制造企业设备更新。", "excluded"),
        ("污水处理设施改造通知", "支持设施升级。", "included"),
        ("设备更新行动方案", "支持供水企业开展设备更新和节能改造。", "included"),
        ("水路运输管理办法", "航行时避开饮用水水源一级保护区。", "needs_review"),
        ("召开供水工作会议", "支持供水企业。", "excluded"),
    ],
)
def test_scope_requires_business_applicability(title, body, decision):
    result = assess_scope(title, body)
    assert result["decision"] == decision
    for proof in result["evidence"]:
        assert proof["term"] in proof["quote"]
        assert proof["quote"] in title + "\n" + body


def test_direction_and_business_are_independent():
    result = assess_scope("工业废水处理设备更新方案", "推进人工智能技术应用。")
    assert "industrial_wastewater" in result["business_domains"]
    assert {"ai", "equipment_renewal"} <= set(result["direction_tags"])
    assert assess_scope("人工智能发展规划", "促进科技创新")["business_domains"] == []


def test_retry_transient_network_failure(monkeypatch):
    calls = []

    def fetch(*args):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("temporary")
        return b"ok", "text/html", nanning.URL

    monkeypatch.setattr(nanning, "_request_once", fetch)
    monkeypatch.setattr(nanning.time, "sleep", lambda _: None)
    assert nanning.request(nanning.URL)[0] == b"ok"
    assert len(calls) == 3


def test_legacy_government_tls_failure_falls_back_to_http(settings, monkeypatch):
    settings.POLICY_CRAWL_ALLOW_LEGACY_HTTP = True
    calls = []

    def fetch(url, *args):
        calls.append(url)
        if url.startswith("https://"):
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]")
        return b"legacy", "text/html", url

    monkeypatch.setattr(nanning, "_request_once", fetch)
    assert nanning.request("https://example.gov.cn/policy.html")[2].startswith("http://")
    assert calls == [
        "https://example.gov.cn/policy.html",
        "http://example.gov.cn/policy.html",
    ]


def test_network_route_switches_between_vpn_proxy_and_direct(settings, monkeypatch):
    settings.POLICY_CRAWL_PROXY_URL = "http://127.0.0.1:7890"
    monkeypatch.setattr(nanning, "_proxy_available", lambda _: True)
    monkeypatch.setattr(
        nanning.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("198.18.0.31", 443))],
    )
    assert nanning._select_proxy(nanning.URL) == "http://127.0.0.1:7890"

    monkeypatch.setattr(
        nanning.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))],
    )
    assert nanning._select_proxy(nanning.URL) is None


def test_fake_ip_without_running_proxy_has_clear_error(settings, monkeypatch):
    settings.POLICY_CRAWL_PROXY_URL = "http://127.0.0.1:7890"
    monkeypatch.setattr(
        nanning.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("198.18.0.31", 443))],
    )
    monkeypatch.setattr(nanning, "_proxy_available", lambda _: False)
    with pytest.raises(SourceUnavailable, match="VPN_PROXY_UNAVAILABLE"):
        nanning._select_proxy(nanning.URL)


@pytest.mark.parametrize(
    "container",
    [
        '<div class="article-con">',
        '<div class="trs_editor_view">',
        '<div id="detail_con">',
        '<div class="content-main">',
    ],
)
def test_common_government_body_containers_are_parsed(container):
    html = f"{container}{'供水政策正文。' * 10}</div>".encode()
    assert "供水政策正文" in nanning.parse_document(html, "https://example.gov.cn/a.html")[
        "text"
    ]


def test_inline_spans_preserve_words_and_paragraphs():
    html = (
        '<div id="UCAP-CONTENT"><div id="contentText">'
        '<p>切实防<span>范项目</span>运营风险。</p>'
        '<p>根据实施<span>细则</span>执行。</p>'
        '<p>政策正文中的其他内容保持原样。</p>'
        '</div><div class="related">相关文章不属于正文</div></div>'
    ).encode()
    parsed = nanning.parse_document(html, "https://example.gov.cn/a.html")
    assert parsed["text"] == (
        "切实防范项目运营风险。\n\n根据实施细则执行。\n\n"
        "政策正文中的其他内容保持原样。"
    )


def test_gb18030_html_and_docx_runs_are_readable():
    html = (
        '<meta charset="gb2312"><div class="article-con"><p>污水处理设施改造通知。</p>'
        '<p>支持供水企业实施设备更新。</p><p>政策正文中的中文必须正确解码。</p></div>'
    ).encode("gb18030")
    assert "污水处理设施改造通知" in nanning.parse_document(
        html, "https://example.gov.cn/a.html"
    )["text"]

    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>防</w:t></w:r><w:r><w:t>范项目风险。</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>第二段。</w:t></w:r></w:p></w:body></w:document>'
    ).encode()
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("word/document.xml", xml)
    assert extract_docx_text(document.getvalue()) == "防范项目风险。\n第二段。"


def test_only_links_inside_policy_body_are_collected_as_attachments():
    html = (
        '<a href="/navigation.pdf">站点导航</a>'
        '<div class="article-con">'
        + "供水政策正文。" * 10
        + '<a href="/policy-annex.pdf">政策附件</a></div>'
    ).encode()
    parsed = nanning.parse_document(html, "https://example.gov.cn/a.html")
    assert list(parsed["attachments"]) == ["https://example.gov.cn/policy-annex.pdf"]


def test_npc_dynamic_document_uses_official_detail_content(monkeypatch):
    detail = {
        "code": 200,
        "data": {
            "title": "中华人民共和国生态环境法典",
            "zdjgName": "全国人民代表大会",
            "gbrq": "2026-03-12",
            "content": "<p>生态环境保护法律正文。</p>" * 10,
        },
    }
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return json.dumps(detail).encode(), "application/json", url

    monkeypatch.setattr("ingestion.nanning_importer.request", fetch)
    body, snapshots, metadata = fetch_npc_document(
        {"url": "https://flk.npc.gov.cn/detail?id=official-id"}
    )
    assert "生态环境保护法律正文" in body
    assert snapshots == []
    assert metadata["title"] == "中华人民共和国生态环境法典"
    assert calls == [
        "https://flk.npc.gov.cn/law-search/search/flfgDetails?bbbs=official-id"
    ]


def test_npc_table_of_contents_downloads_official_document(monkeypatch):
    detail = {
        "code": 200,
        "data": {
            "title": "中华人民共和国水法",
            "content": {"title": "目录", "children": []},
        },
    }
    download = {"code": 200, "data": {"url": "https://flkoss.obs-bj2.cucloud.cn/law.docx"}}
    responses = [
        (json.dumps(detail).encode(), "application/json"),
        (json.dumps(download).encode(), "application/json"),
        (b"official-docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ]
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        content, content_type = responses.pop(0)
        return content, content_type, url

    monkeypatch.setattr("ingestion.nanning_importer.request", fetch)
    monkeypatch.setattr(
        "ingestion.nanning_importer.attachment_text",
        lambda content, suffix: "水资源节约、保护、开发和利用。" * 5,
    )
    monkeypatch.setattr(
        "ingestion.nanning_importer.store_original",
        lambda content, content_type: {"sha256": "a" * 64, "storage_key": "test"},
    )
    body, snapshots, metadata = fetch_npc_document(
        {"url": "https://flk.npc.gov.cn/detail?id=official-id"}
    )
    assert "水资源节约" in body
    assert snapshots[0]["parse_status"] == "parsed"
    assert metadata["title"] == "中华人民共和国水法"
    assert len(calls) == 3


def test_list_rejects_silently_ignored_date_filter(monkeypatch):
    payload = {
        "ok": True,
        "code": 200,
        "data": {
            "config": {"id": nanning.TAB},
            "result": {"search": {"total": 1, "docs": [{"docDate": "2026-01-01"}]}},
        },
    }
    monkeypatch.setattr(nanning, "request", lambda *a, **k: (json.dumps(payload).encode(), "", ""))
    with pytest.raises(SourceUnavailable, match="DATE_MISMATCH"):
        nanning.list_page(start="2020-01-01", end="2020-12-31")


def test_official_access_restriction_is_not_treated_as_empty(monkeypatch):
    payload = {"ok": False, "code": -101, "msg": "access disabled", "data": None}
    cooldowns = []
    monkeypatch.setattr(nanning, "request", lambda *a, **k: (json.dumps(payload).encode(), "", ""))
    monkeypatch.setattr(nanning, "activate_cooldown", lambda status: cooldowns.append(status))
    with pytest.raises(SourceUnavailable, match="NANNING_ACCESS_RESTRICTED"):
        nanning.list_page()
    assert cooldowns == [403]


def test_access_restriction_pauses_scheduled_collection(source, monkeypatch):
    def restricted(*args):
        raise SourceUnavailable("NANNING_ACCESS_RESTRICTED")

    source.enabled = True
    source.save()
    run = SourceCheckRun.objects.create(source=source)
    monkeypatch.setattr(nanning, "list_page", restricted)
    check_source(str(run.pk))
    source.refresh_from_db()
    run.refresh_from_db()
    assert source.enabled is True
    assert source.next_check_at > timezone.now()
    assert run.error_code == "NANNING_ACCESS_RESTRICTED"
    assert run.status == "failed"


@pytest.fixture
def source(db, settings, tmp_path, monkeypatch):
    settings.LOCAL_WORKER = True
    settings.DEBUG = True
    settings.ORIGINAL_STORAGE_BACKEND = "local"
    settings.ORIGINAL_STORAGE_ROOT = tmp_path
    monkeypatch.setattr(nanning.time, "sleep", lambda _: None)
    return Source.objects.create(name="南宁测试", url=nanning.URL, adapter="nanning_v1")


def row(index=1):
    return {
        "id": str(index),
        "title": "供水管理办法",
        "url": f"https://www.nanning.gov.cn/{index}.html",
        "docDate": "2020-01-01",
        "myValues": {"DOCPUBNAME": "南宁市人民政府"},
    }


def test_cursor_survives_failure_after_committed_page(source, monkeypatch):
    run = SourceCheckRun.objects.create(source=source)
    calls = []

    def page(*args):
        calls.append(1)
        if len(calls) > 1:
            raise httpx.ConnectError("temporary")
        return {"total": 21, "docs": [row(i) for i in range(20)], "raw": b"page"}

    monkeypatch.setattr(nanning, "list_page", page)
    advance = nanning.advance
    monkeypatch.setattr(nanning, "advance", lambda run_id: advance(run_id, budget=2))
    check_source(str(run.pk))
    run.refresh_from_db()
    assert run.status == "failed"
    assert run.progress["rows_scanned"] == 20
    assert run.progress["parts"][0]["page"] == 2
    assert run.discovered == DiscoveredItem.objects.count() == 20


def test_dense_result_splits_dates_without_creating_policies(source, monkeypatch):
    run = SourceCheckRun.objects.create(source=source)
    monkeypatch.setattr(nanning, "list_page", lambda *a: {"total": 60565, "docs": [], "raw": b""})
    nanning.advance(run.pk, budget=1)
    run.refresh_from_db()
    assert len(run.progress["parts"]) == 2
    assert run.progress["catalog_total"] is None
    assert Policy.objects.count() == 0


def test_manual_retry_resumes_same_catalog_cursor(source):
    progress = nanning.new_progress()
    progress["parts"][0]["page"] = 7
    run = SourceCheckRun.objects.create(source=source, status="failed", progress=progress)
    resumed = queue_check(source.pk)
    assert resumed.pk == run.pk
    assert resumed.status == "queued"
    assert resumed.progress["parts"][0]["page"] == 7


def test_initial_history_is_targeted_but_incremental_window_checks_all_new_titles():
    assert "" not in nanning.new_progress()["queries"]
    assert nanning.new_progress("2026-09-14", "2026-09-17")["queries"][0] == ""
    assert len(nanning.TERMS) == 27
    assert all(
        not any(earlier in term for earlier in nanning.TERMS[:index])
        for index, term in enumerate(nanning.TERMS)
    )


def test_legacy_full_catalog_cursor_is_upgraded_without_another_blank_query():
    legacy = {
        **nanning.new_progress(),
        "version": 2,
        "queries": [""] + nanning.TERMS,
        "query_index": 0,
        "parts": [{"start": "2020-01-01", "end": "2020-12-31", "page": 37}],
        "catalog_total": 60000,
        "catalog_rows": 720,
    }
    upgraded = nanning._upgrade_progress(legacy)
    assert upgraded["version"] == 4
    assert "" not in upgraded["queries"]
    assert upgraded["parts"][0]["page"] == 1
    assert upgraded["catalog_total"] is None


def test_v3_targeted_cursor_keeps_current_term_and_page_during_compaction():
    progress = {
        **nanning.new_progress(),
        "version": 3,
        "queries": nanning.PRIMARY_TERMS + SEARCH_TERMS,
        "query_index": 1,
        "parts": [{"start": "1000-01-01", "end": "2999-12-31", "page": 12}],
    }
    upgraded = nanning._upgrade_progress(progress)
    assert upgraded["version"] == 4
    assert upgraded["queries"][upgraded["query_index"]] == "废水"
    assert upgraded["parts"][0]["page"] == 12


def test_completed_source_uses_overlapping_incremental_window(source, settings):
    settings.POLICY_CRAWL_INCREMENTAL_LOOKBACK_DAYS = 3
    source.last_success_at = timezone.now() - timedelta(days=1)
    source.save(update_fields=["last_success_at"])
    run = queue_check(source.pk)
    assert run.progress["mode"] == "incremental"
    assert run.progress["window_end"] == timezone.now().date().isoformat()
    assert (
        run.progress["window_start"]
        == (source.last_success_at - timedelta(days=3)).date().isoformat()
    )


def test_source_wide_throttle_spaces_requests_and_stops_during_cooldown(
    source, settings, monkeypatch
):
    source.enabled = True
    source.save(update_fields=["enabled"])
    settings.POLICY_CRAWL_MIN_INTERVAL_SECONDS = 5
    settings.POLICY_CRAWL_JITTER_SECONDS = 0
    waits = []
    monkeypatch.setattr(nanning.time, "sleep", waits.append)
    nanning._reserve_request_slot()
    nanning._reserve_request_slot()
    assert waits and waits[-1] > 4
    nanning.activate_cooldown(429)
    throttle = CrawlThrottle.objects.get(scope=nanning.THROTTLE_SCOPE)
    assert throttle.blocked_until > timezone.now()
    with pytest.raises(SourceUnavailable, match="NANNING_COOLDOWN_ACTIVE"):
        nanning._reserve_request_slot()

    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user("source-admin", is_staff=True))
    payload = client.get("/api/v1/admin/sources").data["items"][0]
    assert payload["crawl_state"] == "cooldown"
    assert payload["cooldown_reason"] == "来源请求限流"
    assert payload["cooldown_until"] is not None


def test_success_releases_items_parked_by_an_expired_shared_cooldown(source):
    item = DiscoveredItem.objects.create(
        source=source,
        url="https://www.nanning.gov.cn/policy.html",
        title="供水政策",
        metadata={"url": "https://www.nanning.gov.cn/policy.html"},
        status="failed",
        error_code="NANNING_COOLDOWN_ACTIVE",
        error_detail="old cooldown",
        retry_at=timezone.now() + timedelta(hours=6),
    )
    nanning._record_success(200)
    item.refresh_from_db()
    assert item.status == "discovered"
    assert item.error_code == item.error_detail == ""
    assert item.retry_at is None


def test_summary_counts_distinct_policy_links_needing_attention(source):
    other = Source.objects.create(name="另一来源", url="https://example.gov.cn/source")
    shared_url = "https://www.nanning.gov.cn/shared-policy.html"
    DiscoveredItem.objects.create(source=source, url=shared_url, status="failed")
    DiscoveredItem.objects.create(source=other, url=shared_url, status="needs_review")
    DiscoveredItem.objects.create(
        source=source,
        url="https://www.nanning.gov.cn/another-policy.html",
        status="needs_review",
    )
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user("summary-admin", is_staff=True))
    payload = client.get("/api/v1/admin/discovered-items/summary").data
    assert payload["failed"] == 1
    assert payload["needs_review"] == 2
    assert payload["needs_attention_links"] == 2


def test_dispatches_only_one_nanning_import_at_a_time(source, monkeypatch):
    for index in range(3):
        DiscoveredItem.objects.create(
            source=source,
            url=f"https://www.nanning.gov.cn/policy-{index}.html",
            title=f"政策 {index}",
            metadata={"url": f"https://www.nanning.gov.cn/policy-{index}.html"},
        )
    dispatched = []
    monkeypatch.setattr(
        ingestion_tasks, "import_discovered", lambda item_id: dispatched.append(item_id)
    )
    ingestion_tasks.dispatch_imports()
    assert len(dispatched) == 1


def test_interrupted_imports_are_requeued_without_parse_failure(source):
    now = timezone.now()
    expired = DiscoveredItem.objects.create(
        source=source,
        url="https://www.nanning.gov.cn/expired.html",
        title="中断任务",
        metadata={"url": "https://www.nanning.gov.cn/expired.html"},
        status="processing",
        attempts=2,
        lease_until=now - timedelta(minutes=1),
    )
    active = DiscoveredItem.objects.create(
        source=source,
        url="https://www.nanning.gov.cn/active.html",
        title="仍在处理",
        metadata={"url": "https://www.nanning.gov.cn/active.html"},
        status="processing",
        attempts=1,
        lease_until=now + timedelta(minutes=5),
    )

    assert ingestion_tasks.recover_interrupted_imports(expired_only=True, now=now) == 1
    expired.refresh_from_db()
    active.refresh_from_db()
    assert expired.status == "discovered"
    assert expired.attempts == 0
    assert expired.error_code == ""
    assert active.status == "processing"

    assert ingestion_tasks.recover_interrupted_imports() == 1
    active.refresh_from_db()
    assert active.status == "discovered"
    assert active.attempts == 0


@pytest.mark.parametrize("included", [True, False])
def test_real_body_gate_before_policy_creation(source, monkeypatch, included):
    record = nanning.record(row())
    record["title"] = "供水管理办法" if included else "人工智能发展规划"
    body = "供水企业应完善水务设施。" if included else "汽车制造企业推进人工智能发展。"
    html = ('<div class="TRS_Editor">' + body * 10 + "</div>").encode()
    monkeypatch.setattr("ingestion.nanning_importer.request", lambda url: (html, "text/html", url))
    item = DiscoveredItem.objects.create(source=source, url=record["url"], metadata=record)
    import_record(record, source)
    item.refresh_from_db()
    assert item.status == ("imported" if included else "excluded")
    assert Policy.objects.count() == int(included)
    assert PublicationEvent.objects.count() == 0
    if included:
        assert item.policy.status == "candidate"
        assert item.policy.scope_evidence["evidence"]
        assert item.policy.snapshots.count() == 1


def test_weak_scope_match_is_kept_as_index_without_manual_review(source, monkeypatch):
    record = nanning.record(row())
    record["title"] = "水路运输管理办法"
    body = "船舶航行时应避开饮用水水源一级保护区。" * 10
    html = ('<div class="TRS_Editor">' + body + "</div>").encode()
    monkeypatch.setattr("ingestion.nanning_importer.request", lambda url: (html, "text/html", url))
    item = DiscoveredItem.objects.create(source=source, url=record["url"], metadata=record)

    policy, *_ = import_record(record, source)

    item.refresh_from_db()
    assert policy is None
    assert item.status == "indexed"
    assert item.metadata["scope_assessment"]["decision"] == "needs_review"


def test_scope_exclusion_wins_over_missing_issuer(source, monkeypatch):
    record = nanning.record(row())
    record["title"] = "人工智能发展规划"
    record["issuer"] = ""
    body = "汽车制造企业推进人工智能发展。" * 10
    html = ('<div class="TRS_Editor">' + body + "</div>").encode()
    monkeypatch.setattr("ingestion.nanning_importer.request", lambda url: (html, "text/html", url))
    item = DiscoveredItem.objects.create(source=source, url=record["url"], metadata=record)

    policy, *_ = import_record(record, source)

    item.refresh_from_db()
    assert policy is None
    assert item.status == "excluded"
    assert item.error_code == ""


def test_source_daily_schedule_uses_selected_local_time(source):
    source.schedule_mode = Source.ScheduleMode.DAILY
    source.daily_check_time = time(2, 30)
    now = timezone.make_aware(datetime(2026, 9, 23, 1, 0))

    scheduled = timezone.localtime(source.next_scheduled_check(now))

    assert scheduled.date().isoformat() == "2026-09-23"
    assert scheduled.time().replace(tzinfo=None) == time(2, 30)


def test_admin_can_add_policy_library_with_friendly_schedule(source):
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_superuser("source-config-admin"))

    response = client.post(
        "/api/v1/admin/sources",
        {
            "name": "中国政府网政策文件库",
            "url": "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary",
            "collection_type": "gov_library_html_v1",
            "enabled": True,
            "schedule_mode": "daily",
            "daily_check_time": "03:00",
            "interval_minutes": 1440,
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["collection_type_label"] == "中国政府网政策文件库"
    assert response.data["schedule_mode"] == "daily"
    assert response.data["next_check_at"] is not None


def test_unparsed_attachment_blocks_publication(source, monkeypatch):
    record = nanning.record(row())
    html = (
        '<div class="TRS_Editor">'
        + "供水企业应完善供水设施。" * 10
        + '<a href="/annex.xls">附件</a></div>'
    ).encode()
    monkeypatch.setattr(
        "ingestion.nanning_importer.request",
        lambda url, **kw: (
            b"unparsed" if url.endswith(".xls") else html,
            "application/vnd.ms-excel" if url.endswith(".xls") else "text/html",
            url,
        ),
    )
    DiscoveredItem.objects.create(source=source, url=record["url"], metadata=record)
    policy, *_ = import_record(record, source)
    admin = get_user_model().objects.create_superuser("reviewer")
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError):
        publish_policy(
            policy.pk,
            admin,
            1,
            document_type="policy",
            provenance={"source_grade": "L1", "geographic_level": "national"},
        )
    assert PublicationEvent.objects.count() == 0
