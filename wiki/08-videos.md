# 08 — Trang `/videos`: lịch sử upload (DB)

[← Index](README.md) | [← 07-schedules](07-schedules.md)

Phase 1 đổi semantics: trang **`/videos`** hiển thị dữ liệu từ bảng **`VideoUpload`** (SQLite), không còn liệt kê file trong `VideosDirPath/` qua `/api/videos`. Endpoint **`/api/videos/disk`** đã bỏ.

---

## Phạm vi dữ liệu

- **`VideoUpload` được ghi khi pipeline** xử lý từng cặp (video YouTube × account) **sau khi** `tiktok_adapter.upload_from_options` trả về — không có trạng thái “đang chạy” trong DB.
- **Upload thủ công** (`/upload` file/YouTube) và **Single-shot** (`ScheduledUpload`) **hiện không** tạo dòng `VideoUpload`. Trang `/videos` **chủ yếu phản ánh lịch sử từ tab Pipelines**. Muốn audit đầy đủ single-shot, xem bảng `scheduled_uploads` hoặc log API.

---

## Trường chính (`VideoUpload`)

| Field | Ghi chú |
|-------|---------|
| `account_id` | FK account |
| `youtube_video_id`, `youtube_url`, `youtube_title` | Nguồn (title cap 500 server-side) |
| `tiktok_video_id`, `tiktok_url` | Kết quả (có thể null nếu TikTok không trả id nhưng upload coi như OK) |
| `rendered_caption` | Caption đã render cho bản ghi này |
| `status` | `succeeded` \| `failed` |
| `attempt_count` | 1…3 theo số lần fail trước đó + 1 |
| `error_text` | Cap 2KB (truncate server) |
| `pipeline_id`, `run_id` | SET NULL nếu xóa pipeline/run — **giữ** dòng lịch sử |
| `created_at` | Index, sort mặc định DESC |

Dedupe toàn cục cho pipeline: index phụ trợ truy vấn `(account_id, youtube_video_id, status)`.

---

## API

```http
GET /api/videos?account_id=&status=succeeded|failed&pipeline_id=&from_date=&to_date=&page=1&size=50
DELETE /api/videos/{id}
```

- **`from_date` / `to_date`**: ISO 8601 (so khớp `created_at`).
- **`size`**: 1…**200**, mặc định 50.

Mọi `DELETE` cần header **`Origin: http://127.0.0.1:3000`** (hoặc `localhost:3000`). Xóa chỉ xóa **bản ghi DB** — **không** xóa video trên TikTok.

---

## UI

- Filter: account, trạng thái, pipeline, khoảng ngày.
- Cột: thời gian, account, YouTube (URL + title), TikTok URL, caption (truncate + tooltip), status, attempts, pipeline (link nếu còn `pipeline_id`), lỗi.
- **Delete** row: modal xác nhận.

---

## File trên đĩa (`VideosDirPath/`)

Pipeline tải video vào thư mục temp (prefix `pipeline_dl_`) và **xóa sau mỗi video**. File `pre-processed.mp4` dùng cho luồng upload đơn/CLI vẫn có thể tồn tại — **không** dùng `/api/videos` để inventory; dùng `ls` / ops thủ công nếu cần (xem [09-operations](09-operations.md)).

---

**Next:** [09-operations](09-operations.md)
