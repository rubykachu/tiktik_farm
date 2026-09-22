# Handover — bật tìm kiếm Douyin theo từ khóa

Trạng thái: **dừng, chờ tài khoản Douyin.** Không phải bug, không phải thiếu code.
Viết 2026-09-22.

Màn hình Tìm kênh (`/discovery`) đã có nền tảng Douyin. Chọn Douyin, nhập từ khóa,
bấm Quét ngay — lượt quét chạy rồi kết thúc ở trạng thái lỗi với đúng dòng này:

```
Douyin status_code=2483: 请先登录，再继续搜索吧
```

Dịch: "đăng nhập trước đã rồi tìm tiếp".

---

## 1. Vì sao

Douyin chia hai mức quyền:

| Việc | Khách vãng lai |
|---|---|
| Đọc danh sách bài của một creator | được |
| **Tìm theo từ khóa** | **không** |

Cookie đang lưu trong bảng `douyin_cookies` chỉ có 5 key:

```
ttwid, msToken, odin_tt, passport_csrf_token, sid_guard
```

Đủ cho mức khách vãng lai. Thiếu `sessionid` — key duy nhất chứng minh phiên đăng
nhập. Douyin thấy khách vãng lai nên từ chối tìm kiếm.

Thiếu vì `api/services/douyin_cookie_import.py` cắt bớt **lúc import**:

```python
_KEYS = ("ttwid", "msToken", "odin_tt", "passport_csrf_token", "sid_guard")
...
return {key: by_name.get(key, "") for key in _KEYS}
```

File cookie người dùng upload có bao nhiêu key cũng vậy — chỉ 5 key trên được giữ.
Nên **export lại cookie đầy đủ rồi upload mà không sửa code thì vẫn ra 5 key.**
Phải sửa code trước, upload sau.

---

## 2. Đã xác minh, đừng kiểm lại

Bốn điều dưới đây đo thật ngày 2026-09-21 và 2026-09-22. Ghi ra để lần sau không
đi lại đường cụt.

**Cookie đăng nhập có tới được API search.** Vendor có
`DouyinAPIClient._BROWSER_COOKIE_BLOCKLIST` chứa `sessionid`, `sid_tt`… trông như
nó chặn cookie đăng nhập. Không phải: blocklist đó chỉ dùng ở
`collect_user_post_ids_via_browser` để dựng payload cookie cho Playwright. Đường
API đi qua `aiohttp.ClientSession(cookies=self.cookies)` và gửi **nguyên jar**.
Thêm `sessionid` là nó tới nơi.

**Đường browser không cứu được.** Đã thử mở `douyin.com/search/<từ khóa>` bằng
headless Chromium trong container, hai lần: một lần không cookie, một lần có cookie
5 key đang lưu. Cả hai đều **không bắn ra XHR search nào** (`xhr: []`) và trang vẫn
hiện nút 登录. Douyin chặn ở tầng phân quyền, không phải ở chữ ký — nên thủ thuật
"để browser ký hộ" đang dùng cho TikTok **không áp dụng được ở đây**. Đừng viết lại
Douyin theo hướng đó.

**Đường tải video không cần sửa.** `api/services/douyin_channel.py` dựng config cho
vendor bằng cách gọi tên từng key:

```python
"cookies": {
    "msToken": cookie_dict.get("msToken", ""),
    "ttwid": cookie_dict.get("ttwid", ""),
    "odin_tt": cookie_dict.get("odin_tt", ""),
    "passport_csrf_token": cookie_dict.get("passport_csrf_token", ""),
    "sid_guard": cookie_dict.get("sid_guard", ""),
},
```

DB có thêm bao nhiêu key nó cũng chỉ nhặt đúng 5 key nó gọi tên. Còn đường search
(`api/services/discovery_sources/douyin.py::_cookies`) lấy nguyên cục:

```python
return json.loads(secrets.decrypt(row.cookie_json_encrypted) or "{}")
```

Nên nới `_KEYS` **chỉ ảnh hưởng search**. Tải video chạy y hệt, không cần
duplicate cookie riêng, không cần thêm bảng, không cần cột mới.

Tính chất này đáng giữ: tải video ở mức khách vãng lai, chỉ search mới mang danh
tính account. Bề mặt rủi ro bị khoá đúng chỗ cần.

**Lỗi đã hiện lên UI.** Trước đây `search_aweme` trả rỗng không kèm lỗi nên lượt
quét kết thúc "done, 0 ứng viên" — đọc như là không có kênh nào. Nay
`douyin_bridge._refusal()` bắt `status_code` khác 0 rồi đẩy `status_msg` của Douyin
lên, nên lượt quét chuyển thành `error` kèm nguyên văn. Không phải sửa thêm.

---

## 3. Cần quyết trước khi làm

Lưu `sessionid` nghĩa là lưu thứ **tương đương mật khẩu account Douyin**. Ai đọc
được `data/tiktok.db` cùng `.secret_key` thì vào được account đó. Cùng mức bảo vệ
với cookie TikTok đang lưu sẵn, nhưng phạm vi rộng hơn 5 key hiện tại.

Người vận hành quyết, không phải người code.

Đồng ý rồi thì chọn một trong hai:

