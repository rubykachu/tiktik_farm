# SPEC — Schedule Log: title, ai_title, pushed_notify_at

## 1. Objective

Enrich the `/schedules` run-log expanded rows with:

1. **Video detail rows** (`VideosList`): show `title` (source video title) and `ai_title` (LLM-rewritten title, pre-template) as explicit columns.
2. **Run rows**: add `pushed_notify_at` to `PipelineRun` to record when Telegram notification fired; surface as a bell icon in the run row.

**Target user:** schedule operator reviewing run history in `/schedules`.

---

## 2. Design decisions

| Question | Decision |
|---|---|
| Where does `title` come from? | `source_title` on `VideoUpload` — already stored for YouTube. For Douyin, `_record_douyin_upload` currently drops the title; save `video.title` into `source_title` (same column, same semantic = "source video title"). |
| New DB column for `title`? | No. `source_title` already serves this role. Just fix the Douyin path to populate it. |
| Where does `ai_title` come from? | Computed in the worker (`ai_title` local var) but discarded — only `rendered_caption` (post-template) is saved. Add `ai_title TEXT` column on `video_uploads` and persist the pre-template rewritten title. |
| `ai_title` when LLM not used? | Store `NULL`. Render `—` in UI. |
| `pushed_notify_at` scope? | `PipelineRun` level — one notification per run. Set only on successful `telegram.send_message()`. |
| UI placement for bell? | Append a narrow bell-icon column after "Trạng thái" in `RunRow`. Day header row gets a matching spacer `<th/>`. |

---

## 3. Files to change

### Backend

| File | Change |
|---|---|
| `api/models.py` | `VideoUpload`: add `ai_title: Optional[str] = Field(default=None)`. `PipelineRun`: add `pushed_notify_at: Optional[datetime] = Field(default=None)`. |
| `api/db_migrations.py` | `apply_schema_patches`: patch `video_uploads` → `ADD COLUMN ai_title TEXT`; patch `pipeline_runs` → `ADD COLUMN pushed_notify_at TEXT`. Both idempotent. |
| `api/routers/pipelines.py` | `list_run_videos` response dict: add `"ai_title": v.ai_title`. `list_runs` response: `pushed_notify_at` auto-included if model field is exposed (verify). |
| `scheduler/pipeline_worker.py` | `_record_upload()` (YouTube): add `ai_title: Optional[str] = None` param, pass to `VideoUpload(...)`. `_record_douyin_upload()` (Douyin): add `ai_title` + `video_title` params, populate `source_title=video_title`. Call sites: pass `ai_title=ai_title` (or `None` when `use_ai_title=False`). `send_telegram_summary()`: after successful send, write `run.pushed_notify_at = now_utc()` to DB. |

### Frontend

| File | Change |
|---|---|
| `webapp/src/api/pipelines.ts` | `VideoUploadItem`: add `ai_title: string \| null`. `PipelineRunItem`: add `pushed_notify_at: string \| null`. |
| `webapp/src/components/PipelineRunsExpand.tsx` | `VideosList`: add **Title** + **AI Title** columns. `RunRow`: add bell icon column for `pushed_notify_at`. `DayGroupRow` header row: add matching spacer `<th/>`. |

---

## 4. Detailed change notes

### `_record_douyin_upload` signature

```python
def _record_douyin_upload(
    *,
    run_id: int,
    pipeline_id: int,
    account_id: int,
    aweme_id: str,
    status: str,
    tiktok_video_id: Optional[str] = None,
    tiktok_url: Optional[str] = None,
    rendered_caption: Optional[str] = None,
    ai_title: Optional[str] = None,       # NEW
    video_title: Optional[str] = None,    # NEW — stored as source_title
    attempt_count: int = 1,
    error_text: Optional[str] = None,
) -> None:
    row = VideoUpload(
        ...
        source_title=video_title,    # NEW
        ai_title=ai_title,            # NEW
        ...
    )
```

Call site (Douyin upload loop, ~line 563):
```python
_record_douyin_upload(
    ...
    ai_title=ai_title if use_ai_title else None,
    video_title=video.title,
    ...
)
```

### `_record_upload` signature (YouTube)

```python
def _record_upload(
    *,
    ...
    rendered_caption: Optional[str] = None,
    ai_title: Optional[str] = None,    # NEW
    attempt_count: int = 1,
    error_text: Optional[str] = None,
) -> None:
    row = VideoUpload(
        ...
        ai_title=ai_title,    # NEW
        ...
    )
```

