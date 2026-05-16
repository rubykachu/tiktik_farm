# 02 — Kiến trúc

[← Index](README.md) | [← 01-overview](01-overview.md)

## Sơ đồ services

```
┌─────────────────────────────────────────────────────────────┐
│  webapp — nginx + React SPA  (port 3000 → host 127.0.0.1)   │
│  ├ /accounts                                                 │
│  ├ /login                                                    │
│  ├ /upload                                                   │
│  ├ /schedules                                                │
│  └ /videos                                                   │
└──────────────────┬────────────────────┬─────────────────────┘
                   │                    │
        proxy /api/*           proxy /novnc/* (ws+html)
                   │                    │
                   ▼                    ▼
┌──────────────────────────────┐   ┌──────────────────────┐
│  api — FastAPI               │   │  novnc               │
│  port 8000 (internal)        │   │  Xvfb + chromium     │
│                              │   │  + x11vnc :5900      │
│  /api/accounts               │   │  + websockify :6080  │
│  /api/uploads/file           │◄──┤  + control_server    │
│  /api/uploads/youtube        │   │    :7900             │
│  /api/schedules              │   │                      │
│  /api/login/browser/*        │   │  POSTs cookie back   │
│  /api/videos                 │   │  to api/.../complete │
└──────────┬───────────────────┘   └──────────────────────┘
           │
           │ shared SQLite + CookiesDir + VideosDirPath
           │ qua volume /data
           ▼
┌──────────────────────────────┐
│  scheduler — Python worker   │
│  poll DB mỗi 30s             │
│  pick due jobs → download    │
│  → upload qua tiktok_adapter │
└───────────────────────────────┘
```

## Volume layout (host)

```
v2/data/
├── tiktok.db                  (SQLite — accounts, schedules, login_sessions)
├── tiktok.db-shm              (WAL shared memory)
├── tiktok.db-wal              (WAL log)
├── CookiesDir/
│   └── tiktok_session-<username>.cookie   (pickle list[dict])
└── VideosDirPath/
    └── pre-processed.mp4      (yt-dlp output đè mỗi lần download)
```

Inside container: mount tại `/data`. `Config.cookies_dir = "./CookiesDir"` + `working_dir=/data` → resolve thành `/data/CookiesDir`.

## Data flow chi tiết

### Login noVNC

```
User                Webapp           API              noVNC           TikTok
 │                    │               │                │                │
 │ click "Start"      │               │                │                │
 │───────────────────►│               │                │                │
 │                    │ POST /api/login/browser/start  │                │
 │                    │──────────────►│                │                │
 │                    │               │ POST /browser/start             │
 │                    │               │───────────────►│                │
 │                    │               │                │ launch chromium│
 │                    │               │                │ GET tiktok.com │
 │                    │               │                │───────────────►│
 │                    │ 201 + vnc_url │                │                │
 │                    │◄──────────────│                │                │
 │ iframe noVNC HTML  │               │                │                │
 │ load /novnc/vnc.html (ws upgrade)  │                │                │
 │◄───────────────────│               │                │                │
 │ login bằng tay     │               │                │ chrome thấy session
 │ (mouse/keyboard qua VNC)           │                │                │
 │                    │               │                │ poll cookies   │
 │                    │               │                │ → có sessionid │
 │                    │               │ POST /api/login/browser/<id>/complete (cookies)
 │                    │               │◄───────────────│                │
 │                    │               │ save pickle    │                │
 │                    │               │ upsert Account │                │
 │                    │ SSE event "completed"           │                │
 │◄───────────────────│               │                │                │
 │ UI navigate /accounts              │                │                │
```

### Upload local file qua UI

