# 05 — Quản lý account + Cookie

[← Index](README.md) | [← 04-setup-native](04-setup-native.md)

## Khái niệm

**Account** trong hệ thống = TikTok username + cookie session đang valid.

Trạng thái lưu ở 2 nơi:

1. **Cookie file** (`/data/CookiesDir/tiktok_session-<username>.cookie`) — pickle list cookie dicts. Là source of truth khi gọi TikTok API.
2. **DB row** trong bảng `accounts` — metadata để UI hiển thị: id, display_name, last_used_at, has_valid_session.

Hai phải đồng bộ. Add account = đảm bảo cả 2 tồn tại.

## 2 cách add account

### Cách A — Import JSON Cookie (UI `/accounts`, khuyên dùng)

Modal **Import JSON**: paste nội dung JSON export từ Cookie-Editor (hoặc upload file) + nhập **username** tay; tùy chọn **Thay thế** nếu account đã tồn tại (`replace=true`).

**`POST /api/accounts/import-json`** (multipart):

- JSON top-level là **list**; **≤ 200** entry; mỗi entry dict có ít nhất `name`, `value`, `domain`; phải có ít nhất một cookie `name=sessionid`.
- **Username**: regex `^[A-Za-z0-9_.]{1,32}$` (chống path traversal — cookie path join an toàn trong `cookie_store`).
- Username đã có mà `replace != true` → **409** `ACCOUNT_EXISTS`.
- Lưu pickle vào `CookiesDir` (quyền file **0600**, thư mục **0700**) + upsert `Account`.

Request cần **`Origin: http://127.0.0.1:3000`** (hoặc `localhost:3000`).

### Cách B — Migrate JSON từ Cookie-Editor / v1

Cookie-Editor extension export format JSON array Chrome DevTools. v1 farm_tiktik cũng dùng format này.

```bash
# Đặt file vào dir bất kỳ, đặt tên đúng pattern
mkdir -p /tmp/cookies && cp my_export.json /tmp/cookies/TK_cookies_<username>.json

# Chạy script convert
./.venv/bin/python scripts/migrate-json-cookie-editor.py \
    /tmp/cookies ./data/CookiesDir

# Output:
#   TK_cookies_<username>.json -> ./data/CookiesDir/tiktok_session-<username>.cookie
#   (20 cookies, sessionid=yes)
```

Sau đó dùng cách A (Import JSON) hoặc gọi API trực tiếp để sync DB row.

Script convert: rename pattern, map Chrome DevTools keys (`expirationDate`, `hostOnly`...) sang Selenium keys (`expiry`, `secure`...), rewrite `sameSite: "None"` → `"Strict"` (bug TikTok yêu cầu).

**Tên file phải match pattern `TK_cookies_<username>.json`** — script regex theo đó để extract username.

## Format pickle cookie file

```python
import pickle
cookies = pickle.load(open("/data/CookiesDir/tiktok_session-x.cookie", "rb"))
# cookies = [
#   {"name": "sessionid", "value": "abc...", "domain": ".tiktok.com",
#    "path": "/", "secure": True, "httpOnly": True,
#    "sameSite": "Strict", "expiry": 1809688260},
#   {"name": "msToken", ...},
#   ... (15-20 cookies thường)
# ]
```

`has_valid_session: true` khi pickle có ít nhất 1 cookie `{name: "sessionid", value: non-empty}`. Code check: `cookie_store.has_valid_session()`.

## Re-login khi cookie chết

TikTok có thể invalidate cookie bất kỳ lúc nào (đổi password, đăng nhập thiết bị khác, antibot flag). Khi upload fail kèm `TikTok refused project/create` trong log → cookie chết.

Re-login: Export lại từ Cookie-Editor trên trình duyệt đã đăng nhập → dùng cách A (Import JSON, chọn **Thay thế**).

## Xóa account

UI `/accounts` row → "Delete":
1. Xóa row `accounts`
2. Xóa cookie file `/data/CookiesDir/tiktok_session-<x>.cookie`

API equivalent:
```bash
curl -X DELETE http://127.0.0.1:8000/api/accounts/<id> \
  -H "Origin: http://127.0.0.1:3000"
```

## DB schema (bảng `accounts`)

| Field | Type | Note |
|---|---|---|
| id | int PK | |
| username | str UNIQUE | TikTok username |
| display_name | str? | label tùy chọn |
| cookie_path | str | absolute path tới pickle |
| has_valid_session | bool | true khi pickle có sessionid |
| created_at | datetime | |
| updated_at | datetime | |
| last_used_at | datetime? | set sau mỗi upload thành công |

---

**Next:** [06-upload](06-upload.md)
