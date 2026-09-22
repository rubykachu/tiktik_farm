# Tìm kênh theo từ khóa

Màn hình **Tìm kênh** (`/discovery` trên webapp). Nhập từ khóa, quét một nền tảng,
xếp hạng kênh tìm được theo độ đều đặn đăng bài. Chỉ gợi ý — người dùng tự quyết
định thêm kênh nào vào lịch đăng. Không có lịch tự động: mỗi lượt quét do nút
"Quét ngay" khởi động và chạy như background task của tiến trình API.

Số đo trong trang này lấy ngày 2026-09-21, chạy trong container Docker.

---

## 1. Bảng tính năng

| | YouTube | TikTok | Douyin |
|---|---|---|---|
| Gợi ý từ khóa | có — 14 gợi ý / 0,4s | có — 14 gợi ý / 14,1s | **không** |
| Tìm kênh theo từ khóa | có | có | **chưa chạy** — cần cookie đăng nhập |
| Cần cookie / API key | không | không | **có** — cần phiên đăng nhập |
| Chọn thị trường (`gl`/`hl`) | có | có | không áp dụng |
| Thêm thẳng vào lịch đăng | có | có | có |
| Số luồng đăng (cột nhịp đăng) | 2 — Shorts và video dài | 1 | 1 |
| Phụ thuộc | `yt-dlp`, `requests` | Node + `playwright-chromium` + Chromium, `yt-dlp`, `curl_cffi` | `vendor/douyin-downloader` + dep của nó |
| Độ ổn định | cao | trung bình | thấp |

"Thêm thẳng vào lịch đăng" phụ thuộc `ChannelPipeline.source_type`, hiện nhận cả
ba nguồn. Nếu về sau có nền tảng quét được mà lịch đăng chưa kéo được, bảng kết quả
tự ẩn nút thêm và hiện dòng giải thích — nó bám theo `pipeline_source_type` chứ
không viết cứng tên nền tảng.

Các khác biệt trên không viết cứng trong webapp. API phát chúng ở
`GET /api/discovery/platforms`, đọc từ cờ khai báo trong từng module nền tảng
(`SINGLE_FEED`, `MARKET_AWARE`, `SUPPORTS_SUGGESTIONS`, `PIPELINE_SOURCE_TYPE`).

---

## 2. Cơ chế từng nền tảng

### YouTube

- **Gợi ý**: một HTTP GET tới `suggestqueries-clients6.youtube.com/complete/search`
  (`client=youtube&ds=yt&q=<seed>&hl=&gl=`), trả JSONP, cắt phần trong ngoặc rồi
  `json.loads`. Không browser, không ký, không cookie.
- **Tìm**: `yt-dlp` với input `ytsearch40:<từ khóa>`, `extract_flat`. Lấy 40 video
  đầu rồi gom về `channel_id` duy nhất. Con số 40 là đo ra: `ytsearch20` chỉ cho
  khoảng 15 kênh.
- **Số liệu kênh**: hai pha. Pha 1 là flat listing tab `/videos` (lùi về `/shorts`
  nếu kênh không có tab `/videos`) — cho sub, view trung vị, thời lượng trung vị,
  mô tả kênh. Pha 2 phải mở vài video lẻ, vì flat listing của YouTube luôn để
  `timestamp` và `upload_date` là `None`. Đây là lý do YouTube chậm hơn TikTok
  trên mỗi kênh.

### TikTok

Gợi ý và tìm dùng chung một đường: Chromium headless mở `tiktok.com` trước, chờ 5
giây, rồi mở `/search?q=<từ khóa>`, và **đọc JSON mà chính trang đó gọi**:

| Việc | Endpoint đọc trộm |
|---|---|
| Tìm | `/api/search/general/full/` |
| Gợi ý | `/api/search/general/preview/` + `/api/search/suggest/guide/` |

Ta không tự ghép request và không tự ký. Bước ghé trang chủ là mấu chốt — đó là
lúc `ttwid`/`msToken` được cấp, và request search được ký bằng chúng. Bỏ bước đó
thì cùng endpoint trả **HTTP 200 với body 0 byte**.

Payload search cho sẵn `author.uniqueId`, `author.secUid`,
`authorStats.followerCount`, `stats.playCount`, `createTime`, `video.duration`,
nên không tốn request riêng để lấy follower và bio.

**Số liệu kênh**: `yt-dlp` gọi `tiktokuser:<secUid>` — một request duy nhất, vì
entry flat của TikTok đã có sẵn `timestamp`, `view_count`, `duration`.

### Douyin

- **Gợi ý**: không có. Vendor không expose endpoint autocomplete nào, nên
  `SUPPORTS_SUGGESTIONS = False` và giao diện ẩn nút "Gợi ý", thay bằng dòng nhắc
  gõ từ khóa tay rồi bấm "Thêm".
- **Tìm**: vendor gọi `/aweme/v1/web/general/search/single/` với chữ ký `a_bogus`
  sinh bằng Python (`vendor/douyin-downloader/utils/abogus.py`), lùi về `X-Bogus`
  khi lỗi, kèm `msToken`. Ngược hẳn TikTok: **tự ghép và tự ký request**, không
  qua browser.
- **Số liệu kênh**: vendor gọi `get_user_info` + `get_user_post` theo `sec_uid`.
- **Cookie**: dùng dòng mới cập nhật gần nhất trong màn hình Douyin cookie.

---

