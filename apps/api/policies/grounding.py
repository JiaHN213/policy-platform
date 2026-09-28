"""Formatting-tolerant evidence matching that preserves factual punctuation."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation


def compact(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value) if not c.isspace())


def source_quote(quote, material, minimum=4):
    if not quote or not quote.strip():
        return ""
    def numeric_boundary(text, needle, start):
        end = start + len(needle)
        if needle[0] in "+-0123456789" and start and text[start - 1] in "+-.0123456789":
            return False
        if needle[-1].isdigit() and end < len(text) and text[end] in ".0123456789":
            return False
        return True

    exact_start = material.find(quote)
    while exact_start >= 0:
        if numeric_boundary(material, quote, exact_start):
            return quote
        exact_start = material.find(quote, exact_start + 1)
    # Only layout whitespace is ignored. Decimal points, signs, ranges and percent
    # marks must survive. Return the actual original span, never reconstructed text.
    chars, positions = [], []
    for index, char in enumerate(material):
        if not char.isspace():
            chars.append(char)
            positions.append(index)
    needle = "".join(c for c in quote if not c.isspace())
    haystack = "".join(chars)
    start = haystack.find(needle)
    if len(needle) < minimum or start < 0 or haystack.find(needle, start + 1) >= 0:
        return ""
    if not numeric_boundary(haystack, needle, start):
        return ""
    return material[positions[start]:positions[start + len(needle) - 1] + 1]


def quote_failure(quote, material):
    needle = "".join(c for c in quote if not c.isspace())
    haystack = "".join(c for c in material if not c.isspace())
    if needle and haystack.count(needle) > 1:
        return "排版归一后存在多个匹配位置，无法确定引用位置。"
    return "当前原文中没有这段连续引文；可能被改写、拼接或改变了数字、标点。"


def number_in_quote(value, quote, unit=""):
    """Check an explicit value/unit without silently converting magnitudes."""
    try:
        expected = Decimal(str(value))
        if not expected.is_finite():
            return False
    except (InvalidOperation, ValueError):
        return False
    normalized = compact(quote)
    for match in re.finditer(r"(?<![\d.])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?![\d.])", normalized):
        if Decimal(match.group().replace(",", "")) != expected:
            continue
        if unit and not normalized[match.end():].startswith(compact(unit)):
            continue
        return True
    return False


def date_in_quote(value, quote):
    from django.utils.dateparse import parse_datetime

    try:
        parsed = parse_datetime(value)
    except (TypeError, ValueError):
        return False
    if parsed is None:
        return False
    normalized = compact(quote)
    pattern = rf"(?<!\d){parsed.year}(?:年|[-/.])0?{parsed.month}(?:月|[-/.])0?{parsed.day}(?:日|号)?(?!\d)"
    if not re.search(pattern, normalized):
        return False
    if parsed.hour or parsed.minute or parsed.second:
        clock = rf"(?<!\d)0?{parsed.hour}(?:[:时点])0?{parsed.minute}(?:分)?(?!\d)"
        if not re.search(clock, normalized):
            return False
        if parsed.second:
            return False  # No inferred sub-minute deadline precision.
    return True


def unsupported_numbers(text, evidence):
    """A conservative guard for generated prose, in addition to citation checks."""
    normalized = compact(text)
    date_pattern = r"(?<!\d)(\d{4})(?:年|[-/.])(\d{1,2})(?:月|[-/.])(\d{1,2})(?:日|号)?(?!\d)"
    missing = []
    for match in re.finditer(date_pattern, normalized):
        year, month, day = map(int, match.groups())
        value = f"{year:04}-{month:02}-{day:02}T00:00:00"
        if not date_in_quote(value, evidence):
            missing.append(match.group())
    normalized = re.sub(date_pattern, "", normalized)
    tokens = re.finditer(r"(?<![\d.])([+-]?\d+(?:,\d{3})*(?:\.\d+)?)(亿元|万元|元|%|‰)?", normalized)
    for match in tokens:
        value, unit = match.groups()
        if not number_in_quote(value.replace(",", ""), evidence, unit or ""):
            missing.append(match.group())
    return missing
