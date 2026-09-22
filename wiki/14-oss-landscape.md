# Bối cảnh OSS: kéo video từ YouTube / TikTok / Douyin

**Khảo sát ngày 2026-09-21.** File này ghi *kiến thức* về các thư viện mã nguồn mở
trong mảng này: cái nào làm được gì, cái nào không, và khi chạy thật thì hỏng ở
đâu. File này **không** chốt nên dùng cái nào — chọn gì còn tùy ngữ cảnh lúc cần.

Mọi con số và lỗi dưới đây là đo thật vào ngày trên, không chép từ README. Mảng
scraping đổi rất nhanh, nên phần cuối file có sẵn lệnh để đo lại thay vì tin số cũ.

---

## 1. Bốn nhu cầu của dự án này

Dự án kéo video từ kênh nguồn rồi đăng lên TikTok theo lịch. Chia ra bốn việc, vì
mỗi việc có bức tranh OSS khác hẳn nhau:

| # | Việc | Ví dụ đầu vào → đầu ra |
|---|---|---|
| A | Tải 1 video | link → file mp4 |
| B | Liệt kê feed của 1 kênh | URL kênh → 12 video mới nhất kèm ngày đăng, view, thời lượng |
| C | Tìm kênh theo từ khóa | "kids dance" → danh sách creator đang làm chủ đề đó |
| D | Đăng lên TikTok | file mp4 + caption + cookie account → video đã publish |

Ba thư viện lớn nhất trong mảng (Douyin_TikTok_Download_API, TikTok-Api, f2) đều
tập trung vào A và B. **Không thư viện nào trong số đó làm C.** D thì chỉ có một
repo nhỏ làm, và đó chính là repo đang được fork vào `tiktok_uploader/`.

---

## 2. Cái gì đang nằm trong repo này

Không phải để so hơn thua, chỉ để biết điểm xuất phát khi đọc phần sau.

| Thư viện | Nằm ở đâu | Dùng cho |
|---|---|---|
| yt-dlp | `requirements.txt` | A + B cho YouTube và TikTok |
| `makiisthenes/TiktokAutoUploader` (fork, đã vá 4 bug) | `tiktok_uploader/` | D |
| `douyin-downloader` (vendor) | `vendor/douyin-downloader/` | A + B cho Douyin |
| Playwright Chromium | `docker/base.Dockerfile`, qua `tiktok_uploader/tiktok-signature/node_modules` | ký request upload TikTok; và C cho TikTok |

---

## 3. Từng thư viện

### yt-dlp

Bản đang cài: `2026.08.19`. Mốc phổ biến để so thang: 192 390 sao, 16 694 fork,
tạo 2020-10.

Extractor liên quan, đọc trực tiếp từ bản đang cài:

| Extractor | Phạm vi |
|---|---|
| `TikTok`, `vm.tiktok` | 1 video |
| `tiktok:user` | feed của 1 creator |
| `tiktok:tag`, `tiktok:collection`, `tiktok:sound`, `tiktok:effect`, `tiktok:live` | có khai báo |
| `Douyin` | **chỉ** `https?://(?:www\.)?douyin\.com/video/(?P<id>[0-9]+)` — video lẻ, không list được feed creator |
| YouTube | đầy đủ, kể cả `ytsearch<N>:<từ khóa>` |

Hai điểm đo được, đáng nhớ:

- **`tiktok:user` với `extract_flat` trả về entry đã có sẵn `timestamp`,
  `view_count`, `duration`.** Khác YouTube, nơi flat listing luôn để ba trường đó
  là `None` và phải mở từng video mới có ngày đăng. Nghĩa là số liệu nhịp đăng của
  một kênh TikTok lấy được bằng **một** request, còn YouTube cần thêm một vòng mở
  video lẻ. Đo: 2,5–6,3 giây cho 12 video.
- Ở tầng playlist, `tiktok:user` **không** trả `channel_follower_count` và
  `description`. Muốn số follower + bio thì phải lấy từ nguồn khác.
