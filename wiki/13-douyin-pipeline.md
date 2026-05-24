# 13 — Douyin Pipeline

[← Index](README.md) | [← 12-settings](12-settings.md)

Mở rộng pipeline để hỗ trợ **nguồn Douyin** (抖音) bên cạnh YouTube đã có. Thay vì yt-dlp, scheduler dùng vendor **douyin-downloader** chạy qua subprocess.

---

## 1. Tổng quan

| | YouTube pipeline | Douyin pipeline |
|---|---|---|
| `source_type` | `youtube` | `douyin` |
| Downloader | yt-dlp (HTTP + auth nội bộ) | douyin-downloader subprocess (XBogus/ABogus signing + cookie) |
| Xác thực | Không cần | `DouyinCookie` — cookies Douyin thật (ttwid, msToken, odin_tt…) |
| Dedup key | `(account_id, youtube_video_id)` | `(account_id, douyin_aweme_id)` |
| Thư mục tạm | `/tmp/pipeline_dl_xxx/` | `/data/douyin_tmp/{pipeline_id}_xxx/` |
| `max_duration_seconds` | Cắt sau khi tải (ffmpeg) | Lọc trước khi upload (không tải lại) |
| Metadata title/desc | YouTube title + description | `desc` field trong `*_data.json` |
| Tên file tạm | `pre-processed.mp4` | `{date}_{aweme_id}.mp4` |

---

## 2. DouyinCookie — quản lý cookie xác thực

Douyin API không public — mỗi request cần cookie session hợp lệ từ tài khoản đã đăng nhập. Cookie lưu mã hóa Fernet (cùng cơ chế với LLM API key).

### 2.1. Các trường cookie cần thiết

| Cookie | Mô tả |
|---|---|
| `ttwid` | Session identity token |
| `msToken` | Short-lived request token (vendor tự refresh nếu cần) |
| `odin_tt` | Device/user binding |
| `passport_csrf_token` | CSRF protection |
| `sid_guard` | Login session guard |

### 2.2. Lấy cookie từ browser

1. Đăng nhập Douyin trên Chrome/Firefox.
2. Mở DevTools → tab **Application** → **Cookies** → `douyin.com`.
3. Copy từng giá trị cookie ở trên.
4. Vào **Settings → Douyin Cookies** trong app → **Add cookie** → paste vào từng ô.

Cookie Douyin hết hạn sau **~30 ngày**. Khi hết hạn, vendor trả lỗi và pipeline tick sẽ ghi `error_text`. Cần vào Settings → Edit cookie → update giá trị mới.

