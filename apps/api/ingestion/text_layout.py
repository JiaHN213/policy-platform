"""Source-preserving text layout, usable by isolated parsers without Django."""

import re

INVISIBLE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
HORIZONTAL_SPACE = re.compile(r"[\t\u00a0\u2002-\u200a\u202f\u205f\u3000 ]+")
CHINESE_GAP = re.compile(r"(?<=[\u3400-\u9fff])[ \t]+(?=[\u3400-\u9fff])")
HEADING_OR_LIST = re.compile(
    r"^(?:第[一二三四五六七八九十百千万0-9]+[章节条款]|[一二三四五六七八九十]+[、.]|"
    r"[（(][一二三四五六七八九十0-9]+[）)]|\d+(?:\.\d+)*[、.．\s])"
)
TABLE_GAP = re.compile(r"\S(?: {2,}|\t+)\S")


def _can_join(previous, line, *, indented, table_line):
    """Only repair likely line wrapping, never infer paragraph/table structure."""
    return (
        len(previous) >= 18
        and not indented
        and not table_line
        and "|" not in previous
        and not HEADING_OR_LIST.match(previous)
        and not HEADING_OR_LIST.match(line)
        and not re.search(r"[。！？；：.!?;:][\"'”’）)]?$", previous)
        and not re.search(r"^(?:附件|表\s*\d|图\s*\d|\d{4}年\d{1,2}月)", line)
    )


def normalize_extracted_text(text, *, join_wrapped=False):
    """Normalize layout artifacts while preserving the source's actual characters."""
    text = INVISIBLE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = []
    previous_table = False
    for raw_line in text.split("\n"):
        table_line = "|" in raw_line or bool(TABLE_GAP.search(raw_line.strip()))
        indented = bool(re.match(r"^(?: {2,}|\t|\u3000)", raw_line))
        line = HORIZONTAL_SPACE.sub(" ", raw_line).strip()
        if not table_line:
            line = CHINESE_GAP.sub("", line)
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if (
            join_wrapped
            and lines
            and lines[-1]
            and not previous_table
            and _can_join(lines[-1], line, indented=indented, table_line=table_line)
        ):
            # A printed hyphen may be part of a name or identifier; keep it.
            separator = " " if re.search(r"[A-Za-z0-9]$", lines[-1]) and re.match(r"[A-Za-z0-9]", line) else ""
            lines[-1] += separator + line
        else:
            lines.append(line)
        previous_table = table_line
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)
