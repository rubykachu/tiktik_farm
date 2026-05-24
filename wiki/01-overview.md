# 01 — Tổng quan hệ thống

[← Index](README.md)

## Hệ thống làm gì

Tự động đăng video lên TikTok thông qua REST API trực tiếp (không Selenium). Cho phép:

- Quản lý nhiều TikTok account trên cùng máy
- **Pipeline** kênh YouTube: lặp theo interval (phút/giờ/ngày), lấy top-N video mới, dedupe, upload lên nhiều TikTok account tuần tự, tùy chọn Telegram + caption template
- **Pipeline** kênh Douyin: tương tự nhưng dùng `douyin-downloader` subprocess + DouyinCookie

Source code base: fork từ `makiisthenes/TiktokAutoUploader`. v2 thêm: REST API (FastAPI), Scheduler worker, React webapp, Docker compose stack.

## Đối tượng dùng

| Persona | Cách dùng |
|---|---|
| **Operator** (anh hiện tại) | UI web để add account + cấu hình pipeline |
| **Power user / dev** | CLI `cli.py` hoặc curl API trực tiếp. Script tự động. |
| **Phase 2 — tenant SaaS** | UI web nhưng có auth + isolation. Chưa implement. |

## Workflow tổng quát

```
┌──────────────────────────────────────────────────────────────┐
│  Lần đầu mỗi account                                          │
├──────────────────────────────────────────────────────────────┤
│  1. Login TikTok lấy cookie session                           │
│     - Cookie-Editor JSON export → paste vào UI /accounts     │
│       (Import JSON modal) → API convert → pickle + DB row    │
│     - Migrate: copy cookie từ v1 → script convert → Import   │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  Mỗi lần upload (pipeline)                                    │
├──────────────────────────────────────────────────────────────┤
│  Option A — Pipeline kênh YouTube (Phase 1):                  │
│  /schedules → New pipeline → URL kênh + interval +           │
│  account(s) + caption template → ChannelPipeline (active) →  │
│  worker: yt-dlp list top-N → mỗi video upload tuần tự        │
│  → ghi VideoUpload + PipelineRun; optional Telegram summary  │
│                                                              │
│  Option B — Pipeline kênh Douyin (Phase 4):                  │
│  /schedules → New pipeline (source=Douyin) → URL kênh +      │
│  DouyinCookie + interval + account(s) →                      │
│  worker: douyin-downloader subprocess → mp4 → upload         │
└──────────────────────────────────────────────────────────────┘
```

## Stack ngắn

| Service | Tech | Vai trò |
|---|---|---|
| api | FastAPI + SQLite | REST endpoints, OpenAPI docs |
| scheduler | Python worker | Poll DB 30s, chạy `pipeline_tick()` mỗi vòng |
| webapp | React + Vite + nginx | UI |
| (shared base) | Python 3.11 + Node 20 + Playwright + ffmpeg | Image base cho api + scheduler |

Volume `./data:/data` share giữa api + scheduler — chứa SQLite db, CookiesDir, VideosDirPath.

Chi tiết kiến trúc: [02-architecture](02-architecture.md).

---

**Next:** [02-architecture](02-architecture.md)