## 3. Xếp hạng

Một ngân sách 100 điểm, chung cho mọi nền tảng:

| Thành phần | Điểm |
|---|---|
| Độ mới của video gần nhất | 20 |
| Nhịp đăng so với mục tiêu "đều đặn" trong Cài đặt | 65 |
| View trung vị theo thang log — chỉ cộng, không trừ | 15 |

YouTube chia 65 điểm nhịp đăng thành 45 (Shorts) + 20 (video dài), và thiếu luồng
nào thì mất trọn phần đó, không cộng bù qua nhau. Nền tảng một luồng dồn cả 65 vào
luồng duy nhất, nên điểm vẫn so sánh được với YouTube thay vì bị trần 80.

View chỉ cộng là cố ý: kênh viral kéo theo rủi ro bản quyền cao hơn, công cụ này
không nên ưu tiên chúng.

Ngưỡng (sub tối thiểu, số tháng im lặng thì loại, mục tiêu ngày/video, thang view)
đọc từ Cài đặt, dùng chung cho cả ba nền tảng.

---

## 4. Ổn định — cái gì vỡ

Bỏ qua chuyện nền tảng đổi cơ chế, chỉ xét bề mặt phụ thuộc của mình.

**YouTube — cao.** Phụ thuộc endpoint autocomplete (format JSONP tồn tại rất lâu,
không đổi) và extractor YouTube của yt-dlp. Không giữ logic riêng nào.

**TikTok — trung bình, và vỡ theo cách dễ sửa.** Phụ thuộc đúng ba thứ, không thứ
nào là thuật toán:

1. hình dạng URL `/search?q=`
2. chuỗi đường dẫn `/api/search/general/full/`
3. tên trường JSON `data[].item.author.*`

Quan trọng: **không phụ thuộc thuật toán ký**. Đó là thứ TikTok đổi thường xuyên
nhất. Trang đổi thì sửa một chuỗi; thuật toán ký đổi thì không phải làm gì.

Điểm yếu riêng: chậm (14 giây cho gợi ý, khoảng 10 giây cho tìm) và cần Chromium
trong image.

**Douyin — thấp.** Ba lớp phụ thuộc, mỗi lớp hỏng độc lập: cookie phải còn hạn và
phải có phiên đăng nhập; chữ ký `a_bogus` do vendor tự implement nên Douyin đổi là
phải chờ vendor vá; và subprocess vendor với bộ dep riêng chỉ cài trong image.

---

## 5. Lỗi hay gặp

| Thông báo | Nghĩa | Xử lý |
|---|---|---|
| `Douyin status_code=2483: 请先登录，再继续搜索吧` | Cookie không mang phiên đăng nhập. `api/services/douyin_cookie_import.py` chỉ giữ `ttwid`, `msToken`, `odin_tt`, `passport_csrf_token`, `sid_guard` — đủ cho mức khách vãng lai, không đủ cho tìm kiếm | Chưa làm, chờ tài khoản Douyin. Cách sửa + những đường cụt đã loại nằm ở `backlogs/douyin-keyword-search/handover.md` |
| `Douyin` trả HTTP `403` | Cookie đã hết hạn | Nhập lại cookie ở màn hình Douyin cookie |
| `Chưa có cookie Douyin nào` | Bảng `douyin_cookies` rỗng | Thêm cookie trước khi quét |
| Lượt quét TikTok ra rất ít ứng viên | TikTok bóp request feed bằng cách trả danh sách rỗng chứ không trả mã lỗi | Đã có retry 3 lần backoff nhân đôi. Nếu vẫn ít, chờ vài phút rồi quét lại |
| `Unable to extract secondary user ID` trên mọi kênh TikTok | Thiếu `curl_cffi`, yt-dlp không impersonate được vân tay TLS trình duyệt | Kiểm tra `curl-cffi` còn trong `requirements.txt`, rồi `make start` |
| `interrupted by a server restart` | Tiến trình API restart giữa lượt quét; background task sống trong tiến trình đó | Bấm "Quét ngay" lại |

---

## 6. File liên quan

| Đường dẫn | Vai trò |
|---|---|
| `api/services/channel_discovery.py` | Điều phối: dedupe, ngưỡng im lặng, lưu DB, tiến độ, trạng thái cuối |
| `api/services/discovery_sources/base.py` | Giao diện mà mỗi nền tảng phải cài, và phần chấm điểm |
| `api/services/discovery_sources/youtube.py` · `tiktok.py` · `douyin.py` | Phần riêng của từng nền tảng |
| `api/services/discovery_sources/tiktok_search.js` | Cầu nối Chromium headless cho tìm và gợi ý TikTok |
| `api/services/discovery_sources/douyin_bridge.py` | Cầu nối subprocess vào API client của vendor Douyin |
| `api/routers/discovery.py` | Endpoint, kể cả `GET /api/discovery/platforms` |
| `webapp/src/pages/Discovery.tsx` | Giao diện |

---

**Liên quan:** [14-oss-landscape](14-oss-landscape.md) — vì sao không dùng thư viện
OSS có sẵn cho phần tìm kiếm TikTok · [07-schedules](07-schedules.md) — lịch đăng mà
một ứng viên được thêm vào · [16-tiktok-pipeline](16-tiktok-pipeline.md) — nguồn
TikTok cho lịch đăng · [13-douyin-pipeline](13-douyin-pipeline.md) — cookie Douyin
và vendor subprocess