- `tiktok:tag` hỏng: `ERROR: No working app info is available`. Extractor này đi
  qua API app của TikTok, cần đăng ký thiết bị, và bước đó đang bị chặn.
- **`tiktok:user` cần `curl_cffi`.** Thiếu gói này, yt-dlp cảnh báo `The extractor
  is attempting impersonation, but no impersonate target is available` rồi lùi về
  TLS thường. Cảnh báo bị chìm giữa log, nên triệu chứng trông như bug của yt-dlp
  chứ không như thiếu dependency. Dấu vân tay TLS khác nhau thật: cùng một máy,
  cùng IP công cộng, `ja3_hash` của Python 3.12 trên macOS là
  `4bdac5abde98465c2a2ffd5401d73cfd`, của Python 3.11 trong container Debian là
  `331a436afb23d4e31134c11b301bdcb5`.
- **TikTok bóp request feed theo đợt, và bóp bằng cách trả về danh sách rỗng —
  không phải mã lỗi.** Quét liên tiếp nhiều kênh thì đến giữa chừng mọi kênh bắt
  đầu trả 0 video, mỗi lần chỉ mất ~0,7 giây (quá nhanh so với một vòng HTTP
  thật), rồi vài giây sau chính kênh đó lại trả bình thường. Không phân biệt được
  với "kênh chết" nếu chỉ thử một lần — phải retry mới biết. Đây là lý do một lượt
  quét không retry cho ra 0 ứng viên trên 12 kênh, còn có retry thì ra 12/12.
- **Có hai cách gọi `tiktok:user`, không tương đương nhau.** Dạng
  `https://www.tiktok.com/@<handle>` bắt yt-dlp cào trang profile trước để moi
  `secUid` ra. Dạng `tiktokuser:<secUid>` đi thẳng vào feed, bỏ hẳn bước cào đó —
  chính bước hay hỏng. Thông báo lỗi của yt-dlp có gợi ý sẵn dạng thứ hai.

### Evil0ctal/Douyin_TikTok_Download_API

Apache-2.0. 20 213 sao, 2 802 fork, tạo 2021-11, push gần nhất 2026-09-15, **3
issue đang mở** — tỉ lệ issue mở trên sao thấp nhất trong nhóm này, tác giả đóng
rất nhanh.

Nhánh `main` hiện là v5, viết lại toàn bộ. Nhánh `v4` vẫn được cập nhật.

Nó là gì: một **service tự host**, không phải thư viện import. Python 3.12 +
FastAPI + PostgreSQL/TimescaleDB + Redis, console React, 93 operation REST, kèm
MCP server và CLI `dtk`. Khởi điểm 2021 là trang web dán link lấy mp4 không
watermark, cộng iOS Shortcut — đó là nguồn của phần lớn số sao.

Bao phủ, theo bảng endpoint trong tài liệu của nó (`{platform}` là `douyin` hoặc
`tiktok`):

```
POST /api/v1/parse                          bất kỳ link nào
GET  /api/v1/{platform}/video               1 post
GET  /api/v1/{platform}/user                profile
GET  /api/v1/{platform}/user/posts          feed creator, có phân trang
GET  /api/v1/{platform}/video/comments
GET  /api/v1/{platform}/mix/posts
GET  /api/v1/{platform}/user/collections | collection | bookmarks | reposts | followers | following
```

Tức là **A và B, cho cả hai nền tảng**.

**Không có route search.** Kiểm chứng: grep toàn bộ cây 665 file, không file nào
có "search" trong đường dẫn; `src/dtk/api/routes/content.py` không khai báo
handler search nào. Riêng `src/dtk/platforms/douyin/endpoints.py` **có** hằng
`GENERAL_SEARCH`, `VIDEO_SEARCH`, `USER_SEARCH`, `LIVE_SEARCH` nhưng không route
nào gọi tới. `src/dtk/platforms/tiktok/endpoints.py` không có hằng search nào.
Chữ "search" trong mô tả dự án chỉ ám chỉ tìm trong kho lưu cục bộ
(`/api/v1/archive/...`).

