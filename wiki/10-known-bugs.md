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
- `cli.py login` Selenium / noVNC: tự collect cả 2 cookie (`wait_for_session_cookies` blocks đến khi có cả `sessionid` + `tt-target-idc`) → bug 3 không trigger
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
| #3 | ❌ một phần | Chỉ trigger nếu cookie thiếu `tt-target-idc`. Login qua Selenium/noVNC → an toàn. Export Cookie-Editor → bị. |
| #4 | ✅ | Source code hardcode |

→ Trên VPS Ubuntu Docker, **cả 4 bug dính** nếu chưa fix. Đảm bảo fork ta đã apply patches trước deploy.

## Chiến lược maintain với upstream

Repo upstream activity thấp. Đề xuất:

1. **Fork** trên GitHub dưới account/org của anh
2. Branch `farm-tiktik-patches` từ upstream main
3. Commit từng fix riêng (4 commits độc lập, message rõ ràng)
4. Submit PR upstream cho bug 1, 2, 4 (obviously correct, đỡ phải tự maintain)
5. Bug 3 giữ local (upstream có thể reject vì "login flow chúng tôi không hit case này")
6. `git fetch upstream && git rebase upstream/main` định kỳ
7. **Pin versions** trong `requirements.txt`:
   ```
   fake-useragent==2.2.0
   ```
   để bug 2 không tự đổi mặt giữa các deploy

## Regression test

Viết test pin các fix để CI red khi upstream regress:

```python
# tests/test_upload_regression.py
def test_full_cookie_jar_loaded_into_session(monkeypatch): ...
def test_desktop_ua_filter_applied(monkeypatch): ...
def test_no_default_tt_target_idc(monkeypatch): ...
def test_upload_video_returns_true_on_success(monkeypatch): ...
```

(Chưa viết — phase 2 task.)

## Memory link

Chi tiết fix được lưu trong Claude memory: `tiktok-project-create.md`. Khi debug `KeyError: 'project'` trong tương lai, đọc memory đó trước.

---

**Next:** [11-roadmap](11-roadmap.md)
