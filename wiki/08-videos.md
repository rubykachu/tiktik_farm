# 08 — Trang `/videos` — inventory file

[← Index](README.md) | [← 07-schedules](07-schedules.md)

## Đây KHÔNG phải lịch sử TikTok

Trang `/videos` chỉ liệt kê **file trong thư mục `VideosDirPath/`** trên disk container. Không phải "video đã đăng lên TikTok".

```python
GET /api/videos
→ os.listdir("/data/VideosDirPath/")
→ filter file
→ return [{"name": "...", "size_bytes": ..., "modified_at": "..."}, ...]
```

## Tại sao file upload qua UI không xuất hiện

UI drag & drop flow:

```
Browser → upload bytes (multipart/form-data) →
API tempfile.NamedTemporaryFile() → /tmp/xxx.mp4 →
tiktok_uploader.upload_video() →
os.unlink(/tmp/xxx.mp4)   ⬅️ XÓA temp sau upload
```

File temp KHÔNG copy vào `VideosDirPath/`. Logic muốn vậy — tránh đầy disk với file user drop một lần dùng.

YouTube URL upload:
```
yt-dlp → /data/VideosDirPath/pre-processed.mp4 (FIXED filename) →
upload TikTok → file VẪN ở đó, nhưng lần upload YouTube tiếp theo OVERWRITE cùng tên
```

→ Sau upload YouTube, `/videos` vẫn chỉ thấy `pre-processed.mp4` (file mới nhất).

## Khi nào nên dùng `/videos`?

- Kiểm tra file CLI đã có sẵn để upload (CLI flow `cli.py upload -v <name>` đọc từ đây)
- Verify yt-dlp đã download xong (nếu debug)
- Cleanup disk — xem file gì còn sót

Workflow điển hình kết hợp:
```bash
# 1. SSH vào VPS, đặt video sẵn
scp my_video.mp4 vps:/path/to/v2/data/VideosDirPath/

# 2. Xem qua UI hoặc API
curl http://127.0.0.1:8000/api/videos

# 3. Upload qua CLI hoặc API (file đã có)
./.venv/bin/python cli.py upload --user lmbk37 -v "my_video.mp4" -t "title"
```

## Gap: không có upload history

Hệ thống hiện **không** track "video nào đã đăng lên TikTok khi nào". Để biết:
- Hỏi qua TikTok app/web manual
- Hoặc parse log API (`grep "Published successfully"`)

Phase 2 add bảng `upload_history` (xem [11-roadmap](11-roadmap.md)):

```sql
CREATE TABLE upload_history (
  id INTEGER PRIMARY KEY,
  account_id INTEGER REFERENCES accounts(id),
  source_type TEXT,
  source_ref TEXT,
  title TEXT,
  tiktok_post_id TEXT,           -- TikTok video ID (cần wire từ upload_video)
  status TEXT,
  published_at TIMESTAMP
);
```

Khi có bảng này, UI `/videos` (hoặc `/history` mới) sẽ hiển thị thực sự "video đã đăng" thay vì "file trên disk".

## Cleanup disk

`VideosDirPath/` có thể đầy theo thời gian (yt-dlp output, file thả thủ công). Manual cleanup:

```bash
# Docker
docker compose exec api ls -lah /data/VideosDirPath/
docker compose exec api rm /data/VideosDirPath/old_file.mp4

# Hoặc trực tiếp trên host (vì volume bind mount)
ls -lah ./data/VideosDirPath/
rm ./data/VideosDirPath/old_file.mp4
```

Phase 2 nên add task scheduler tự cleanup file >30 ngày (chưa implement).

---

**Next:** [09-operations](09-operations.md)
