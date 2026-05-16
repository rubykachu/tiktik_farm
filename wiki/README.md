# farm_tiktik v2 — Wiki

Tài liệu nghiệp vụ + vận hành. Đọc theo thứ tự nếu lần đầu, hoặc nhảy thẳng đến chủ đề cần.

## Mục lục

1. [Tổng quan hệ thống](01-overview.md) — flow nghiệp vụ chính, đối tượng user, workflow tổng quát
2. [Kiến trúc](02-architecture.md) — services, data flow, image sizes
3. [Setup Docker (production path)](03-setup-docker.md) — build, up/down, restart
4. [Setup Native (dev path)](04-setup-native.md) — chỉ dùng cho dev mac/linux nhanh
5. [Quản lý account + Cookie](05-accounts.md) — 3 cách add account, format cookie pickle
6. [Upload video](06-upload.md) — local file vs YouTube URL, single video only
7. [Schedule (đăng theo lịch)](07-schedules.md) — chỉ áp dụng cho YouTube URL
8. [Trang `/videos` — inventory file](08-videos.md) — KHÔNG phải lịch sử TikTok
9. [Vận hành: log, debug, restart](09-operations.md) — Docker operational rules
10. [Bug đã fix từ upstream](10-known-bugs.md) — 4 bug source code upstream
11. [Roadmap Phase 2 + SaaS](11-roadmap.md) — port nghiệp vụ v1, multi-tenant prep

## Quick reference

| Tôi muốn... | Đọc file |
|---|---|
| Hiểu hệ thống làm gì | [01-overview](01-overview.md) |
| Deploy lên VPS | [03-setup-docker](03-setup-docker.md) |
| Thêm account mới | [05-accounts](05-accounts.md) |
| Đăng 1 video lên TikTok | [06-upload](06-upload.md) |
| Lên lịch đăng video YouTube | [07-schedules](07-schedules.md) |
| Debug khi UI lỗi | [09-operations](09-operations.md) |
| Hiểu vì sao có 4 fix tiktok.py | [10-known-bugs](10-known-bugs.md) |
| Mở rộng SaaS | [11-roadmap](11-roadmap.md) |

---

**Next:** [01-overview](01-overview.md)
