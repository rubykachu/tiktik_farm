# 02 — Kiến trúc

[← Index](README.md) | [← 01-overview](01-overview.md)

## Sơ đồ services

```
┌─────────────────────────────────────────────────────────────┐
│  webapp — nginx + React SPA  (port 3000 → host 127.0.0.1)   │
│  ├ /accounts                                                 │
│  ├ /schedules  (Pipelines)                                   │
│  └ /videos     (lịch sử VideoUpload từ pipeline)             │
└──────────────────┬───────────────────────────────────────────┘
                   │
        proxy /api/*
                   │
                   ▼
┌──────────────────────────────┐
│  api — FastAPI               │
│  port 8000 (internal)        │
│                              │
│  /api/accounts               │
│  /api/pipelines (+ runs…)    │
│  /api/videos                 │
└──────────┬───────────────────┘
           │
           │ shared SQLite + CookiesDir + VideosDirPath
           │ qua volume /data
           ▼
┌──────────────────────────────┐
│  scheduler — Python worker   │
│  poll DB mỗi 30s             │
│  pipeline_tick()             │
│  (yt-dlp / douyin-downloader │
│   → tiktok_adapter)          │
└───────────────────────────────┘
```

## Volume layout (host)

```
v2/data/
├── tiktok.db                  (SQLite — accounts, channel_pipelines, pipeline_runs, video_uploads, douyin_cookies, …)
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

### Pipeline (YouTube kênh → TikTok, Phase 1)

```
User → webapp → POST /api/pipelines (channel_url, interval, account_ids, caption_template, …)
                    │
                    ▼
                INSERT ChannelPipeline(state=active, next_fire_at=first_fire_at, …)
                    │
        scheduler tick:
            reap_orphans() [startup + implicit recovery paths]
            claim_due_pipeline() — CAS set lock_until = now+10 phút
            LockHeartbeat thread — mỗi 5 phút bump lock_until (CAS)
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

### Pipeline (Douyin kênh → TikTok, Phase 4)

```
User → POST /api/pipelines (source_type=douyin, douyin_cookie_id=N, …)
                │
        scheduler tick:
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
| tiktok-webapp | 51.9 MB | Multi-stage: vite build → nginx alpine serve |

**Tổng disk**: ~8 GB (image base share layer nên thực tế nhỏ hơn).

VPS production: cần ≥8 GB disk free + ≥4 GB RAM. Idle ~1.5 GB RAM, peak ~3-4 GB khi pipeline tick.

## Network

- `api` port 8000: chỉ bind compose network (`expose`), nginx proxy ra port 3000
- `webapp` port 3000 → host `127.0.0.1:3000` (chỉ localhost, không expose Internet)
- Production VPS muốn expose Internet: dùng Cloudflare Tunnel hoặc đặt reverse proxy (Caddy/Traefik) lên trước `127.0.0.1:3000`, bật **HTTPS + auth** (Phase 1 không có auth trong app).

## DB migrations

Không dùng Alembic. Schema quản lý qua 2 bước:

1. **`api/db.py` → `init_db()`**: gọi `SQLModel.metadata.create_all(engine)` — tạo table mới nếu chưa tồn tại. Idempotent. **Không thêm cột vào table đã có.**

2. **`api/db_migrations.py` → `apply_schema_patches()`**: tập hợp `ALTER TABLE ... ADD COLUMN` với guard `PRAGMA table_info(...)` — chạy ngay sau `init_db()` mỗi lần khởi động. Idempotent, an toàn khi chạy nhiều lần. Đây là nơi đặt mọi additive column migration.

Thêm cột mới cho table đã tồn tại:
```python
# trong apply_schema_patches(), block tương ứng với table:
if "ten_cot_moi" not in col_names:
    try:
        conn.exec_driver_sql(
            "ALTER TABLE ten_table ADD COLUMN ten_cot_moi TYPE"
        )
    except Exception:
        pass
```

Gọi thứ tự startup (`api/main.py` và `scheduler/main.py`):
```python
init_db()
apply_schema_patches(engine)
apply_indexes(engine)
seed_app_settings(engine)
```

## Code structure

```
v2/
├── api/                  — FastAPI app
│   ├── main.py           — app factory
│   ├── db.py             — SQLite engine + WAL
│   ├── models.py         — SQLModel: Account, ChannelPipeline,
│   │                     PipelineRun, VideoUpload, DouyinCookie
│   ├── schemas.py        — Pydantic (PipelineCreate/Update, …)
│   ├── routers/          — endpoint groups (accounts, pipelines, videos)
│   └── services/         — cookie_store, caption, youtube_channel, douyin_channel, telegram, secrets, …
├── scheduler/
│   ├── main.py           — entrypoint
│   ├── worker.py         — poll loop → pipeline_tick()
│   └── pipeline_worker.py — claim, run tick (YouTube + Douyin), Telegram, advance next_fire
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
├── config.txt            — config uploader legacy (Config class đọc trực tiếp; KHÔNG phải .env)
├── .env                  — chỉ `TIKTOK_LOGIN_URL` (login flow); .env.example = mẫu biến v2 API/scheduler
└── requirements.txt
```

---

**Next:** [03-setup-docker](03-setup-docker.md)
