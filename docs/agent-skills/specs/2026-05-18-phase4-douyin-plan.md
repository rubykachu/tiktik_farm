# Plan: Phase 4 — Tích hợp Douyin

**Spec:** `docs/superpowers/specs/2026-05-18-phase4-douyin-design.md`  
**Branch:** `feat/phase4`

---

## Context

Hệ thống hiện tại chỉ có YouTube làm nguồn video. Phase 4 bổ sung Douyin (抖音) như nguồn thứ hai.
Vendor `jiji262/douyin-downloader` là layer trung gian — không bao giờ sửa source của nó.
6 task dưới đây theo thứ tự dependency; mỗi task là một vertical slice có thể test độc lập.

---

## Dependency graph

```
Task 1 (Vendor + DB schema)
  ├── Task 2 (Cookie service + router)
  │     └── Task 4 (Webapp: cookie tab)
  │           └── Task 5 (Pipeline form extension)
  ├── Task 3 (Douyin download service)
  └── Task 6 (Scheduler Pass 3) ← cần Task 2 + Task 3
```

---

## Task 1 — Vendor clone + DB schema

**Scope:** Clone vendor, migration, models. Foundation cho mọi thứ phía sau.

### Files touched
| File | Action |
|------|--------|
| `vendor/douyin-downloader/` | `git clone` + `git remote remove origin` (commit toàn bộ) |
| `scripts/update-douyin-vendor.sh` | New — backup branch + fetch upstream + remove remote |
| `Makefile` | Add `douyin-update` target |
| `api/models.py` | Add `DouyinCookie`; extend `ChannelPipeline` + `VideoUpload` |
| `api/db_migrations.py` | `apply_schema_patches()`: add `source_type`, `douyin_cookie_id` to `channel_pipelines`; add `douyin_aweme_id` to `video_uploads`; CREATE TABLE `douyin_cookies` |

### Model changes
```python
# DouyinCookie — new table
class DouyinCookie(SQLModel, table=True):
    __tablename__ = "douyin_cookies"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    cookie_json_encrypted: str          # Fernet via api.services.secrets
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

# ChannelPipeline — additive columns
source_type: str = Field(default="youtube", max_length=16)
douyin_cookie_id: Optional[int] = Field(default=None)   # no FK constraint

# VideoUpload — additive column
douyin_aweme_id: Optional[str] = Field(default=None, max_length=64)
```

### Migration pattern (follow db_migrations.py exactly)
```python
# apply_schema_patches():
# 1. PRAGMA table_info(channel_pipelines) → add source_type, douyin_cookie_id if missing
# 2. PRAGMA table_info(video_uploads) → add douyin_aweme_id if missing
# 3. sqlite_master check → CREATE TABLE IF NOT EXISTS douyin_cookies
```

### Acceptance
- `python -c "from api.models import DouyinCookie; print('ok')"` passes
- `apply_schema_patches()` idempotent (run twice, no error)
- `git log --oneline vendor/douyin-downloader` shows commits but `git -C vendor/douyin-downloader remote -v` is empty

### Verify
```bash
pytest tests/ -q
python -c "from api.db_migrations import apply_schema_patches; print('ok')"
```

---

## Task 2 — Cookie management (backend)

**Scope:** Service parse + encrypt Douyin cookies; CRUD router. Phụ thuộc Task 1.

### Files touched
| File | Action |
|------|--------|
| `api/services/douyin_cookie_import.py` | New — parse + validate Cookie-Editor JSON, encrypt |
| `api/schemas.py` | Add `DouyinCookieRead`, `DouyinCookieCreate` |
| `api/routers/douyin_cookies.py` | New — GET/POST/PUT/DELETE `/api/douyin-cookies` |
| `api/main.py` | Register `douyin_cookies` router |

### Service: `douyin_cookie_import.py`
```python
class DouyinCookieImportError(Exception): pass

def parse_cookie_editor_json(raw_bytes: bytes) -> dict:
    """Parse Cookie-Editor JSON array → dict {name: value}.
    Raises DouyinCookieImportError if:
    - size > 256KB, not JSON array, missing 'ttwid' with non-empty value
    """

def to_downloader_cookie_dict(cookie_dict: dict) -> dict:
    """Filter dict to only keys douyin-downloader config.yml needs."""
```

- Reuse size/structure validation pattern from `api/services/cookie_import.py`
- Validation key: `ttwid` (vs `sessionid` for TikTok)
- Store: `secrets.encrypt(json.dumps(cookie_dict))` → `cookie_json_encrypted`

