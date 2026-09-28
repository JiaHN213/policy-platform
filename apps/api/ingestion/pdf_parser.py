"""Isolated PDF extraction process; a caller enforces a wall-clock timeout."""

import io
import json
import sys

from pypdf import PdfReader

from ingestion.text_layout import normalize_extracted_text


def extract_pages(data):
    if not data.startswith(b"%PDF-"):
        raise ValueError("INVALID_PDF")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise ValueError("ENCRYPTED_PDF")
    if not 1 <= len(reader.pages) <= 100:
        raise ValueError("PDF_PAGE_LIMIT")
    result = []
    unreadable = []
    for number, page in enumerate(reader.pages, 1):
        contents = page.get_contents()
        stream = contents.get_data() if contents is not None else b""
        if len(stream) > 10_000_000:
            raise ValueError("PDF_STREAM_LIMIT")
        text = normalize_extracted_text(page.extract_text() or "", join_wrapped=True)
        # Cover pages and intentionally blank pages are common. Keep useful text
        # from the remaining pages instead of failing the whole official PDF.
        if len(text) >= 10:
            result.append({"page": number, "text": text})
        elif stream.strip() or text.strip():
            # Empty extraction is not proof of a blank page: images and vectorized
            # characters have drawing streams. Report missing pages conservatively.
            unreadable.append(number)
        if sum(len(p["text"]) for p in result) > 1_000_000:
            raise ValueError("PDF_TEXT_LIMIT")
    if sum(len(page["text"]) for page in result) < 30:
        raise ValueError("OCR_OR_PAGE_REVIEW_REQUIRED")
    if unreadable:
        pages = "、".join(str(number) for number in unreadable[:30])
        raise ValueError(f"PDF 第 {pages} 页未提取到足够文字，可能是扫描页、轮廓文字或图表。请补充 OCR 或核对原件；该附件未标记为完整解析。")
    return result


if __name__ == "__main__":
    try:
        payload = sys.stdin.buffer.read(30_000_001)
        if len(payload) > 30_000_000:
            raise ValueError("PDF_SIZE_LIMIT")
        output = {"pages": extract_pages(payload)}
    except Exception as exc:
        output = {"error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}
    sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False).encode("utf-8"))
