# farm_tiktik v2 — Wiki

Tài liệu nghiệp vụ + vận hành. Đọc theo thứ tự nếu lần đầu, hoặc nhảy thẳng đến chủ đề cần.

## Mục lục

1. [Tổng quan hệ thống](01-overview.md) — flow nghiệp vụ chính, đối tượng user, workflow tổng quát
2. [Kiến trúc](02-architecture.md) — services, data flow, image sizes
3. [Setup Docker (production path)](03-setup-docker.md) — build, up/down, restart
4. [Setup Native (dev path)](04-setup-native.md) — chỉ dùng cho dev mac/linux nhanh
5. [Quản lý account + Cookie](05-accounts.md) — 2 cách add account, format cookie pickle
6. [Upload video](06-upload.md) — CLI upload + flow TikTok API
7. [Lịch & pipeline](07-schedules.md) — **Pipelines** (YouTube/Douyin → TikTok theo chu kỳ)
8. [Trang `/histories` — lịch sử upload DB](08-videos.md) — bảng `VideoUpload` (chủ yếu từ pipeline)
9. [Vận hành: log, debug, restart, bảo mật](09-operations.md) — Docker, SQLite, hardening Phase 1
10. [Bug đã fix từ upstream](10-known-bugs.md) — 4 bug source code upstream
11. [Roadmap Phase 2 + SaaS](11-roadmap.md) — port nghiệp vụ v1, multi-tenant prep
12. [Lấy Telegram Chat ID (Bot API)](how_to_get_telegram_chat_id.md) — token, `getUpdates`, group/channel
13. [Settings — LLM caption rewrite](12-settings.md) — toggle global, CRUD model (Gemini/OpenAI/Grok/Azure), secret key, SSRF guard, validation `{ai_title}`
14. [Douyin Pipeline](13-douyin-pipeline.md) — source_type=douyin, DouyinCookie, vendor subprocess, dedup aweme_id, anti-bot, vận hành

## Quick reference

| Tôi muốn... | Đọc file |
|---|---|
| Hiểu hệ thống làm gì | [01-overview](01-overview.md) |
| Deploy lên VPS | [03-setup-docker](03-setup-docker.md) |
| Vận hành hằng ngày (bàn giao) | [RUNBOOK.md](../RUNBOOK.md) |
| Cập nhật code / tránh image cũ | `make start` — [03-setup-docker](03-setup-docker.md) |
| Thêm account mới | [05-accounts](05-accounts.md) |
| Pipeline kênh YouTube → TikTok lặp lại | [07-schedules](07-schedules.md) |
| Pipeline kênh Douyin → TikTok | [13-douyin-pipeline](13-douyin-pipeline.md) |
| Xem lịch sử upload (pipeline) + filter (`/histories`) | [08-videos](08-videos.md) |
| Dev Docker hot-reload (override) | [09-operations](09-operations.md) § Dev với override |
| Debug khi UI lỗi | [09-operations](09-operations.md) |
| Hiểu vì sao có 4 fix tiktok.py | [10-known-bugs](10-known-bugs.md) |
| Mở rộng SaaS | [11-roadmap](11-roadmap.md) |
| Lấy `chat_id` số cho Telegram (pipeline) | [how_to_get_telegram_chat_id](how_to_get_telegram_chat_id.md) |
| Cấu hình LLM rewrite caption (Gemini/OpenAI/Grok/Azure) | [12-settings](12-settings.md) |
| Setup Azure OpenAI / Foundry model | [12-settings § Provider matrix](12-settings.md#4-provider-matrix) |
| Quản lý Douyin Cookie | [13-douyin-pipeline § 2](13-douyin-pipeline.md#2-douyincookie--quản-lý-cookie-xác-thực) |
| Cookie Douyin hết hạn (pulled_count=0) | [13-douyin-pipeline § 8.3](13-douyin-pipeline.md#83-cookie-hết-hạn) |

---

**Next:** [01-overview](01-overview.md)
