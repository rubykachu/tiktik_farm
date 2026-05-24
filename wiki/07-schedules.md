# 07 — Lịch đăng: Single-shot & Pipeline (Phase 1)

[← Index](README.md) | [← 06-upload](06-upload.md)

Trang **`/schedules`** có **hai tab**:

| Tab | Entity DB | Mục đích |
|-----|-----------|----------|
| **Single-shot** | `ScheduledUpload` | Một job YouTube URL, một thời điểm cụ thể trong tương lai (như trước Phase 1). |
| **Pipelines** | `ChannelPipeline` + `PipelineRun` + `VideoUpload` | Lặp theo khoảng thời gian: quét top-N video mới từ URL kênh/playlist YouTube, dedupe, upload lên một hoặc nhiều TikTok account **tuần tự**. |

Scheduler (`scheduler/worker.py`) mỗi vòng **30 giây**:

1. **Pass 1** — `reclaim_stale` → `claim_next_due` → `run_one` → `finalize` cho `ScheduledUpload` (không đổi).
2. **Pass 2** — nếu pass 1 không có việc: `pipeline_tick()` (`scheduler/pipeline_worker.py`).

---

## Tab Single-shot

### Tính năng

Lên lịch upload **một YouTube URL** vào **một** thời điểm tương lai.

### Giới hạn

- **Chỉ YouTube URL**. Local file không schedule được vì file drag&drop không persist.
- TikTok: thời gian trong khoảng **15 phút → 10 ngày** từ now (`900s ≤ schedule ≤ 864000s`).
- Visibility `private` (=1) **không** schedule được.

### UI

Tab **Single-shot**: bảng lịch như cũ; xóa lịch có **modal xác nhận**.

### API (ví dụ)

Mọi request **đổi dữ liệu** từ `curl` cần header `Origin: http://127.0.0.1:3000` (middleware Phase 1).

