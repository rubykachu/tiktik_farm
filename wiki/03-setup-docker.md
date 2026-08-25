# 03 — Setup Docker

[← Index](README.md) | [← 02-architecture](02-architecture.md)

> Cài đặt từng bước cho người vận hành: **[RUNBOOK.md](../RUNBOOK.md)**.
> Trang này giải thích *vì sao* stack chạy như vậy, cho người sửa code.

## Một môi trường duy nhất

Máy host chính là máy chủ. Không có bản "dev" và bản "prod" riêng — `make start`
build và chạy đúng thứ mà tên miền công khai phục vụ.

| File | Vai trò |
|---|---|
| `docker-compose.yml` | Bản duy nhất được chạy. nginx + bundle tĩnh. |
| `docker-compose.dev.yml` | Overlay hot-reload, chỉ dùng qua `make dev`. **Không** tự merge. |

Tên file thứ hai là cố ý. Trước đây nó tên `docker-compose.override.yml` — Compose
tự merge file tên đó, khiến `make deploy` âm thầm chạy Vite thay vì bản nginx thật,
và mọi lần kiểm tra đều kiểm tra nhầm thứ.

## Yêu cầu

| Thành phần | Phiên bản | Note |
|---|---|---|
| Docker | ≥ 24 | Docker Desktop trên mac, hoặc docker.io trên Ubuntu |
| Docker Compose | v2 | Bundled với Docker Desktop, hoặc `apt install docker-compose-plugin` |
| Disk | ≥ 8 GB free | Image stack ~12.5 GB nhưng layer share |
| RAM | ≥ 4 GB | Idle ~1.5 GB, peak ~3-4 GB |

## Vì sao `make start` luôn build

Bản chạy thật **không mount source**. Chỉ ba thứ được mount:

| Mount | Lý do |
|---|---|
| `./data:/data` | DB SQLite, cookie, video — phải sống qua các lần build |
| `./config.txt` | Cấu hình uploader, đọc lúc chạy |
| `./.htpasswd` | Mật khẩu Basic Auth của nginx |

Code `api/`, `scheduler/`, `tiktok_uploader/` được COPY vào image lúc build, còn
webapp là bundle tĩnh do Vite build **bên trong** image. Nên `git pull` không đổi
gì cho tới khi build lại. Build có cache: không đổi gì thì mất ~6 giây; sửa
`api/` thì chỉ layer copy source chạy lại. Lần đầu mới mất 10–20 phút (pip
install + tải Playwright Chromium).

Hệ quả: **`make restart` không nạp code mới và không đọc lại `.env`.** Đổi `.env`
hay `git pull` xong đều phải `make start`.

## Layer image

`docker/base.Dockerfile` dựng `tiktok-base` (~2.6 GB: Python + Node + Playwright
Chromium + ffmpeg). `api` và `scheduler` đều `FROM tiktok-base` rồi chỉ copy source
của mình. Sửa `requirements.txt` hoặc `tiktok_uploader/` làm cache của base vỡ và
`make start` tự build lại — không cần lệnh riêng.

## Lần đầu setup data

```bash
# 1. (Optional) cookie từ Cookie-Editor JSON hoặc v1 export
./.venv/bin/python scripts/migrate-json-cookie-editor.py \
    <src_dir> ./data/CookiesDir

# 2. Import cookie vào DB qua UI /accounts → "Add Cookie TikTok"
#    (paste JSON hoặc upload file)

# 3. (Optional) thả video local sẵn vào dir cho CLI dùng
cp my_video.mp4 ./data/VideosDirPath/
```

**Lưu ý path**: trong Docker stack working_dir = `/data`, nên
`Config.cookies_dir = ./CookiesDir` resolve thành `/data/CookiesDir`. Host path
tương ứng = `./data/CookiesDir/`. **KHÔNG** dùng `./CookiesDir/` (path đó chỉ cho
native run).

## Expose ra Internet

Webapp bind `127.0.0.1:2705`, không ra ngoài. Đường ra là Cloudflare Tunnel với
**named tunnel** + tên miền riêng — các bước ở [RUNBOOK](../RUNBOOK.md) Bước 5.

Quick tunnel (`trycloudflare.com`) đổi URL mỗi lần khởi động lại. Không dùng được
nữa: `PUBLIC_BASE_URL` là gốc của link tải video gửi qua Telegram, URL đổi là mọi
link đã gửi cùng chết.

### Basic Auth

```bash
htpasswd -bc .htpasswd admin 'mat-khau'
```

`docker-compose.yml` mount `./.htpasswd` → `/etc/nginx/.htpasswd`; `webapp/nginx.conf`
bật `auth_basic` cho toàn bộ server. Đổi file → `make start`.

Một ngoại lệ duy nhất: `location /api/public/` tắt `auth_basic`, vì link tải video
gửi qua Telegram phải mở được trên điện thoại không có credential — chữ ký HMAC
trong đường dẫn là lớp xác thực ở đó. `webapp/vite.config.ts` miễn trừ đúng prefix
đó cho `make dev`.

`.htpasswd` và `.env` đều gitignored; mẫu xem `.env.example`.

### `HOST` và `PUBLIC_BASE_URL`

| Biến | Dùng để |
|---|---|
| `HOST` | Whitelist origin cho CSRF middleware (`api/middleware/origin.py`). Thiếu → `403 INVALID_ORIGIN` khi bấm nút trên web. |
| `PUBLIC_BASE_URL` | Gốc của link tải video trong thông báo Telegram. Thiếu → thông báo tự hạ xuống dạng chỉ có chữ. |

Cả hai đặt bằng tên miền của **chính máy đó**. Sửa xong phải `make start`.

## Checklist trước khi expose

- [x] Webapp bind `127.0.0.1` → đặt Cloudflare Tunnel phía trước
- [x] Basic Auth qua `.htpasswd`
- [x] `HOST` + `PUBLIC_BASE_URL` trỏ tên miền thật
- [x] Tunnel cài dạng service để tự chạy lại sau reboot
- [ ] Backup `./data/` định kỳ — `make backup` gói sẵn cả khoá tunnel
- [ ] Log rotation: `docker compose logs` không tự xoay, thêm driver `json-file` với `max-size` nếu chạy dài ngày

## Nhiều máy

Mỗi máy một tunnel và một tên miền con riêng. Đừng chạy chung một tunnel: đó là
chế độ HA replica, Cloudflare chia tải ngẫu nhiên giữa các máy, mà mỗi máy có DB
và file video riêng → link tải 404 khoảng một nửa số lần.

Dedupe nằm trong SQLite của từng máy, nên hai máy cùng chạy một kênh với cùng một
tài khoản TikTok sẽ đăng trùng. Chia theo kênh hoặc theo tài khoản.

---

**Next:** [04-setup-native](04-setup-native.md)