Hai thứ nó làm mà repo này chưa có:

- **Ký `a_bogus` / `X-Bogus` / chữ ký TikTok bằng Python thuần, trong tiến trình.**
  Headless browser chỉ còn là fallback và là bộ dò khi thuật toán đổi.
- **Identity pool tự vận hành**: tự tạo, tự test, tự cho nghỉ, tự loại cookie +
  proxy + fingerprint. Tài liệu của nó nói `ttwid` là cookie duy nhất bắt buộc và
  pool tự mint được cookie đó; cookie account của người dùng chỉ cần cho nội dung
  riêng tư. Đi kèm health từng identity và circuit breaker từng endpoint.

Cái giá, theo tài liệu cài đặt của nó:

| Hạng mục | Số |
|---|---|
| Image lõi trên đĩa | ~4,5 GB (riêng `timescale/timescaledb-ha:pg17` ~3,9 GB, `dtk-app` ~420 MB, `redis:8-alpine` ~160 MB) |
| Thêm container browser | +1,7 GB |
| RAM khuyến nghị | 2 GB đủ chạy lõi, 4 GB an toàn, 8 GB nếu bật container browser |
| RAM lúc nghỉ | postgres 112 MiB, api 104 MiB, worker 71 MiB, redis 8 MiB |
| Container | api, worker, migrate, postgres, redis |

Không có registry image công khai — build tại chỗ ở lần `up` đầu.

### davidteather/TikTok-Api

MIT. 6 638 sao, 1 214 fork, tạo 2019-05, push gần nhất 2026-08-24, 151 issue đang
mở. Bản trên PyPI: `TikTokApi` 7.3.3.

Thư viện Python, chạy Playwright bên dưới, có bộ script stealth riêng
(`TikTokApi/stealth/`). Object model rộng: `user`, `video`, `hashtag`, `sound`,
`comment`, `playlist`, `trending`, `search`. Đối tượng chính là người làm nghiên
cứu và phân tích dữ liệu, không phải người tải video.

Điểm đáng chú ý về mặt kỹ thuật: nó công khai luôn tầng thấp —
`create_sessions()` quản lý pool phiên browser kèm `_is_session_valid`,
`_recover_sessions`, `health_check`; và `sign_url()`, `generate_x_bogus()`,
`make_request(url, params, headers, session_index)` cho phép gọi **bất kỳ**
endpoint web nào của TikTok với chữ ký do chính trang sinh ra.

Giới hạn ghi ngay trong `TikTokApi/api/search.py`:

> Currently only supports searching for users, other endpoints require auth.

Và: `Your ms_token needs to have done a search before for this to work.`

Nghĩa là `search` của nó tìm **user theo tên**, không tìm creator theo nội dung.

### Johnserf-Seed/f2

Apache-2.0. 2 654 sao, 405 fork, tạo 2023-01, push gần nhất 2026-09-12, 114 issue
đang mở. Kế thừa `Johnserf-Seed/TikTokDownload` (8 864 sao nhưng đứng im từ
2024-06).

Downloader async đa nền tảng chạy bằng CLI + config YAML: douyin, tiktok, và các
site khác. Handler cho mỗi nền tảng nằm ở `f2/apps/<platform>/handler.py`.

**f2 là thư viện duy nhất trong nhóm có tìm kiếm theo từ khóa cho TikTok:**
`TiktokHandler.fetch_search_videos(keyword, offset, page_counts, max_counts)`, có
phân trang qua `search_id` + `cursor`. Filter `PostSearchFilter` bóc sẵn
`author.nickname`, `author.id`, `author.secUid`, `video_duration`, `createTime` —
**không** bóc `author.uniqueId` và `authorStats.followerCount`, nhưng
`_to_raw()` trả về nguyên JSON nên vẫn lấy được.

