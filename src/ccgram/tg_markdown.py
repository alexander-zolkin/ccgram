"""CCGRAM-HOTFIX:tg-entities-markdown — Telegram entities → plain Markdown.

Telegram sends formatting as entities (UTF-16 offsets) next to bare text, so
``message.text`` alone loses bold, code, links… on the way to the agent. This
rebuilds the CommonMark the agent understands. PTB's ``text_markdown_v2`` is
no use here: it backslash-escapes every ``.`` and ``(``.

Underline has no Markdown form and is dropped; spoilers become ``||…||``.
Partially overlapping entities (not nested) are ignored.
"""

from collections.abc import Sequence
from typing import Any

_INLINE = {"bold": "**", "italic": "*", "strikethrough": "~~", "spoiler": "||"}
_BLOCK = frozenset({"pre", "blockquote", "expandable_blockquote"})


def entities_to_markdown(text: str, entities: Sequence[Any] | None) -> str:
    """Return ``text`` with its Telegram ``entities`` rendered as Markdown."""
    if not text or not entities:
        return text
    raw = text.encode("utf-16-le")
    ents = sorted(
        (e for e in entities if e.length > 0),
        key=lambda e: (e.offset, -e.length),
    )
    return _render(raw, 0, len(raw) // 2, ents)


def _slice(raw: bytes, start: int, end: int) -> str:
    return raw[2 * start : 2 * end].decode("utf-16-le", errors="replace")


def _render(raw: bytes, start: int, end: int, ents: list[Any]) -> str:
    out: list[str] = []
    pos = start
    i = 0
    while i < len(ents):
        ent = ents[i]
        e_end = ent.offset + ent.length
        if ent.offset < pos or e_end > end:  # overlaps a sibling: skip
            i += 1
            continue
        j = i + 1
        while j < len(ents) and ents[j].offset < e_end:
            j += 1
        children = [c for c in ents[i + 1 : j] if c.offset + c.length <= e_end]
        out.append(_slice(raw, pos, ent.offset))
        before = "".join(out)
        after = _slice(raw, e_end, e_end + 1)
        out.append(_wrap(raw, ent, children, before, after))
        pos = e_end
        i = j
    out.append(_slice(raw, pos, end))
    return "".join(out)


def _wrap(raw: bytes, ent: Any, children: list[Any], before: str, after: str) -> str:
    kind = str(ent.type)
    e_end = ent.offset + ent.length
    if kind in ("code", "pre"):
        inner = _slice(raw, ent.offset, e_end)
    else:
        inner = _render(raw, ent.offset, e_end, children)

    if kind in _BLOCK:
        if kind == "pre":
            body = f"```{getattr(ent, 'language', None) or ''}\n{inner.strip(chr(10))}\n```"
        else:
            body = "\n".join(f"> {line}" for line in inner.split("\n"))
        lead = "\n" if before and not before.endswith("\n") else ""
        trail = "\n" if after and after != "\n" else ""
        return f"{lead}{body}{trail}"

    # Markdown emphasis must hug the text: keep edge whitespace outside.
    core = inner.strip()
    if not core:
        return inner
    left = inner[: len(inner) - len(inner.lstrip())]
    right = inner[len(inner.rstrip()) :]
    if kind == "code":
        fence = "``" if "`" in core else "`"
        core = f"{fence}{core}{fence}"
    elif kind == "text_link" and getattr(ent, "url", None):
        core = f"[{core}]({ent.url})"
    elif kind in _INLINE:
        mark = _INLINE[kind]
        core = f"{mark}{core}{mark}"
    return f"{left}{core}{right}"


__all__ = ["entities_to_markdown"]
