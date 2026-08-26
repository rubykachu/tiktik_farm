"""Best-effort video transcript from yt-dlp's already-fetched caption metadata.

Shared by youtube_channel.download() (channel pipelines) and
manual_download.download() (the "Tải video" screen) so both LLM caption
rewrite flows can draw on the same topic signal. Only YouTube's extractor
populates `subtitles`/`automatic_captions` with useful entries — TikTok and
Douyin info dicts simply have none, so this quietly returns "" for them.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

log = logging.getLogger(__name__)

_LANG_PREFERENCE = ("en",)
_FETCH_TIMEOUT = 10  # seconds


def _pick_caption_track(captions: dict) -> Optional[list[dict]]:
    """Pick one language's list of caption format entries, preferring English."""
    for lang in _LANG_PREFERENCE:
        if captions.get(lang):
            return captions[lang]
    for lang in sorted(captions):
        if captions[lang]:
            return captions[lang]
    return None


def _pick_vtt_entry(entries: list[dict]) -> Optional[dict]:
    for entry in entries:
        if entry.get("ext") == "vtt":
            return entry
    return entries[0] if entries else None


def vtt_to_text(vtt: str) -> str:
    """Flatten a WebVTT caption file to plain text, deduping the repeated
    lines that rolling auto-captions produce."""
    lines = []
    last = None
    for raw_line in vtt.splitlines():
        line = raw_line.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        if line.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if not text or text == last:
            continue
        last = text
        lines.append(text)
    return " ".join(lines)


def fetch_transcript(info: dict) -> str:
    """Best-effort transcript from yt-dlp's already-fetched caption metadata.

    Prefers uploader-provided subtitles over auto-generated ones. Returns ""
    on any failure or when the extractor has no captions at all — a
    transcript is a nice-to-have for the LLM rewrite, not something worth
    failing the download over.
    """
    track = _pick_caption_track(info.get("subtitles") or {})
    if track is None:
        track = _pick_caption_track(info.get("automatic_captions") or {})
    if track is None:
        return ""
    entry = _pick_vtt_entry(track)
    if not entry or not entry.get("url"):
        return ""
    try:
        resp = requests.get(entry["url"], timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Failed to fetch transcript: %s", e)
        return ""
    return vtt_to_text(resp.text)
