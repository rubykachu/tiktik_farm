# 10 — Bug đã fix từ upstream

[← Index](README.md) | [← 09-operations](09-operations.md)

## Bối cảnh

Source `makiisthenes/TiktokAutoUploader` upstream có 4 bug khiến upload fail trên thực tế. Đã fix trong cây code v2 này. Khi sync upstream về sau, đảm bảo các fix không bị overwrite.

Tất cả 4 bug đều trong `tiktok_uploader/tiktok.py`. Trigger không phụ thuộc OS / Docker — VPS Ubuntu cũng dính y vậy nếu chưa fix.

## Bug 1 — Cookie jar không đầy đủ

### Triệu chứng

`POST /api/v1/web/project/create/` trả `{"status_code":8,"status_msg":""}` → code parse `r.json()["project"]["project_id"]` → `KeyError: 'project'`.

### Root cause

Upstream code:
```python
session.cookies.set("sessionid", session_id, domain=".tiktok.com")
session.cookies.set("tt-target-idc", dc_id, domain=".tiktok.com")
```

Chỉ set 2 cookies. TikTok antibot reject vì thiếu `msToken`, `ttwid`, `tt_csrf_token`, `sid_guard`, ... (15-18 cookies khác).

### Fix

Loop set toàn bộ cookies từ pickle vào `requests.Session.cookies`:

```python
for c in cookies:
    session.cookies.set(
        c["name"], c["value"],
        domain=c.get("domain", ".tiktok.com"),
        path=c.get("path", "/"),
    )
```

## Bug 2 — Random UA trả mobile

### Triệu chứng

Giống bug 1: status_code 8, no `project` key. Xảy ra ngẫu nhiên ~50% requests.

### Root cause

```python
user_agent = UserAgent().random
```

`fake_useragent>=1.4` (loose pin trong requirements.txt) trả random UA từ pool — bao gồm iPhone, Android. TikTok `/api/v1/web/*` endpoint **chỉ chấp nhận desktop UA**. Mobile UA → status_code 8.

Test reproduce:
```python
>>> from fake_useragent import UserAgent
>>> for _ in range(5): print(UserAgent().random)
Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 ...
Mozilla/5.0 (Linux; Android 14 ...
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...
```

### Fix

Filter UserAgent với platform = desktop:

```python
try:
    ua_obj = UserAgent(platforms="desktop", browsers="Chrome")
    user_agent = ua_obj.random
except Exception:
    user_agent = _UA   # fallback constant
```

## Bug 3 — Hardcode datacenter `useast2a`

### Triệu chứng

Giống bug 1: status_code 8. Chỉ trigger khi cookie pickle KHÔNG chứa `tt-target-idc`.

### Root cause

```python
if not dc_id:
    print("[WARNING]: ... datacenter id must be allocated")
    dc_id = "useast2a"  # hardcoded guess
```

`tt-target-idc` là routing hint cho TikTok datacenter (`useast1a`, `useast2a`, `useast5`, `alisg`, ...). Mỗi account "thuộc" 1 DC. Force `useast2a` cho account ở DC khác → TikTok routing fail → status_code 8.

Cookie từ:
- `cli.py login` Selenium: tự collect cả 2 cookie (`wait_for_session_cookies` blocks đến khi có cả `sessionid` + `tt-target-idc`) → bug 3 không trigger
- Cookie-Editor / Chrome DevTools export: thường miss `tt-target-idc` (HttpOnly subdomain) → bug 3 trigger

### Fix

Bỏ default. Để TikTok tự resolve DC từ `sessionid`:

```python
if not dc_id:
    print("[INFO] tt-target-idc not in cookie; relying on session-resolved routing")
# KHÔNG default `dc_id = "useast2a"` nữa
```

## Bug 4 — Thiếu `return True` trên success

### Triệu chứng

CLI in `Published successfully` (upload đã thành công trên TikTok). Nhưng API response `{"ok": false, "message": "upload failed"}`. User confused.

### Root cause

Cuối hàm `upload_video()`:

```python
if not uploaded:
    print("[-] Could not upload video")
    return False
# Function falls off → returns None
```

`tiktok_adapter.perform_upload()` đọc:
```python
return bool(result)   # bool(None) = False
```

→ API thấy `ok=False` dù video đã đăng.

### Fix