### Router: `douyin_cookies.py`
```
GET    /api/douyin-cookies       → list [{id, name, created_at}] (no cookie_json_encrypted)
POST   /api/douyin-cookies       → multipart: file (JSON) + name (str) → DouyinCookieRead
PUT    /api/douyin-cookies/{id}  → multipart: file only (re-upload cookie)
DELETE /api/douyin-cookies/{id}  → 204 (no 409 check — no FK constraint)
```

### Schemas
```python
class DouyinCookieRead(SQLModel):
    id: int
    name: str
    created_at: datetime
    model_config = {"from_attributes": True}

class DouyinCookieCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
```

### Acceptance
- `POST /api/douyin-cookies` với valid Cookie-Editor JSON → 201, cookie_json_encrypted lưu vào DB
- `POST` thiếu `ttwid` → 422 rõ ràng
- `GET` không trả `cookie_json_encrypted`
- `DELETE` xóa thành công dù có pipeline dùng

### Verify
```bash
pytest tests/ -q
curl -X GET http://localhost:8000/api/douyin-cookies  # trả []
```

---

## Task 3 — Douyin download service

**Scope:** `douyin_channel.py` wrap subprocess douyin-downloader. Phụ thuộc Task 1 (vendor).

### Files touched
| File | Action |
|------|--------|
| `api/services/douyin_channel.py` | New — subprocess wrapper |
| `Dockerfile` (api + scheduler) | Add `pip install -r vendor/douyin-downloader/requirements.txt` |

### Service interface
```python
@dataclass
class DownloadedVideo:
    aweme_id: str
    file_path: str       # absolute path to .mp4
    title: str
    description: str
    duration_seconds: int   # aweme["video"]["duration"] // 1000

class DouyinDownloadError(Exception): pass

def download_creator_posts(
    creator_url: str,
    fetch_count: int,
    cookie_dict: dict,        # plaintext dict from secrets.decrypt
    output_dir: str,
    db_path: str,             # /data/douyin_dl.db
    max_duration_seconds: Optional[int] = None,
) -> list[DownloadedVideo]:
    """
    1. Write temp config.yml (increase.post=true, folderstyle=false, json=true,
       browser_fallback.enabled=false, database_path=db_path)
    2. subprocess: python vendor/douyin-downloader/run.py -c {config_path}
    3. Scan output_dir for *_data.json → parse aweme_id, title, desc, duration
    4. Match .mp4 files; skip awemes with no .mp4 (image-notes)
    5. Duration filter: if duration_seconds > max_duration_seconds → cleanup_aweme_files() + skip
    6. Return list[DownloadedVideo]
    7. Delete temp config.yml
    """

def cleanup_aweme_files(output_dir: str, aweme_id: str) -> None:
    """Delete all files matching {aweme_id}* in output_dir."""
```

**Important:** Config YAML is written to `tempfile.mktemp(suffix=".yml")`, deleted in `finally` block.
`db_path` = `/data/douyin_dl.db` — never deleted.
Duration field: `aweme_data["video"]["duration"]` (milliseconds) → `// 1000`.

### Acceptance
- Mock subprocess + sample `_data.json` → returns correct `DownloadedVideo` list
- Image-note aweme (no `.mp4`) → skipped without error
- `max_duration_seconds` filter → `cleanup_aweme_files()` called, not returned
- Config YAML deleted after run (even on error)

### Verify
```bash
pytest tests/test_douyin_channel.py -q
```

---

## Task 4 — Webapp: Douyin cookie tab

**Scope:** UI quản lý Douyin cookies. Phụ thuộc Task 2.

### Files touched
| File | Action |
|------|--------|
| `webapp/src/api/douyin_cookies.ts` | New — API client + types |
| `webapp/src/components/DouyinCookieModal.tsx` | New — add/update cookie modal |
| `webapp/src/pages/Accounts.tsx` | Add tab switcher (TikTok Accounts / Douyin Cookies) |

### `douyin_cookies.ts`
```ts
export interface DouyinCookie { id: number; name: string; created_at: string; }

export const DouyinCookies = {
  list: () => api.get<DouyinCookie[]>("/douyin-cookies").then(r => r.data),
  create: (file: File, name: string) => { /* FormData */ },
  update: (id: number, file: File) => { /* FormData PUT */ },
  remove: (id: number) => api.delete(`/douyin-cookies/${id}`),
};
```

### `DouyinCookieModal.tsx`
- Props: `{ open, onClose, cookie?: DouyinCookie | null }`
- Mode create: Name field (required) + file dropzone (accept `.json`, max 256KB)
- Mode update: file dropzone only (name hidden, greyed label)
- Same dropzone pattern as `ImportJsonModal.tsx` (react-dropzone)
- On success: `qc.invalidateQueries(["douyin-cookies"])` + `onClose()`

