import json
import re
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Comment
from bs4.dammit import EncodingDetector

from .gov_library import SourceUnavailable, validate_url
from .text_layout import normalize_extracted_text

BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "div",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "p",
    "section",
}


def decode_html(data):
    """Decode government HTML without forcing modern UTF-8 on legacy pages."""
    if isinstance(data, str):
        return data
    declared = EncodingDetector.find_declared_encoding(data, is_html=True)
    candidates = [declared, "utf-8", "gb18030"]
    tried = set()
    for encoding in candidates:
        if not encoding:
            continue
        encoding = encoding.lower()
        if encoding in tried:
            continue
        tried.add(encoding)
        try:
            return data.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
    raise SourceUnavailable("HTML_ENCODING_UNVERIFIED")


def readable_html_text(node):
    """Render block structure without inserting newlines between inline spans."""
    for unwanted in node.select(
        "script,style,noscript,template,[style*='display:none'],"
        ".related,.xgwd,.xlfgx,.share,.pages_print"
    ):
        unwanted.decompose()

    def render(item):
        if isinstance(item, Comment):
            return ""
        name = getattr(item, "name", None)
        if name is None:
            # HTML source indentation is not a visual line break. Actual blocks
            # and <br> supply breaks below, including adjacent inline spans.
            return re.sub(r"[\t\r\n ]+", " ", str(item))
        if name == "br":
            return "\n"
        if name == "table":
            rows = []
            spans = {}
            for row in item.find_all("tr"):
                if row.find_parent("table") is not item:
                    continue
                cells = []
                column = 0
                occupied = set(spans)
                spans = {key: count - 1 for key, count in spans.items() if count > 1}
                for cell in row.find_all(["td", "th"], recursive=False):
                    while column in occupied:
                        cells.append("")
                        column += 1
                    value = " ".join("".join(render(child) for child in cell.children).split())
                    cells.append(value)
                    def span_size(attr):
                        try:
                            return max(1, min(100, int(cell.get(attr, 1))))
                        except (TypeError, ValueError):
                            return 1
                    width, height = span_size("colspan"), span_size("rowspan")
                    cells.extend([""] * (width - 1))
                    if height > 1:
                        for index in range(column, column + width):
                            spans[index] = height - 1
                    column += width
                while column <= max(occupied, default=-1):
                    cells.append("")
                    column += 1
                if cells:
                    rows.append(" | ".join(cells))
            return "\n\n" + "\n".join(rows) + "\n\n"
        content = "".join(render(child) for child in item.children)
        if name == "li" and item.parent.name in {"ol", "ul"}:
            # Browser-generated list markers are absent from text nodes.
            marker = "• "
            if item.parent.name == "ol" and item.parent.get("type", "1") == "1":
                siblings = item.parent.find_all("li", recursive=False)
                step = -1 if item.parent.has_attr("reversed") else 1
                try:
                    number = int(item.parent.get("start", len(siblings) if step < 0 else 1))
                    for sibling in siblings:
                        number = int(sibling.get("value", number))
                        if sibling is item:
                            break
                        number += step
                    marker = f"{number}. "
                except (TypeError, ValueError):
                    marker = "• "
            elif item.parent.name == "ol":
                # Non-decimal schemes cannot be reconstructed as decimal facts.
                marker = ""
            if not re.match(r"^(?:[•●\-]|\d+[.、．]|[一二三四五六七八九十]+、|[（(])", content.strip()):
                content = marker + content.strip()
        if name in BLOCK_TAGS:
            return "\n" + content + "\n"
        return content

    return normalize_extracted_text(render(node))


def extract_docx_text(data):
    """Read DOCX by paragraphs and table rows instead of one line per text run."""
    with zipfile.ZipFile(BytesIO(data)) as archive:
        entry = archive.getinfo("word/document.xml")
        if entry.file_size > 20_000_000:
            raise SourceUnavailable("DOCX_TOO_LARGE")
        root = ElementTree.fromstring(archive.read(entry))
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = root.find(f"{namespace}body")
    if body is None:
        raise SourceUnavailable("DOCX_BODY_MISSING")

    def paragraph_text(node):
        parts = []
        for child in node.iter():
            if child.tag == f"{namespace}t":
                parts.append(child.text or "")
            elif child.tag == f"{namespace}tab":
                parts.append("\t")
            elif child.tag in {f"{namespace}br", f"{namespace}cr"}:
                parts.append("\n")
        return "".join(parts)

    lines = []
    for child in body:
        if child.tag == f"{namespace}p":
            lines.append(paragraph_text(child))
        elif child.tag == f"{namespace}tbl":
            for row in child.findall(f"{namespace}tr"):
                cells = []
                for cell in row.findall(f"{namespace}tc"):
                    value = " ".join(
                        filter(None, (paragraph_text(p).strip() for p in cell.findall(f"{namespace}p")))
                    )
                    cells.append(value)
                lines.append(" | ".join(cells))
    return normalize_extracted_text("\n".join(lines))


def _safe_zip(data):
    archive = zipfile.ZipFile(BytesIO(data))
    entries = archive.infolist()
    if len(entries) > 5000 or sum(item.file_size for item in entries) > 100_000_000:
        archive.close()
        raise SourceUnavailable("ATTACHMENT_ARCHIVE_LIMIT")
    return archive