```
User → webapp → POST /api/uploads/file (multipart)
                    │
                    ▼
                api: write tempfile → tiktok_adapter.upload_from_options()
                    │
                    ▼
                tiktok_uploader.upload_video(username, tempfile, title, ...)
                    │
                    ▼
                load pickle → requests.Session với full cookie jar
                    │
                    ▼
                POST tiktok.com/api/v1/web/project/create/ → project_id
                POST tiktok.com/.../upload   → store_uri + upload chunks
                POST tiktok.com/.../commit   → commit
                POST tiktok.com/tiktok/web/project/post/v1/ → published
                    │
                    ▼
                return True → adapter → ok=true → API response
                    │
                    ▼
                delete tempfile
```

### Schedule (YouTube)

```
User → webapp → POST /api/schedules (datetime, youtube_url, options)
                    │
                    ▼
                INSERT ScheduledUpload(status=pending, scheduled_for=...)
                    │
                    ▼
        ... 30s pass ...
                    │
        scheduler poll loop:
            SELECT pending WHERE scheduled_for <= now_utc()
                    │
                    ▼
            UPDATE status=running, heartbeat_at=now
                    │
                    ▼
            youtube.download(url) → /data/VideosDirPath/pre-processed.mp4
                    │
                    ▼
            tiktok_adapter.upload_from_options(...) → True/False
                    │
                    ▼
            UPDATE status=succeeded|failed, result_text=...
```

## Image sizes (đã build trên máy m4)

| Image | Size | Note |
|---|---|---|
| tiktok-base | 2.44 GB | Python 3.11 + Node 20 + Playwright Chromium + ffmpeg + tiktok_uploader package |
| tiktok-api | 2.44 GB | + entrypoint uvicorn |
| tiktok-scheduler | 2.44 GB | + entrypoint python -m scheduler.main |
| tiktok-novnc | 3.03 GB | + Xvfb + x11vnc + chromium debian package + fluxbox |
| tiktok-webapp | 51.9 MB | Multi-stage: vite build → nginx alpine serve |

**Tổng disk**: ~12.5 GB (image base share layer nên thực tế nhỏ hơn).

VPS production: cần ≥8 GB disk free + ≥4 GB RAM. Idle ~1.5 GB RAM, peak ~3-4 GB khi upload concurrent + noVNC active.

## Network

- `api` port 8000: chỉ bind compose network (`expose`), nginx proxy ra port 3000
- `webapp` port 3000 → host `127.0.0.1:3000` (chỉ localhost, không expose Internet)
- `novnc` port 6080 + 7900: internal compose only
- Production VPS muốn expose Internet: dùng Cloudflare Tunnel hoặc đặt reverse proxy (Caddy/Traefik) lên trước `127.0.0.1:3000`

## Code structure

```
v2/
├── api/                  — FastAPI app
│   ├── main.py           — app factory
│   ├── db.py             — SQLite engine + WAL
│   ├── models.py         — SQLModel tables: Account, ScheduledUpload, LoginSession
│   ├── schemas.py        — Pydantic request/response schemas
│   ├── routers/          — endpoint groups
│   └── services/         — adapters: cookie_store, novnc_client, tiktok_adapter, youtube
├── scheduler/            — worker
│   ├── main.py           — entrypoint
│   └── worker.py         — poll loop
├── novnc/                — control server inside noVNC container
│   ├── control_server.py — FastAPI :7900
│   └── entrypoint.sh     — boot Xvfb + x11vnc + websockify + uvicorn
├── tiktok_uploader/      — upstream package (đã fix 4 bug)
│   ├── tiktok.py         — upload_video chính
│   ├── cookies.py        — pickle load/save
│   ├── Video.py          — yt-dlp wrapper
│   └── tiktok-signature/ — Node.js subprocess (sign x-bogus)
├── webapp/               — React + Vite + nginx
│   ├── src/
│   ├── nginx.conf
│   └── Dockerfile
├── scripts/
│   └── migrate-json-cookie-editor.py
├── docker/base.Dockerfile
├── docker-compose.yml
├── cli.py                — legacy CLI
├── config.txt
└── requirements.txt
```

---

**Next:** [03-setup-docker](03-setup-docker.md)