### 2.3. API

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/douyin-cookies` | List (không trả plaintext cookie values) |
| POST | `/api/douyin-cookies` | Tạo mới |
| GET | `/api/douyin-cookies/{id}` | Chi tiết (không trả plaintext) |
| PATCH | `/api/douyin-cookies/{id}` | Cập nhật; ô trống → giữ giá trị cũ |
| DELETE | `/api/douyin-cookies/{id}` | Xóa; 409 nếu có pipeline đang dùng |

### 2.4. Schema DB

```sql
CREATE TABLE douyin_cookies (
  id INTEGER PRIMARY KEY,
  label VARCHAR(128) NOT NULL,
  ttwid_encrypted TEXT NOT NULL,
  ms_token_encrypted TEXT NOT NULL,
  odin_tt_encrypted TEXT NOT NULL,
  passport_csrf_token_encrypted TEXT NOT NULL,
  sid_guard_encrypted TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

---

## 3. Tạo Douyin Pipeline

Trong modal **New Pipeline** → chọn **Source type: Douyin**. Các trường khác biệt so với YouTube:

| Trường | Ghi chú |
|---|---|
| `channel_url` | URL trang profile creator, vd `https://www.douyin.com/user/MS4wLjAB...` |
| `douyin_cookie_id` | **Bắt buộc** khi `source_type=douyin`. Chọn từ danh sách cookie đã lưu. |
| `fetch_count` | Số video mới nhất cần pull mỗi tick. Nên đặt ≥ số video creator post trong 1 interval để không bỏ sót. |
| `max_duration_seconds` | Lọc video dài hơn N giây trước khi upload (không tải lại). |

Các trường còn lại (`interval`, `caption_template`, `account_ids`, `upload_delay_minutes`, Telegram, LLM…) hoạt động y hệt YouTube pipeline.

### Validation đặc biệt

- PATCH `douyin_cookie_id=null` trên Douyin pipeline → 422 (`Douyin pipeline requires a cookie`).
- Không thể đổi `source_type` sau khi tạo.

---

## 4. Cơ chế download

### 4.1. Vendor subprocess

Scheduler gọi `douyin_channel.download_creator_posts()`, hàm này:

1. Tạo temp config YAML (`/tmp/douyin_cfg_xxx.yml`) với cookies, output path, fetch_count.
2. Chạy `python vendor/douyin-downloader/run.py -c <config>` qua `subprocess.Popen`.
3. Chờ tối đa **3600 giây** (timeout cho video dài như series compilation 5K).
4. Sau khi vendor kết thúc: scan `output_dir/**/*_data.json` để tìm file đã tải.
5. Xóa config YAML (luôn, kể cả lỗi).

### 4.2. Cấu trúc thư mục output

Vendor tạo cây thư mục:

```
/data/douyin_tmp/{pipeline_id}_xxxx/
└── {author_sec_uid}/          ← folder creator (ASCII, không dùng nickname CJK)
    └── post/
        ├── 2026-05-15_{aweme_id}.mp4
        ├── 2026-05-15_{aweme_id}_data.json
        ├── 2026-05-09_{aweme_id}.mp4
        └── 2026-05-09_{aweme_id}_data.json
```

Config đặt `author_dir: sec_uid` và `filename_template: {date}_{id}` để tên file/folder là ASCII thuần — tránh CJK gây lỗi filesystem/tooling.

Thư mục `/data/douyin_tmp/` mount qua volume `./data:/data` → có thể kiểm tra file vật lý tại `data/douyin_tmp/` trên host trong lúc run.

### 4.3. Anti-bot (không cần giải captcha)

Vendor bypass captcha bằng 3 lớp:

1. **Cookies thật** từ browser đã đăng nhập → server Douyin nhận diện session hợp lệ.
2. **XBogus + ABogus signing** — mỗi API URL được ký bằng thuật toán reverse-engineered từ JS Douyin; server verify chữ ký trước khi trả data.
3. **msToken refresh** — vendor tự lấy msToken thật từ mssdk endpoint; fallback token ngẫu nhiên nếu thất bại.

Captcha chỉ trigger khi signature sai hoặc cookie expired.

### 4.4. Thứ tự video (newest first)

Douyin API trả post theo thứ tự **mới nhất trước** (reverse chronological). Với `fetch_count=N`, vendor lấy N video mới nhất. DB vendor là temp (tạo mới mỗi run) nên không có memory giữa các lần — mỗi tick luôn pull N video mới nhất. Dedup thật sự xảy ra ở tầng `VideoUpload` (xem mục 5).

---

## 5. Dedup và lifecycle VideoUpload

Dedup key: `(account_id, douyin_aweme_id)` — index `ix_video_upload_account_aweme` trên bảng `video_uploads`.

| Trạng thái | Hành vi |
|---|---|
| `douyin_aweme_id` chưa có trong DB | Tải + upload, tạo `VideoUpload` mới |
| Đã có, status `succeeded` | Skip (skipped dup) |
| Đã có, failed ≥ 3 lần | Skip (skipped exhausted) |

`aweme_id` đọc từ field `aweme_id` trong `*_data.json` — không derive từ tên file.

---

## 6. Caption và LLM

`desc` (mô tả video Douyin) được dùng làm `title` và `description` của `DownloadedVideo`. Khi pipeline dùng `{ai_title}`:

- Worker gọi LLM với `desc` làm input.
- LLM rewrite thành caption TikTok theo prompt.
- Cache theo `aweme_id` trong phạm vi tick (nhiều account đăng cùng video → 1 lần gọi LLM).

Xem [12-settings](12-settings.md) để cấu hình LLM model.

---

## 7. Sơ đồ data flow

```
User → POST /api/pipelines (source_type=douyin, douyin_cookie_id=N, …)
                │
                ▼
        INSERT ChannelPipeline(source_type=douyin, …)
                │
    scheduler tick — pass 2:
        claim_due_pipeline() — CAS lock
        LockHeartbeat thread
                │
                ▼
        INSERT PipelineRun(trigger_reason=scheduled|manual)
        decrypt DouyinCookie → cookie_dict
        douyin_channel.download_creator_posts(
            creator_url, fetch_count, cookie_dict,
            output_dir=/data/douyin_tmp/{id}_xxx/,
            max_duration_seconds
        )
            │
            ├─ write temp config YAML → /tmp/douyin_cfg_xxx.yml
            ├─ subprocess Popen: vendor/douyin-downloader/run.py -c config
            │   [vendor: XBogus sign → Douyin API → download .mp4 + _data.json]
            ├─ stream stdout → log "[douyin-dl] ..."
            └─ scan output_dir/**/*_data.json → list[DownloadedVideo]
                │
                ▼
        filter max_duration_seconds (nếu đặt)
        cleanup files của video quá dài
                │
        Với mỗi DownloadedVideo × mỗi account:
            dedupe (account_id, douyin_aweme_id) qua VideoUpload
            [optional] LLM rewrite desc → {ai_title}
            render_caption(template, title=desc)
            tiktok_adapter.upload_from_file(mp4_path, caption, …)
            INSERT VideoUpload(douyin_aweme_id=…, status=succeeded|failed)
            cleanup_aweme_files(output_dir, aweme_id)  ← giải phóng disk sớm
                │
                ▼
        shutil.rmtree(output_dir)  ← dọn toàn bộ thư mục tạm
        UPDATE PipelineRun(counts, summary_text, finished_at)
        advance next_fire_at / clear run_now (CAS)
        release lock, stop heartbeat
        [optional] Telegram summary
```

---

## 8. Vận hành

### 8.1. Kiểm tra file đang tải

```bash
ls data/douyin_tmp/                     # xem run đang active
ls data/douyin_tmp/<pipeline_id>_xxx/   # xem creator folder
```

Thư mục tự xóa sau khi tick xong. Còn tồn tại → tick chưa xong hoặc crash.

### 8.2. Xem log vendor

```bash
docker compose logs scheduler -f | grep "\[douyin-dl\]"
```

Log vendor xuất hiện **sau khi** subprocess hoàn tất (không stream real-time).

### 8.3. Cookie hết hạn

Triệu chứng: `PipelineRun.error_text` chứa lỗi xác thực, `pulled_count=0`. Xử lý:

1. Đăng nhập lại Douyin trên browser.
2. Copy cookies mới.
3. Vào **Settings → Douyin Cookies** → Edit → cập nhật từng giá trị.
4. Tick tiếp theo sẽ tự dùng cookie mới.

### 8.4. Video quá dài (compilation)

Douyin có video compilation 1-2 giờ. Đặt `max_duration_seconds` để bỏ qua:

- Vendor tải xong → `_scan_output_dir` trả về duration từ JSON.
- Video vượt limit: `cleanup_aweme_files` xóa ngay, không upload.
- Timeout subprocess: **3600 giây** — đủ cho video ~30 phút. Compilation 2h cần tăng `_SUBPROCESS_TIMEOUT` trong `douyin_channel.py`.

---

## 9. Schema DB

```sql
-- Thêm vào channel_pipelines
ALTER TABLE channel_pipelines ADD COLUMN source_type VARCHAR(20) DEFAULT 'youtube';
ALTER TABLE channel_pipelines ADD COLUMN douyin_cookie_id INTEGER REFERENCES douyin_cookies(id);

-- Thêm vào video_uploads
ALTER TABLE video_uploads ADD COLUMN douyin_aweme_id VARCHAR(64);
CREATE INDEX ix_video_upload_account_aweme ON video_uploads (account_id, douyin_aweme_id);
```

---

## 10. Cài đặt / deploy

Thêm vendor vào base image:

```dockerfile
# docker/base.Dockerfile
COPY vendor/douyin-downloader /app/vendor/douyin-downloader
RUN pip install -r /app/vendor/douyin-downloader/requirements.txt
```

Sau khi pull code mới:

```bash
make rebuild-base   # rebuild tiktok-base (chỉ cần lần đầu sau khi thêm vendor)
make deploy         # build + up tất cả service
```

Không cần rebuild-base cho mỗi deploy thông thường — vendor đã baked vào base image.

---

**Next:** [Index](README.md)