### `Accounts.tsx` tab switcher
```tsx
const [tab, setTab] = useState<"tiktok" | "douyin">("tiktok");

// Tab bar
<div className="flex gap-1 border-b mb-4">
  <button onClick={() => setTab("tiktok")} className={tab === "tiktok" ? "tab-active" : "tab"}>
    TikTok Accounts
  </button>
  <button onClick={() => setTab("douyin")} className={tab === "douyin" ? "tab-active" : "tab"}>
    Douyin Cookies
  </button>
</div>

{tab === "tiktok" && /* existing accounts table */}
{tab === "douyin" && /* douyin cookies table + DouyinCookieModal */}
```

Douyin tab: table `Name | Ngày tạo | Actions (Cập nhật / Xóa)`.
Xóa: `ConfirmDialog` reuse, body text "Pipeline đang dùng cookie này sẽ không chạy được cho đến khi chọn cookie mới".

### Acceptance
- `/accounts` → tab "TikTok Accounts" hiện table cũ không thay đổi
- Tab "Douyin Cookies" → list, add, update, delete cookie
- File > 256KB → error hiển thị ngay tại dropzone

### Verify
```bash
npm run build    # trong webapp/
# Manual: open http://localhost:3000/accounts, click tab Douyin Cookies
```

---

## Task 5 — Pipeline form extension

**Scope:** Thêm source_type + douyin_cookie_id vào PipelineFormModal + API schemas. Phụ thuộc Task 1, 2, 4.

### Files touched
| File | Action |
|------|--------|
| `api/schemas.py` | Extend `PipelineCreate` + `PipelineUpdate`: add `source_type`, `douyin_cookie_id`; validation logic |
| `api/routers/pipelines.py` | Cross-field validation: Douyin → cookie_id required; YouTube → cookie_id null; URL check gated by source_type |
| `webapp/src/components/PipelineFormModal.tsx` | Add source_type dropdown; conditional fields |

### API validation (pipelines.py)
```python
# On create/update, after merge:
if payload.source_type == "douyin":
    if not payload.douyin_cookie_id:
        raise HTTPException(422, "douyin_cookie_id required when source_type=douyin")
    # skip YouTube URL check
elif payload.source_type == "youtube":
    payload.douyin_cookie_id = None
    # run existing YouTube URL check
```

### PipelineFormModal Zod extension
```ts
source_type: z.enum(["youtube", "douyin"]).default("youtube"),
douyin_cookie_id: z.number().nullable().optional(),

// .refine(): source_type=douyin → douyin_cookie_id must not be null
```

### PipelineFormModal UI
- Add `source_type` `<select>` at top (before channel_url)
- `channel_url` label: `source_type === "douyin"` → "URL trang creator Douyin" else "URL kênh YouTube"
- When `source_type === "douyin"`: show `<select>` "Cookie Douyin" (options from `DouyinCookies.list`) + link "Thêm cookie mới" → open `DouyinCookieModal` inline
- `DouyinCookies.list()` called via `useQuery` inside modal (only when `source_type==="douyin"`)

### Acceptance
- Tạo pipeline YouTube → form cũ không thay đổi
- Chọn Douyin → label thay đổi, cookie selector hiện, URL check không block Douyin URL
- Submit Douyin pipeline mà không chọn cookie → validation error
- Existing YouTube pipelines render bình thường (source_type default "youtube")

### Verify
```bash
npm run build
# Manual: tạo pipeline YouTube, tạo pipeline Douyin
```

---

## Task 6 — Scheduler Pass 3

**Scope:** `process_douyin_pipelines()` trong `pipeline_worker.py`. Phụ thuộc Task 1, 2, 3.

### Files touched
| File | Action |
|------|--------|
| `scheduler/pipeline_worker.py` | Add imports + `process_douyin_pipelines()` + call in `pipeline_tick()` |

### Flow in `pipeline_worker.py`

**Import thêm:**
```python
from api.services import douyin_channel, douyin_cookie_import
from api.models import DouyinCookie
from api.services.secrets import decrypt
```

**`process_douyin_pipelines(session)` — gọi sau E5 Telegram trong `pipeline_tick()`:**
```python
def process_douyin_pipelines(session: Session) -> int:
    """Returns number of pipelines processed."""
    pipelines = session.exec(
        select(ChannelPipeline)
        .where(ChannelPipeline.source_type == "douyin")
        .where(ChannelPipeline.state == "active")
        .where(ChannelPipeline.next_fire_at <= now_utc())
        .where(or_(ChannelPipeline.lock_until == None, ChannelPipeline.lock_until <= now_utc()))
    ).all()

    for pipeline in pipelines:
        _run_douyin_pipeline(session, pipeline)
    return len(pipelines)
```

