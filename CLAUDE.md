# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`farm_tiktik v2` — automation tool that pulls videos from YouTube/Douyin channels and re-uploads them to TikTok accounts on a schedule. Three services: FastAPI (`api/`), Python scheduler (`scheduler/`), React SPA (`webapp/`).

## Commands

### Tests

```bash
# Local (fast, needs .venv)
. .venv/bin/activate && python -m pytest -q
# Or via RTK:
rtk pytest

# Single test file
. .venv/bin/activate && python -m pytest tests/test_pipeline_worker.py -q

# In Docker container
make test
```

### Dev (native, hot-reload)

```bash
# API
TIKTOK_CONFIG_PATH=./config.txt ./.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

# Scheduler
TIKTOK_CONFIG_PATH=./config.txt ./.venv/bin/python -m scheduler.main

# Webapp
cd webapp && npm run dev -- --port 3000 --host 127.0.0.1
```

### Docker (production-like)

```bash
make up              # Start full stack
make restart         # After editing api/ or scheduler/ Python
make rebuild-base    # After changing requirements.txt or tiktok_uploader/
make deploy          # Build all + recreate (safe default after any change)
make logs-api        # Tail API logs
```

### Webapp

```bash
cd webapp && npm run build   # Type-check + bundle
```

## Architecture

```
webapp (React/Vite → nginx, :3000)
  └─ /api/* proxied to →
api (FastAPI, :8000 internal)
  ├─ api/models.py       SQLModel: Account, ChannelPipeline, PipelineRun, VideoUpload, DouyinCookie,
  │                      PipelineSeenVideo (dedupe for account-less pipelines), ManualDownload
  ├─ api/schemas.py      Pydantic request/response shapes
  ├─ api/routers/        endpoint groups (accounts, pipelines, videos, telegram, settings, llm_models,
  │                      douyin_cookies, downloads)
  └─ api/services/       tiktok_adapter, youtube_channel, douyin_channel, caption, llm, llm_rewrite,
                         manual_download, account_fields, caption_prompt, secrets, telegram, ...
scheduler (Python worker)
  └─ polls DB every 30s → pipeline_worker.py: claim lock → yt-dlp/douyin-downloader → tiktok_adapter → VideoUpload
```

**Shared volume** (`data/`): `tiktok.db` (SQLite WAL), `CookiesDir/`, `VideosDirPath/`, `douyin_tmp/`, `.secret_key`.

**Docker image layers**: `docker/base.Dockerfile` (tiktok-base, ~2.4 GB: Python + Node + Playwright Chromium + ffmpeg) → `api` and `scheduler` FROM tiktok-base, copy own source only. Rebuild decision:

| Changed | Action |
|---|---|
| `api/` or `scheduler/` Python | `make restart` |
| `requirements.txt` or `tiktok_uploader/` | `make rebuild-base` |
| yt-dlp stops downloading YouTube | bump the `yt-dlp>=` floor, `make rebuild-base` |
| `webapp/` src | nothing (Vite HMR) in dev; `make recreate-webapp` in prod |
| Dockerfile or new service | `make rebuild` |

## Critical: `tiktok_uploader/tiktok.py` has 4 local fixes

`tiktok_uploader/` is a vendored fork of `makiisthenes/TiktokAutoUploader` with 4 bugs patched. **Never overwrite these when syncing upstream.**

| Bug | Fix (must remain) |
|---|---|
| #1 full cookie jar | `for c in cookies: session.cookies.set(...)` loop (not just 2 cookies) |
| #2 desktop UA | `UserAgent(platforms="desktop", browsers="Chrome")` |
| #3 no default DC | No `dc_id = "useast2a"` default when `tt-target-idc` absent |
| #4 return True | Explicit `return True` at end of `upload_video()` |

Symptoms when broken: `POST /api/v1/web/project/create/` returns `{"status_code":8}` → `KeyError: 'project'`.

To sync only the signing JS (safe): `bash scripts/update-tiktok-signature.sh`.

## Key invariants

- `POST/PUT/PATCH/DELETE` require `Origin` or `Referer` matching `http://127.0.0.1:3000` or `http://localhost:3000` — enforced by `api/middleware/origin.py`.
- `config.txt` (not `.env`) is what `Config` class reads for uploader settings. `.env` is only used for `TIKTOK_LOGIN_URL`.
- Native dev cookie dir is `./CookiesDir/`; Docker uses `./data/CookiesDir/`. They don't share.
- `api/logging.py` scrubs Telegram `bot_token` from all log output.
- A pipeline with `account_ids_json = "[]"` uploads nothing: it downloads, notifies Telegram
  (optionally with the video file and the accounts' login/password), then deletes the files.
  Deletion is deferred to `_pipeline_run_context` so the notification can still attach them.
- Telegram sends retry transient failures (network, 5xx, 429 with its `retry_after`) up to
  `MAX_SEND_ATTEMPTS` with doubling backoff; other 4xx are not retried. Constants live in
  `api/services/telegram.py`. Tests must patch `api.services.telegram.time.sleep`.
- Manual downloads (`/api/downloads`) run as FastAPI background tasks in the api process and are
  kept under `MANUAL_DOWNLOAD_DIR` for 24h. Expired ones are deleted by the scheduler's daily
  retention pass and by `GET /api/downloads`, so retention holds without the scheduler.
- Telegram messages are sent with `parse_mode=HTML`, so every interpolated value goes through
  `_esc()` in `scheduler/pipeline_worker.py`. An unescaped `<` in a video title fails the send.
- `telegram_notify_mode` decides whether a run with nothing new still notifies: `changes_only`,
  `cycle` (default — one message when the schedule cycle closes, so a dead scheduler shows up as
  a missing message), or `every_tick`. Errors always notify. A window fires many times waiting
  for the author to post; `advance_next_fire` returns whether this tick closed the cycle.
- Every Telegram notification carries a signed download link per video: the message is cheap and
  the bytes only move when someone clicks, which is also the only way past Telegram's 50 MB
  bot-upload cap. Publishing MOVES the file into the manual-download store, so anything holding
  the old path must follow `PublishedVideo.file_path`. Needs `PUBLIC_BASE_URL` (or `HOST`);
  without one the notification degrades to text. `telegram_attach_video` additionally uploads
  the file into the chat.
- `tiktok_uploader` calls `load_dotenv()` on import, so the developer's real `.env` reaches tests.
  Any test whose behavior depends on `HOST` / `PUBLIC_BASE_URL` must set or clear them itself.
- `/api/public/downloads/{id}/{token}` is the ONLY route nginx exempts from Basic Auth
  (`webapp/nginx.conf`), because the link has to open on a phone. The HMAC (`secrets.sign`) is the
  authentication there. Never put another endpoint under `/api/public/`.
- `pipeline_seen_videos` rows have no age cutoff — they are the only dedupe key for account-less
  pipelines and are deleted only with their pipeline.
- `fake-useragent` must stay pinned in `requirements.txt` to prevent bug #2 regression.

---

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
