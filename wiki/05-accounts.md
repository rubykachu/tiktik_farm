# 05 — Quản lý account + Cookie

[← Index](README.md) | [← 04-setup-native](04-setup-native.md)

## Khái niệm

**Account** trong hệ thống = TikTok username + cookie session đang valid.

Trạng thái lưu ở 2 nơi:

1. **Cookie file** (`/data/CookiesDir/tiktok_session-<username>.cookie`) — pickle list cookie dicts. Là source of truth khi gọi TikTok API.
2. **DB row** trong bảng `accounts` — metadata để UI hiển thị: id, display_name, last_used_at, has_valid_session.

Hai phải đồng bộ. Add account = đảm bảo cả 2 tồn tại.

## 3 cách add account (+ Import JSON trên UI)

### Cách A — noVNC login (Docker, đầy đủ tự động)

1. UI: http://127.0.0.1:3000/login
2. Nhập username → click "Start login session"
3. Iframe noVNC mở → bạn thấy Chromium đang load tiktok.com/login trong container
4. Đăng nhập qua mouse + keyboard (proxy qua VNC websocket)
5. noVNC control server detect `sessionid` cookie → POST callback `/api/login/browser/<id>/complete`
6. API: ghi pickle vào `/data/CookiesDir/` + upsert Account row
7. UI tự nhảy về `/accounts`

→ **Không cần bấm Import**. Tự động cả file + DB.

### Cách B — CLI `cli.py login` (chỉ native)

```bash
./.venv/bin/python cli.py login -n <username>
```

Chrome thật trên máy host mở. Login. `wait_for_session_cookies()` polling cookies → khi có `sessionid` + `tt-target-idc` → pickle lưu `./CookiesDir/tiktok_session-<username>.cookie`.

→ **DB chưa biết**. Sync bằng cách:
```bash
curl -X POST http://127.0.0.1:8000/api/accounts/import-from-disk \
  -H "Origin: http://127.0.0.1:3000"
```
Hoặc UI `/accounts` → click "Import from disk".

**Caveat**: nếu cookie file đã tồn tại với cùng tên, `login()` bail out với `"Unnecessary login: session already saved!"`. Muốn re-login, xóa cookie file trước:
```bash
rm ./CookiesDir/tiktok_session-<username>.cookie
./.venv/bin/python cli.py login -n <username>
```

### Cách C — Migrate JSON từ Cookie-Editor / v1

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

# Sync DB
curl -X POST http://127.0.0.1:8000/api/accounts/import-from-disk \
  -H "Origin: http://127.0.0.1:3000"
```

Script convert: rename pattern, map Chrome DevTools keys (`expirationDate`, `hostOnly`...) sang Selenium keys (`expiry`, `secure`...), rewrite `sameSite: "None"` → `"Strict"` (bug TikTok yêu cầu).

**Tên file phải match pattern `TK_cookies_<username>.json`** — script regex theo đó để extract username.

### Cách D — Import JSON Cookie (UI `/accounts`, Phase 1)

Modal **Import JSON**: upload một file export kiểu Cookie-Editor (JSON array) + nhập **username** tay; tùy chọn **Thay thế** nếu account đã tồn tại (`replace=true`).

**`POST /api/accounts/import-json`** (multipart):

- File **≤ 256 KB**; JSON top-level là **list**; **≤ 200** entry; mỗi entry dict có ít nhất `name`, `value`, `domain`; phải có ít nhất một cookie `name=sessionid`.
- **Username**: regex `^[A-Za-z0-9_.]{1,32}$` (chống path traversal — cookie path join an toàn trong `cookie_store`).
- Username đã có mà `replace != true` → **409** `ACCOUNT_EXISTS`.
- Lưu pickle vào `CookiesDir` (quyền file **0600**, thư mục **0700**) + upsert `Account`.

Request cần **`Origin: http://127.0.0.1:3000`** (hoặc `localhost:3000`).

Xóa account: **modal xác nhận** (đồng bộ UX toàn site Phase 1).

## "Import from disk" hoạt động thế nào

**KHÔNG** phải file picker trên UI. Endpoint chạy SERVER-side:

```python
POST /api/accounts/import-from-disk

# Logic:
existing = {acct.username for acct in DB.accounts}
for fname in os.listdir(/data/CookiesDir/):
    if fname matches "tiktok_session-*.cookie":
        username = strip_prefix_suffix(fname)
        if username not in existing:
            INSERT INTO accounts (username, cookie_path, ...)
return imported_list   # [] nếu không có cookie mới
```

→ "Quét lại `/data/CookiesDir/`, đăng ký account chưa biết vào DB". Idempotent — bấm 100 lần cũng OK.

UI hiển thị banner:
- `Imported N account: <names>` — có cookie mới
- `Nothing to import — every cookie file on disk is already registered.` — không có cookie mới
- `Import failed: ...` — lỗi

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

Re-login:
- noVNC: UI `/accounts` → row → "Re-login" → đi flow A
- CLI: xóa cookie file → `cli.py login -n <username>` → đi flow B

## Xóa account

UI `/accounts` row → "Delete":
1. Xóa dependent rows trong `scheduled_uploads` (cascade thủ công)
2. Xóa row `accounts`
3. Xóa cookie file `/data/CookiesDir/tiktok_session-<x>.cookie`

API equivalent:
```bash
curl -X DELETE http://127.0.0.1:8000/api/accounts/<id> \
  -H "Origin: http://127.0.0.1:3000"
```

⚠️ Cascade xóa cả schedule pending. Nếu muốn giữ schedule history, sửa endpoint thành soft-delete (xem [11-roadmap](11-roadmap.md)).

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
