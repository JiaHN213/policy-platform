"""User supplied material and bounded public-site reads (no search credentials)."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import zipfile
from io import BytesIO
from pathlib import PurePath
from urllib.parse import quote, urldefrag, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from django.utils import timezone
from ingestion.documents import decode_html, extract_attachment_text, readable_html_text

MAX_UPLOAD = 5 * 1024 * 1024
MAX_TEXT = 40000
FILE_TYPES = {".pdf", ".docx", ".pptx", ".txt", ".md"}
USER_AGENT = "PolicyObserverEnterprise/1.0"
PAGE_HINTS = ("关于", "简介", "介绍", "业务", "产品", "案例", "公司", "about", "profile", "business", "product", "case")
FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_public_dns_cache = {}
DNS_ERROR = "官网地址仍受到网络虚拟地址影响，且备用解析暂不可用。请稍后重试，或上传、粘贴企业介绍；无需开启 VPN。"
CUSTOMER_MATERIAL_ERRORS = frozenset({
    DNS_ERROR,
    "官网解析到了内网或代理虚拟地址，请使用公网官网或改为上传、粘贴介绍。",
    "无法解析企业官网地址，请检查网址，或改为上传、粘贴企业介绍。",
    "官网返回了无效跳转，请检查网址。",
    "网址跳转到了其他网站，请填写最终的企业官网地址。",
    "该网站限制自动读取，请改为上传或粘贴企业介绍。",
    "该网页过大，请提供企业简介页面，或上传介绍文件。",
    "官网暂时无法连接或证书验证失败，请检查网址，或改为上传、粘贴介绍。",
    "官网跳转次数过多，请填写最终页面地址。",
    "官网暂时无法确认自动读取规则，请稍后重试或提供企业介绍。",
    "官网拒绝访问或页面不存在，请检查网址或提供企业介绍。",
    "该网址不是可读取的网页，请提供官网首页或企业简介页面。",
    "官网没有可直接读取的文字，可能依赖动态加载；请上传或粘贴企业介绍。",
    "未能读取官网文字，请上传或粘贴企业介绍。",
})


class MaterialError(ValueError):
    pass


def normalize_website(url):
    url = str(url).strip()
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password:
            raise ValueError()
        if parsed.port not in {None, 80, 443} or "." not in host or host.endswith((".local", ".localhost", ".internal")):
            raise ValueError()
        if any(ord(c) < 32 or c.isspace() for c in url):
            raise ValueError()
        try:
            if not ipaddress.ip_address(host).is_global:
                raise MaterialError("请填写可公开访问的企业官网，不能使用本机或内网地址。")
        except ValueError as exc:
            if isinstance(exc, MaterialError):
                raise
        return urlunparse((parsed.scheme, host + (f":{parsed.port}" if parsed.port else ""), parsed.path or "/", parsed.params, parsed.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise MaterialError("请填写有效的公开企业官网地址，不能包含账号、密码或自定义端口。") from exc


def fallback_public_addresses(host):
    """Resolve only residual Fake-IP DNS through domestic, TLS-verified public DNS.

    No environment proxies, redirects, credentials or page content are sent.
    Validate the question and CNAME chain before caching public IPv4 records.
    """
    cached = _public_dns_cache.get(host)
    if cached and cached[0] > time.monotonic():
        return list(cached[1])
    for endpoint in ("https://223.5.5.5/resolve", "https://223.6.6.6/resolve"):
        try:
            with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                with client.stream("GET", endpoint, params={"name": host, "type": "1"}) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 65536:
                            raise ValueError("DNS response too large")
            payload = json.loads(raw)
            questions = payload.get("Question", [])
            if isinstance(questions, dict):
                questions = [questions]
            if payload.get("Status") != 0 or payload.get("TC") or not any(
                q.get("name", "").rstrip(".").lower() == host and q.get("type") == 1
                for q in questions
            ):
                raise ValueError("DNS question mismatch")
            records = payload.get("Answer", [])
            if not isinstance(records, list) or len(records) > 64:
                raise ValueError("Invalid DNS records")
            names = {host}
            for _ in range(8):
                aliases = {r["data"].rstrip(".").lower() for r in records
                           if r.get("type") == 5 and r.get("name", "").rstrip(".").lower() in names}
                if aliases <= names:
                    break
                names.update(aliases)
            matched = [r for r in records if r.get("type") == 1 and r.get("name", "").rstrip(".").lower() in names]
            addresses = list(dict.fromkeys(str(ipaddress.IPv4Address(r["data"])) for r in matched))
            if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
                raise ValueError("Non-public DNS answer")
            ttl = max(0, min(300, *(int(r.get("TTL", 0)) for r in records if r.get("name", "").rstrip(".").lower() in names)))
            if len(_public_dns_cache) >= 256:
                _public_dns_cache.clear()
            _public_dns_cache[host] = (time.monotonic() + ttl, tuple(addresses))
            return addresses
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            continue
    raise MaterialError(DNS_ERROR)


def public_addresses(host, port):
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(row[4][0] for row in rows))
        nonpublic = [ipaddress.ip_address(ip) for ip in addresses if not ipaddress.ip_address(ip).is_global]
        if nonpublic and all(ip in FAKE_IP_NETWORK for ip in nonpublic):
            return fallback_public_addresses(host)
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise MaterialError("官网解析到了内网或代理虚拟地址，请使用公网官网或改为上传、粘贴介绍。")
        return addresses
    except OSError as exc:
        raise MaterialError("无法解析企业官网地址，请检查网址，或改为上传、粘贴企业介绍。") from exc


def public_get(url, allowed_hosts, limit=3_000_000, can_fetch=None, *, robots_request=False):
    """Pin the TCP connection to the validated address; keep TLS hostname verification."""
    for _ in range(4):
        url = normalize_website(url)
        parsed = urlparse(url)
        if parsed.hostname not in allowed_hosts:
            raise MaterialError("网址跳转到了其他网站，请填写最终的企业官网地址。")
        if can_fetch and not can_fetch(url):
            raise MaterialError("该网站限制自动读取，请改为上传或粘贴企业介绍。")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = public_addresses(parsed.hostname, port)
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        kwargs = {"context": ssl.create_default_context()} if parsed.scheme == "https" else {}
        connection = cls(parsed.hostname, port, timeout=12, **kwargs)
        # No environment proxy, and no second DNS resolution of the original host.
        connection._create_connection = lambda address, timeout, source_address=None: socket.create_connection((addresses[0], port), timeout, source_address)
        try:
            path = quote(urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, "")), safe="/%?=&;:+,@!$'()*~-._")
            connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            status = response.status
            if status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if location:
                    url = urljoin(url, location)
                    continue
                if not (robots_request and status == 301 and parsed.path == "/robots.txt" and not parsed.query):
                    raise MaterialError("官网返回了无效跳转，请检查网址。")
            content_type = response.getheader("Content-Type", "").lower()
            data = response.read(limit + 1)
            if len(data) > limit:
                raise MaterialError("该网页过大，请提供企业简介页面，或上传介绍文件。")
            return status, content_type, data, url
        except (OSError, http.client.HTTPException) as exc:
            raise MaterialError("官网暂时无法连接或证书验证失败，请检查网址，或改为上传、粘贴介绍。") from exc
        finally:
            connection.close()
    raise MaterialError("官网跳转次数过多，请填写最终页面地址。")


def material_source(text, title, *, url="", kind="企业提供", material="粘贴简介", source_id=1):
    return {"id": source_id, "url": url, "title": title, "text": text,
            "retrieved_at": timezone.now().isoformat(), "material": material, "source_type": kind}


def website_sources(website, progress, *, max_pages=5, include_links=False):
    first = normalize_website(website)
    hostname = urlparse(first).hostname
    base_host = hostname.removeprefix("www.")
    hosts = {base_host, "www." + base_host}
    progress("正在读取企业官网")
    # Check robots on every origin before fetching its pages, including redirects.
    robots_cache = {}
    warnings = []

    def allowed(url):
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in robots_cache:
            status, content_type, content, _ = public_get(origin + "/robots.txt", hosts, limit=256_000, robots_request=True)
            robot = RobotFileParser()
            if status in {401, 403}:
                robot.disallow_all = True
            elif status in {404, 410}:
                robot.allow_all = True
            elif status == 200:
                robot.parse(content.decode("utf-8", errors="replace").splitlines())
            elif status == 301:
                text = content.decode("utf-8", errors="replace")
                rules = bool(re.search(r"(?im)^\s*user-agent\s*:", text))
                html = "text/html" in content_type and bool(re.search(r"(?i)<(?:html|body)(?:\s|>)", text))
                if not rules and not html:
                    raise MaterialError("官网返回了无效跳转，请检查网址。")
                # A few sites route a missing robots file to their HTML homepage
                # with 301 and no Location. Preserve any actual rules in the body.
                robot.parse(text.splitlines() if rules else [])
                warnings.append("官网的自动读取规则地址返回了不规范响应，已兼容处理；本次仅读取少量同站公开页面。")
            else:
                raise MaterialError("官网暂时无法确认自动读取规则，请稍后重试或提供企业介绍。")
            robots_cache[origin] = robot
        return robots_cache[origin].can_fetch(USER_AGENT, url)

    queue, seen, sources, failures = [first], set(), [], []
    for index in range(max(1, min(max_pages, 5))):
        if not queue:
            break
        url = queue.pop(0)
        if index:
            time.sleep(1)
        seen.add(url)
        try:
            if not allowed(url):
                raise MaterialError("该网站限制自动读取，请改为上传或粘贴企业介绍。")
            status, content_type, raw, final_url = public_get(url, hosts, can_fetch=allowed)
            if status != 200:
                raise MaterialError("官网拒绝访问或页面不存在，请检查网址或提供企业介绍。")
            if content_type and not any(item in content_type for item in ("text/html", "text/plain", "application/xhtml")):
                raise MaterialError("该网址不是可读取的网页，请提供官网首页或企业简介页面。")
            soup = BeautifulSoup(decode_html(raw), "html.parser")
            title = soup.title.get_text(" ", strip=True)[:300] if soup.title else "企业官网资料"
            links = []
            for link in soup.select("a[href]"):
                href = urldefrag(urljoin(final_url, link["href"]))[0]
                parsed = urlparse(href)
                if parsed.hostname in hosts and parsed.scheme in {"http", "https"} and not parsed.query and not re.search(r"\.(pdf|zip|docx?|xlsx?|jpe?g|png)$", parsed.path, re.I):
                    label = link.get_text(" ", strip=True).lower() + parsed.path.lower()
                    if any(hint in label for hint in PAGE_HINTS) and href not in seen and href not in queue:
                        links.append(href)
            queue.extend(links[:8])
            for unwanted in soup.select("script,style,noscript,template,nav"):
                unwanted.decompose()
            text = readable_html_text(soup)[:7500]
            if len(text.strip()) < 40:
                raise MaterialError("官网没有可直接读取的文字，可能依赖动态加载；请上传或粘贴企业介绍。")
            sources.append(material_source(text, title, url=final_url, kind="用户提供网站（官网身份待确认）", material="网页正文", source_id=len(sources) + 1))
            if include_links:
                sources[-1]["links"] = links[:8]
            seen.add(final_url)
            progress(f"已读取 {len(sources)} 个官网页面")
        except (MaterialError, ValueError) as exc:
            failures.append(str(exc) if isinstance(exc, MaterialError) else "部分网页文字编码无法识别。")
            if not sources and index == 0:
                raise MaterialError(failures[-1]) from exc
    if not sources:
        raise MaterialError("未能读取官网文字，请上传或粘贴企业介绍。")
    return sources, list(dict.fromkeys(warnings + failures))


def upload_text(data, filename):
    suffix = PurePath(filename).suffix.lower()
    if suffix not in FILE_TYPES or not data or len(data) > MAX_UPLOAD:
        raise MaterialError("请上传不超过5MB的 PDF、DOCX、PPTX、TXT 或 Markdown 企业介绍。")
    try:
        if suffix in {".txt", ".md"}:
            text = decode_html(data.removeprefix(b"\xef\xbb\xbf"))
            if "\x00" in text:
                raise ValueError("binary")
        else:
            if suffix == ".pdf" and not data.startswith(b"%PDF-"):
                raise ValueError("not a PDF")
            if suffix in {".docx", ".pptx"}:
                with zipfile.ZipFile(BytesIO(data)) as archive:
                    entries = archive.infolist()
                    if len(entries) > 1000 or sum(e.file_size for e in entries) > 30_000_000:
                        raise ValueError("archive size")
                    if suffix == ".docx" and "word/document.xml" not in archive.namelist():
                        raise ValueError("not DOCX")
                    if suffix == ".pptx":
                        slides = sorted((e.filename for e in entries if re.fullmatch(r"ppt/slides/slide\d+\.xml", e.filename)), key=lambda s: int(re.search(r"slide(\d+)", s)[1]))
                        text = "\n\n".join("\n".join(node.text or "" for node in ElementTree.fromstring(archive.read(slide)).iter() if node.tag.endswith("}t")) for slide in slides)
            if suffix != ".pptx":
                text = extract_attachment_text(data, suffix)
    except Exception as exc:
        raise MaterialError("文件无法解析，可能已加密、损坏或格式不符。请另存为可复制文字的 PDF、DOCX、PPTX，或直接粘贴简介。") from exc
    if len(text.strip()) < 20:
        raise MaterialError("文件中可读取的文字不足。扫描件或纯图片暂不能提取，请上传文字版或粘贴企业简介。")
    return text
