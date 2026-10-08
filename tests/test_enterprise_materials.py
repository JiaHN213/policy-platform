import json
from io import BytesIO
from unittest.mock import Mock, patch
from zipfile import ZipFile

import pytest
from accounts.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from enterprises import materials
from enterprises.materials import (
    MaterialError,
    normalize_website,
    public_addresses,
    public_get,
    upload_text,
    website_sources,
)
from enterprises.models import ResearchRun
from enterprises.research import CompanyDraft, company_graph
from enterprises.tasks import schedule_refreshes
from enterprises.views import EnterpriseResearchRunSerializer
from rest_framework.test import APIClient
from test_enterprise_profiles import profile_for


@pytest.fixture
def client_user():
    user = User.objects.create_user(username="material-customer")
    client = APIClient()
    client.force_authenticate(user)
    return client, user


@pytest.mark.django_db
@pytest.mark.parametrize("mode,extra", [
    ("website", {"website": "www.example.com"}),
    ("text", {"introduction": "测试水务公司从事污水处理、智能运维及再生水项目建设。"}),
])
def test_material_modes_queue_without_search_credentials(client_user, mode, extra):
    client, _ = client_user
    with patch("enterprises.views.get_ai_profile") as ai, patch("enterprises.views.enqueue"):
        ai.return_value.configured = True
        response = client.post("/api/v1/enterprise-research", {"name": "测试水务公司", "source_mode": mode, **extra}, format="json")
    assert response.status_code == 202, response.data
    run = ResearchRun.objects.get()
    assert run.inputs["source_mode"] == mode
    if mode == "website":
        assert run.inputs["website"] == "https://www.example.com/"


@pytest.mark.django_db
def test_file_upload_retries_and_material_access_are_private(client_user):
    client, user = client_user
    content = "测试水务公司主营污水处理设备和智慧水务系统，服务城市污水设施。".encode()
    with patch("enterprises.views.get_ai_profile") as ai, patch("enterprises.views.enqueue"):
        ai.return_value.configured = True
        response = client.post("/api/v1/enterprise-research", {"name": "测试水务公司", "source_mode": "file", "file": SimpleUploadedFile("介绍.txt", content)}, format="multipart")
        assert response.status_code == 202, response.data
        run = ResearchRun.objects.get()
        assert bytes(run.uploaded_material) == content
        assert "uploaded_material" not in response.data
        assert content.decode() not in str(response.data)
        run.status = "failed"
        run.save()
        retry = client.post("/api/v1/enterprise-research", {"name": "测试水务公司", "source_mode": "file", "retry_run_id": str(run.pk)}, format="json")
        assert retry.status_code == 202
        assert bytes(ResearchRun.objects.get(pk=retry.data["id"]).uploaded_material) == content
        other = User.objects.create_user(username="not-file-owner")
        client.force_authenticate(other)
        assert client.get(f"/api/v1/enterprise-research/{run.pk}").status_code == 404
        assert client.post("/api/v1/enterprise-research", {"name": "测试水务公司", "source_mode": "file", "retry_run_id": str(run.pk)}, format="json").status_code == 404


def test_inactive_private_fields_removed_from_search():
    from enterprises.views import ResearchInput
    serializer = ResearchInput(data={"source_mode": "search", "name": "企业名称", "introduction": "不能发送到搜索服务的企业私有材料，这是内部介绍。"})
    assert serializer.is_valid(), serializer.errors
    assert "introduction" not in serializer.validated_data


def test_text_graph_keeps_grounded_fields_without_search():
    intro = "测试水务公司主营污水处理设施运营和再生水利用，提供智慧水务设备。"
    draft = CompanyDraft.model_validate({"candidates": [{"name": "测试水务公司", "identity_evidence": {"source_id": 2, "quote": "企业名称：测试水务公司"}, "fields": [
        {"field": "business_domains", "value": ["urban_sewage"], "evidence": [{"source_id": 1, "quote": "主营污水处理设施运营"}]},
        {"field": "city", "value": "南宁", "evidence": [{"source_id": 1, "quote": "地址位于南宁市"}]},
    ]}]})
    with patch("enterprises.research.model_json", return_value=draft), patch("enterprises.research.search_sources") as search:
        result = company_graph({"name": "测试水务公司", "source_mode": "text", "introduction": intro}, lambda stage: None)
    search.assert_not_called()
    candidate = result["candidates"][0]
    assert candidate["data"] == {"business_domains": ["urban_sewage"]}
    assert candidate["evidence"]["business_domains"][0]["material"] == "粘贴简介"
    assert result["warnings"]


