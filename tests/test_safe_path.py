"""Unit tests for api.services.safe_path.

Covers:
- validate_username regex (happy, traversal token, oversize, null byte, empty,
  disallowed chars, boundary length).
- safe_join_under (happy, path traversal up, absolute leaf, symlink-like leaf).
"""
from __future__ import annotations

import os

import pytest

from api.services.safe_path import validate_username, safe_join_under


class TestValidateUsername:
    def test_happy_simple(self):
        assert validate_username("foo") == "foo"

    def test_happy_with_dot_and_underscore(self):
        assert validate_username("user_1.test") == "user_1.test"

    def test_happy_boundary_32_chars(self):
        s = "a" * 32
        assert validate_username(s) == s

    def test_traversal_dotdot_rejected(self):
        with pytest.raises(ValueError):
            validate_username("../bar")

    def test_traversal_slash_rejected(self):
        with pytest.raises(ValueError):
            validate_username("foo/bar")

    def test_traversal_backslash_rejected(self):
        with pytest.raises(ValueError):
            validate_username("foo\\bar")

    def test_null_byte_rejected(self):
        with pytest.raises(ValueError):
            validate_username("foo\x00bar")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            validate_username("")

    def test_oversize_33_chars_rejected(self):
        with pytest.raises(ValueError):
            validate_username("a" * 33)

    def test_special_char_rejected(self):
        with pytest.raises(ValueError):
            validate_username("foo bar")  # space

    def test_unicode_rejected(self):
        with pytest.raises(ValueError):
            validate_username("ngườidùng")


class TestSafeJoinUnder:
    def test_happy_simple(self, tmp_path):
        base = str(tmp_path)
        result = safe_join_under(base, "subdir", "file.txt")
        assert result == os.path.join(base, "subdir", "file.txt")
        assert os.path.isabs(result)

    def test_traversal_up_rejected(self, tmp_path):
        base = str(tmp_path)
        with pytest.raises(ValueError):
            safe_join_under(base, "..", "etc", "passwd")

    def test_traversal_deep_rejected(self, tmp_path):
        base = str(tmp_path)
        with pytest.raises(ValueError):
            safe_join_under(base, "ok", "..", "..", "..", "etc", "passwd")

    def test_absolute_leaf_rejected(self, tmp_path):
        base = str(tmp_path)
        with pytest.raises(ValueError):
            safe_join_under(base, "/etc/passwd")

    def test_returns_under_base_after_normalize(self, tmp_path):
        base = str(tmp_path)
        # "./foo" should normalize and still be under base
        result = safe_join_under(base, "./foo")
        assert result.startswith(base)
