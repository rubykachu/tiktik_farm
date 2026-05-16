# CLAUDE.md — Agent Guide

Project-specific notes for AI agents working on `farm_tiktik/v2`. Generic Python/Docker/React knowledge is omitted.

## What this project is

Fork of `makiisthenes/TiktokAutoUploader`. v2 adds REST API (FastAPI), Scheduler worker, noVNC service, React webapp, Docker Compose stack. Replaces a heavier v1 multi-service stack the user previously maintained at `../`. Single-user today; SaaS planned.

Full operational/business docs: **`v2/wiki/`** (start at `wiki/README.md`). Treat the wiki as authoritative when explaining behavior to the user.

## Critical upstream bugs (`tiktok_uploader/tiktok.py`)

Four upstream bugs are fixed in-tree. Easy to regress when syncing upstream. **Memory file**: `tiktok-project-create.md` has the full diagnosis. Quick summary:

1. **Full cookie jar** — load every cookie from the pickle into `requests.Session.cookies`, not just `sessionid`+`tt-target-idc`. Antibot otherwise → status_code 8.
2. **Desktop UA** — `UserAgent(platforms="desktop", browsers="Chrome")`. Mobile UAs make the web API reply status_code 8.
3. **No default `tt-target-idc`** — never force `useast2a`. Cookie without IDC is fine; TikTok resolves DC from `sessionid`. Hardcoding the wrong DC breaks accounts that live elsewhere.
4. **Explicit `return True`** at the end of `upload_video()` success path. Otherwise function returns `None` → `tiktok_adapter` reports `ok=false` even though TikTok published.

Anyone touching this file: run a real upload via API after the change. CLI alone doesn't catch bug #4.

## File-specific quirks

- `tiktok_uploader/*.py` is upstream code: **tabs, not spaces**. Don't auto-format it.
- `tiktok_uploader/tiktok.py` uses `print()` for upload progress, not the `logging` module. Lines like `[INFO] tt-target-idc not in cookie` are emitted via stdout — they show up in `docker compose logs api` because uvicorn captures stdout.
- `tiktok_uploader/Video.py:get_youtube_video` writes to a **fixed filename** `pre-processed.mp4` (overwrites each download). Don't assume per-video filenames.
- `cli.py login -n <user>` **bails out** with `"Unnecessary login: session already saved!"` if the cookie file exists. To force re-login, delete the cookie file first.

## Cookie format

- On disk: pickle of `list[dict]` with Selenium-style keys (`name`, `value`, `domain`, `path`, `secure`, `httpOnly`, `expiry`, `sameSite`).
- `tiktok_uploader/cookies.py` rewrites `sameSite == "None"` → `"Strict"` on load.
- Chrome DevTools / Cookie-Editor exports use **different keys** (`expirationDate`, `hostOnly`, etc.). Convert via `scripts/migrate-json-cookie-editor.py`.
- Selenium login (`cli.py login`, noVNC) only saves the 2 required cookies (`sessionid` + `tt-target-idc`) into the pickle (see `wait_for_session_cookies`). Antibot still works because TikTok itself sets the rest on the first request. Migration paths produce richer pickles (15-20 cookies). Both work since fix #1.

## Path resolution gotcha

`tiktok_uploader/Config.py` resolves `cookies_dir` and `videos_dir` **relative to `os.getcwd()`**.

| Run mode | cwd | Cookies on host |
|---|---|---|
| Docker | `/data` (set via `working_dir`) | `./data/CookiesDir/` |
| Native | `v2/` | `./CookiesDir/` |

Native and Docker do **not** share cookies. Anything that touches files needs to know which mode is running.

## API ↔ noVNC ↔ webapp wiring