Call site (YouTube upload loop): pass `ai_title=ai_title if use_ai_title else None`.

### `send_telegram_summary` — include ai_title + record push timestamp

In the video detail loop, replace the current `title = truncate(v.source_title or "video", 60)` line with:

```python
source_title = truncate(v.source_title or "video", 60)
ai = truncate(v.ai_title, 60) if v.ai_title and v.ai_title != v.source_title else None
display_title = f"{source_title} → {ai}" if ai else source_title
```

Use `display_title` in the video lines instead of `title`.

After the `telegram.send_message(...)` call succeeds, before the except:

```python
try:
    telegram.send_message(pipeline.telegram_bot_token, pipeline.telegram_chat_id, text)
    if run.id is not None:
        with Session(_api_db.engine) as s:
            db_run = s.get(PipelineRun, run.id)
            if db_run:
                db_run.pushed_notify_at = now_utc()
                s.add(db_run)
                s.commit()
except Exception as e:
    log.warning("Telegram notify failed for pipeline %d: %s", pipeline.id, e)
```

### VideosList UI

Current headers: `Tài khoản | Nguồn | TikTok | Trạng thái | Lỗi`  
New headers:     `Tài khoản | Nguồn | Title | AI Title | TikTok | Trạng thái | Lỗi`

- **Title**: `v.source_title` — plain text, `max-w-[160px] truncate`, show `—` if null
- **AI Title**: `v.ai_title` — plain text, `max-w-[160px] truncate`, show `—` if null

### RunRow bell icon — dedicated column

`RunRow` columns expand from 8 → 9 cols. New column after "Trạng thái":

```tsx
<td className="py-1.5 px-1 w-5 text-center">
  {run.pushed_notify_at ? (
    <Bell size={10} className="text-blue-400" title={new Date(run.pushed_notify_at).toLocaleString()} />
  ) : null}
</td>
```

`DayGroupRow` sub-header row: add `<th className="py-1 px-1 w-5" />` (empty, no label).

`DayGroupRow` day header row: update `colSpan` values to sum to 9.

`VideosList` wrapper row: update `colSpan={8}` → `colSpan={9}`.

---

## 5. Code style

- Match existing patterns. All new fields optional, default `None` / `null`.
- Migration: raw SQLite `ALTER TABLE ... ADD COLUMN`, idempotent (check existing cols — follow `api/db_migrations.py` pattern). No Alembic.
- No backfill of existing rows.
- Minimal UI changes: no new state, no new queries, no layout restructure.

---

## 6. Testing strategy

```bash
rtk pytest api/tests    # backend unit + migration tests
rtk tsc                 # webapp typecheck
```

### Key test cases

- Migration: `apply_schema_patches` idempotent on DB with and without the new columns
- `_record_upload` with `ai_title=None` → `VideoUpload.ai_title IS NULL`
- `_record_upload` with `ai_title="rewritten"` → persisted correctly
- `_record_douyin_upload` with `video_title="…"` → `source_title` populated
- `send_telegram_summary` success → `pushed_notify_at` set; exception → stays `NULL`
- `list_run_videos` response contains `ai_title` field
- UI: `VideoUploadItem` type includes `ai_title`; `PipelineRunItem` includes `pushed_notify_at`

---

## 7. Acceptance criteria

1. `video_uploads` has `ai_title TEXT`; `pipeline_runs` has `pushed_notify_at TEXT` — both idempotent migrations.
2. YouTube pipeline run: `VideoUpload.ai_title` = LLM-rewritten title or `NULL` when AI off.
3. Douyin pipeline run: `VideoUpload.source_title` = `video.title`; `ai_title` = LLM-rewritten or `NULL`.
4. `GET /pipelines/{id}/runs/{run_id}/videos` includes `ai_title` in each item.
5. VideosList shows "Title" and "AI Title" columns; null → `—`; long text truncated.
6. After successful Telegram send: `PipelineRun.pushed_notify_at` non-null.
7. Run rows show bell icon when `pushed_notify_at` set; nothing when null.
8. `rtk pytest`, `rtk tsc` pass.

---

## 8. Out of scope

- No changes to Telegram message content
- No retroactive backfill on existing rows
- No new endpoint — existing endpoints extended only
- No Alembic migrations
