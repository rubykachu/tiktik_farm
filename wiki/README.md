# farm_tiktik v2 — Wiki

Tài liệu nghiệp vụ + vận hành. Đọc theo thứ tự nếu lần đầu, hoặc nhảy thẳng đến chủ đề cần.

## Mục lục

1. [Tổng quan hệ thống](01-overview.md) — flow nghiệp vụ chính, đối tượng user, workflow tổng quát
2. [Kiến trúc](02-architecture.md) — services, data flow, image sizes
3. [Setup Docker (production path)](03-setup-docker.md) — build, up/down, restart
4. [Setup Native (dev path)](04-setup-native.md) — chỉ dùng cho dev mac/linux nhanh
5. [Quản lý account + Cookie](05-accounts.md) — 3 cách add account, format cookie pickle
6. [Upload video](06-upload.md) — local file vs YouTube URL, single video only
7. [Lịch & pipeline](07-schedules.md) — tab **Single-shot** (YouTube 1 lần) + tab **Pipelines** (YouTube → TikTok theo chu kỳ)
8. [Trang `/histories` — lịch sử upload DB](08-videos.md) — bảng `VideoUpload` (chủ yếu từ pipeline)
9. [Vận hành: log, debug, restart, bảo mật](09-operations.md) — Docker, SQLite, hardening Phase 1
10. [Bug đã fix từ upstream](10-known-bugs.md) — 4 bug source code upstream
11. [Roadmap Phase 2 + SaaS](11-roadmap.md) — port nghiệp vụ v1, multi-tenant prep
12. [Lấy Telegram Chat ID (Bot API)](how_to_get_telegram_chat_id.md) — token, `getUpdates`, group/channel
13. [Settings — LLM caption rewrite](12-settings.md) — Phase 3: toggle global, CRUD model (Gemini/OpenAI/Grok/Azure), secret key, SSRF guard, validation `{ai_title}`

## Quick reference

| Tôi muốn... | Đọc file |
|---|---|
| Hiểu hệ thống làm gì | [01-overview](01-overview.md) |
| Deploy lên VPS | [03-setup-docker](03-setup-docker.md) |
| Cập nhật code / tránh image cũ | `make deploy` — [03-setup-docker](03-setup-docker.md) |
| Thêm account mới | [05-accounts](05-accounts.md) |
| Đăng 1 video lên TikTok | [06-upload](06-upload.md) |
| Lên lịch đăng video YouTube (1 lần) | [07-schedules](07-schedules.md) tab Single-shot |
| Pipeline kênh YouTube → TikTok lặp lại | [07-schedules](07-schedules.md) tab Pipelines |
| Xem lịch sử upload (pipeline) + filter (`/histories`) | [08-videos](08-videos.md) |
| Dev Docker hot-reload (override) | [09-operations](09-operations.md) § Dev với override |
| Debug khi UI lỗi | [09-operations](09-operations.md) |
| Hiểu vì sao có 4 fix tiktok.py | [10-known-bugs](10-known-bugs.md) |
| Mở rộng SaaS | [11-roadmap](11-roadmap.md) |
| Lấy `chat_id` số cho Telegram (pipeline) | [how_to_get_telegram_chat_id](how_to_get_telegram_chat_id.md) |
| Cấu hình LLM rewrite caption (Gemini/OpenAI/Grok/Azure) | [12-settings](12-settings.md) |
| Setup Azure OpenAI / Foundry model | [12-settings § Provider matrix](12-settings.md#4-provider-matrix) |

---

**Next:** [01-overview](01-overview.md)
