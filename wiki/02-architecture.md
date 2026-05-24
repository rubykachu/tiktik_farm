# 02 — Kiến trúc

[← Index](README.md) | [← 01-overview](01-overview.md)

## Sơ đồ services

```
┌─────────────────────────────────────────────────────────────┐
│  webapp — nginx + React SPA  (port 3000 → host 127.0.0.1)   │
│  ├ /accounts                                                 │
│  ├ /login                                                    │
│  ├ /upload                                                   │
│  ├ /schedules  (2 tab: Pipelines | Single-shot)              │
│  └ /videos     (lịch sử VideoUpload từ pipeline)             │
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
│  /api/pipelines (+ runs…)  │   │                      │
│  /api/login/browser/*      │   │  POSTs cookie back   │
│  /api/videos               │   │  to api/.../complete │
└──────────┬───────────────────┘   └──────────────────────┘
           │
           │ shared SQLite + CookiesDir + VideosDirPath
           │ qua volume /data
           ▼
┌──────────────────────────────┐
│  scheduler — Python worker   │
│  poll DB mỗi 30s             │
│  Pass 1: ScheduledUpload     │
│  Pass 2: ChannelPipeline     │
│  (yt-dlp → tiktok_adapter)   │
└───────────────────────────────┘
```

## Volume layout (host)

```
v2/data/
├── tiktok.db                  (SQLite — accounts, scheduled_uploads, channel_pipelines, pipeline_runs, video_uploads, douyin_cookies, …)
├── tiktok.db-shm              (WAL shared memory)
├── tiktok.db-wal              (WAL log)
├── .secret_key                (Fernet key, mode 600 — mã hóa LLM API key + Douyin cookies)
├── CookiesDir/
│   └── tiktok_session-<username>.cookie   (pickle list[dict])
├── VideosDirPath/
│   └── pre-processed.mp4      (yt-dlp output đè mỗi lần download — YouTube pipeline)
└── douyin_tmp/                (thư mục tạm Douyin pipeline — tự xóa sau mỗi tick)
    └── {pipeline_id}_xxxx/
        └── {author_sec_uid}/post/
            ├── {date}_{aweme_id}.mp4
            └── {date}_{aweme_id}_data.json
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

### Pipeline (Douyin kênh → TikTok)

```
User → POST /api/pipelines (source_type=douyin, douyin_cookie_id=N, …)
                │
        scheduler tick — pass 2:
            claim_due_pipeline() + LockHeartbeat
                │
                ▼
            INSERT PipelineRun
            decrypt DouyinCookie → cookie_dict
            douyin_channel.download_creator_posts(
                creator_url, fetch_count, cookie_dict,
                output_dir=/data/douyin_tmp/{id}_xxx/
            )
                ├─ write temp YAML config → /tmp/douyin_cfg_xxx.yml
                ├─ subprocess Popen: vendor/douyin-downloader/run.py
                │   [vendor: XBogus/ABogus sign → Douyin API → .mp4 + _data.json]
                └─ scan output_dir/**/*_data.json → list[DownloadedVideo]
                │
            filter max_duration_seconds → cleanup long videos
                │
            Với mỗi DownloadedVideo × mỗi account:
                dedupe (account_id, douyin_aweme_id)
                [optional] LLM rewrite desc → {ai_title}
                tiktok_adapter.upload_from_file(mp4_path, caption)
                INSERT VideoUpload(douyin_aweme_id=…)
                cleanup_aweme_files(output_dir, aweme_id)
                │
            shutil.rmtree(output_dir)
            UPDATE PipelineRun + advance next_fire + Telegram
