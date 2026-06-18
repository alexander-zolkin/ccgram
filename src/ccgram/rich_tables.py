# CCGRAM-HOTFIX:rich-tables — Alexander wants markdown TABLES in Kara's replies to
# render as real Telegram tables (Bot API 10.1 sendRichMessage / editMessageText
# rich_message). Telegram entities have no table type, so ccgram's normal
# convert_to_entities() leaves tables as ASCII. Strategy: after a reply is sent
# normally (plain text + entities), if it contains a GFM table, best-effort
# UPGRADE that same message via editMessageText(rich_message={html}). On any
# failure the already-sent plain message stands — zero regression.
#
# Source of truth lives in ~/.ccgram/hotfixes/rich_tables.py and is copied into
# the package by reapply.sh after every `ccgram upgrade`.
"""Rich-table upgrade helper (markdown table -> Telegram rich HTML)."""

from __future__ import annotations

import html as _html
import re
import warnings
from typing import Any

import structlog

logger = structlog.get_logger()

# A GFM table needs a header row of pipes followed by a separator row like
# |---|:--:|---| . Detect that pair anywhere in the text.
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_ROW = re.compile(r"^\s*\|.*\|\s*$")

# Constructs that Telegram message *entities* cannot express, so a reply
# containing any of them is worth upgrading to a rich message. Plain prose,
# **bold**, *italic*, `code` and bare lists are left on the entity path (no
# re-render risk). Detectors below are intentionally line-anchored / explicit.
_RE_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_RE_DIVIDER = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_RE_TASK = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+")
_RE_MATH_FENCE = re.compile(r"^\s*```math\s*$")
_RE_MATH_BLOCK = re.compile(r"^\s*\$\$.+\$\$\s*$")
_RE_MATH_INLINE = re.compile(r"\$\$.+?\$\$")
_RE_SPOILER = re.compile(r"\|\|[^|]+\|\|")
_RE_MEDIA = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)")
_RE_DETAILS = re.compile(r"<details\b", re.I)


def has_rich_table(text: str) -> bool:
    """True if text contains at least one GFM table (header + separator)."""
    lines = text.splitlines()
    for i in range(len(lines) - 1):
        if _ROW.match(lines[i]) and _TABLE_SEP.match(lines[i + 1]):
            return True
    return False


def has_rich_content(text: str) -> bool:
    """True if text has any construct worth a rich upgrade (entities can't do it)."""
    if has_rich_table(text):
        return True
    if (_RE_SPOILER.search(text) or _RE_MEDIA.search(text)
            or _RE_DETAILS.search(text) or _RE_MATH_INLINE.search(text)):
        return True
    for ln in text.splitlines():
        if (_RE_HEADING.match(ln) or _RE_DIVIDER.match(ln) or _RE_TASK.match(ln)
                or _RE_MATH_FENCE.match(ln) or _RE_MATH_BLOCK.match(ln)):
            return True
    return False


# ---- inline markdown -> HTML --------------------------------------------------

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_STRIKE = re.compile(r"~~([^~]+)~~")
_ITALIC = re.compile(r"(?<![\*\w])\*([^*\n]+)\*(?![\*\w])|(?<![_\w])_([^_\n]+)_(?![_\w])")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_SPOILER = re.compile(r"\|\|([^|]+)\|\|")
_MARK = re.compile(r"==([^=]+)==")


def _inline(s: str) -> str:
    """Convert inline markdown in a single text run to safe Telegram HTML.

    Code spans are extracted first (so their contents aren't re-parsed), then the
    remaining text is HTML-escaped and the other inline forms applied.
    """
    placeholders: list[str] = []

    def _stash(htmlfrag: str) -> str:
        placeholders.append(htmlfrag)
        return f"\x00{len(placeholders) - 1}\x00"

    # Stash inline formulas and code first so their raw contents aren't re-parsed.
    s = _RE_MATH_INLINE.sub(
        lambda m: _stash("<tg-math>" + _html.escape(m.group(0)[2:-2].strip()) + "</tg-math>"), s)
    s = _INLINE_CODE.sub(lambda m: _stash("<code>" + _html.escape(m.group(1)) + "</code>"), s)
    s = _html.escape(s)
    s = _LINK.sub(lambda m: f'<a href="{_html.escape(m.group(2))}">{m.group(1)}</a>', s)
    s = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
    s = _STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", s)
    s = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)
    s = _SPOILER.sub(lambda m: f"<tg-spoiler>{m.group(1)}</tg-spoiler>", s)
    s = _MARK.sub(lambda m: f"<mark>{m.group(1)}</mark>", s)

    def _restore(m: re.Match[str]) -> str:
        return placeholders[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", _restore, s)


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _table_html(rows: list[str]) -> str:
    header = _split_row(rows[0])
    body = [_split_row(r) for r in rows[2:]]  # rows[1] is the separator
    out = ["<table>"]
    out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr>")
    for r in body:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
    out.append("</table>")
    return "".join(out)


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_TASK = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*)$")
_MEDIA_LINE = re.compile(r'^\s*!\[[^\]]*\]\((https?://[^)\s]+)(?:\s+"([^"]*)")?\)\s*$')
_FENCE = re.compile(r"^\s*```(\w+)?\s*$")
_MATH_BLOCK_LINE = re.compile(r"^\s*\$\$(.+?)\$\$\s*$")

_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
_VIDEO_EXT = (".mp4", ".mov", ".webm", ".gif", ".mkv")
_AUDIO_EXT = (".mp3", ".ogg", ".oga", ".wav", ".m4a", ".opus")


