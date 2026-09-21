"""A tiny, safe Markdown-to-HTML renderer.

Every character of the input is HTML-escaped before any markup is added, so
raw HTML in the source is shown as text and never injected. Supported:
headings, paragraphs, unordered/ordered lists, pipe tables, **bold**,
`code`, and bare http(s) links. Nothing else.
"""
from __future__ import annotations

import html
import re

_URL = re.compile(r"https?://[^\s<>\"'`]+")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_TRAILING = ".,;:!?)"


def _link(match: re.Match) -> str:
    url = match.group(0)
    tail = ""
    while url and url[-1] in _TRAILING:
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        tail = url[-1] + tail
        url = url[:-1]
    # `url` is already HTML-escaped text; the scheme is fixed by the regex.
    return f'<a href="{url}" rel="noopener noreferrer" target="_blank">{url}</a>{tail}'


def _inline(text: str) -> str:
    out = html.escape(text, quote=True)
    # Code spans first, so their content is not touched by the other rules.
    chunks = _CODE.split(out)
    for i, chunk in enumerate(chunks):
        if i % 2:
            chunks[i] = f"<code>{chunk}</code>"
        else:
            chunk = _BOLD.sub(r"<strong>\1</strong>", chunk)
            chunks[i] = _URL.sub(_link, chunk)
    return "".join(chunks)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render(source: str) -> str:
    lines = source.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    i = 0

    def flush() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush()
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush()
            level = len(m.group(1))
            out.append(f'<h{level} id="q-{_slug(m.group(2))}">{_inline(m.group(2))}</h{level}>')
            i += 1
            continue

        if stripped.startswith("|"):
            flush()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            has_head = len(block) > 1 and re.fullmatch(r"[\s|:\-]+", block[1]) is not None
            out.append('<div class="table-wrap"><table>')
            body = block
            if has_head:
                out.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in _cells(block[0])) + "</tr></thead>")
                body = block[2:]
            out.append("<tbody>")
            for row in body:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(row)) + "</tr>")
            out.append("</tbody></table></div>")
            continue

        m = re.match(r"^([-*]|\d+\.)\s+", stripped)
        if m:
            flush()
            tag = "ol" if m.group(1)[0].isdigit() else "ul"
            pattern = r"^\d+\.\s+" if tag == "ol" else r"^[-*]\s+"
            items: list[str] = []
            while i < len(lines):
                s = lines[i].strip()
                if re.match(pattern, s):
                    items.append(re.sub(pattern, "", s, count=1))
                elif s and items and lines[i].startswith((" ", "\t")):
                    items[-1] += " " + s          # continuation line
                else:
                    break
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + f"</{tag}>")
            continue

        para.append(stripped)
        i += 1

    flush()
    return "\n".join(out)
