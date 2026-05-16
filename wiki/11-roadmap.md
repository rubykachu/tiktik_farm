# 11 — Roadmap Phase 2 + SaaS

[← Index](README.md) | [← 10-known-bugs](10-known-bugs.md)

## Hiện trạng (sau Phase 1 implementation)

✅ Core upload pipeline (local file + YouTube URL single)  
✅ Schedule **Single-shot** (YouTube only, `ScheduledUpload`)  
✅ **Channel pipeline** lặp: YouTube → TikTok, dedupe, `VideoUpload` history, drill-down API/UI  
✅ Caption template (`{title}`, `{title_hashtag}`, `{now}`), Telegram tùy chọn (summary theo tick)  
✅ Trang `/schedules` hai tab; `/videos` = DB `VideoUpload` (chủ yếu pipeline)  
✅ Import JSON Cookie (`/api/accounts/import-json`), modal confirm delete nhiều chỗ  
✅ Worker hai pass + CAS lock/heartbeat/orphan reaper  
✅ noVNC browser login  
✅ Multi-account local single-user  
✅ Docker stack production-ready (smoke test pass)  
✅ 4 bug upstream đã fix  
✅ Security baseline tối thiểu: Origin check mutating, validation URL/caption/Telegram, log scrub token  

**Còn gap so với “full upload history”**: `VideoUpload` **không** ghi cho upload thủ công / single-shot — chỉ pipeline. Phase 2 có thể mở rộng bảng hoặc ghi thêm nguồn.

## Phase 2 — Port nghiệp vụ v1

v1 farm_tiktik có:

- `services/downloader` — yt-dlp wrapper với `date_filter` + `fallback` strategy (latest_unseen, oldest_unseen)
- `services/filter` — moviepy/ffmpeg pipeline: speed_factor, hflip, color (brightness/saturation/contrast/gamma/hue), target 9:16 (1080×1920)
- `services/uploader` — wrapper v1 (thay bằng v2 core)
- `accounts.yml` — config tập trung defaults + per-account override
- `data/state/<username>.json` — track video đã đăng để skip
- `data/raw/`, `data/filtered/`, `data/uploaded/` — pipeline staging

### Mapping sang v2

| v1 | Đề xuất v2 |
|---|---|
| `accounts.yml` defaults/override | Bảng `pipeline_config` + `pipeline_config_override` SQLite; endpoint `PUT /api/pipelines/{username}/config` |
| downloader yt-dlp + date_filter | Service mới `api/services/downloader.py` + endpoint `POST /api/pipelines/{username}/run` → enqueue ScheduledUpload với `source_type=youtube_channel` |
| filter moviepy | Service `api/services/filter.py`; chạy trong `scheduler/worker.py` trước khi gọi `tiktok_adapter` |
| state tracker JSON | Bảng `processed_video` (channel_id, video_id, processed_at, account_id) — thay file JSON |
| folder staging | Mount `./data/{raw,filtered,uploaded}/` y v1 layout |

### Workflow Phase 2

```
POST /api/pipelines/lmbk37/run
  ├ scan_channel(channel_url) → list candidate videos
  ├ apply date_filter (today / N days / fallback) → 1 video
  ├ check processed_video table → skip nếu đã có
  ├ enqueue ScheduledUpload(status=downloading)
  └ return job_id

Scheduler picks:
  ├ download → /data/raw/<video_id>.mp4
  ├ filter (speed_factor, hflip, color, scale 1080×1920) → /data/filtered/<video_id>.mp4
  ├ upload qua tiktok_adapter
  ├ move /data/filtered → /data/uploaded
  ├ INSERT processed_video
  └ UPDATE status=succeeded
```

### UI mới

- `/pipelines` page — hiển thị từng account + nút "Run now" + lịch sử pipeline run
- `/config/{username}` page — override defaults (sliders cho filter, input cho date_filter, channel URL)

## Phase 2a — Mở rộng lịch sử upload (đã có một phần)

Phase 1 đã có bảng **`VideoUpload`** cho pipeline. Phase 2a còn lại:

- Ghi lịch sử cho **manual upload** và/hoặc **ScheduledUpload** (cùng schema hoặc bảng liên quan).
- Thống nhất hiển thị `/videos` cho mọi nguồn.

Sơ đồ cũ `upload_history` có thể gộp concept với `VideoUpload` thay vì tạo bảng trùng.

## Phase 2b — Playlist support

Hiện tại YouTube URL chỉ 1 video. Phase 2b:

