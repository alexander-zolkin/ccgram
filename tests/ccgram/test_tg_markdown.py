from telegram import MessageEntity as E

from ccgram.tg_markdown import entities_to_markdown as md


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def test_no_entities_returns_text():
    assert md("plain *text*", None) == "plain *text*"
    assert md("plain", ()) == "plain"


def test_inline_styles():
    text = "bold italic strike spoiler under"
    ents = [
        E("bold", 0, 4),
        E("italic", 5, 6),
        E("strikethrough", 12, 6),
        E("spoiler", 19, 7),
        E("underline", 27, 5),
    ]
    assert md(text, ents) == "**bold** *italic* ~~strike~~ ||spoiler|| under"


def test_edge_whitespace_stays_outside_markers():
    assert md("a bold b", [E("bold", 1, 6)]) == "a **bold** b"


def test_nested_bold_italic():
    text = "very important"
    ents = [E("bold", 0, 14), E("italic", 5, 9)]
    assert md(text, ents) == "**very *important***"


def test_code_and_link():
    text = "run ls here"
    ents = [E("code", 4, 2), E("text_link", 7, 4, url="https://x.io")]
    assert md(text, ents) == "run `ls` [here](https://x.io)"


def test_code_with_backtick_uses_double_fence():
    assert md("a`b", [E("code", 0, 3)]) == "``a`b``"


def test_pre_block_gets_own_lines():
    text = "see\nprint(1)\nok"
    ents = [E("pre", 4, 8, language="python")]
    assert md(text, ents) == "see\n```python\nprint(1)\n```\nok"


def test_pre_inline_in_paragraph_breaks_lines():
    assert md("x y", [E("pre", 2, 1)]) == "x \n```\ny\n```"


def test_blockquote_prefixes_each_line():
    text = "q1\nq2"
    assert md(text, [E("blockquote", 0, 5)]) == "> q1\n> q2"


def test_utf16_offsets_after_emoji():
    text = "🔥 hot"
    ents = [E("bold", _u16("🔥 "), 3)]
    assert md(text, ents) == "🔥 **hot**"


def test_plain_urls_and_mentions_untouched():
    text = "@kara https://a.b"
    ents = [E("mention", 0, 5), E("url", 6, 11)]
    assert md(text, ents) == text


def test_partial_overlap_is_ignored_not_corrupted():
    text = "abcdef"
    ents = [E("bold", 0, 4), E("italic", 2, 4)]
    assert md(text, ents) == "**abcd**ef"


def test_with_effort_swaps_or_appends():
    from ccgram.providers.claude import with_effort

    assert with_effort("--effort xhigh --model x", "low") == "--effort low --model x"
    assert with_effort("", "max") == "--effort max"
    assert with_effort("--effort xhigh", "bogus") == "--effort xhigh"
    assert with_effort("--effort xhigh", None) == "--effort xhigh"