```

### Pipeline (YouTube kênh → TikTok, Phase 1)

```
User → webapp → POST /api/pipelines (channel_url, interval, account_ids, caption_template, …)
                    │
                    ▼
                INSERT ChannelPipeline(state=active, next_fire_at=first_fire_at, …)
                    │
        scheduler tick — pass 2 (chỉ khi pass 1 idle):
            reap_orphans() [startup + implicit recovery paths]
            claim_due_pipeline() — CAS set lock_until = now+10 phút
            LockHeartbeat thread — mỗi 5 phút bump lock (CAS)
                    │
                    ▼
            INSERT PipelineRun(started_at, trigger_reason=scheduled|manual)
            yt-dlp flat-playlist → top fetch_count videos
            Với mỗi video, mỗi account (tuần tự):
              dedupe (account_id, youtube_video_id) qua VideoUpload
              download 1 lần/video (trim/skip theo max_duration_seconds)
              render_caption(template) → upload tiktok_adapter
              INSERT VideoUpload (sau khi adapter trả về)
                    │
                    ▼
            UPDATE PipelineRun(counts, summary_text, finished_at)
            advance next_fire_at HOẶC clear run_now (CAS)
            release lock, stop heartbeat
            optional Telegram send_message(summary counts)
```

**Run now**: `POST /api/pipelines/{id}/run-now` — xếp cờ `run_now_requested_at`; không đẩy `next_fire_at`. Hai lần bấm khi đang khóa → 409.

**Pause / Resume**: `PATCH` `state=paused` — tick đang chạy vẫn hoàn tất; tick sau bỏ qua. `Resume` → `state=active`, `next_fire_at=now` (worker vẫn tôn trọng `lock_until`).

## Bảo mật mặt phẳng (Phase 1)

- **Origin / Referer**: mọi `POST/PUT/PATCH/DELETE` phải có `Origin` hoặc `Referer` trỏ tới `http://127.0.0.1:3000` hoặc `http://localhost:3000` — giảm CSRF / DNS rebinding tới API local.
- **Cấu hình mạng**: nên bind dịch vụ ra `127.0.0.1`; mở LAN/Internet chỉ qua reverse proxy có auth (ví dụ nginx basic auth). Trong `docker-compose`, map port API về `127.0.0.1:...` nếu host-expose.
- **Cookie pickle**: file `.cookie` là mặt phẳng thực thi mã khi `pickle.load` — quyền file chặt (`0600`), thư mục `0700`; không chia sẻ file cho user khác.
- **Telegram `bot_token`**: không bao giờ trả về trong JSON GET; log được lọc token (`api/logging.py`).
- **YouTube URL**: validate domain YouTube trước khi đưa vào yt-dlp; caption render dùng `re.sub`, không `str.format`.
- **Chưa có đăng nhập user** trên API — mô hình self-hosted một người dùng; multi-tenant là roadmap.

Chi tiết vận hành: [09-operations](09-operations.md).

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
- Production VPS muốn expose Internet: dùng Cloudflare Tunnel hoặc đặt reverse proxy (Caddy/Traefik) lên trước `127.0.0.1:3000`, bật **HTTPS + auth** (Phase 1 không có auth trong app).

## Code structure

```
v2/
├── api/                  — FastAPI app
│   ├── main.py           — app factory
│   ├── db.py             — SQLite engine + WAL
│   ├── models.py         — SQLModel: Account, ScheduledUpload, ChannelPipeline,
│   │                     PipelineRun, VideoUpload, LoginSession, DouyinCookie
│   ├── schemas.py        — Pydantic (PipelineCreate/Update, …)
│   ├── routers/          — endpoint groups (+ pipelines, videos history)
│   └── services/         — cookie_store, caption, youtube_channel, douyin_channel, telegram, secrets, …
├── scheduler/
│   ├── main.py           — entrypoint
│   ├── worker.py         — poll loop (pass 1 + pass 2)
│   └── pipeline_worker.py — claim, run tick (YouTube + Douyin), Telegram, advance next_fire
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
├── vendor/
│   └── douyin-downloader/    — vendored CLI tool (baked vào base image)
├── docker/base.Dockerfile
├── docker-compose.yml
├── cli.py                — legacy CLI
├── config.txt
└── requirements.txt
```

---

**Next:** [03-setup-docker](03-setup-docker.md)