| | Cách A — whitelist mở rộng | Cách B — lưu nguyên jar |
|---|---|---|
| Làm gì | Thêm 6 tên vào `_KEYS`: `sessionid`, `sessionid_ss`, `sid_tt`, `uid_tt`, `uid_tt_ss`, `passport_auth_status` | Bỏ hẳn bước cắt, giữ mọi key trong file export |
| Được | Biết chính xác lưu cái gì | Đúng ngay lần đầu; Douyin thêm cookie mới không phải sửa code |
| Mất | Douyin cần key ngoài 6 tên đó thì vẫn lỗi, phải làm thêm một vòng | Lưu cả cookie không liên quan (quảng cáo, A/B test) |

Khuyến nghị **B**: một khi `sessionid` đã nằm trong DB thì phần nhạy cảm nhất đã ở
đó rồi, mấy key còn lại gần như không tăng rủi ro, mà B thì không phải đoán tên key.

---

## 4. Các bước

**Bước 1 — code.** Sửa `_KEYS` trong `api/services/douyin_cookie_import.py` theo
cách đã chọn. Giữ nguyên yêu cầu `ttwid` bắt buộc (`parse_cookie_editor_json` raise
`DouyinCookieImportError` khi thiếu). Không đụng `douyin_channel.py`.

**Test sẽ đỏ, và đây là danh sách chính xác:**

| Chỗ | Đang khoá gì |
|---|---|
| `tests/test_douyin_cookie_import.py::TestToDownloaderYamlDict::test_returns_expected_keys` | `assert set(result.keys()) == {"ttwid","msToken","odin_tt","passport_csrf_token","sid_guard"}` |
| `tests/test_douyin_cookie_import.py` các test quanh `parse_cookie_editor_json` | kiểm dict trả về đúng 5 key |

Cập nhật chúng theo danh sách mới. Đừng nới assert thành "chứa ít nhất" — mất luôn
tác dụng canh gác thứ mình lưu.

**Về `to_downloader_yaml_dict()`:** nó **không có caller nào trong code chạy** —
chỉ `tests/test_douyin_cookie_import.py` gọi, tức test đang test một hàm không ai
dùng. Đường tải video không đi qua nó mà tự gọi tên key trong
`douyin_channel._build_config` (xem mục 2). Nó có sẵn từ trước, không phải hệ quả
của việc này. Sửa `_KEYS` mà để nguyên thì thành hai nơi khai báo danh sách key và
lệch nhau ngay. Gợi ý: xoá hàm cùng class test của nó, hoặc cho nó gọi `_KEYS`.
Quyết định thuộc người làm việc này.

`make start`.

**Bước 2 — người vận hành.**

1. Chrome mở `douyin.com`, đăng nhập (quét QR bằng app Douyin).
2. Xác nhận đã vào: góc phải hiện avatar, **không còn** nút 登录.
3. Cookie-Editor → Export → Export as JSON.
4. Kiểm file trước khi upload:

   ```bash
   python3 -c "import json,sys;d=json.load(open(sys.argv[1]));ks={c['name'] for c in d};print('sessionid:', 'sessionid' in ks);print('tổng key:',len(ks))" <file.json>
   ```

   Phải in `sessionid: True`. `False` nghĩa là chưa đăng nhập thật — làm lại bước 1.
5. Màn hình Douyin cookie trên webapp → upload đè lên cookie cũ. Không cần xoá
   dòng cũ.

**Bước 3 — kiểm.**

```bash
docker compose exec -T api python -c "
from api.services.discovery_sources import douyin
c = douyin._cookies()
print('keys:', sorted(c))
print('có sessionid?', 'sessionid' in c)
print(douyin.search_channels(['舞蹈'],'CN','zh-CN'))
"
```

Đạt: in ra danh sách creator, không còn `2483`.

---

## 5. Nếu vẫn lỗi

| Dấu hiệu | Nghĩa | Làm gì |
|---|---|---|
| Vẫn `status_code=2483` | Còn thiếu key nào đó | Đang ở cách A thì chuyển sang cách B |
| HTTP `403` | Cookie hết hạn, hoặc IP bị chặn | Export lại cookie. Vẫn 403 thì thử IP khác |
| `Chưa có cookie Douyin nào` | Bảng `douyin_cookies` rỗng | Upload cookie trước |

---

## 6. Việc khác còn treo ở phía Douyin

**Đường tải video Douyin chưa từng chạy trong deployment này.** DB không có
pipeline nào `source_type=douyin`, nên chưa ai xác minh nó hoạt động. Cookie đang
lưu từ **2026-05-24** — 4 tháng, nhiều khả năng đã hết hạn. Khi nào có account thì
kiểm luôn bằng một link kênh Douyin bất kỳ, đừng giả định nó chạy.

**Douyin không có gợi ý từ khóa.** `SUPPORTS_SUGGESTIONS = False` trong
`api/services/discovery_sources/douyin.py` — vendor không expose endpoint
autocomplete nào. Giao diện ẩn nút "Gợi ý" và nhắc gõ tay. Không nằm trong phạm vi
việc này.

---

**Đọc thêm:** `wiki/15-discovery.md` — bảng tính năng 3 nền tảng và cơ chế từng cái ·
`wiki/13-douyin-pipeline.md` — cookie Douyin và vendor subprocess ·
`wiki/14-oss-landscape.md` — vì sao không thay vendor bằng thư viện OSS khác