Phía Douyin thì khác với những gì tên hàm gợi ý: `fetch_home_post_search` có chữ
ký `(user_id, keyword, search_id, offset, page_counts, max_counts)` — tham số đầu
là user_id, tức tìm trong trang của một user, **không** phải tìm toàn nền tảng.

**Bản trên PyPI đã cũ 21 tháng.** Bản mới nhất là `0.0.1.7` phát hành
2024-12-31, trong khi repo vẫn push đều. Muốn bản chạy được phải cài từ git.

### makiisthenes/TiktokAutoUploader

MIT. 1 168 sao, 245 fork, tạo 2021-03, push gần nhất 2026-08-10, 45 issue đang mở.

Là upstream của `tiktok_uploader/` trong repo này. Cách tiếp cận: gọi thẳng API
upload của TikTok với cookie + chữ ký sinh bằng Node
(`tiktok_uploader/tiktok-signature/`, dùng `playwright-chromium`).

Đối chiếu ngày 2026-09-21: **upstream vẫn còn nguyên cả bốn bug** mà bản fork
trong repo này đã vá — chỉ set 2 cookie thay vì cả jar, `UserAgent().random`
thay vì ghim desktop Chrome, `dc_id = "useast2a"` khi thiếu `tt-target-idc`, và
thiếu `return True` ở cuối `upload_video()`. Chi tiết từng bug ở
[10-known-bugs](10-known-bugs.md).

### wkaisertexas/tiktok-uploader

MIT. 772 sao, push gần nhất 2026-02-12. Cách tiếp cận khác hẳn: Selenium điều
khiển giao diện TikTok Studio — click nút, điền ô mô tả. Issue đang mở phản ánh
đúng điểm yếu của cách này: "Upload fails due to modal overlay intercepting
clicks", "Something went wrong error when clicking Post", "handle got it modal".
Giao diện đổi là hỏng; gọi API có ký thì không phụ thuộc giao diện.

---

## 4. Bảng bao phủ

Đánh dấu theo những gì kiểm chứng được ngày 2026-09-21.

| | A. Tải 1 video | B. Feed creator | C. Tìm theo từ khóa | D. Đăng lên TikTok |
|---|---|---|---|---|
| **YouTube** | yt-dlp | yt-dlp | yt-dlp (`ytsearch<N>:`) | — |
| **TikTok** | yt-dlp, Evil0ctal | yt-dlp, Evil0ctal | f2 (chạy thử hỏng, mục 5), TikTok-Api (chỉ tìm user theo tên) | makiisthenes |
| **Douyin** | yt-dlp (video lẻ), douyin-downloader, Evil0ctal | douyin-downloader, Evil0ctal, f2 | douyin-downloader (`search_aweme`) | — |

Ô trống thật sự: **không có OSS nào nhận từ khóa rồi trả về danh sách creator
TikTok theo nội dung.**

---

## 5. Chạy thật thì hỏng ở đâu

Phần này là giá trị chính của file — README không nói những điều dưới đây.

### f2: TikTok hỏng ngay lúc import

Cài `f2` từ PyPI, rồi cài lại từ `git+https://github.com/Johnserf-Seed/f2.git@main`,
kết quả giống nhau:

```
from f2.apps.tiktok.handler import TiktokHandler
...
  File ".../f2/apps/tiktok/utils.py", line 1120, in DeviceIdManager
    _MSTOKEN = TokenManager.gen_real_msToken()
  File ".../f2/apps/tiktok/utils.py", line 174, in gen_real_msToken
    raise APIResponseError(_("{0} 内容不符合要求").format("msToken"))
f2.exceptions.api_exceptions.APIResponseError: msToken 内容不符合要求
```

`gen_real_msToken()` gọi mạng ngay lúc định nghĩa class `DeviceIdManager`, và kiểm
tra `len(msToken) != 148`. TikTok đổi độ dài token → import fail. Không phải một
đường chạy bị suy giảm, mà là `import` chết hẳn, không cách nào bắt lỗi từ phía
người dùng thư viện.