```bash
curl -X POST http://127.0.0.1:8000/api/schedules \
  -H "Origin: http://127.0.0.1:3000" \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

Lifecycle, huỷ, retry: giữ nguyên như tài liệu cũ (`pending` → `running` → `succeeded` / `failed`).

### DB (`scheduled_uploads`)

| Field | Ghi chú |
|-------|---------|
| `scheduled_for` | UTC, index |
| `heartbeat_at` | Heartbeat thread 30s khi `running` |
| `attempts` | Tối đa 3 lần claim |

---

## Tab Pipelines — khái niệm `ChannelPipeline`

**Pipeline** = cấu hình tự động: **URL kênh** → downloader lấy **fetch_count** video mới nhất mỗi “tick” → upload lên **tất cả** `account_ids` đã chọn (lần lượt).

Pipeline hỗ trợ 2 nguồn qua trường `source_type`:

| `source_type` | Downloader | Xác thực | Dedup key |
|---|---|---|---|
| `youtube` (mặc định) | yt-dlp | Không cần | `(account_id, youtube_video_id)` |
| `douyin` | douyin-downloader subprocess | `DouyinCookie` (cookie Douyin thật) | `(account_id, douyin_aweme_id)` |

Chi tiết Douyin: [13-douyin-pipeline](13-douyin-pipeline.md).

- **Dedupe**: khóa `(account_id, youtube_video_id)` hoặc `(account_id, douyin_aweme_id)` trên bảng `VideoUpload`. Đã **succeeded** → bỏ qua (đếm skipped dup). Đã **failed** ≥ 3 lần → bỏ qua (skipped exhausted).
- **Caption**: template trong DB, render bằng `api/services/caption.py` (biến whitelist, hashtag dedupe, tối đa 2200 ký tự). Worker gọi `render_caption` trước mỗi lần gọi adapter trong tick; kết quả lưu vào `VideoUpload.rendered_caption` cho bản ghi tương ứng.
- **Tùy chọn nâng cao** (giống upload): `allow_comment`, `allow_duet`, `allow_stitch`, `visibility_type` — lưu trong `upload_options_json`.
- **`max_duration_seconds`** (optional): yt-dlp **bỏ qua** video dài hơn `3 × N` giây; nếu tải, **cắt** 0…N giây (native, không shell ffmpeg riêng). Giới hạn file **500MB** mỗi lần tải.
- **`upload_delay_minutes`** (0–30, mặc định 0): **Nghỉ giữa upload** — số phút scheduler `sleep` giữa **mỗi lần** upload TikTok thực hiện trong cùng một tick (áp dụng sau mỗi bản ghi upload thành công hoặc thất bại). `0` = hành vi cũ, không nghỉ. Giảm rủi ro bị TikTok coi là spam khi một tick đẩy nhiều video/account liên tiếp. Ví dụ: `fetch_count=10`, mỗi video 1 account, `upload_delay_minutes=5` → thời gian chờ upload khoảng **9 × 5 = 45** phút (cộng thời gian tải/upload thực tế). **LockHeartbeat** (mỗi 5 phút) vẫn gia hạn `lock_until` trong lúc tick dài.
- **Đĩa**: kiểm tra free trên `/tmp` — nếu **< 1 GB**, bỏ qua tick, `PipelineRun.error_text = low_disk`.

### Lặp lại & thời điểm bắn

- `first_fire_at` / `next_fire_at` (UTC). Khi **scheduled** tick xong, advance:  
  `next_fire_at = max(prev_next + interval, now + interval)` — tránh “bão tick” sau downtime dài.
- **Run now** (`POST .../run-now`): **không** lùi/tiến `next_fire_at`; chỉ xử lý cờ `run_now_requested_at`.
- **Pause**: `state=paused` — worker **không** claim. **Resume**: `state=active`, `next_fire_at=now`. Resume **không** bypass `lock_until`.

### Khóa & chạy ngay (chống race)

- Worker claim: CAS set `lock_until = now + 10 phút`.
- **LockHeartbeat**: thread cứ **5 phút** bump `lock_until` (CAS theo giá trị lock hiện tại).
- `POST /run-now`:  
  - `lock_until > now` → **409** `PIPELINE_RUNNING`.  
  - Đã có `run_now_requested_at` → **409** `RUN_NOW_QUEUED` (tránh double-queue; user đợi tick xong rồi bấm lại).  
  - Khác → **202**, set `run_now_requested_at`.
- Kết thúc tick: xóa `run_now_requested_at` bằng **CAS** (chỉ khi giá trị khớp lúc claim) — request mới giữa chừng tick được giữ cho vòng sau.
- **Xóa pipeline** khi `lock_until > now` → **409**; cần Stop/pause và đợi tick xong.

### Orphan reaper & emergency finalize

- **Khởi động worker**: `reap_orphans()` — mọi `PipelineRun` `finished_at IS NULL` và `started_at < now - 15 phút` → `finished_at=now`, `error_text=worker crashed`, `failed_count=pulled_count`; đồng thời xóa `lock_until` hết hạn.
- **Exception không bắt** trong `pipeline_tick`: `_emergency_finalize` gắn `finished_at` / `error_text` cho run đang treo của pipeline đó; vẫn `advance_next_fire` + gỡ lock trong `finally`.

### Cảnh báo capacity (UI)

Nếu số pipeline **active** > **5**: banner vàng — gợi ý cân nhắc Stop bớt (không chặn).

---

## Caption template (pipeline)

| Biến | Ý nghĩa |
|------|---------|
| `{title}` | Tiêu đề video YouTube |
| `{title_hashtag}` | Các token `#...` extract từ title, nối bằng space |
| `{now}` | Thời điểm render, TZ `Asia/Ho_Chi_Minh`, format `YYYY-MM-DD HH:mm` |

- Biến lạ → **400** khi create/patch pipeline.
- Sau khi thay biến: **dedupe hashtag** không phân biệt hoa thường, **giữ lần xuất hiện đầu** theo thứ tự trong chuỗi.
- Output cắt **2200** ký tự.

---

## Telegram (tùy chọn, theo pipeline)

