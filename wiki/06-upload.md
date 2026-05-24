# 06 — Upload video

[← Index](README.md) | [← 05-accounts](05-accounts.md)

> **Lưu ý**: Tính năng upload thủ công qua UI (`/upload`) và REST API (`/api/uploads/*`) đã bị xóa để đơn giản hóa hệ thống. Upload hiện tại chỉ qua **pipeline** hoặc **CLI**.

## Upload qua Pipeline (khuyên dùng)

Xem [07-schedules](07-schedules.md) — pipeline tự động fetch + upload theo kênh.

## Upload qua CLI (legacy)

```bash
# Local (file phải đặt sẵn trong VideosDirPath/)
./.venv/bin/python cli.py upload \
    --user lmbk37 \
    -v "pre-processed.mp4" \
    -t "My title"

# YouTube
./.venv/bin/python cli.py upload \
    --user lmbk37 \
    -yt "https://www.youtube.com/shorts/xxxxx" \
    -t "My title"
```

Full flag list: `./.venv/bin/python cli.py upload -h`.

## Flow upload TikTok (dùng chung bởi cả CLI và pipeline)

```
1. cookie_store.load(username) → pickle list[dict]
2. requests.Session() — set toàn bộ cookies vào jar
3. fake_useragent → desktop Chrome UA (filter mobile)
4. POST tiktok.com/api/v1/web/project/create/?creation_id=... → project_id
5. GET tiktok.com/api/v1/video/upload/auth/ → AWS credentials
6. POST upload_host/<store_uri> chunks → upload video bytes
7. POST tiktok.com/.../commit upload → server merge chunks
8. POST tiktok.com/.../CommitUploadInner → get meta
9. Node subprocess generate x-bogus + signature
10. POST tiktok.com/tiktok/web/project/post/v1/ → publish (kèm title + options)
11. Check r.json()["status_code"] == 0 → "Published successfully" + return True
```

Subprocess Node ở bước 9: `tiktok_uploader/tiktok-signature/browser.js` dùng playwright Chromium để execute TikTok JS sign function. Bắt buộc, không có thì TikTok reject.

## Lỗi thường gặp

| Lỗi | Nguyên nhân | Fix |
|---|---|---|
| `account 'x' has no valid session` (409) | Pickle thiếu `sessionid` | Re-import JSON cookie |
| `TikTok refused project/create` | Cookie chết | Re-import JSON cookie |
| `Failed to generate signatures` | Node sig subprocess fail | Check `tiktok-signature/node_modules`, rebuild Docker |
| `[-] The title has to be less than 2200 characters` | Title quá dài | Cut title |

Tail log để xác định nguyên nhân: xem [09-operations](09-operations.md).

---

**Next:** [07-schedules](07-schedules.md)