def test_plaintext_office_and_invalid_files():
    text = "测试水务公司主营污水处理设施运营和再生水利用，提供智慧水务设备。"
    assert upload_text(text.encode(), "企业.txt") == text
    for suffix, path, xml in [
        (".docx", "word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'),
        (".pptx", "ppt/slides/slide1.xml", f'<a:slide xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>{text}</a:t></a:slide>'),
    ]:
        archive = BytesIO()
        with ZipFile(archive, "w") as output:
            output.writestr(path, xml)
        assert text in upload_text(archive.getvalue(), "企业" + suffix)
    for data, filename in [(b"fakepdf", "intro.pdf"), (b"short", "intro.txt"), (b"bad", "intro.exe")]:
        with pytest.raises(MaterialError):
            upload_text(data, filename)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/", "http://example.com:8080", "https://user:password@example.com"])
def test_private_or_credential_urls_rejected(url):
    with pytest.raises(MaterialError):
        normalize_website(url)


def test_dns_redirect_and_robots_protections():
    with patch("enterprises.materials.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("93.184.216.34", 443)), (0, 0, 0, "", ("127.0.0.1", 443))]):
        with pytest.raises(MaterialError):
            public_addresses("example.com", 443)
    response = Mock(status=302)
    response.getheader.return_value = "http://127.0.0.1/private"
    with patch("enterprises.materials.public_addresses", return_value=["93.184.216.34"]), patch("enterprises.materials.http.client.HTTPSConnection") as connection:
        connection.return_value.getresponse.return_value = response
        with pytest.raises(MaterialError):
            public_get("https://example.com/", {"example.com"})
        assert connection.call_count == 1
    with patch("enterprises.materials.public_get", return_value=(200, "text/plain", b"User-agent: *\nDisallow: /", "https://example.com/robots.txt")) as fetch:
        with pytest.raises(MaterialError, match="限制自动读取"):
            website_sources("https://example.com", lambda stage: None)
        assert fetch.call_count == 1


def test_website_reads_bounded_same_site_pages():
    page = '<html><title>测试企业</title><body>测试水务公司主营污水处理设施运营和再生水利用，提供智慧水务设备及配套软件产品。<a href="/about">企业介绍</a><a href="https://other.com/about">其他公司</a></body></html>'.encode()
    visited = []

    def fetch(url, hosts, **kwargs):
        visited.append(url)
        if url.endswith("robots.txt"):
            return 404, "text/plain", b"", url
        if kwargs.get("can_fetch"):
            assert kwargs["can_fetch"](url)
        return 200, "text/html", page, url

    with patch("enterprises.materials.public_get", side_effect=fetch), patch("enterprises.materials.time.sleep"):
        sources, warnings = website_sources("https://example.com", lambda stage: None)
    assert len(sources) == 2 and not warnings
    assert all("other.com" not in url for url in visited)
    assert sources[1]["url"] == "https://example.com/about"


def test_missing_location_is_only_tolerated_for_robots():
    page = b"<html><body>Public homepage</body></html>"
    response = Mock(status=301)
    response.getheader.side_effect = lambda key, default="": {"Content-Type": "text/html"}.get(key, default)
    response.read.return_value = page
    with patch("enterprises.materials.public_addresses", return_value=["93.184.216.34"]), patch("enterprises.materials.http.client.HTTPSConnection") as connection:
        connection.return_value.getresponse.return_value = response
        assert public_get("https://example.com/robots.txt", {"example.com"}, robots_request=True)[:3] == (301, "text/html", page)
        with pytest.raises(MaterialError, match="无效跳转"):
            public_get("https://example.com/about", {"example.com"})


def test_malformed_robots_homepage_allows_bounded_read():
    page = '<html><body><p>测试水务公司主营污水处理设施运营和再生水利用，提供智慧水务设备及配套软件产品，服务全国城市污水处理工程和工业园区项目。</p></body></html>'.encode()
    def fetch(url, hosts, **kwargs):
        return (301 if url.endswith("robots.txt") else 200), "text/html", page, url
    with patch("enterprises.materials.public_get", side_effect=fetch):
        sources, warnings = website_sources("https://example.com/about", lambda _: None, max_pages=1)
    assert len(sources) == 1 and "污水处理" in sources[0]["text"]
    assert "不规范响应" in warnings[0]


@pytest.mark.parametrize("status,kind,body,message", [
    (301, "text/plain", b"User-agent: *\nDisallow: /", "限制自动读取"),
    (301, "text/html", b"", "无效跳转"),
    (403, "text/html", b"Denied", "限制自动读取"),
    (503, "text/html", b"Unavailable", "无法确认"),
])
def test_robots_restrictions_and_unavailable_rules_still_block(status, kind, body, message):
    with patch("enterprises.materials.public_get", return_value=(status, kind, body, "https://example.com/robots.txt")) as fetch:
        with pytest.raises(MaterialError, match=message):
            website_sources("https://example.com/", lambda _: None)
        assert fetch.call_count == 1


def dns_response(address="93.184.216.34", question="example.com.", owner="cdn.example.com."):
    return {"Status": 0, "Question": [{"name": question, "type": 1}], "Answer": [
        {"name": "example.com.", "type": 5, "data": "cdn.example.com.", "TTL": 60},
        {"name": owner, "type": 1, "data": address, "TTL": 60},
    ]}


def mock_dns_client(payload):
    client = Mock()
    response = Mock()
    response.iter_bytes.return_value = [json.dumps(payload).encode()]
    client.return_value.__enter__ = Mock(return_value=client)
    client.return_value.__exit__ = Mock(return_value=False)
    client.stream.return_value.__enter__ = Mock(return_value=response)
    client.stream.return_value.__exit__ = Mock(return_value=False)
    return client


def test_fake_ip_uses_validated_domestic_dns_and_cache():
    materials._public_dns_cache.clear()
    client = mock_dns_client(dns_response())
    with patch("enterprises.materials.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("198.18.0.8", 443))]), patch("enterprises.materials.httpx.Client", client):
        assert public_addresses("example.com", 443) == ["93.184.216.34"]
        assert public_addresses("example.com", 443) == ["93.184.216.34"]
    assert client.call_count == 1
    assert client.call_args.kwargs["trust_env"] is False
    assert client.call_args.kwargs["follow_redirects"] is False
    assert client.stream.call_args.args[1] == "https://223.5.5.5/resolve"
    materials._public_dns_cache.clear()


@pytest.mark.parametrize("payload", [dns_response("127.0.0.1"), dns_response(question="other.com."), dns_response(owner="unrelated.com.")])
def test_invalid_fallback_dns_is_rejected(payload):
    materials._public_dns_cache.clear()
    with patch("enterprises.materials.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("198.18.0.8", 443))]), patch("enterprises.materials.httpx.Client", mock_dns_client(payload)):
        with pytest.raises(MaterialError, match="备用解析暂不可用"):
            public_addresses("example.com", 443)
    assert not materials._public_dns_cache


@pytest.mark.parametrize("addresses,allowed", [(["93.184.216.34"], True), (["198.18.0.8", "10.0.0.1"], False)])
def test_regular_or_private_dns_never_uses_fallback(addresses, allowed):
    with patch("enterprises.materials.socket.getaddrinfo", return_value=[(0, 0, 0, "", (ip, 443)) for ip in addresses]), patch("enterprises.materials.fallback_public_addresses") as fallback:
        if allowed:
            assert public_addresses("example.com", 443) == addresses
        else:
            with pytest.raises(MaterialError):
                public_addresses("example.com", 443)
        fallback.assert_not_called()


def test_only_controlled_material_failures_are_shown_to_customer():
    message = "官网返回了无效跳转，请检查网址。"
    run = ResearchRun(status="failed", error=message, inputs={"source_mode": "website"})
    assert EnterpriseResearchRunSerializer(run).data["error"] == message
    run.error = "PRIVATE_ERROR internal trace"
    result = EnterpriseResearchRunSerializer(run).data
    assert "PRIVATE" not in str(result) and "stage" not in result


@pytest.mark.django_db
def test_website_refresh_without_search_key_and_materials_confirmation(client_user):
    client, user = client_user
    profile = profile_for(user, website="https://example.com/")
    profile.research_method = "website"
    profile.refresh_days = 7
    profile.next_research_at = timezone.now()
    profile.save()
    with patch("enterprises.tasks.get_ai_profile") as ai, patch("enterprises.tasks.enqueue"):
        ai.return_value.configured = True
        schedule_refreshes()
    assert ResearchRun.objects.get().inputs["source_mode"] == "website"
    run = ResearchRun.objects.create(user=user, profile=profile, inputs={"source_mode": "text"}, status="completed", fingerprint="draft", result={"candidates": [{"name": profile.organization.name, "data": {}, "evidence": {}}]})
    response = client.patch(f"/api/v1/enterprises/{profile.pk}", {"name": profile.organization.name, "data": {}, "revision": 1, "run_id": str(run.pk), "refresh_days": 7}, format="json")
    assert response.status_code == 200, response.data
    profile.refresh_from_db()
    assert profile.research_method == "materials" and profile.refresh_days == 0 and profile.next_research_at is None
