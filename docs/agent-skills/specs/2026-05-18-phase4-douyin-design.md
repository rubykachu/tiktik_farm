# Spec: Phase 4 — Tích hợp Douyin làm nguồn dữ liệu

**Ngày:** 2026-05-18  
**Branch:** feat/phase4  
**Tác giả:** Minh Tang

---

## Mục tiêu

Bổ sung Douyin (抖音) như một nguồn video thứ hai bên cạnh YouTube. Hệ thống tải video từ trang creator Douyin, sau đó upload lên TikTok theo cùng pipeline flow hiện tại.

**Người dùng hưởng lợi:** Operator muốn đa dạng hóa nguồn nội dung — không phụ thuộc hoàn toàn vào YouTube.

**Thành công khi:** Pipeline chạy định kỳ, tải video mới từ Douyin creator profile → upload TikTok → ghi log → gửi Telegram alert như YouTube pipeline hiện tại.

---

## Giới hạn scope

- **Chỉ hỗ trợ `post` mode** (video đăng công khai của creator). Không hỗ trợ: like, mix, music, collect.
- **Ảnh/image-note bị skip** — chỉ lấy `.mp4`.
- **Proxy: không làm ở phase này** — làm sau cùng VPN.
- **Không lưu video trên server** — download → upload TikTok → xóa file. Chỉ giữ `douyin_dl.db`.
- **Browser fallback tắt** — số lượng lấy ít (1–3 video/lần), không cần pagination.
- **"Import from disk"** sẽ bị deprecated/xóa (task riêng, không trong phase này).

---

## Kiến trúc tổng quan

```
vendor/douyin-downloader/    ← git submodule (jiji262/douyin-downloader main)
                                bug fix = git submodule update --remote (không sửa code)

DB changes (additive migration):
  channel_pipelines  + source_type (default "youtube")
                     + douyin_cookie_id (nullable FK → douyin_cookies)
                     + max_duration_seconds (nullable int, áp dụng cả YouTube lẫn Douyin)

  video_uploads      + douyin_aweme_id (nullable str — khi != null = nguồn Douyin)

  NEW: douyin_cookies (id, name, cookie_json, created_at, updated_at)

api/services/douyin_channel.py   ← service mới (parallel với youtube_channel.py)
api/routers/douyin_cookies.py    ← CRUD douyin_cookies

scheduler/pipeline_worker.py     ← Pass 3 mới (Douyin Pipelines), chạy sau Pass 2 (YouTube)

webapp:
  /accounts  → tab "Douyin Cookies" mới (quản lý cookie)
  Pipeline form → dropdown "Nguồn" (YouTube/Douyin) + cookie selector khi chọn Douyin
```

---

## 1. Vendor Integration

```
vendor/douyin-downloader/   (git clone — không phải submodule, không có git remote)
```

**Clone lần đầu:**
```bash
git clone https://github.com/jiji262/douyin-downloader vendor/douyin-downloader
git -C vendor/douyin-downloader remote remove origin   # xóa remote — bảo mật dự án
```

- Không sửa bất kỳ file nào bên trong `vendor/douyin-downloader/`.
- Commit toàn bộ `vendor/douyin-downloader/` vào repo (không dùng submodule).
- `.gitignore` của project KHÔNG ignore `vendor/douyin-downloader/`.

**Khi cần cập nhật (bug fix upstream):**
```bash
make douyin-update
```
Script `scripts/update-douyin-vendor.sh`:
```bash
#!/usr/bin/env bash
set -e
VENDOR="vendor/douyin-downloader"
BACKUP_BRANCH="backup/douyin-vendor-$(date +%Y%m%d-%H%M%S)"

# Backup trạng thái hiện tại
git checkout -b "$BACKUP_BRANCH"
git add "$VENDOR" && git commit -m "chore(vendor): backup douyin-downloader before update"

# Fetch + merge từ upstream (thêm remote tạm, xóa ngay sau)
git -C "$VENDOR" init          # đảm bảo là git repo (nếu bị mất .git)
git -C "$VENDOR" remote add upstream https://github.com/jiji262/douyin-downloader
git -C "$VENDOR" fetch upstream main
git -C "$VENDOR" merge upstream/main --no-edit
git -C "$VENDOR" remote remove upstream

# Quay lại branch gốc, commit vendor mới
ORIG_BRANCH=$(git rev-parse --abbrev-ref HEAD | tail -1)   # nhánh trước checkout
git checkout -
git add "$VENDOR"
git commit -m "chore(vendor): update douyin-downloader from upstream"
echo "Done. Backup: $BACKUP_BRANCH"
```

- Requirements: `vendor/douyin-downloader/requirements.txt` cài trong Docker image (thêm vào `Dockerfile` của `api` + `scheduler`).

---

## 2. Database Schema

