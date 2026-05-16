"""Tests for api/services/caption.py — render_caption()."""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from api.services.caption import render_caption, find_unknown_vars, CAPTION_MAX


TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class TestFindUnknownVars:
    def test_all_allowed(self):
        assert find_unknown_vars("{title} {title_hashtag} {now}") == []

    def test_unknown_single(self):
        assert find_unknown_vars("{baz}") == ["baz"]

    def test_unknown_multiple_sorted(self):
        assert find_unknown_vars("{zz} {aa}") == ["aa", "zz"]

    def test_no_vars(self):
        assert find_unknown_vars("hello world") == []


class TestRenderCaptionHappy:
    def test_title_substitution(self):
        result = render_caption("{title} #foo", title="Hello")
        assert result == "Hello #foo"

    def test_title_hashtag_extraction(self):
        result = render_caption("{title_hashtag}", title="Check #python #code")
        assert result == "#python #code"

    def test_now_format(self):
        dt = datetime(2025, 1, 15, 10, 30, tzinfo=TZ)
        result = render_caption("{now}", now=dt)
        assert result == "2025-01-15 10:30"

    def test_all_vars(self):
        dt = datetime(2025, 6, 1, 8, 0, tzinfo=TZ)
        result = render_caption(
            "{title} {title_hashtag} uploaded at {now}",
            title="My Video #cool",
            now=dt,
        )
        assert result == "My Video #cool uploaded at 2025-06-01 08:00"


class TestRenderCaptionHashtagDedupe:
    def test_case_insensitive_dedupe(self):
        result = render_caption("#foo {title_hashtag}", title="stuff #Foo #bar")
        assert result == "#foo #bar"

    def test_first_occurrence_kept(self):
        result = render_caption("{title_hashtag} #FOO", title="#foo #bar")
        assert result == "#foo #bar"

    def test_multiple_duplicates(self):
        result = render_caption("#a #A #b #B #a", title="")
        assert result == "#a #b"

    def test_no_hashtags(self):
        result = render_caption("{title}", title="plain text")
        assert result == "plain text"


class TestRenderCaptionEdgeCases:
    def test_unknown_var_raises(self):
        with pytest.raises(ValueError, match="unknown caption variable"):
            render_caption("{baz}")

    def test_cap_2200(self):
        long_title = "x" * 3000
        result = render_caption("{title}", title=long_title)
        assert len(result) == CAPTION_MAX

    def test_title_with_braces_no_reexpand(self):
        result = render_caption("{title}", title="{title}")
        assert result == "{title}"

    def test_empty_title(self):
        result = render_caption("{title} post", title="")
        assert result == "post"

    def test_whitespace_collapse(self):
        result = render_caption("{title}  #foo", title="hi  ")
        assert result == "hi #foo"

    def test_naive_now_gets_tz(self):
        dt = datetime(2025, 3, 1, 12, 0)
        result = render_caption("{now}", now=dt)
        assert result == "2025-03-01 12:00"

    def test_double_braces_with_var_name_rejected(self):
        """{{curly}} contains {curly} which is unknown → ValueError."""
        with pytest.raises(ValueError, match="unknown caption variable"):
            render_caption("use {{curly}} {title}", title="hey")

    def test_non_var_braces_passthrough(self):
        """Braces not matching var pattern (e.g. {123}) pass through."""
        result = render_caption("{title} {123}", title="hi")
        assert result == "hi {123}"
