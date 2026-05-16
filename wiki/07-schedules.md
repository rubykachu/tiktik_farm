# 07 — Schedule (đăng theo lịch)

[← Index](README.md) | [← 06-upload](06-upload.md)

## Tính năng

Lên lịch upload **YouTube URL** vào thời điểm tương lai. Scheduler worker poll DB mỗi 30s, khi đến hạn thì download + upload tự động.

## Giới hạn

- **Chỉ YouTube URL**. Local file không schedule được vì file drag&drop chỉ tồn tại trong request HTTP, không persist.
- TikTok limit: schedule time phải trong khoảng **15 phút → 10 ngày** từ now (`900s ≤ schedule ≤ 864000s`). Wrapper check ở app side trước khi gửi TikTok.
- Visibility `private` (=1) **không** schedule được (TikTok rule).

## Schedule qua UI

1. `/upload` page → chọn YouTube URL source
2. Paste URL + title
3. Nhập "Scheduled for" — datetime tương lai (datetime-local input)
4. Submit → API tạo row `ScheduledUpload(status=pending)`
5. Banner hiển thị `Scheduled — job #N at <datetime>`
6. Vào `/schedules` để xem trạng thái

## Schedule qua API

```bash
curl -X POST http://127.0.0.1:8000/api/schedules \
  -H "Content-Type: application/json" \
  -d '{
    "username": "lmbk37",
    "title": "My title",
    "source_type": "youtube",
    "source_ref": "https://www.youtube.com/shorts/xxxxx",
    "scheduled_for": "2026-05-17T10:00:00Z",
    "options": {
      "visibility_type": 0,
      "allow_comment": 1
    }
  }'
```

Response:
```json
{
  "id": 5,
  "account_id": 1,
  "source_type": "youtube",
  "source_ref": "https://...",
  "title": "My title",
  "options_json": "{...}",
  "scheduled_for": "2026-05-17T10:00:00",
  "status": "pending",
  ...
}
```

## Trạng thái lifecycle

```
pending → running → succeeded
                 ↘ failed
   ↘ cancelled (user huỷ trước khi đến hạn)
```

| Status | Nghĩa |
|---|---|
| `pending` | Trong DB, chờ scheduler pick |
| `running` | Scheduler đã pick, đang download + upload |
| `succeeded` | Upload thành công, `result_text` chứa "Published successfully" |
| `failed` | Lỗi, `result_text` chứa traceback/error message |
| `cancelled` | User huỷ trước khi đến hạn |

## Huỷ schedule

UI `/schedules` → row → "Cancel".

API:
```bash
curl -X PATCH http://127.0.0.1:8000/api/schedules/<id> \
  -H "Content-Type: application/json" \
  -d '{"status": "cancelled"}'
```

Chỉ cancel được khi status = `pending`. Nếu đã `running` → không cancel được giữa chừng (worker không có signal handler).

## Retry failed

API:
```bash
curl -X PATCH http://127.0.0.1:8000/api/schedules/<id> \
  -H "Content-Type: application/json" \
  -d '{"status": "pending"}'
```

Set lại status=pending → scheduler pick lại ở vòng poll tiếp theo. `attempts` field tăng mỗi lần thử.

## Scheduler internals

`scheduler/worker.py`:

```python
def loop(poll_interval=30):
    while True:
        with Session(engine) as s:
            due = s.exec(
                select(ScheduledUpload)
                .where(ScheduledUpload.status == "pending")
                .where(ScheduledUpload.scheduled_for <= now_utc())
                .order_by(ScheduledUpload.scheduled_for)
            ).all()
        for job in due:
            run_job(job)   # serial: 1 job tại 1 thời điểm
        time.sleep(poll_interval)
```

Mỗi job:
1. Set status=running, heartbeat=now_utc(), attempts += 1
2. Resolve account (account_id → Account row → username)
3. Nếu source_type == "youtube": `youtube.download(source_ref)` → file path
4. `tiktok_adapter.upload_from_options(username, path, title, options)` → True/False
5. Set status=succeeded|failed + result_text

Concurrency = 1 (serial). Bumping cần đổi `worker.loop()` thành multi-thread/process pool — chưa implement.

## Time zone

Toàn bộ datetime trong DB lưu UTC (`datetime.now(timezone.utc)`). UI `datetime-local` input → JS convert sang ISO UTC khi POST. Khi hiển thị, UI convert ngược về local time.

→ Server time zone không ảnh hưởng. Nếu schedule "lúc 10AM" thì phải hiểu là 10AM theo timezone client.

## DB schema (bảng `scheduled_uploads`)

| Field | Type | Note |
|---|---|---|
| id | int PK | |
| account_id | int FK → accounts.id | Cascade delete (xem [05-accounts](05-accounts.md)) |
| source_type | str | `local` hoặc `youtube` |
| source_ref | str | YouTube URL hoặc filename |
| title | str (≤2200) | |
| options_json | str | JSON `{"allow_comment":1,...}` |
| scheduled_for | datetime UTC | Index để query nhanh |
| status | str | pending/running/succeeded/failed/cancelled |
| result_text | str? | Error message hoặc "Published successfully" |
| attempts | int | Increment mỗi lần worker pick |
| heartbeat_at | datetime? | Update khi worker đang run |
| created_at | datetime | |
| updated_at | datetime | |

---

**Next:** [08-videos](08-videos.md)