### 2a. Bảng mới: `douyin_cookies`

```sql
CREATE TABLE douyin_cookies (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,     -- label do user đặt, ví dụ "Account chính"
    cookie_json_encrypted TEXT NOT NULL,   -- Fernet ciphertext (api.services.secrets)
    created_at           DATETIME NOT NULL,
    updated_at           DATETIME NOT NULL
);
```

**SQLModel:**
```python
class DouyinCookie(SQLModel, table=True):
    __tablename__ = "douyin_cookies"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    cookie_json_encrypted: str             # Fernet ciphertext, cùng key với LLM API keys
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
```

**Encryption pattern** (dùng lại `api.services.secrets`):
- Lưu: `secrets.encrypt(json.dumps(cookie_dict))` → `cookie_json_encrypted`
- Đọc: `json.loads(secrets.decrypt(row.cookie_json_encrypted))`
- API `GET /api/douyin-cookies` KHÔNG trả `cookie_json_encrypted` — chỉ trả `id, name, created_at`.

### 2b. Migration: `channel_pipelines`

Thêm 2 column nullable (migration additive, không breaking):

| Column | Type | Default | Ghi chú |
|---|---|---|---|
| `source_type` | TEXT | `"youtube"` | `"youtube"` hoặc `"douyin"` |
| `douyin_cookie_id` | INT nullable | NULL | **Không FK constraint** — chỉ lưu reference ID |

> **Không dùng FK constraint** cho `douyin_cookie_id`: khi cookie bị xóa, pipeline giữ nguyên (không cascade, không SET NULL tự động). Application layer xử lý: nếu `douyin_cookie_id` trỏ tới cookie không tồn tại → hiển thị cảnh báo "Cookie không còn tồn tại" trong UI, pipeline tạm dừng cho đến khi user chọn cookie mới.

> `max_duration_seconds` **đã tồn tại** trong model `ChannelPipeline` (Optional[int], default=None) — không cần migration. Áp dụng cho cả YouTube (service layer) lẫn Douyin (post-download check).

### 2c. Migration: `video_uploads`

Thêm 1 column nullable:

| Column | Type | Default | Ghi chú |
|---|---|---|---|
| `douyin_aweme_id` | TEXT nullable | NULL | aweme_id từ Douyin. Khi != NULL = nguồn Douyin |

Dedup Douyin: check `VideoUpload.douyin_aweme_id == aweme_id AND account_id == X AND status == "succeeded"`.

---

## 3. Cookie Management

### Cấu trúc cookie Douyin

User export từ browser dùng **Cookie-Editor extension** (cùng tool với TikTok), lấy từ `www.douyin.com`. Output = JSON array chuẩn Chrome DevTools. Ta parse lấy các key sau:

| Key | Bắt buộc |
|---|---|
| `msToken` | Khuyến nghị |
| `ttwid` | **Bắt buộc** — dùng để validate |
| `odin_tt` | Khuyến nghị |
| `passport_csrf_token` | Khuyến nghị |
| `sid_guard` | Khuyến nghị |

**Validation:** phải có `ttwid` với value không rỗng (tương tự `sessionid` với TikTok).

### Service: `api/services/douyin_cookie_import.py`

```python
def parse_cookie_editor_json(raw_bytes: bytes) -> dict:
    """Parse Cookie-Editor JSON array → dict {msToken, ttwid, odin_tt, ...}.
    Raises DouyinCookieImportError nếu thiếu ttwid hoặc malformed."""

def to_downloader_yaml_dict(cookie_dict: dict) -> dict:
    """Chuyển dict sang format config.yml của douyin-downloader."""
```

Reuse pattern của `cookie_import.py` (size limit, JSON parse, error handling) nhưng logic hoàn toàn riêng.

### API: `api/routers/douyin_cookies.py`

```
GET    /api/douyin-cookies          — list tất cả (id, name, created_at — không trả cookie_json)
POST   /api/douyin-cookies          — upload Cookie-Editor JSON + name → tạo mới
PUT    /api/douyin-cookies/{id}     — re-upload cookie file (cập nhật cookie_json)
DELETE /api/douyin-cookies/{id}     — xóa (cho phép xóa dù pipeline vẫn reference ID này)
```

### UI: Tab "Douyin Cookies" trong `/accounts`

- Tab mới cạnh tab TikTok accounts hiện tại.
- Table: Name | Ngày tạo | Actions (Cập nhật cookie / Xóa).
- Button "Thêm cookie": mở modal → nhập Name + upload file JSON.
- Modal "Cập nhật cookie": chỉ upload file JSON mới (name giữ nguyên).
- Xóa: confirm dialog (cảnh báo "Pipeline đang dùng cookie này sẽ không chạy được cho đến khi chọn cookie mới"), cho phép xóa.

---