```python
POST /api/uploads/youtube-playlist
{
  "username": "lmbk37",
  "playlist_url": "https://www.youtube.com/playlist?list=...",
  "title_template": "Day {n}: {video_title}",
  "max_videos": 10,
  "stagger_minutes": 30
}
→ yt-dlp --flat-playlist extract video list
→ for each video, INSERT ScheduledUpload với scheduled_for = now + (i * stagger_minutes)
→ return [job_ids]
```

Stagger để tránh TikTok rate-limit khi đăng quá nhanh.

## Phase 3 — Multi-tenant (SaaS)

### Schema changes

```sql
CREATE TABLE tenant (
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE,
  plan TEXT,                       -- 'free' | 'pro' | 'enterprise'
  created_at TIMESTAMP
);

ALTER TABLE accounts ADD COLUMN tenant_id INTEGER REFERENCES tenant(id);
ALTER TABLE scheduled_uploads ADD COLUMN tenant_id INTEGER REFERENCES tenant(id);
ALTER TABLE video_uploads ADD COLUMN tenant_id INTEGER REFERENCES tenant(id);
```

Mọi query trong API thêm `WHERE tenant_id = :current_tenant`.

### Auth

- FastAPI middleware inject `tenant_id` từ JWT vào request state
- Webapp: login page (Auth0 / Clerk / Supabase Auth / self-hosted)
- Session cookie httpOnly + Secure (production HTTPS bắt buộc)

### Isolation

```
/data/CookiesDir/<tenant_id>/tiktok_session-<username>.cookie
/data/VideosDirPath/<tenant_id>/...
```

Sửa `Config.cookies_dir` thành function `cookies_dir(tenant_id) → path`. Inject tenant_id từ request vào mọi service call.

### Proxy pool

TikTok phát hiện IP datacenter → ban account. SaaS phải có residential proxy pool:

- Thêm bảng `proxy(id, url, region, tenant_id, status, last_used_at)`
- Per-account assign proxy: `account.proxy_id`
- `upload_video(..., proxy=account_proxy_url)` — flag đã sẵn

Cung cấp:
- Tự host proxy (Squid + residential IP sub) — phức tạp
- Mua từ Bright Data / Smartproxy / Oxylabs

### noVNC concurrency

Hiện tại single-slot serial. SaaS cần nhiều người login đồng thời:

- **Option A**: Multiple Xvfb display per container — phức tạp lock display
- **Option B**: Dedicated worker pod per login session — Kubernetes job
- **Option C**: Queue + estimated wait time — UX kém nhưng đơn giản

Đề xuất bắt đầu C, scale dần B khi tải tăng.

### Rate limit

Per-tenant:
- Max N upload/giờ (TikTok khuyến nghị ≤5/account/giờ)
- Max M account per plan
- Max K schedule pending per plan

Implement qua middleware FastAPI + Redis counter.

### Billing

Stripe + webhook:
- `customer.subscription.updated` → cập nhật `tenant.plan`
- `invoice.payment_failed` → suspend tenant

## Phase 4 — Horizontal scale

Khi traffic vượt single-server:

- SQLite → PostgreSQL (Phase 4 task, hiện SQLite WAL đủ ~100 concurrent users)
- Stateless API + scheduler (đã sẵn, chỉ cần DB external)
- noVNC → K8s job dynamic spawn
- Object storage cho video (S3-compat) thay file local
- CDN cho webapp bundle

## Compliance / risk

- TikTok ToS không cho phép automated upload (gray area)
- Account có thể bị ban → SaaS phải warn user
- Cookie xếp loại sensitive data — GDPR/CCPA cần consider khi launch EU/US
- Có lawful basis cho processing (consent từ user)

## Timeline đề xuất

| Phase | Estimate | Mục tiêu |
|---|---|---|
| 2a — Upload history | 1 week | UI `/history`, wire tiktok_post_id |
| 2 — Pipeline nghiệp vụ | 2-3 weeks | Port v1 downloader/filter, UI `/pipelines` |
| 2b — Playlist | 3 days | Endpoint + UI tab |
| 3 — Multi-tenant schema + auth | 2 weeks | DB migrate, JWT, basic isolation |
| 3 — Proxy pool | 1 week | Schema + per-account assign |
| 3 — Billing | 1 week | Stripe webhook + UI plan management |
| 4 — Postgres migrate | 3 days | Pin sqlalchemy URL, test |
| 4 — K8s noVNC | 2 weeks | Convert compose → manifest |

Tổng ~3 tháng cho MVP SaaS từ phase 1 hiện tại.

---

[← Index](README.md)

🎉 Đã đọc hết wiki. Mọi câu hỏi đột xuất → tail log + paste cho Claude.
