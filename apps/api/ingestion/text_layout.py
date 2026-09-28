"""Source-preserving text layout, usable by isolated parsers without Django."""

import re

INVISIBLE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
HORIZONTAL_SPACE = re.compile(r"[\t\u00a0\u2002-\u200a\u202f\u205f\u3000 ]+")
CHINESE_GAP = re.compile(r"(?<=[\u3400-\u9fff])[ \t]+(?=[\u3400-\u9fff])")
HEADING_OR_LIST = re.compile(
    r"^(?:第[一二三四五六七八九十百千万0-9]+[章节条款]|[一二三四五六七八九十]+[、.]|"
    r"[（(][一二三四五六七八九十0-9]+[）)]|\d+(?:\.\d+)*[、.．\s])"
)


def normalize_extracted_text(text, *, join_wrapped=False):
    """Normalize layout artifacts while preserving the source's actual characters."""
    text = INVISIBLE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = []
    for raw_line in text.split("\n"):
        line = HORIZONTAL_SPACE.sub(" ", raw_line).strip()
        line = CHINESE_GAP.sub("", line)
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if (
            join_wrapped
            and lines
            and lines[-1]
            and len(lines[-1]) >= 18
            and not re.search(r"[。！？；：.!?;:]$", lines[-1])
            and not HEADING_OR_LIST.match(line)
            and not re.search(r"^(?:附件|表\s*\d|图\s*\d|\d{4}年\d{1,2}月)", line)
        ):
            if lines[-1].endswith("-") and re.match(r"^[A-Za-z]", line):
                lines[-1] = lines[-1][:-1] + line
            else:
                lines[-1] += line
        else:
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)
