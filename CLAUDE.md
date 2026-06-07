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
  ├─ api/models.py       SQLModel: Account, ChannelPipeline, PipelineRun, VideoUpload, DouyinCookie
  ├─ api/schemas.py      Pydantic request/response shapes
  ├─ api/routers/        endpoint groups (accounts, pipelines, videos, telegram, settings, llm_models, douyin_cookies)
  └─ api/services/       tiktok_adapter, youtube_channel, douyin_channel, caption, llm, secrets, telegram, ...
scheduler (Python worker)
  └─ polls DB every 30s → pipeline_worker.py: claim lock → yt-dlp/douyin-downloader → tiktok_adapter → VideoUpload
```

**Shared volume** (`data/`): `tiktok.db` (SQLite WAL), `CookiesDir/`, `VideosDirPath/`, `douyin_tmp/`, `.secret_key`.

**Docker image layers**: `docker/base.Dockerfile` (tiktok-base, ~2.4 GB: Python + Node + Playwright Chromium + ffmpeg) → `api` and `scheduler` FROM tiktok-base, copy own source only. Rebuild decision:

| Changed | Action |
|---|---|
| `api/` or `scheduler/` Python | `make restart` |
| `requirements.txt` or `tiktok_uploader/` | `make rebuild-base` |
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
