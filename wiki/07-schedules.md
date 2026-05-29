# 07 — Lịch & Pipeline (Phase 1+4)

[← Index](README.md) | [← 06-upload](06-upload.md)

Trang **`/schedules`** hiển thị bảng **Pipelines**.

Scheduler (`scheduler/worker.py`) mỗi vòng **30 giây** gọi `pipeline_tick()` (`scheduler/pipeline_worker.py`).

---

## Pipeline — khái niệm `ChannelPipeline`

**Pipeline** = cấu hình tự động: **URL kênh** → downloader lấy **fetch_count** video mới nhất mỗi "tick" → upload lên **tất cả** `account_ids` đã chọn (lần lượt).

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
- **Đĩa**: kiểm tra free trên `PIPELINE_TMP_DIR` (mặc định: system `/tmp`) — nếu **< 1 GB**, bỏ qua tick, `PipelineRun.error_text = low_disk`.

### Lặp lại & thời điểm bắn

- `first_fire_at` / `next_fire_at` (UTC). Khi **scheduled** tick xong, advance:  
  `next_fire_at = max(prev_next + interval, now + interval)` — tránh "bão tick" sau downtime dài.
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
- Trong modal pipeline (**Telegram**): **Tự động phát hiện** gọi `getUpdates` và liệt kê các chat loại **group / supergroup / channel / private** có trong buffer cập nhật (bấm chọn một dòng để điền `chat_id`).
  - Nếu buffer trống và **webhook đang bật**: lỗi "Xóa webhook trước, gửi một tin nhắn cho bot, rồi thử lại."
  - Nếu buffer trống và không webhook: lỗi "Hãy gửi một tin nhắn cho bot rồi bấm Auto-detect lại."
- **Gửi tin test** gửi một tin nhắn thử qua Bot API (cùng token + `chat_id` đang nhập) để xác nhận trước khi lưu pipeline.
- Worker chỉ gọi `sendMessage` với `chat_id` + text — **không** hỗ trợ `message_thread_id` (forum topic); thông báo pipeline không nhắm vào topic con trong nhóm forum.
- Cần **cả** `telegram_bot_token` + `telegram_chat_id` hoặc **cả hai trống** (validate ở API).
- Token **không** xuất hiện trong GET list/detail — chỉ `telegram_bot_token_set: bool`. PATCH: token rỗng/không gửi → giữ cũ; xóa token: `clear_bot_token: true`.
- Sau **mỗi** tick (kể cả khi crash), gửi **một** tin nhắn plaintext (không `parse_mode`) gồm:
  - Header: tên pipeline, giờ bắt đầu (HH:mm VN)
  - Tóm tắt: ⬇️ Tải xuống / ✅ Đăng tải / ⏭️ Bỏ qua (trùng/hết) / ⚠️ Thất bại — hoặc ❌ Lỗi hệ thống nếu pipeline crash
  - Chi tiết từng video: tiêu đề + link TikTok (thành công) hoặc lý do lỗi (thất bại)
  - Soft cap **3500 ký tự** cho phần video — phần còn lại hiển thị "... và N video khác"; hard cap **4096 ký tự** toàn tin.
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

- **+ New pipeline** / **Edit** modal chia 4 section:
  - **Thông tin cơ bản**: tên pipeline + lịch chạy
    - *Lặp lại mỗi* N `ngày` hoặc `phút` (**không còn `hours`**)
    - *Chạy lúc* HH:MM — chỉ hiện khi unit là `days`
    - Checkbox **"Chạy nhiều lần trong ngày?"** — khi bật hiện thêm *Chạy đến lúc* + *Lặp lại mỗi N phút* (thay thế field "Window end" cũ). Tắt → tự clear 2 field. Edit pipeline có window đã set → checkbox tự bật.
  - **Tải xuống**: source type (chỉ hiện khi tạo mới), Douyin cookie (nếu Douyin), URL kênh, số video mỗi lần chạy, thời gian chờ giữa các video, max duration
  - **Đăng tải**: danh sách tài khoản TikTok, caption template, AI rewrite (collapsible), tùy chọn nâng cao — comment/duet/stitch/riêng tư (collapsible)
  - **Thông báo**: Telegram bot token + chat ID + auto-detect + gửi tin test
- Validation chạy khi **submit** (không phải khi blur từng field).
- Pipeline cũ lưu `interval_unit=hours` → khi mở Edit tự coerce thành `days`.
- Bảng: Run now, Stop/Resume, Edit, Delete (modal).
- **Drill-down**: mở hàng pipeline → lazy load `runs`; mở hàng run → video con. Poll ~5s khi có run chưa `finished_at`.
- **Xem lỗi chi tiết**: click vào chip lỗi hoặc cột error của run → modal hiện full stack trace (scrollable).

---

## Scheduler internals

`scheduler/worker.py` chạy `loop()` với `ThreadPoolExecutor` — số luồng song song đọc từ `SCHEDULER_CONCURRENCY`:

```python
def loop(poll_interval: float = 30.0) -> None:
    concurrency = max(1, min(int(os.getenv("SCHEDULER_CONCURRENCY", "1")), 20))
    reap_orphans()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        pending = set()
        while True:
            # reap done futures, log exceptions
            ...
            # fill up to `concurrency` slots
            for _ in range(concurrency - len(pending)):
                pending.add(executor.submit(pipeline_tick))
            time.sleep(poll_interval)
```

- `reap_orphans()` gọi **một lần** khi khởi động.
- Mỗi poll cycle: fill đủ `concurrency` slot, sleep `SCHEDULER_POLL_SECONDS` (mặc định 30s).
- `SCHEDULER_CONCURRENCY=1` (mặc định) → serial, behavior giống phiên bản cũ.
- Giá trị 0 / âm / không hợp lệ → tự clamp về 1, log warning.
- Mỗi `pipeline_tick()` thread-safe: atomic CAS claim `lock_until` trên DB, không shared state giữa các luồng.
- **Pipeline timeout**: `run_pipeline()` chạy trong thread con với `fut.result(timeout=MAX_PIPELINE_SECONDS)`. Nếu quá giờ → `_emergency_finalize` → Telegram notification → tick kết thúc; thread cũ tiếp tục chạy ngầm đến khi tự xong hoặc process restart.

### Env vars scheduler

| Env var | Mặc định | Mô tả |
|---------|---------|-------|
| `SCHEDULER_CONCURRENCY` | `1` | Số pipeline chạy song song (1–20) |
| `SCHEDULER_POLL_SECONDS` | `30` | Giây giữa mỗi poll cycle |
| `MAX_PIPELINE_SECONDS` | `1800` | Timeout một lần chạy pipeline (giây). Quá giờ → finalize + Telegram, tick kết thúc. |
| `PIPELINE_TMP_DIR` | system `/tmp` | Thư mục lưu video YouTube tạm. Set sang real disk để tránh ăn RAM (tmpfs) khi chạy concurrent. |
| `DOUYIN_TMP_DIR` | `{tmp}/douyin_tmp` | Thư mục lưu video Douyin tạm. |

> **Capacity guide** (sau khi set `PIPELINE_TMP_DIR` sang real disk):
> - 1 vCPU / 2 GB RAM → `SCHEDULER_CONCURRENCY=2`
> - 2 vCPU / 4 GB RAM → `SCHEDULER_CONCURRENCY=4–5`

---

## Time zone

Datetime pipeline trong DB: **UTC**. UI convert khi hiển thị. Caption `{now}` cố định **Asia/Ho_Chi_Minh**.

---

**Next:** [08-videos](08-videos.md)