- `api` calls `novnc` over the compose network using hostname `novnc:7900`. On native (no Docker), `NOVNC_CONTROL_URL=http://127.0.0.1:7900` override is required, plus a way to actually run noVNC (Docker `docker run` is the practical option — Xvfb doesn't run on macOS natively).
- `webapp` nginx caches upstream DNS IPs at boot — **no TTL**. When `api`/`novnc` containers are recreated, the IP changes and the cached one returns "Host is unreachable" / 502.
  - **Operational rule**: any `docker compose build <svc> && docker compose up -d <svc>` MUST also force-recreate `webapp`. Easiest: `docker compose up -d --force-recreate api webapp` or `--force-recreate` the whole stack.
  - User explicitly rejected nginx `resolver` workaround in favor of this simpler operational rule. Don't re-add `resolver` to `nginx.conf`.

## Single-slot noVNC

The control server queues nothing. Concurrent `POST /browser/start` while one session is `starting|active|completing` returns **409**. By design for now (Phase 2/SaaS will need real queue).

## DB / scheduler invariants

- SQLite with WAL (`PRAGMA journal_mode=WAL`), `check_same_thread=False`, `foreign_keys=ON`. Set per-connection in `api/db.py`.
- All datetimes stored UTC. Time comparison done in **Python**, not SQL (`now_utc()` in code, see `scheduler/worker.py`). Don't add `WHERE scheduled_for <= datetime('now')` style queries — container TZ drift would silently break it.
- Account delete must cascade-clean `scheduled_uploads` rows manually (FK has no `ON DELETE CASCADE`). See `api/routers/accounts.py:delete_account`.
- TikTok `schedule_time` rules enforced both client-side (Zod in `webapp/src/pages/Upload.tsx`) and inside `upload_video`: must be `900 ≤ seconds ≤ 864000` (15 min — 10 days). Private+schedule incompatible.
- Title max 2200 chars (TikTok rule, enforced in both UI Zod and `upload_video`).

## `/videos` page semantics

`/api/videos` lists **files in `VideosDirPath/`** — it is **not** TikTok upload history. Files dropped via UI upload go through a `tempfile` and are deleted after upload, so they never appear here. yt-dlp downloads do appear (as `pre-processed.mp4`). Upload history table is a Phase 2 task (see `wiki/11-roadmap.md`).

## Pinning notes

- `fake-useragent>=1.4` (loose) — version drift can flip bug #2 back on. Pin to a known-good version (currently 2.2.0) before any production deploy.
- `undetected-chromedriver` is pulled from a fork commit in `requirements.txt` — main pip release is broken against current Chrome. Don't "upgrade" it casually.

## Where things live

```
v2/
├── api/                  FastAPI app (routers, services, db, models, schemas)
├── scheduler/            Worker that polls scheduled_uploads
├── novnc/                Xvfb + chromium + control_server (Docker only)
├── tiktok_uploader/      UPSTREAM package — minimal edits, preserve tabs
├── webapp/               React + Vite + nginx (multi-stage Docker build)
├── scripts/migrate-json-cookie-editor.py   Convert Cookie-Editor JSON → pickle
├── wiki/                 Operational docs (authoritative for the user)
├── data/                 Docker volume mount (DB + cookies + videos)
└── CookiesDir/, VideosDirPath/   Native-mode paths (not shared with Docker)
```

## Common commands

```bash
# Docker (production path)
docker compose build
docker compose up -d --force-recreate         # safe default
docker compose logs -f api scheduler

# Force a clean test from scratch
docker compose down -v && rm -rf data/        # ⚠️ destroys cookies + DB

# Native dev
./.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000
./.venv/bin/python -m scheduler.main
(cd webapp && ./node_modules/.bin/vite --port 3000 --host 127.0.0.1)

# Migrate cookies (Cookie-Editor / v1 JSON)
./.venv/bin/python scripts/migrate-json-cookie-editor.py <src_dir> ./data/CookiesDir
curl -X POST http://127.0.0.1:8000/api/accounts/import-from-disk

# Manual upload smoke test (any fix in tiktok.py must pass this)
curl -X POST http://127.0.0.1:8000/api/uploads/file \
  -F "username=<u>" -F "title=test" -F "options_json={}" \
  -F "video=@./data/VideosDirPath/pre-processed.mp4"
```

## When the user reports a failure

1. Pull recent logs: `docker compose logs --tail=50 api` (or the relevant service).
2. Cross-reference with `wiki/09-operations.md` "Phrases nên grep" table.
3. If it's a `KeyError: 'project'` or upload returns `ok:false` unexpectedly → re-read the 4 bugs above before changing code; the cause is almost always one of them.
4. If it's a 502 from the webapp → check whether `api`/`novnc` were recently rebuilt; the fix is `docker compose up -d --force-recreate webapp` (not a code change).
5. Cookie debug: load the pickle and dump cookie names (`docker compose exec api python -c "import pickle; print(sorted(c['name'] for c in pickle.load(open('/data/CookiesDir/tiktok_session-X.cookie','rb'))))"`). `sessionid` missing → re-login needed.

## Things to ask the user before doing

- Anything that touches `./data/` destructively (cookies are hard to recover).
- Adding new dependencies (user prefers thin, deliberate stack — v2 was chosen to replace a heavy v1).
- Changing the production deploy path. The user runs this on a VPS; assume any change must work in Docker on Ubuntu, not just macOS dev.
- Pull requests upstream — user wants to fork+maintain, decide per-PR which patches to push (see `wiki/10-known-bugs.md` strategy section).

## Conventions

- User communicates in Vietnamese with English technical terms; respond in kind.
- Caveman compression mode often active for chat replies. Code and commit messages always use normal English.
- Commit style: Conventional Commits (`fix(scope):`, `feat(scope):`, `docs:`, `refactor:`).
- When proposing fixes, prefer minimal in-place edits over refactors. The user has rejected over-engineering twice already (nginx resolver, premature abstractions).
