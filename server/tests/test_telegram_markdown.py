"""Port of server/test_telegram_client.py: MarkdownV2 conversion is a pure
function - no DB, no fixtures, no network needed."""

from __future__ import annotations

from telegram_client import _to_telegram_markdown_v2


def test_bold_converts_to_markdownv2_single_asterisk():
    assert _to_telegram_markdown_v2("**bold**") == "*bold*"


def test_escapes_a_literal_exclamation_mark_outside_bold():
    assert _to_telegram_markdown_v2("Done!") == "Done\\!"


def test_escapes_a_literal_period():
    assert _to_telegram_markdown_v2("Due Aug 10.") == "Due Aug 10\\."


def test_escapes_a_literal_hyphen_without_touching_an_em_dash():
    assert _to_telegram_markdown_v2("well-defined") == "well\\-defined"
    assert _to_telegram_markdown_v2("a—b") == "a—b"


def test_escapes_parentheses():
    assert _to_telegram_markdown_v2("(note)") == "\\(note\\)"


def test_reserved_characters_inside_bold_span_are_still_escaped():
    assert _to_telegram_markdown_v2("**Q4 (final)**") == "*Q4 \\(final\\)*"


def test_multiple_bold_spans_are_all_handled():
    result = _to_telegram_markdown_v2("**one** and **two**")
    assert result == "*one* and *two*"


def test_plain_text_with_no_special_characters_passes_through():
    assert _to_telegram_markdown_v2("hello world") == "hello world"
