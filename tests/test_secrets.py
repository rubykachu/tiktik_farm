"""Tests for api/services/secrets.py — Fernet symmetric encryption helpers.

Tests isolate the on-disk key file via TIKTOK_SECRET_KEY_FILE + _reset_for_tests()
so they never touch the real data/.secret_key.
"""
from __future__ import annotations

import os
import stat

import pytest


@pytest.fixture
def isolated_key(tmp_path, monkeypatch):
    """Point secrets.py at a throwaway key file and force a fresh Fernet."""
    key_file = tmp_path / "test_secret.key"
    monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(key_file))
    from api.services import secrets as _secrets
    _secrets._reset_for_tests()
    yield key_file
    _secrets._reset_for_tests()


class TestEncryptDecryptRoundTrip:
    def test_ascii_round_trips(self, isolated_key):
        from api.services.secrets import encrypt, decrypt
        token = encrypt("hello world")
        assert token != "hello world"
        assert decrypt(token) == "hello world"

    def test_empty_string_round_trips(self, isolated_key):
        from api.services.secrets import encrypt, decrypt
        token = encrypt("")
        assert decrypt(token) == ""

    def test_unicode_round_trips(self, isolated_key):
        from api.services.secrets import encrypt, decrypt
        plain = "AIzaSy-日本語-🔑-key"
        assert decrypt(encrypt(plain)) == plain

    def test_two_encryptions_of_same_plaintext_differ(self, isolated_key):
        # Fernet uses a random IV; ciphertexts must not be deterministic.
        from api.services.secrets import encrypt
        assert encrypt("same") != encrypt("same")

    def test_decrypt_bad_token_returns_empty_string(self, isolated_key):
        from api.services.secrets import decrypt
        assert decrypt("not-a-valid-fernet-token") == ""

    def test_decrypt_empty_token_returns_empty_string(self, isolated_key):
        from api.services.secrets import decrypt
        assert decrypt("") == ""


class TestKeyFileBootstrap:
    def test_key_file_created_when_missing(self, isolated_key):
        from api.services.secrets import encrypt
        assert not isolated_key.exists()
        encrypt("trigger")
        assert isolated_key.exists()
        assert len(isolated_key.read_bytes()) > 0

    def test_key_file_mode_600(self, isolated_key):
        from api.services.secrets import encrypt
        encrypt("trigger")
        mode = stat.S_IMODE(os.stat(isolated_key).st_mode)
        assert mode == 0o600

    def test_existing_key_file_is_reused(self, tmp_path, monkeypatch):
        key_file = tmp_path / "reuse.key"
        monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(key_file))
        from api.services import secrets as _secrets
        _secrets._reset_for_tests()
        token = _secrets.encrypt("payload")
        first_key_bytes = key_file.read_bytes()
        _secrets._reset_for_tests()
        assert _secrets.decrypt(token) == "payload"
        assert key_file.read_bytes() == first_key_bytes
        _secrets._reset_for_tests()

    def test_parent_directory_is_created(self, tmp_path, monkeypatch):
        nested = tmp_path / "nested" / "dir" / "secret.key"
        monkeypatch.setenv("TIKTOK_SECRET_KEY_FILE", str(nested))
        from api.services import secrets as _secrets
        _secrets._reset_for_tests()
        _secrets.encrypt("trigger")
        assert nested.exists()
        _secrets._reset_for_tests()


class TestMask:
    def test_long_value_masked_with_first_four_and_last_four(self):
        from api.services.secrets import mask
        out = mask("AIzaSyABCDEFGHIJKLMNOP")
        assert out.startswith("AIza")
        assert out.endswith("MNOP")
        assert "•" in out
        assert "ABCDEFGHIJ" not in out

    def test_short_value_fully_masked(self):
        from api.services.secrets import mask
        assert mask("abc") == "•" * 8
        assert mask("1234567") == "•" * 8

    def test_empty_value_fully_masked(self):
        from api.services.secrets import mask
        assert mask("") == "•" * 8

    def test_exactly_eight_chars_fully_masked(self):
        # Boundary: keep "len < 8 → mask" — 8-char strings would only expose
        # first 4 + last 4, which equals the original. Mask instead.
        from api.services.secrets import mask
        out = mask("12345678")
        assert "1234" not in out or "5678" not in out
