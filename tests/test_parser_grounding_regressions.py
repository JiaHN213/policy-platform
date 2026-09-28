import io
import zipfile
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from ingestion.diagnostics import attachment_failure
from ingestion.documents import parse_policy_html
from ingestion.pdf_parser import extract_pages
from knowledge.synthesis import WikiDocument, WikiParagraph, WikiSection, _render, synthesis_failure
from policies.enrichment import OpportunityProposal, validate_opportunity_fields
from policies.grounding import date_in_quote, source_quote, unsupported_numbers


@pytest.mark.parametrize("quote,source", [
    ("补助比例为15%", "补助比例为1.5%"),
    ("温度为5度", "温度为-5度"),
    ("期限为15年", "期限为1-5年"),
    ("15%", "补贴115%"),
    ("5%", "补贴1.5%"),
])
def test_numeric_punctuation_cannot_be_erased(quote, source):
    assert source_quote(quote, source) == ""


def test_layout_tolerance_returns_original_span_and_rejects_ambiguity():
    assert source_quote("污水处理", "对污水\n处理给予支持", minimum=2) == "污水\n处理"
    assert source_quote("污水处理", "污水\n处理和污水 处理", minimum=2) == ""


def test_claim_numeric_units_and_dates_are_checked():
    assert unsupported_numbers("补贴15%", "补贴1.5%")
    assert unsupported_numbers("补贴10亿元", "补贴10万元")
    assert not unsupported_numbers("2026-09-27截止，补贴1.5%", "2026年9月27日截止，补贴1.5%")
    assert unsupported_numbers("2026-09-28截止", "2026年9月27日截止")
    assert not date_in_quote("2026-09-27T18:00:00", "2026年9月27日截止")


def test_mixed_pdf_rejects_unreadable_page_but_allows_real_blank(monkeypatch):
    text_page = SimpleNamespace(get_contents=lambda: None, extract_text=lambda: "A policy with more than thirty characters of text content.")
    image_page = SimpleNamespace(get_contents=lambda: SimpleNamespace(get_data=lambda: b"/Image Do"), extract_text=lambda: "")
    blank_page = SimpleNamespace(get_contents=lambda: None, extract_text=lambda: "")
    reader = SimpleNamespace(is_encrypted=False, pages=[text_page, image_page])
    monkeypatch.setattr("ingestion.pdf_parser.PdfReader", lambda _: reader)
    with pytest.raises(ValueError, match="第 2 页"):
        extract_pages(b"%PDF-test")
    reader.pages = [text_page, blank_page]
    assert len(extract_pages(b"%PDF-test")) == 1


def test_spreadsheet_attachments_are_discovered():
    html = '<div id="UCAP-CONTENT">' + "政策正文。" * 10 + '<a href="/a.xlsx">表格</a><a href="/a.xls">附表</a></div>'
    assert len(parse_policy_html(html.encode(), "https://www.gov.cn/x.htm")["attachments"]) == 2


def test_failures_are_distinguished_without_raw_server_details():
    assert "超时" in attachment_failure(httpx.ReadTimeout("secret"), "download")
    assert "损坏" in attachment_failure(zipfile.BadZipFile("secret"), "parse")
    assert "secret" not in attachment_failure(ValueError("secret"), "parse")
    assert "超时" in synthesis_failure(httpx.ReadTimeout("secret"))


def test_opportunity_fields_are_withheld_without_losing_supported_opportunity():
    proposal = OpportunityProposal(
        name="污水补贴", category="fiscal", percentage=15, amount=10, amount_unit="万元",
        evidence=[{"field": "percentage", "quote": "补贴比例1.5%"},
                  {"field": "amount", "quote": "支持金额10万元"},
                  {"field": "deadline_at", "quote": "2026年9月27日截止"}],
        batches=[{"name": "第一批", "deadline_at": "2026-09-28T00:00:00", "status": "open"}],
    )
    diagnostics = []
    validate_opportunity_fields(proposal, diagnostics)
    assert proposal.percentage is None
    assert proposal.amount == 10
    assert proposal.batches[0].deadline_at == ""
    assert proposal.batches[0].status == "unverified"
    assert len(diagnostics) == 2


def test_wiki_rejects_wrong_amount_despite_valid_citation_id():
    document = WikiDocument(abstract="支持政策", sections=[
        WikiSection(heading="措施", paragraphs=[WikiParagraph(text="补贴15%", citation_ids=[1])]),
        WikiSection(heading="说明", paragraphs=[WikiParagraph(text="以原文为准", citation_ids=[1])]),
    ])
    with pytest.raises(ValueError, match="WIKI_UNSUPPORTED_NUMERIC_CLAIM"):
        _render(document, 1, [{"quote": "补贴比例1.5%"}])


@pytest.mark.django_db
def test_wiki_worker_restart_preserves_live_rebuild_lease(monkeypatch, settings):
    from datetime import timedelta

    from django.core.management import call_command
    from django.utils import timezone
    from knowledge.models import KnowledgeBuild

    settings.LOCAL_WORKER = True
    build = KnowledgeBuild.objects.create(status="running", lease_until=timezone.now() + timedelta(minutes=60))
    monkeypatch.setattr("core.management.commands.local_wiki_worker.dispatch_knowledge_builds", lambda: None)
    call_command("local_wiki_worker", once=True)
    build.refresh_from_db()
    assert build.status == "running" and build.lease_until > timezone.now()


@pytest.mark.django_db
def test_central_import_parses_docx_and_keeps_failed_attachment(monkeypatch, settings, tmp_path):
    from ingestion.importer import import_record
    from ingestion.models import DiscoveredItem, Source

    settings.ORIGINAL_STORAGE_BACKEND = "local"
    settings.DEBUG = True
    settings.ORIGINAL_STORAGE_ROOT = tmp_path
    url = "https://www.gov.cn/test.htm"
    html = ('<div id="UCAP-CONTENT">' + "支持城镇污水处理设施建设。" * 10 +
            '<a href="/plan.docx">方案</a><a href="/old.doc">旧附件</a></div>').encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>支持农村污水处理设施建设和运行。</w:t></w:r></w:p></w:body></w:document>')

    def fetch(target, *args):
        if target == url:
            return html, "text/html", target
        if target.endswith("docx"):
            return buffer.getvalue(), "application/octet-stream", target
        return b"unsupported-doc", "application/msword", target

    monkeypatch.setattr("ingestion.importer.fetch_resource", fetch)
    monkeypatch.setattr("ingestion.importer.time.sleep", lambda _: None)
    source = Source.objects.create(name="测试来源", url="https://www.gov.cn", adapter="gov_library_html_v1")
    policy, *_ = import_record({"url": url, "title": "污水处理政策", "issuer": "国务院",
                               "publication_date": date(2026, 9, 27)}, source)
    assert "农村污水处理" in policy.body
    assert policy.snapshots.filter(parse_status="pending").count() == 1
    item = DiscoveredItem.objects.get(policy=policy)
    assert len(item.metadata["attachment_issues"]) == 1
    assert item.error_code == "ATTACHMENTS_REQUIRE_REVIEW"