## 4. Pipeline — Mở rộng ChannelPipeline

### UI: `PipelineFormModal`

Thêm field **"Nguồn video"** (dropdown, đầu form):

```
[ YouTube ▼ ]   hoặc   [ Douyin ▼ ]
```

Khi chọn **YouTube**: form giữ nguyên như hiện tại.  
Khi chọn **Douyin**:
- Label "URL kênh YouTube" → "URL trang creator Douyin" (ví dụ: `https://www.douyin.com/user/MS4wLj...`)
- Ẩn: (không có field nào khác cần ẩn)
- Hiện thêm: dropdown **"Cookie Douyin"** (chọn từ danh sách đã lưu) + link "Thêm cookie mới" → mở modal upload.
- `max_duration_seconds` field (optional, cả hai nguồn): nhập số giây, bỏ trống = không giới hạn.

### Validation bổ sung (API layer)

- `source_type == "douyin"` → `douyin_cookie_id` phải có giá trị (không null).
- `source_type == "youtube"` → `douyin_cookie_id` phải null.
- URL validation: YouTube URL check hiện tại chỉ chạy khi `source_type == "youtube"`.

---

## 5. Service: `api/services/douyin_channel.py`

```python
from dataclasses import dataclass

@dataclass
class DownloadedVideo:
    aweme_id: str
    file_path: str       # đường dẫn tuyệt đối tới file .mp4
    title: str
    description: str     # từ _data.json, dùng cho LLM caption rewrite
    duration_seconds: int  # từ _data.json

class DouyinDownloadError(Exception): pass
class DouyinVideoTooLong(Exception): pass

def download_creator_posts(
    creator_url: str,
    fetch_count: int,
    cookie_dict: dict,
    output_dir: str,
    db_path: str,
    max_duration_seconds: Optional[int] = None,
) -> list[DownloadedVideo]:
    """
    1. Tạo temp config.yml với incremental=true, number.post=fetch_count
    2. Chạy: python vendor/douyin-downloader/run.py -c {config_path}
    3. Scan output_dir tìm *_data.json → parse aweme_id, title, desc, duration
    4. Tìm .mp4 tương ứng cho mỗi aweme
    5. Filter: bỏ qua aweme không có .mp4 (image-note)
    6. Trả về list[DownloadedVideo]
    """

def cleanup_aweme_files(output_dir: str, aweme_id: str) -> None:
    """Xóa tất cả files của một aweme (mp4, jpg, json, mp3) sau khi upload xong."""
```

**Config YAML được generate (per pipeline run):**
```yaml
link:
  - {creator_url}
path: {output_dir}/
mode: [post]
number:
  post: {fetch_count}
increase:
  post: true          # chỉ tải video mới (chưa có trong douyin_dl.db)
thread: 3
retry_times: 3
database: true
database_path: {db_path}   # /data/douyin_dl.db — persist qua các lần chạy
music: false
cover: false
avatar: false
json: true              # cần _data.json để lấy metadata
folderstyle: false      # flat output để dễ scan
browser_fallback:
  enabled: false        # tắt — lấy ít video, không cần pagination
progress:
  quiet_logs: true
cookies:
  msToken: {cookie_dict.msToken}
  ttwid: {cookie_dict.ttwid}
  odin_tt: {cookie_dict.odin_tt}
  passport_csrf_token: {cookie_dict.passport_csrf_token}
  sid_guard: {cookie_dict.sid_guard}
```

**Post-download duration filter:**
- Đọc `{aweme_id}_data.json` → field `aweme["video"]["duration"]` (milliseconds, xác nhận từ source `core/downloader_base.py`).
- Nếu `duration_seconds > max_duration_seconds` → `cleanup_aweme_files()` → log skip → không trả về video đó.

**Cleanup rule:**
- Sau khi upload thành công (hoặc skip vì quá dài) → `cleanup_aweme_files()` xóa tất cả files của aweme đó.
- `douyin_dl.db` KHÔNG xóa — giữ để incremental dedup hoạt động lần sau.
- Temp config YAML xóa ngay sau khi subprocess kết thúc.

---

## 6. Scheduler: Pass 3 — Douyin Pipelines

Trong `scheduler/pipeline_worker.py`, thêm `process_douyin_pipelines(session)` được gọi sau pass 2.

**Flow:**

