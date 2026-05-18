"""Unit tests for api/services/douyin_cookie_import.py"""
from __future__ import annotations

import json
import pytest


VALID_COOKIE_LIST = [
    {"name": "ttwid", "value": "abc123xyz", "domain": ".douyin.com", "path": "/"},
    {"name": "msToken", "value": "tok456", "domain": ".douyin.com", "path": "/"},
    {"name": "odin_tt", "value": "odin789", "domain": ".douyin.com", "path": "/"},
    {"name": "passport_csrf_token", "value": "csrf000", "domain": ".douyin.com", "path": "/"},
    {"name": "sid_guard", "value": "sid111", "domain": ".douyin.com", "path": "/"},
]


class TestParseCookieEditorJson:
    def test_valid_returns_dict_with_keys(self):
        from api.services.douyin_cookie_import import parse_cookie_editor_json
        raw = json.dumps(VALID_COOKIE_LIST).encode()
        result = parse_cookie_editor_json(raw)
        assert result["ttwid"] == "abc123xyz"
        assert result["msToken"] == "tok456"

    def test_missing_ttwid_raises(self):
        from api.services.douyin_cookie_import import (
            parse_cookie_editor_json,
            DouyinCookieImportError,
        )
        no_ttwid = [c for c in VALID_COOKIE_LIST if c["name"] != "ttwid"]
        raw = json.dumps(no_ttwid).encode()
        with pytest.raises(DouyinCookieImportError, match="ttwid"):
            parse_cookie_editor_json(raw)

    def test_empty_ttwid_value_raises(self):
        from api.services.douyin_cookie_import import (
            parse_cookie_editor_json,
            DouyinCookieImportError,
        )
        cookies = [dict(c) for c in VALID_COOKIE_LIST]
        for c in cookies:
            if c["name"] == "ttwid":
                c["value"] = ""
        raw = json.dumps(cookies).encode()
        with pytest.raises(DouyinCookieImportError, match="ttwid"):
            parse_cookie_editor_json(raw)

    def test_malformed_json_raises(self):
        from api.services.douyin_cookie_import import (
            parse_cookie_editor_json,
            DouyinCookieImportError,
        )
        with pytest.raises(DouyinCookieImportError, match="malformed"):
            parse_cookie_editor_json(b"not json{{{")

    def test_not_a_list_raises(self):
        from api.services.douyin_cookie_import import (
            parse_cookie_editor_json,
            DouyinCookieImportError,
        )
        raw = json.dumps({"name": "ttwid", "value": "x"}).encode()
        with pytest.raises(DouyinCookieImportError, match="list"):
            parse_cookie_editor_json(raw)

    def test_file_too_large_raises(self):
        from api.services.douyin_cookie_import import (
            parse_cookie_editor_json,
            DouyinCookieImportError,
        )
        with pytest.raises(DouyinCookieImportError, match="size"):
            parse_cookie_editor_json(b"x" * (256 * 1024 + 1))

    def test_optional_keys_default_to_empty_string(self):
        from api.services.douyin_cookie_import import parse_cookie_editor_json
        minimal = [{"name": "ttwid", "value": "abc123"}]
        raw = json.dumps(minimal).encode()
        result = parse_cookie_editor_json(raw)
        assert result["msToken"] == ""
        assert result["odin_tt"] == ""


class TestToDownloaderYamlDict:
    def test_returns_expected_keys(self):
        from api.services.douyin_cookie_import import to_downloader_yaml_dict
        cookie_dict = {
            "ttwid": "abc",
            "msToken": "tok",
            "odin_tt": "odin",
            "passport_csrf_token": "csrf",
            "sid_guard": "sid",
        }
        result = to_downloader_yaml_dict(cookie_dict)
        assert result["ttwid"] == "abc"
        assert result["msToken"] == "tok"
        assert set(result.keys()) == {
            "ttwid", "msToken", "odin_tt", "passport_csrf_token", "sid_guard"
        }
