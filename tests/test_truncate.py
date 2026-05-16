"""Unit tests for api.utils.text.truncate.

Caps user-controlled text fields (summary_text, error_text, captions,
youtube titles) so a verbose stderr dump does not bloat the DB or break
TikTok's caption limit.
"""
from __future__ import annotations

from api.utils.text import truncate


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 100) == "hello"

    def test_empty_string_unchanged(self):
        assert truncate("", 100) == ""

    def test_exact_boundary_unchanged(self):
        s = "x" * 10
        assert truncate(s, 10) == s

    def test_one_over_boundary_truncated(self):
        s = "x" * 11
        out = truncate(s, 10)
        assert len(out) == 10
        assert out.endswith("…(truncated)") or len(out) == 10  # suffix-bounded

    def test_total_length_at_most_max_chars(self):
        s = "x" * 5000
        out = truncate(s, 1000)
        assert len(out) <= 1000

    def test_suffix_present_when_truncated(self):
        s = "x" * 500
        out = truncate(s, 100)
        assert out.endswith("…(truncated)")

    def test_custom_suffix(self):
        s = "x" * 500
        out = truncate(s, 100, suffix="…")
        assert out.endswith("…")
        assert len(out) == 100

    def test_cap_smaller_than_suffix_falls_back_to_slice(self):
        # If the caller passes a tiny cap, the function should still respect
        # the cap rather than overshoot with the suffix.
        out = truncate("xxxxxxxxxx", 3, suffix="…(truncated)")
        assert len(out) == 3

    def test_unicode_chars_counted_as_chars(self):
        s = "日本語" * 100  # 300 chars
        out = truncate(s, 50)
        assert len(out) == 50

    def test_zero_cap_returns_empty(self):
        out = truncate("hello", 0)
        assert out == ""