```
1. Query: DouyinPipeline WHERE source_type="douyin" AND state="active" AND next_fire_at <= now()
2. Với mỗi pipeline:
   a. Load cookie từ DB → parse dict
   b. Tạo temp output_dir = /data/douyin_tmp/{pipeline.id}_{timestamp}/
   c. Gọi douyin_channel.download_creator_posts(...)
   d. Với mỗi DownloadedVideo:
      - Check dedup: VideoUpload WHERE douyin_aweme_id=aweme_id AND status="succeeded" → skip nếu đã upload
      - Với mỗi account trong pipeline:
          i.  Caption: LLM rewrite từ video.description (giống YouTube flow)
          ii. Upload: tiktok_adapter.upload(account, video.file_path, caption, upload_opts)
          iii. Ghi VideoUpload (douyin_aweme_id=aweme_id, youtube_video_id="")
          iv. cleanup_aweme_files() sau khi tất cả accounts đã upload video đó
   e. Update pipeline.next_fire_at
   f. Ghi PipelineRun (reuse bảng pipeline_runs nếu FK cho phép, hoặc tạo bảng riêng nếu cần)
3. Telegram summary: giống YouTube pipeline (reuse existing telegram service)
```

**Error handling:**
- Cookie expired (HTTP 401/403 từ douyin API) → ghi error vào PipelineRun → Telegram alert "Cookie Douyin hết hạn, cần cập nhật".
- Download fail → log, skip video đó, tiếp tục.
- Upload TikTok fail → ghi VideoUpload với `status="failed"` → tiếp tục account khác.

---

## 7. Commands

```bash
# Thêm submodule
git submodule add https://github.com/jiji262/douyin-downloader vendor/douyin-downloader

# Cập nhật khi có bug fix
make douyin-update
# = git submodule update --remote vendor/douyin-downloader && git add vendor/douyin-downloader && git commit

# Cài dependencies (trong Docker build)
pip install -r vendor/douyin-downloader/requirements.txt

# Dev / test
pytest tests/ -q
npm run build   # trong webapp/
```

---

## 8. Cấu trúc file mới

```
vendor/
  douyin-downloader/          ← git submodule

api/
  services/
    douyin_channel.py         ← download service
    douyin_cookie_import.py   ← parse Cookie-Editor JSON → dict
  routers/
    douyin_cookies.py         ← CRUD /api/douyin-cookies

webapp/src/
  components/
    DouyinCookieModal.tsx     ← modal upload/update cookie
  pages/
    Accounts.tsx              ← thêm tab "Douyin Cookies"
  api/
    douyin_cookies.ts         ← API client functions

data/
  douyin_dl.db                ← persist (không xóa)
  douyin_tmp/                 ← temp download dir (xóa sau mỗi run)
```

---

## 9. Testing Strategy

- **Unit:** `douyin_cookie_import.py` — test parse valid JSON, missing ttwid, malformed.
- **Unit:** `douyin_channel.py` — mock subprocess, test scan output dir, duration filter, cleanup.
- **Integration:** Pipeline worker pass 3 — mock douyin_channel, verify VideoUpload rows ghi đúng.
- **Manual:** Full end-to-end với cookie thật + creator URL thật trước khi ship.

---

## 10. Boundaries

**Always:**
- Xóa tất cả file video/ảnh/metadata sau khi pipeline run hoàn thành (giữ `douyin_dl.db`).
- Không sửa bất kỳ file nào trong `vendor/douyin-downloader/`.
- Validate `ttwid` khi import cookie Douyin.
- Log lỗi + Telegram alert khi cookie expired.

**Ask first:**
- Thay đổi schema `VideoUpload` ngoài việc thêm `douyin_aweme_id`.
- Thêm bất kỳ mode Douyin nào khác ngoài `post`.
- Enable `browser_fallback`.

**Never:**
- Commit `douyin_dl.db` vào git.
- Lưu raw cookie values vào log.
- Tắt incremental mode (`increase.post: false`) trong production config.

---

## 11. Open Questions (đã resolve)

| Câu hỏi | Quyết định |
|---|---|
| Tách bảng hay mở rộng? | Mở rộng `channel_pipelines` + `video_uploads` |
| Subprocess hay REST server mode? | Subprocess |
| max_duration_seconds? | Post-download check từ `_data.json` |
| Proxy? | Phase sau, cùng VPN |
| Import from disk? | Deprecated — task riêng |

---

## 12. Success Criteria

- [ ] Pipeline `source_type="douyin"` chạy định kỳ, tải 1–3 video mới từ creator Douyin
- [ ] Video upload thành công lên TikTok, ghi `VideoUpload` với `douyin_aweme_id`
- [ ] Incremental: video đã upload không bị tải lại lần sau
- [ ] Ảnh/image-note bị skip, không gây lỗi
- [ ] Video quá `max_duration_seconds` bị skip + xóa file
- [ ] Tất cả file tạm bị xóa sau mỗi run (chỉ `douyin_dl.db` còn lại)
- [ ] Cookie expired → Telegram alert rõ ràng
- [ ] UI: tạo/sửa/xóa Douyin cookie; tạo pipeline Douyin từ form hiện tại
- [ ] Không có code nào trong `vendor/douyin-downloader/` bị sửa