def extract_xlsx_text(data):
    """Extract cell values from XLSX with the standard library and bounded XML reads."""
    with _safe_zip(data) as archive:
        names = set(archive.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.iter():
                if item.tag.rsplit("}", 1)[-1] == "si":
                    shared.append(
                        "".join(
                            node.text or ""
                            for node in item.iter()
                            if node.tag.rsplit("}", 1)[-1] == "t"
                        )
                    )
        lines = []
        sheet_names = sorted(
            name
            for name in names
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        for sheet_name in sheet_names[:100]:
            root = ElementTree.fromstring(archive.read(sheet_name))
            for row in root.iter():
                if row.tag.rsplit("}", 1)[-1] != "row":
                    continue
                cells = []
                for cell in row:
                    if cell.tag.rsplit("}", 1)[-1] != "c":
                        continue
                    kind = cell.attrib.get("t", "")
                    values = [
                        node.text or ""
                        for node in cell.iter()
                        if node.tag.rsplit("}", 1)[-1] in {"v", "t"}
                    ]
                    value = "".join(values)
                    if kind == "s" and value.isdigit() and int(value) < len(shared):
                        value = shared[int(value)]
                    cells.append(value.strip())
                if any(cells):
                    lines.append(" | ".join(cells))
                if sum(len(line) for line in lines) > 1_000_000:
                    raise SourceUnavailable("ATTACHMENT_TEXT_LIMIT")
    return normalize_extracted_text("\n".join(lines))


def extract_xls_text(data):
    import xlrd

    try:
        workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
    except Exception as exc:
        raise SourceUnavailable("XLS_PARSE_FAILED") from exc
    lines = []
    try:
        for sheet in workbook.sheets():
            for row_index in range(sheet.nrows):
                values = [str(value).strip() for value in sheet.row_values(row_index)]
                if any(values):
                    lines.append(" | ".join(values))
                if sum(len(line) for line in lines) > 1_000_000:
                    raise SourceUnavailable("ATTACHMENT_TEXT_LIMIT")
    finally:
        workbook.release_resources()
    return normalize_extracted_text("\n".join(lines))


def extract_ofd_text(data):
    with _safe_zip(data) as archive:
        lines = []
        for name in archive.namelist():
            if not name.lower().endswith(".xml"):
                continue
            root = ElementTree.fromstring(archive.read(name))
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] == "TextCode" and node.text:
                    lines.append(node.text)
            if sum(len(line) for line in lines) > 1_000_000:
                raise SourceUnavailable("ATTACHMENT_TEXT_LIMIT")
    return normalize_extracted_text("\n".join(lines))


def extract_attachment_text(data, suffix=""):
    suffix = suffix.casefold().lstrip(".")
    if data.startswith(b"%PDF-"):
        return "\n".join(page["text"] for page in parse_pdf(data))
    if data.startswith(b"PK\x03\x04"):
        with _safe_zip(data) as archive:
            names = set(archive.namelist())
        if "word/document.xml" in names:
            return extract_docx_text(data)
        if "xl/workbook.xml" in names:
            return extract_xlsx_text(data)
        if "OFD.xml" in names or suffix == "ofd":
            return extract_ofd_text(data)
    if suffix == "xls" and data.startswith(b"\xd0\xcf\x11\xe0"):
        return extract_xls_text(data)
    head = data[:500].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html", b"<?xml")):
        return readable_html_text(BeautifulSoup(decode_html(data), "html.parser"))
    raise SourceUnavailable("ATTACHMENT_PARSER_REQUIRED")


def parse_policy_html(data, url):
    soup = BeautifulSoup(decode_html(data), "html.parser")
    body = soup.select_one("#UCAP-CONTENT")
    if body is None:
        raise SourceUnavailable("BODY_STRUCTURE_UNVERIFIED")
    for node in body.select("script,style,noscript"):
        node.decompose()
    attachments = {}
    for link in body.select("a[href]"):
        target = urldefrag(urljoin(url, link["href"]))[0]
        if urlparse(target).path.lower().endswith((".pdf", ".doc", ".docx", ".ofd", ".zip", ".xls", ".xlsx")):
            validate_url(target, resolve=False)
            attachments[target] = link.get_text(" ", strip=True)
    text = readable_html_text(body)
    if len(text) < 30:
        raise SourceUnavailable("BODY_TOO_SHORT")
    if len(attachments) > 30:
        raise SourceUnavailable("ATTACHMENT_LIMIT")
    return {"text": text, "attachments": attachments}


def parse_pdf(data):
    try:
        process = subprocess.run(
            [sys.executable, "-m", "ingestion.pdf_parser"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45,
            cwd=Path(__file__).resolve().parents[1],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if process.returncode:
            raise SourceUnavailable("PDF_PROCESS_FAILED")
        result = json.loads(process.stdout)
        if result.get("error"):
            raise SourceUnavailable(result["error"])
        return result["pages"]
    except subprocess.TimeoutExpired as exc:
        raise SourceUnavailable("PDF_PARSE_TIMEOUT") from exc
