"""No-network unit test for the **bold**-to-MarkdownV2 conversion in
telegram_client.py. Pure regex logic, no bot token or Telegram API needed.

Run with:
  .venv/bin/python test_telegram_client.py
"""

import sys

from telegram_client import _to_telegram_markdown_v2

failures = []


def check(label, condition):
    print(f"[{'ok' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def main():
    check(
        "converts **bold** to MarkdownV2's single-asterisk bold",
        _to_telegram_markdown_v2("**Owen** is overdue") == "*Owen* is overdue",
    )
    check(
        "escapes a literal exclamation mark outside any bold span",
        _to_telegram_markdown_v2("let's wrap this up!") == "let's wrap this up\\!",
    )
    check(
        "escapes a literal period",
        _to_telegram_markdown_v2("1.2 days overdue.") == "1\\.2 days overdue\\.",
    )
    check(
        "escapes a literal hyphen without touching an em dash",
        _to_telegram_markdown_v2("Clean-up — due soon")
        == "Clean\\-up — due soon",
    )
    check(
        "escapes parentheses",
        _to_telegram_markdown_v2("Review (final pass)") == "Review \\(final pass\\)",
    )
    check(
        "does not escape reserved characters inside a bold span's delimiters",
        _to_telegram_markdown_v2("**Q4 (final)**") == "*Q4 \\(final\\)*",
    )
    check(
        "handles multiple bold spans in one message",
        _to_telegram_markdown_v2("**Owen**: Clean-up. **Daniel**: Deck.")
        == "*Owen*: Clean\\-up\\. *Daniel*: Deck\\.",
    )
    check(
        "plain text with no special characters passes through unchanged",
        _to_telegram_markdown_v2("Task assigned to Owen") == "Task assigned to Owen",
    )

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
