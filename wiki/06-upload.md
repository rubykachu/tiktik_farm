# 06 — Upload video

[← Index](README.md) | [← 05-accounts](05-accounts.md)

## 2 source types

| Source | Behavior |
|---|---|
| **Local file** | Drag & drop video `.mp4`/`.webm` từ máy → API ghi temp file → upload TikTok → xóa temp |
| **YouTube URL** | Paste 1 URL (KHÔNG playlist) → yt-dlp tải về `/data/VideosDirPath/pre-processed.mp4` → upload TikTok |

## Caption / title

- Max 2200 ký tự
- Hỗ trợ emoji, hashtag — TikTok parse khi đăng

## Visibility options

| Flag | Default | Note |
|---|---|---|
| visibility_type | 0 (public) | `1` = private. Không schedule được private. |
| allow_comment | 1 (on) | |
| allow_duet | 0 (off) | |
| allow_stitch | 0 (off) | |
| ai_label | 0 (off) | TikTok yêu cầu AI-content disclosure cho video AI-generated |

## Upload qua UI `/upload`

1. Chọn account từ dropdown (chỉ hiện account `has_valid_session: true`)
2. Chọn source type:
   - **Local file**: dropzone drag & drop, hoặc click chọn file
   - **YouTube URL**: paste URL (regex check, phải match `watch?v=` / `shorts/` / `embed/` / `youtu.be/`)
3. Nhập title (max 2200 char)
4. (Optional) Schedule time — chỉ áp dụng với YouTube URL
5. (Optional) Toggle options
6. Click Submit
7. Đợi response — banner hiện "upload completed" hoặc error message

## Upload qua CLI

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

## Upload qua API (curl)

### Local file

```bash
curl -X POST http://127.0.0.1:8000/api/uploads/file \
  -F "username=lmbk37" \
  -F "title=My title" \
  -F "options_json={}" \
  -F "video=@/path/to/video.mp4"
```

Response:
```json
{"ok": true,  "message": "upload completed"}
{"ok": false, "message": "upload failed"}
```

### YouTube

```bash
curl -X POST http://127.0.0.1:8000/api/uploads/youtube \
  -H "Content-Type: application/json" \
  -d '{
    "username": "lmbk37",
    "title": "My title",
    "youtube_url": "https://www.youtube.com/shorts/xxxxx",
    "options": {
      "visibility_type": 0,
      "allow_comment": 1
    }
  }'
```

## YouTube playlist — KHÔNG support

UI regex chặn playlist URL. yt-dlp option dùng `outtmpl: pre-processed.mp4` (fixed filename) → playlist sẽ overwrite cùng file → chỉ giữ video cuối.

Muốn upload nhiều video, gọi API/CLI từng URL một (loop client-side).

Phase 2 sẽ add endpoint `/api/uploads/youtube-playlist` (xem [11-roadmap](11-roadmap.md)).

## Flow chi tiết khi upload

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

Subprocess Node ở bước 9: `tiktok_uploader/tiktok-signature/browser.js` dùng playwright Chromium để execute TikTok JS sign function, tạo `x-bogus` token. Bắt buộc, không có thì TikTok reject.

## Tại sao có 4 fix trong `tiktok.py`

Upstream code có bug khiến upload fail. Xem [10-known-bugs](10-known-bugs.md).

## Lỗi thường gặp

| Lỗi | Nguyên nhân | Fix |
|---|---|---|
| `account 'x' has no valid session` (409) | Pickle thiếu `sessionid` | Re-login |
| `TikTok refused project/create` | Cookie chết | Re-login |
| `Failed to generate signatures` | Node sig subprocess fail | Check `tiktok-signature/node_modules`, rebuild Docker |
| `[-] The title has to be less than 2200 characters` | Title quá dài | Cut title |
| `[-] Cannot schedule video in more than 10 days or less than 20 minutes` | schedule_time ngoài range | Đặt 900-864000 giây |
| `[-] Private videos cannot be uploaded with schedule` | visibility=1 + schedule_time>0 | Chọn 1 trong 2 |

Tail log để xác định nguyên nhân: xem [09-operations](09-operations.md).

---

**Next:** [07-schedules](07-schedules.md)
