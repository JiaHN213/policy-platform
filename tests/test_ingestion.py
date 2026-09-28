import pytest
from ingestion.gov_library import SourceUnavailable, discover_links, validate_url


def test_html_links_deduplicated():
    html = '<a href="https://www.gov.cn/zhengce/2026/test.htm">测试用政策文件标题</a>' * 2
    assert len(discover_links(html)) == 1


def test_empty_javascript_shell_is_failure_not_no_change():
    with pytest.raises(SourceUnavailable, match="LIST_STRUCTURE_UNVERIFIED"):
        discover_links('<html><div id="app"></div></html>')


@pytest.mark.parametrize(
    "url",
    [
        "http://www.gov.cn/",
        "https://127.0.0.1/",
        "https://www.gov.cn.evil.test/",
        "https://user:pass@www.gov.cn/",
        "https://www.gov.cn:8080/",
    ],
)
def test_disallowed_urls(url):
    with pytest.raises(SourceUnavailable):
        validate_url(url, resolve=False)


def test_vpn_fake_ip_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "ingestion.gov_library.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("198.18.0.1", 443))],
    )
    with pytest.raises(SourceUnavailable, match="NON_PUBLIC_ADDRESS"):
        validate_url("https://www.gov.cn/")