`from f2.apps.douyin.handler import DouyinHandler` thì import bình thường.

### TikTok-Api: bị chặn ở search

```
resp = await api.make_request(url="https://www.tiktok.com/api/search/general/full/",
                              params={...}, headers={}, session_index=0)
TikTokApi.exceptions.EmptyResponseException: None -> TikTok returned an empty
response. They are detecting you're a bot, try some of these: headless=False,
browser='webkit', consider using a proxy
```

Thử thêm: cho phiên của nó `goto` trang `/search?q=...` trước rồi chờ 12 giây rồi
mới `make_request` — vẫn chặn. Và chính trang đó cũng không render kết quả: quét
`a[href*="/@"]` chỉ ra `['/@']`. Phiên browser của TikTok-Api bị nhận diện ngay
cả khi duyệt bình thường.

### Đo đạc trên TikTok web

Bốn phép thử, cùng một máy, cùng ngày:

| Cách | Kết quả |
|---|---|
| `GET /api/search/general/full/?keyword=...` bằng `requests`, UA trình duyệt | HTTP 200, body **0 byte** |
| `GET /search?q=...` bằng `requests` | HTTP 200, 380 KB HTML, có `__UNIVERSAL_DATA_FOR_REHYDRATION__` nhưng trong đó **không có** kết quả tìm kiếm (render phía client). `seo.abtest.vidList` rỗng |
| Chromium mở thẳng `/search?q=...` | XHR `/api/search/general/full/` trả HTTP 200, body **0 byte** |
| Chromium mở trang chủ `tiktok.com`, chờ ~5 giây, **rồi** mở `/search?q=...` | XHR đó trả **251 KB JSON**, có `data[].item.author` (`id`, `uniqueId`, `nickname`, `signature`), `authorStats.followerCount`, `stats.playCount`, `createTime`, `video.duration` |

Điểm mấu chốt: **khác biệt nằm ở việc ghé trang chủ trước** — đó là bước mint
`ttwid`/`msToken` mà request search được ký bằng. Ba thư viện trên đều tự ký rồi
gọi thẳng API, nên đều rơi vào hàng đầu tiên của bảng. Đọc lại JSON mà chính
trang đã gọi thì không phải ký gì cả.

Cùng phép thử đó cho gợi ý từ khóa: `/api/search/suggest/guide/` và
`/api/search/general/preview/` trả dữ liệu ngay cả khi chưa đăng nhập.

### douyin-downloader: cookie thiếu phiên đăng nhập

Douyin phân biệt hai mức. Liệt kê bài của một creator thì khách vãng lai xem
được; **tìm kiếm theo từ khóa thì bắt buộc đăng nhập**. Gọi `search_aweme` với
cookie không có phiên đăng nhập, Douyin trả về:

```json
{"status_code": 2483, "status_msg": "请先登录，再继续搜索吧"}
```

`api/services/douyin_cookie_import.py` chỉ giữ 5 key — `ttwid`, `msToken`,
`odin_tt`, `passport_csrf_token`, `sid_guard` — đủ cho mức khách vãng lai, không
đủ cho tìm kiếm (thiếu `sessionid` / `sessionid_ss` / `sid_tt`). Phân biệt hai mã
lỗi khi gỡ rối: `2483` là cookie không có phiên đăng nhập, HTTP `403` là cookie
đã hết hạn.

### douyin-downloader: phụ thuộc cookie

Vendor trong repo đọc cookie từ bảng `douyin_cookies`. Cookie hết hạn thì
`search_aweme` ghi log `Search returned no items ... Possible causes: cookies
expired, signature rejected, or query blocked` rồi trả rỗng — hỏng âm thầm, không
raise. Xem thêm [13-douyin-pipeline](13-douyin-pipeline.md).