**`_run_douyin_pipeline(session, pipeline)` flow:**
```
1. Load DouyinCookie row → if missing → log error + Telegram "Cookie không còn tồn tại" + skip
2. cookie_dict = json.loads(secrets.decrypt(cookie_row.cookie_json_encrypted))
3. output_dir = /data/douyin_tmp/{pipeline.id}_{timestamp}/
4. Create PipelineRun row (trigger_reason="scheduled")
5. Call douyin_channel.download_creator_posts(pipeline.channel_url, pipeline.fetch_count,
       cookie_dict, output_dir, "/data/douyin_dl.db", pipeline.max_duration_seconds)
6. For each DownloadedVideo:
     a. Dedup: VideoUpload WHERE douyin_aweme_id=aweme_id AND status="succeeded" → skip if exists
     b. LLM rewrite caption (reuse _rewrite_with_llm())
     c. For each account_id in pipeline.account_ids_json:
          - upload: tiktok_adapter.upload(...)
          - _record_douyin_upload(pipeline_id, run_id, account_id, video, status, ...)
     d. douyin_channel.cleanup_aweme_files(output_dir, aweme_id)
7. _finalize_run(run.id, ...)
8. advance_next_fire(pipeline, "scheduled")
9. release_lock(pipeline.id)
10. send_telegram_summary(pipeline, run)
```

**`_record_douyin_upload()` — thin wrapper over existing `_record_upload()`:**
```python
VideoUpload(
    pipeline_id=pipeline_id,
    run_id=run_id,
    account_id=account_id,
    youtube_video_id="",          # NOT NULL, use empty string for Douyin
    youtube_url=pipeline.channel_url,
    youtube_title=video.title,
    douyin_aweme_id=video.aweme_id,
    status=status,
    ...
)
```

**Error: Cookie expired (HTTP 4xx from douyin subprocess stderr):**
- `DouyinDownloadError` with message containing "401"/"403" → Telegram alert "Cookie Douyin hết hạn"
- `_finalize_run(error_text="douyin_cookie_expired")`

**Error: Download fail (non-auth):**
- Log, set `error_text` on run, continue to next pipeline

### Acceptance
- Pipeline `source_type="douyin"` fires → downloads + uploads + `VideoUpload.douyin_aweme_id` populated
- Second run → same aweme_id → dedup skip (no re-upload)
- Image-note awemes → not returned by service → not in VideoUpload
- `douyin_dl.db` still exists after run; `douyin_tmp/` cleaned
- Cookie missing → Telegram alert, pipeline skipped, no crash

### Verify
```bash
pytest tests/ -q
# Integration: set up pipeline with source_type=douyin in DB, run pipeline_tick() with mocked download service
```

---

## Checkpoints

| After | Verify |
|-------|--------|
| Task 1 | `pytest tests/ -q` + migration idempotent check |
| Task 2 | Cookie CRUD via curl; `pytest tests/ -q` |
| Task 3 | `pytest tests/test_douyin_channel.py -q` |
| Task 4 | `npm run build` + manual tab check |
| Task 5 | `npm run build` + create Douyin pipeline end-to-end |
| Task 6 | Integration test + manual full pipeline run |

---

## Reusable code (don't rewrite)

| Component | Location | Use for |
|-----------|----------|---------|
| `secrets.encrypt/decrypt` | `api/services/secrets.py` | Douyin cookie encryption |
| `_rewrite_with_llm()` | `scheduler/pipeline_worker.py:~400` | Caption rewrite in Pass 3 |
| `send_telegram_summary()` | `scheduler/pipeline_worker.py:~650` | Pass 3 notifications |
| `advance_next_fire()` | `scheduler/pipeline_worker.py:~550` | Schedule advance after run |
| `release_lock()` | `scheduler/pipeline_worker.py:~570` | Lock release |
| `_finalize_run()` | `scheduler/pipeline_worker.py:~470` | PipelineRun completion |
| `apply_schema_patches()` pattern | `api/db_migrations.py:37` | Add new columns |
| `ImportJsonModal` dropzone pattern | `webapp/src/components/ImportJsonModal.tsx` | DouyinCookieModal |
| `ConfirmDialog` | `webapp/src/components/ConfirmDialog.tsx` | Delete confirm |
