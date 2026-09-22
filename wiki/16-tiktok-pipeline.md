# Lịch đăng nguồn TikTok

`source_type = "tiktok"` trên một pipeline: kéo video mới nhất của một creator
TikTok rồi đăng lên account TikTok theo lịch, giống hệt pipeline YouTube.

Đo ngày 2026-09-21 trong container Docker.

---

## 1. Khác gì YouTube và Douyin

| | YouTube | TikTok | Douyin |
|---|---|---|---|
| Đường chạy trong worker | chung | **chung** | riêng (`_run_douyin_pipeline`) |
| Cách tải | yt-dlp | yt-dlp | subprocess vendor |
| Cần cookie | không | **không** | có |
| Phụ đề cho LLM rewrite | có | không | không |
| `sync_from_date` áp ở đâu | yt-dlp `dateafter` | lọc `timestamp` trong code | vendor `start_time` |
| Cột lưu id nguồn | `youtube_video_id` | `youtube_video_id` | `douyin_aweme_id` |
| Huỷ giữa chừng | có | **chưa** | chưa |

TikTok dùng chung đường với YouTube vì cả hai đều đi qua `yt_dlp.YoutubeDL` trên
một trang kênh rồi một trang video — cùng một logic, chỉ khác URL. Douyin thì khác
bản chất nên giữ đường riêng.

Muốn thêm một nền tảng thứ tư cũng chạy bằng yt-dlp: thêm một module vào
`pipeline_sources.SOURCES`, **không** chép thêm một bản tick nữa.

---

## 2. Kiến trúc

```
scheduler/pipeline_worker.py
  └─ pipeline_sources.get(source_type)   api/services/pipeline_sources.py
       ├─ "youtube" → api/services/youtube_channel.py
       └─ "tiktok"  → api/services/tiktok_channel.py
              └─ cả hai gọi xuống api/services/ytdlp_source.py
```

`ytdlp_source.py` không biết nền tảng nào cả — nó chỉ có `list_entries`,
`extract_info`, `download_video`, cộng hai exception `VideoTooLong` và
`DownloadError`. Hai module nền tảng thêm đúng phần riêng:

| Phần riêng | YouTube | TikTok |
|---|---|---|
| Host hợp lệ | `youtube_validate.py` | `tiktok_validate.py` |
| URL dựng từ id trần | `youtube.com/watch?v=<id>` | `tiktok.com/@/video/<id>` |
| Phụ đề | `fetch_transcript(info)` | luôn `""` |

Một module nguồn phải có: `list_top_n`, `download`, `get_video_info`, `video_url`,
và hai exception trên. Test nào cần giả lập yt-dlp thì patch
`api.services.ytdlp_source.yt_dlp`, không patch module nền tảng.

---

## 3. Tạo pipeline TikTok

```bash
curl -X POST http://127.0.0.1:2706/api/pipelines \
  -H 'Origin: http://127.0.0.1:2705' -H 'Content-Type: application/json' -d '{
    "name": "kids-dance",
    "source_type": "tiktok",
    "channel_url": "https://www.tiktok.com/@kidssmile26",
    "fetch_count": 1,
    "interval_value": 1,
    "interval_unit": "days",
    "first_fire_at": "2026-10-01T00:00:00Z",
    "caption_template": "{title}",
    "account_ids": []
  }'
```

Trên webapp: chọn **TikTok** ở ô "Nguồn video" trong form pipeline, hoặc bấm nút
thêm vào lịch ngay trên một ứng viên ở màn hình Tìm kênh — form mở ra đã chọn sẵn
nguồn và điền sẵn URL kênh.

Quy tắc kiểm:

- `channel_url` phải thuộc `tiktok.com`, `www.tiktok.com`, `m.tiktok.com`,
  `vm.tiktok.com`, `vt.tiktok.com`. Sai thì 422.
- `douyin_cookie_id` phải để trống. Đặt vào thì 422 — cookie Douyin không dùng cho
  nguồn khác.
- Không có ô cookie nào cho TikTok. Feed creator đọc được không cần đăng nhập.

`account_ids` rỗng nghĩa là chỉ tải rồi báo Telegram, không đăng — đúng như
pipeline YouTube không gắn account.

---

## 4. Một tick chạy gì

1. `tiktok_channel.list_top_n(channel_url, fetch_count, sync_from_date)` — một flat
   listing, lọc theo `timestamp` nếu có `sync_from_date`.
2. Bỏ video đã xử lý: theo `VideoUpload` nếu có account, theo `PipelineSeenVideo`
   nếu không.
3. `tiktok_channel.download(...)` cho từng video còn lại.
4. Rewrite caption bằng LLM nếu bật — với `transcript` rỗng, nên LLM chỉ có title
   và description.
5. Đăng lên từng account, ghi `VideoUpload`.
6. Thông báo Telegram.

Đo thật: một tick `fetch_count=1` không gắn account mất **6,5 giây** từ lúc
scheduler nhận đến lúc kết thúc.

---

## 5. Giới hạn đã biết

- **`POST /api/pipelines/{id}/cancel` chỉ hỗ trợ nguồn YouTube.** Pipeline TikTok
  đang tải dở thì chưa huỷ được, phải đợi nó xong.
- **`VideoUpload.youtube_video_id` và `youtube_url` chứa id và URL TikTok.** Tên cột
  nói sai với dữ liệu. Không đổi tên vì hai cột này là khoá dedupe, có index tổ hợp
  đi kèm, và `youtube_url` là tên trường trong response API nên đổi là phá hợp đồng
  với frontend. Đổi tên là việc riêng, không gộp vào đây.
- **TikTok bóp request theo đợt**, trả danh sách rỗng chứ không trả mã lỗi. Màn hình
  Tìm kênh có retry cho chuyện này; pipeline chỉ liệt kê một lần mỗi tick nên ít
  gặp hơn, nhưng nếu một tick báo `pulled_count=0` mà kênh vẫn đang đăng đều thì
  đây là nghi phạm đầu tiên.
- **Đăng TikTok → TikTok là re-upload trong cùng nền tảng.** Cơ chế phát hiện trùng
  của TikTok trên nội dung của chính nó gắt hơn video lấy từ YouTube. Đây là rủi ro
  vận hành, không phải lỗi code.

---

## 6. Gỡ rối

| Triệu chứng | Nghĩa |
|---|---|
| `pulled_count=0` mà kênh vẫn đăng đều | TikTok đang bóp request, hoặc `sync_from_date` đặt sau ngày video mới nhất |
| `Not a valid TikTok URL` | `channel_url` không thuộc danh sách host ở mục 3 |
| `Unable to extract secondary user ID` | Thiếu `curl_cffi` — xem `requirements.txt`, rồi `make start` |
| Video tải về bị cắt ngắn | `max_duration_seconds` đang cắt; video dài hơn 3 lần giá trị đó thì bị bỏ hẳn chứ không cắt |

---

**Liên quan:** [07-schedules](07-schedules.md) — pipeline nói chung ·
[15-discovery](15-discovery.md) — tìm kênh TikTok để thêm vào đây ·
[13-douyin-pipeline](13-douyin-pipeline.md) — vì sao Douyin có đường riêng
