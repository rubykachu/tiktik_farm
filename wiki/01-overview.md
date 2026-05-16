# 01 — Tổng quan hệ thống

[← Index](README.md)

## Hệ thống làm gì

Tự động đăng video lên TikTok thông qua REST API trực tiếp (không Selenium). Cho phép:

- Quản lý nhiều TikTok account trên cùng máy
- Upload video local hoặc tải từ YouTube URL rồi upload
- Lên lịch upload tương lai (chỉ YouTube URL, lên đến 10 ngày)
- Login account qua trình duyệt ảo (noVNC) — debug bằng mắt người
- Đồng thời chạy được CLI (legacy) và REST API + UI (mới)

Source code base: fork từ `makiisthenes/TiktokAutoUploader`. v2 thêm: REST API (FastAPI), Scheduler worker, noVNC service, React webapp, Docker compose stack.

## Đối tượng dùng

| Persona | Cách dùng |
|---|---|
| **Operator** (anh hiện tại) | UI web để add account + upload thủ công. Dùng VNC iframe để login khi cookie chết. |
| **Power user / dev** | CLI `cli.py` hoặc curl API trực tiếp. Script tự động. |
| **Phase 2 — tenant SaaS** | UI web nhưng có auth + isolation. Chưa implement. |

## Workflow tổng quát

```
┌──────────────────────────────────────────────────────────────┐
│  Lần đầu mỗi account                                          │
├──────────────────────────────────────────────────────────────┤
│  1. Login TikTok lấy cookie session                           │
│     - noVNC: UI /login → Chrome ảo trong container → login   │
│       → cookie tự lưu /data/CookiesDir/ + DB upsert  ✓ tự    │
│     - CLI native: cli.py login -n <user> → Chrome host       │
│       → cookie lưu ./CookiesDir/ → bấm Import từ UI để       │
│       sync DB (vì CLI không tự ghi DB)                       │
│     - Migrate: copy cookie từ Cookie-Editor JSON → script    │
│       convert → đặt vào /data/CookiesDir → bấm Import        │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  Mỗi lần upload                                               │
├──────────────────────────────────────────────────────────────┤
│  Option A — Local file:                                      │
│  /upload page → drag&drop video → API tạo temp file →        │
│  tiktok_uploader.upload_video() → TikTok web API → xong      │
│                                                              │
│  Option B — YouTube URL:                                     │
│  /upload page → paste URL (1 video, không playlist) →        │
│  yt-dlp tải về /data/VideosDirPath/pre-processed.mp4 →       │
│  upload TikTok → xong                                        │
│                                                              │
│  Option C — Schedule YouTube:                                │
│  /upload page → paste URL + future datetime → ghi row        │
│  ScheduledUpload(status=pending) vào SQLite → scheduler      │
│  worker poll 30s/lần → khi đến hạn: download + upload →      │
│  status=succeeded|failed                                     │
└──────────────────────────────────────────────────────────────┘
```

## Hỏi đã được xác nhận

> "VNC login trên server xong nó lưu vào CookieDir, rồi bấm Import để đưa vào DB đúng không?"

**Gần đúng, nhưng có sai sót nhỏ**:

- **noVNC login**: API **tự** ghi cookie file + **tự** upsert DB row qua callback. KHÔNG cần bấm Import. UI tự navigate sang `/accounts` sau khi login xong.
- **CLI login (native)** hoặc **migrate script**: chỉ tạo cookie file trên disk, KHÔNG ghi DB → cần bấm Import để sync DB row.

→ "Import from disk" = phương án bridge cho cookie có sẵn trên disk mà DB chưa biết. Không phải file picker (xem chi tiết [05-accounts](05-accounts.md)).

## Stack ngắn

| Service | Tech | Vai trò |
|---|---|---|
| api | FastAPI + SQLite | REST endpoints, OpenAPI docs |
| scheduler | Python worker | Poll DB 30s, chạy job đến hạn |
| novnc | Xvfb + chromium + x11vnc + websockify | Trình duyệt ảo cho login |
| webapp | React + Vite + nginx | UI |
| (shared base) | Python 3.11 + Node 20 + Playwright + ffmpeg | Image base cho 3 service Python |

Volume `./data:/data` share giữa api + scheduler + novnc — chứa SQLite db, CookiesDir, VideosDirPath.

Chi tiết kiến trúc: [02-architecture](02-architecture.md).

---

**Next:** [02-architecture](02-architecture.md)