Add explicit `return True` sau `if not uploaded`:

```python
if not uploaded:
    return False
return True   # ⬅️ thêm
```

## Tất cả 4 bug môi trường-independent?

| Bug | Env-independent | Note |
|---|---|---|
| #1 | ✅ | Source code hardcode |
| #2 | ✅ | Phụ thuộc `fake_useragent` version → pin chặt nếu muốn deterministic |
| #3 | ❌ một phần | Chỉ trigger nếu cookie thiếu `tt-target-idc`. Login qua Selenium CLI → an toàn. Export Cookie-Editor → bị. |
| #4 | ✅ | Source code hardcode |

→ Trên VPS Ubuntu Docker, **cả 4 bug dính** nếu chưa fix. Đảm bảo fork ta đã apply patches trước deploy.

## Chiến lược maintain với upstream

### Khi nào cần sync

Upload fail với log: `[-] Failed to generate signatures` → TikTok đã đổi signing algorithm. Kiểm tra upstream `makiisthenes/TiktokAutoUploader` — họ thường fix trong 1-2 ngày.

### Cái gì quan trọng

TikTok signing nằm trong 2 thành phần:

| Path | Vai trò |
|---|---|
| `tiktok_uploader/tiktok-signature/index.js` | Playwright launcher, gọi TikTok JS |
| `tiktok_uploader/tiktok-signature/javascript/signer.js` | TikTok sign function (57 KB) |
| `tiktok_uploader/tiktok-signature/javascript/webmssdk.js` | TikTok SDK (265 KB) |
| `tiktok_uploader/tiktok-signature/javascript/xbogus.js` | X-Bogus calculator (49 KB) |
| `tiktok_uploader/tiktok.py` | Upload flow — có **4 bug fix của mình** |

Khi upstream update, phần hay thay đổi nhất: `javascript/*.js` + cách gọi trong `index.js`.

### Quy trình manual sync

```bash
# 1. Download upstream tiktok-signature mới
#    (tương tự pattern scripts/update-douyin-vendor.sh)
curl -L https://raw.githubusercontent.com/makiisthenes/TiktokAutoUploader/main/tiktok_uploader/tiktok-signature/javascript/signer.js \
    -o tiktok_uploader/tiktok-signature/javascript/signer.js
curl -L https://raw.githubusercontent.com/makiisthenes/TiktokAutoUploader/main/tiktok_uploader/tiktok-signature/javascript/webmssdk.js \
    -o tiktok_uploader/tiktok-signature/javascript/webmssdk.js
curl -L https://raw.githubusercontent.com/makiisthenes/TiktokAutoUploader/main/tiktok_uploader/tiktok-signature/javascript/xbogus.js \
    -o tiktok_uploader/tiktok-signature/javascript/xbogus.js

# 2. Nếu index.js / browser.js cũng thay đổi — download thêm:
curl -L https://raw.githubusercontent.com/makiisthenes/TiktokAutoUploader/main/tiktok_uploader/tiktok-signature/index.js \
    -o tiktok_uploader/tiktok-signature/index.js

# 3. Nếu upstream fix gì trong tiktok.py — diff thủ công:
#    So sánh upstream tiktok.py với local, chỉ lấy thay đổi liên quan đến signing.
#    KHÔNG copy toàn bộ file — sẽ mất 4 bug fix.
```

### 4 bug fix KHÔNG được overwrite

Khi diff `tiktok.py` với upstream, đảm bảo 4 dòng này còn nguyên:

| Bug | Dòng cần giữ |
|---|---|
| #1 full cookie jar | Loop `for c in cookies: session.cookies.set(...)` |
| #2 desktop UA | `UserAgent(platforms="desktop", browsers="Chrome")` |
| #3 no default dc | `if not dc_id: print("[INFO] ...")` — KHÔNG có `dc_id = "useast2a"` |
| #4 return True | `return True` cuối `upload_video()` |

### Pin version

`requirements.txt` phải pin `fake-useragent` để bug 2 không tự regress giữa các deploy:

```
fake-useragent==2.2.0
```

## Memory link

Chi tiết fix được lưu trong Claude memory: `tiktok-project-create.md`. Khi debug `KeyError: 'project'` trong tương lai, đọc memory đó trước.

---

**Next:** [11-roadmap](11-roadmap.md)
