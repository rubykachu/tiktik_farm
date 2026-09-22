"""Standalone bridge into vendor/douyin-downloader's API client.

Run as a subprocess, never imported: the vendor package imports its own
top-level `core.*` / `utils.*` modules, which only resolve when its
directory is on sys.path, and it drives everything through asyncio. Same
reason api/services/douyin_channel.py shells out to the vendor's run.py.

Protocol: argv[1] is the vendor directory, one JSON request on stdin, one
JSON result on stdout.

  {"mode":"search","keywords":[...],"max_items":40,"cookies":{...}}
    -> {"results": {keyword: [creator, ...]}, "errors": {keyword: "..."}}
  {"mode":"metrics","sec_uid":"...","count":12,"cookies":{...}}
    -> {"follower_count":N,"signature":"...","posts":[{...}]}

A creator is {channel_id, channel_url, nickname, follower_count,
signature}; a post is {create_time, play_count, duration_seconds}. Any
failure exits non-zero with {"error": "..."}.

A keyword Douyin refuses is reported under `errors` carrying Douyin's own
`status_code` and `status_msg`, because those say what to do and an empty
result does not: 2483 is "请先登录，再继续搜索吧" (log in first), which means
the cookie carries no logged-in session, while an expired cookie answers
403 instead. Returning [] for either would show up as "0 candidates found"
and read like the keyword was simply unpopular.
"""
from __future__ import annotations

import asyncio
import json
import sys


def _creator(author: dict) -> dict | None:
    sec_uid = author.get("sec_uid")
    if not sec_uid:
        return None
    return {
        "channel_id": sec_uid,
        "channel_url": f"https://www.douyin.com/user/{sec_uid}",
        "nickname": author.get("nickname") or sec_uid,
        "follower_count": int(author.get("follower_count") or 0),
        "signature": author.get("signature") or "",
    }


def _post(aweme: dict) -> dict:
    return {
        "create_time": int(aweme.get("create_time") or 0),
        "play_count": int((aweme.get("statistics") or {}).get("play_count") or 0),
        "duration_seconds": int((aweme.get("video") or {}).get("duration") or 0) // 1000,
    }


def _refusal(page: dict) -> str | None:
    """Douyin's own words for why a page came back empty, or None when the
    platform did not complain (a genuinely empty page)."""
    status_code = int(page.get("status_code") or 0)
    if status_code == 0:
        return None
    status_msg = (page.get("raw") or {}).get("status_msg") or ""
    return f"Douyin status_code={status_code}" + (f": {status_msg}" if status_msg else "")


async def _search(client, keywords: list[str], max_items: int) -> dict:
    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    for keyword in keywords:
        found: dict[str, dict] = {}
        offset = 0
        while len(found) < max_items:
            page = await client.search_aweme(keyword, offset=offset, count=20)
            items = page.get("items") or []
            if not items:
                refusal = _refusal(page)
                if refusal and not found:
                    errors[keyword] = refusal
                break
            for item in items:
                creator = _creator(item.get("author") or {})
                if creator:
                    found.setdefault(creator["channel_id"], creator)
            if not page.get("has_more"):
                break
            next_offset = int(page.get("max_cursor") or 0)
            if next_offset == offset:
                break
            offset = next_offset
        results[keyword] = list(found.values())
    return {"results": results, "errors": errors}


async def _metrics(client, sec_uid: str, count: int) -> dict:
    info = await client.get_user_info(sec_uid) or {}
    page = await client.get_user_post(sec_uid, count=count)
    posts = [_post(a) for a in (page.get("items") or []) if isinstance(a, dict)]
    return {
        "follower_count": int(info.get("follower_count") or 0),
        "signature": info.get("signature") or "",
        "posts": posts[:count],
    }


async def _main() -> dict:
    request = json.loads(sys.stdin.read() or "{}")
    from core.api_client import DouyinAPIClient

    async with DouyinAPIClient(request.get("cookies") or {}) as client:
        if request.get("mode") == "metrics":
            return await _metrics(client, request["sec_uid"], int(request.get("count") or 12))
        return await _search(client, request.get("keywords") or [], int(request.get("max_items") or 40))


if __name__ == "__main__":
    sys.path.insert(0, sys.argv[1])
    try:
        sys.stdout.write(json.dumps(asyncio.run(_main())))
    except Exception as exc:  # noqa: BLE001 — the parent process only gets stdout
        sys.stdout.write(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