def _media_html(url: str, caption: str | None) -> str:
    """Pick img/video/audio by URL extension; wrap in <figure> when captioned."""
    low = url.lower().split("?", 1)[0]
    safe = _html.escape(url)
    if low.endswith(_AUDIO_EXT):
        tag = f'<audio src="{safe}"></audio>'
    elif low.endswith(_VIDEO_EXT):
        tag = f'<video src="{safe}"></video>'
    else:
        tag = f'<img src="{safe}"/>'
    if caption:
        return f"<figure>{tag}<figcaption>{_inline(caption)}</figcaption></figure>"
    return tag


def md_to_html(text: str) -> str:
    """Convert a markdown reply to Telegram rich-message HTML.

    Handles headings, tables, ordered/unordered/task lists, code & ```math```
    blocks, $$…$$ formulas, blockquotes, dividers, inline media (img/video/audio
    by URL), <details> passthrough, and inline emphasis / spoilers / marked text.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    para: list[str] = []
    list_buf: list[str] = []
    list_tag: str | None = None

    def flush_para() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_buf and list_tag:
            # list_buf already holds full <li>…</li> strings; "task" renders as <ul>.
            wrap = "ul" if list_tag == "task" else list_tag
            out.append(f"<{wrap}>" + "".join(list_buf) + f"</{wrap}>")
        list_buf.clear()
        list_tag = None

    while i < n:
        line = lines[i]

        # fenced block — ```math``` becomes a formula, otherwise a code block
        mf = _FENCE.match(line)
        if mf:
            flush_para(); flush_list()
            lang = (mf.group(1) or "").lower()
            i += 1
            body: list[str] = []
            while i < n and not _FENCE.match(lines[i]):
                body.append(lines[i]); i += 1
            i += 1  # skip closing fence
            content = "\n".join(body)
            if lang == "math":
                out.append("<tg-math>" + _html.escape(content) + "</tg-math>")
            elif lang:
                out.append(f'<pre><code class="language-{lang}">'
                           + _html.escape(content) + "</code></pre>")
            else:
                out.append("<pre>" + _html.escape(content) + "</pre>")
            continue

        # collapsible <details>…</details> — pass through verbatim (already HTML)
        if _RE_DETAILS.match(line.lstrip()):
            flush_para(); flush_list()
            block = [line]
            while i < n and "</details>" not in lines[i]:
                i += 1
                if i < n:
                    block.append(lines[i])
            i += 1
            out.append("\n".join(block))
            continue

        # standalone block formula  $$ … $$
        mm = _MATH_BLOCK_LINE.match(line)
        if mm:
            flush_para(); flush_list()
            out.append("<tg-math>" + _html.escape(mm.group(1).strip()) + "</tg-math>")
            i += 1
            continue

        # horizontal divider
        if _RE_DIVIDER.match(line):
            flush_para(); flush_list()
            out.append("<hr/>")
            i += 1
            continue

        # standalone media by URL:  ![alt](url "caption")
        md = _MEDIA_LINE.match(line)
        if md:
            flush_para(); flush_list()
            out.append(_media_html(md.group(1), md.group(2)))
            i += 1
            continue

        # task-list item:  - [ ] / - [x]
        mt = _TASK.match(line)
        if mt:
            flush_para()
            if list_tag != "task":
                flush_list()
            list_tag = "task"
            checked = " checked" if mt.group(1).lower() == "x" else ""
            list_buf.append(f'<li><input type="checkbox"{checked}>{_inline(mt.group(2))}</li>')
            i += 1
            continue

        # table
        if _ROW.match(line) and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            flush_para(); flush_list()
            tbl = [line, lines[i + 1]]
            i += 2
            while i < n and _ROW.match(lines[i]):
                tbl.append(lines[i]); i += 1
            out.append(_table_html(tbl))
            continue

        m = _HEADING.match(line)
        if m:
            flush_para(); flush_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        m = _BULLET.match(line)
        if m:
            flush_para()
            if list_tag not in (None, "ul"):
                flush_list()
            list_tag = "ul"
            list_buf.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue

        m = _ORDERED.match(line)
        if m:
            flush_para()
            if list_tag not in (None, "ol"):
                flush_list()
            list_tag = "ol"
            list_buf.append(f"<li>{_inline(m.group(1))}</li>")
            i += 1
            continue

        m = _QUOTE.match(line)
        if m:
            flush_para(); flush_list()
            out.append("<blockquote>" + _inline(m.group(1)) + "</blockquote>")
            i += 1
            continue

        if line.strip() == "":
            flush_para(); flush_list()
        else:
            para.append(line.strip())
        i += 1

    flush_para(); flush_list()
    return "".join(out)


async def maybe_rich_upgrade(message: Any, raw_text: str) -> None:
    """If raw_text has rich constructs, upgrade the sent `message` to a rich one.

    Best-effort: any failure is swallowed (the plain message already stands).
    `message` is a PTB Message returned by the normal send.
    """
    try:
        if message is None or not has_rich_content(raw_text):
            return
        bot = message.get_bot()
        # do_api_request is intentional: PTB has no typed param for rich_message
        # (Bot API 10.1). Silence its "use editMessageText instead" hint.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".*do_api_request.*")
            await bot.do_api_request(
                "editMessageText",
                api_kwargs={
                    "chat_id": message.chat_id,
                    "message_id": message.message_id,
                    "rich_message": {"html": md_to_html(raw_text)},
                },
            )
    except Exception as exc:  # noqa: BLE001 — never let a cosmetic upgrade break a reply
        logger.debug("rich-table upgrade skipped: %s", exc)
