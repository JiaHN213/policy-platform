import io
from datetime import date

import pytest
from django.core.management import call_command
from ingestion.documents import parse_pdf, parse_policy_html
from ingestion.gov_library import SourceUnavailable, parse_list_page
from policies.models import DocumentSnapshot, Policy, PublicationEvent
from pypdf import PdfWriter

URL = "https://www.gov.cn/zhengce/zhengceku/202609/test.htm"


def response():
    return {
        "code": 200,
        "paramsVO": {"t": "zhengcelibrary_bm", "p": 1, "sort": "pubtime"},
        "searchVO": {
            "totalCount": 1,
            "listVO": [
                {
                    "title": "测试供水人工智能政策",
                    "url": URL,
                    "pubtimeStr": "2026.09.11",
                    "puborg": "测试部门",
                    "pcode": "测试〔2026〕1号",
                }
            ],
        },
    }


def test_live_list_response_contract():
    page = parse_list_page(response(), "bm", 1)
    assert page["items"][0]["publication_date"] == date(2026, 9, 11)


@pytest.mark.parametrize("failure", ["wrong_page", "empty", "outside_url", "bad_date"])
def test_invalid_list_response_fails_closed(failure):
    data = response()
    if failure == "wrong_page":
        data["paramsVO"]["p"] = 2
    elif failure == "empty":
        data["searchVO"]["listVO"] = []
    elif failure == "outside_url":
        data["searchVO"]["listVO"][0]["url"] = "https://127.0.0.1/file.htm"
    else:
        data["searchVO"]["listVO"][0]["pubtimeStr"] = "not-a-date"
    with pytest.raises(SourceUnavailable):
        parse_list_page(data, "bm", 1)


def test_html_ignores_mobile_duplicate_and_keeps_attachment():
    content = "测试政策内容。" * 10
    html = (
        f'<div id="UCAP-CONTENT">{content}<a href="./plan.pdf">附件</a></div><div>{content}</div>'
    )
    parsed = parse_policy_html(html.encode(), URL)
    assert parsed["text"].count(content) == 1
    assert list(parsed["attachments"]) == [URL.rsplit("/", 1)[0] + "/plan.pdf"]


def test_blank_pdf_is_not_treated_as_successful_text_extraction():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(SourceUnavailable, match="OCR_OR_PAGE_REVIEW_REQUIRED"):
        parse_pdf(buffer.getvalue())


@pytest.mark.django_db
def test_sample_import_is_idempotent_and_never_publishes(monkeypatch, settings, tmp_path):
    settings.DEBUG = True
    settings.ORIGINAL_STORAGE_BACKEND = "local"
    settings.ORIGINAL_STORAGE_ROOT = tmp_path
    module = "core.management.commands.import_source_sample"
    page = parse_list_page(response(), "bm", 1)
    monkeypatch.setattr(f"{module}.fetch_list_page", lambda *args: page)
    html = (
        '<div id="UCAP-CONTENT">' + "供水企业人工智能政策测试内容。" * 10 + "</div>"
    ).encode()
    monkeypatch.setattr("ingestion.importer.fetch_resource", lambda url: (html, "text/html", url))
    call_command("bootstrap")
    call_command("import_source_sample", url=URL)
    call_command("import_source_sample", url=URL)
    assert Policy.objects.count() == 1
    assert Policy.objects.get().status == "candidate"
    assert Policy.objects.get().is_demo is False
    assert DocumentSnapshot.objects.count() == 1
    assert PublicationEvent.objects.count() == 0