Dep của vendor (`aiofiles`, ...) được cài từ
`vendor/douyin-downloader/requirements.txt` trong image Docker, **không** có
trong venv trên máy host. Chạy vendor bằng venv host sẽ gặp
`ModuleNotFoundError: No module named 'aiofiles'`.

---

## 6. Đọc con số sao/fork thế nào

Vài điều rút ra khi đối chiếu số với kết quả chạy thật:

- **Sao đếm số người có nhu cầu, không đếm mức phù hợp với nhu cầu của mình.**
  "Tải video TikTok này về máy" là nhu cầu đại chúng — đó là việc A. "Tìm kênh
  theo từ khóa rồi hẹn giờ re-upload lên nhiều account" là nhu cầu của rất ít
  người — đó là việc C và D. Chênh lệch sao giữa hai nhóm phản ánh chênh lệch quy
  mô người dùng, không phản ánh chênh lệch chất lượng.
- **Sao tích lũy theo năm.** TikTok-Api tạo 2019, Evil0ctal 2021.
- **Fork cao ở repo scraping thường là triệu chứng.** Phần lớn fork là người tự vá
  khi thư viện hỏng. Đọc kèm số issue đang mở sẽ rõ hơn: TikTok-Api 151, f2 114,
  Evil0ctal 3.
- **Ngày push của repo không bằng ngày phát hành package.** f2 push 2026-09-12
  nhưng PyPI đứng ở 2024-12-31.
- **Phần khó của các service lớn là quy mô.** Identity pool, xoay proxy, circuit
  breaker từng endpoint — những thứ đó giải bài toán phục vụ nhiều người dùng và
  hàng nghìn request. Dự án này chạy một operator, vài chục kênh, quét thủ công.
  Chi phí hạ tầng đi kèm là để giải bài toán khác.

---

## 7. Đo lại

Số ở trên hỏng nhanh. Lệnh để kiểm lại, không cần đọc lại README ai:

```bash
# Sao / fork / ngày push / issue đang mở
for r in Evil0ctal/Douyin_TikTok_Download_API davidteather/TikTok-Api \
         Johnserf-Seed/f2 makiisthenes/TiktokAutoUploader; do
  gh api "repos/$r" --jq '"\(.full_name) ★\(.stargazers_count) fork=\(.forks_count) push=\(.pushed_at[:10]) open=\(.open_issues_count)"'
done

# Package trên PyPI có theo kịp repo không
curl -s https://pypi.org/pypi/f2/json | python3 -c \
  "import sys,json;d=json.load(sys.stdin);v=d['info']['version'];print(v, d['releases'][v][0]['upload_time'])"

# Evil0ctal đã có route search chưa
gh api "repos/Evil0ctal/Douyin_TikTok_Download_API/git/trees/main?recursive=1" \
  --jq '.tree[].path' | grep -i search

# Upstream uploader đã vá 4 bug chưa (xem wiki/10-known-bugs.md)
gh api "repos/makiisthenes/TiktokAutoUploader/contents/tiktok_uploader/tiktok.py" \
  -H "Accept: application/vnd.github.raw" | grep -nE "useast2a|UserAgent\(|for c in cookies"

# yt-dlp còn nhận URL nào
. .venv/bin/activate && python -c "
from yt_dlp.extractor import gen_extractor_classes
for c in gen_extractor_classes():
    if any(k in c.IE_NAME.lower() for k in ('tiktok','douyin')):
        print(c.IE_NAME, '|', (c._VALID_URL or '')[:120])"
```

Thử một thư viện thì cài vào venv tạm, đừng đụng `.venv` của dự án:

```bash
python3 -m venv /tmp/oss-probe && /tmp/oss-probe/bin/pip install <thư-viện>
```

---

**Liên quan:** [10-known-bugs](10-known-bugs.md) — bốn bug trong `tiktok.py` và vì
sao bản fork không được ghi đè khi sync upstream ·
[13-douyin-pipeline](13-douyin-pipeline.md) — vendor douyin-downloader chạy thế nào