- **`telegram_chat_id`**: chỉ chấp nhận **chuỗi số** (ví dụ `21xxxxxx38`, `-1001234567890`). **Không** dùng `@username`, invite link hay `t.me/...` trong form — API trả **422** nếu format sai. Cách lấy ID chi tiết: [how_to_get_telegram_chat_id.md](how_to_get_telegram_chat_id.md).
- Trong modal pipeline (**Telegram**): **Tự động phát hiện** gọi `getUpdates` và liệt kê các chat loại **group / supergroup / channel** có trong buffer cập nhật (bấm chọn một dòng để điền `chat_id`). Chat **private** với bot **không** xuất hiện trong danh sách này — nếu cần, lấy ID bằng `getUpdates` (hoặc bot khác) rồi **dán tay** vào ô Chat ID.
- **Gửi tin test** gửi một tin nhắn thử qua Bot API (cùng token + `chat_id` đang nhập) để xác nhận trước khi lưu pipeline.
- Worker chỉ gọi `sendMessage` với `chat_id` + text — **không** hỗ trợ `message_thread_id` (forum topic); thông báo pipeline không nhắm vào topic con trong nhóm forum.
- Cần **cả** `telegram_bot_token` + `telegram_chat_id` hoặc **cả hai trống** (validate ở API).
- Token **không** xuất hiện trong GET list/detail — chỉ `telegram_bot_token_set: bool`. PATCH: token rỗng/không gửi → giữ cũ; xóa token: `clear_bot_token: true`.
- Sau **mỗi** tick (kể cả 0 video), gửi **một** tin nhắn plaintext (không `parse_mode`) tóm tắt số liệu: tên pipeline, giờ bắt đầu (HH:mm VN), Pulled / Uploaded / Skipped / Failed, và dòng Error nếu `PipelineRun.error_text`. Chi tiết từng URL nằm trong **`summary_text`** trên DB / UI drill-down, không nhất thiết lặp đủ trong Telegram (triển khai hiện tại ưu tiên gọn).
- Gửi lỗi Telegram **không** làm fail tick.

---

## API Pipelines (tóm tắt)

| Method | Path | Mô tả |
|--------|------|--------|
| GET | `/api/pipelines?state=active\|paused` | List (không có `telegram_bot_token`, có `telegram_bot_token_set`) |
| POST | `/api/pipelines` | Tạo; mặc định `state=active`. Muốn tạo paused → PATCH ngay sau đó. |
| GET | `/api/pipelines/{id}` | Chi tiết |
| PATCH | `/api/pipelines/{id}` | Sửa; Resume set `next_fire_now` trong server khi `paused`→`active` |
| DELETE | `/api/pipelines/{id}` | Xóa; CASCADE `PipelineRun`; `VideoUpload` giữ lại (`pipeline_id`/`run_id` → NULL) |
| POST | `/api/pipelines/{id}/run-now` | 202 hoặc 409 (xem trên) |
| GET | `/api/pipelines/{id}/runs?page=&size=` | Lịch sử tick; `size` ≤ **200**, mặc định 50 |
| GET | `/api/pipelines/{id}/runs/{run_id}/videos` | Tối đa **500** bản ghi `VideoUpload` của tick đó |

**Pagination**: `page` bắt đầu 1; kích thước trang tối đa **200** (runs và `/api/videos`).

---

## UI Pipelines

- **+ New pipeline** / **Edit** modal: Basic, Trigger (interval + datetime bắt đầu), Content (`fetch_count`, **Nghỉ giữa upload** (`upload_delay_minutes`), `max_duration_seconds`), Caption, Advanced (collapsible), Accounts (multi-select session hợp lệ), Telegram.
- Bảng: Run now, Stop/Resume, Edit, Delete (modal).
- **Drill-down**: mở hàng pipeline → lazy load `runs`; mở hàng run → video con. Poll ~5s khi có run chưa `finished_at`.

---

## Scheduler internals (cập nhật)

`scheduler/worker.py`:

```python
def tick() -> bool:
    # Pass 1 — single-shot
    with Session(engine) as s:
        reclaim_stale(s)
        claimed = claim_next_due(s)
    if claimed:
        hb = Heartbeat(claimed.id)
        hb.start()
        try:
            ok, msg = run_one(claimed)
        finally:
            hb.stop()
        finalize(claimed.id, ok, msg)
        return True

    # Pass 2 — pipeline
    return pipeline_tick()
```

`loop` gọi `reap_orphans()` **một lần** khi khởi động, sau đó `tick()` + `sleep(30)` khi idle.

---

## Time zone

Datetime pipeline trong DB: **UTC**. UI convert khi hiển thị. Caption `{now}` cố định **Asia/Ho_Chi_Minh**.

---

**Next:** [08-videos](08-videos.md)
