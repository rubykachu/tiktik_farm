# 03 — Setup Docker (production path)

[← Index](README.md) | [← 02-architecture](02-architecture.md)

> Đây là cách deploy lên VPS Ubuntu thực tế. Trên mac M-series chip cũng work (đã verify M4).

## Yêu cầu

| Thành phần | Phiên bản | Note |
|---|---|---|
| Docker | ≥ 24 | Docker Desktop trên mac, hoặc docker.io trên Ubuntu |
| Docker Compose | v2 | Bundled với Docker Desktop, hoặc `apt install docker-compose-plugin` |
| Disk | ≥ 8 GB free | Image stack ~12.5 GB nhưng layer share |
| RAM | ≥ 4 GB | Idle ~1.5 GB, peak ~3-4 GB |

## Makefile — lệnh chính

Chạy trong thư mục `v2/`. Gõ `make` hoặc `make help` để xem đầy đủ.

| Lệnh | Khi nào dùng |
|---|---|
| **`make deploy`** | **Mặc định an toàn** sau mọi thay đổi code: `build` toàn bộ image rồi `--force-recreate` container. Tránh lỗi “đã sửa code nhưng container vẫn chạy image cũ”. |
| `make up` | Lần đầu start stack (không rebuild). |
| `make down` | Stop + xóa container (giữ volume + image). |
| `make restart` | Kick lại `api` + `scheduler` + `webapp` (không rebuild — **không** lấy code bake trong image mới, **không** đọc lại `.env`). |
| `make recreate` | Tạo lại mọi container từ image **hiện có** (không build). Đọc lại `.env`. |
| `make recreate-api` | Tạo lại `api` + `scheduler` (đọc lại `.env` — dùng khi đổi `HOST`/`OLLAMA_*`). |
| `make recreate-webapp` | Build + tạo lại riêng `webapp` (đổi `nginx.conf` / `.htpasswd` / asset prod). |
| `make rebuild-base` | `requirements.txt` hoặc `tiktok_uploader/` đổi — rebuild base + api/scheduler. |
| `make rebuild` | Full `build --no-cache` (chậm nhất). |
| `make deploy-prod` | Giống `deploy` nhưng **không** merge `docker-compose.override.yml` (nginx static, không Vite). |
| `make logs` / `make logs-api` | Tail log. |
| `make test` | `pytest` trong container `api`. |
| `make nuke` | Xóa container + image local; volume `data/` giữ nguyên. |

**Quy tắc vàng:** `docker compose restart` và `docker compose up --force-recreate` **không** rebuild image. Nếu không chắc → **`make deploy`**.

```bash
cd v2/
make deploy    # build + recreate — dùng sau git pull hoặc thêm tính năng
```

## Build (lần đầu hoặc thủ công)

Tương đương bước build trong `make deploy` (không recreate):

```bash
cd v2/
docker compose build
# hoặc: make deploy   # khuyên dùng — gồm cả recreate
```

Build mất ~10-15 phút trên VPS 2-core, ~5 phút trên M4. 4 images sinh ra:

- `tiktok-base:latest`
- `tiktok-api:latest`
- `tiktok-scheduler:latest`
- `tiktok-webapp:latest`

## Up / Down

```bash
make up                           # start dev stack (detached)
make down                         # stop + remove containers (volume vẫn còn)
make ps                           # trạng thái container

# tương đương thuần compose:
docker compose up -d
docker compose ps
docker compose stop               # stop tạm, giữ volume
docker compose down
make nuke-all EXTRA=yes           # ⚠️ xóa cả named volumes (mất DB) — cần xác nhận
```

Sau khi up:

| URL | Service |
|---|---|
| http://127.0.0.1:3000 | Webapp UI |
| http://127.0.0.1:8000/docs | Swagger UI (FastAPI auto-doc) |
| http://127.0.0.1:8000/openapi.json | Schema raw |


## Workflow update code

### Một lệnh (khuyên dùng)

```bash
make deploy
```

Làm đủ: `docker compose build` (mọi service, gồm `tiktok-base` khi `tiktok_uploader/` hoặc `requirements.txt` đổi) + `up -d --force-recreate` (mọi container, gồm `webapp` — tránh nginx/Vite proxy DNS stale).

### Dev với override — khi nào *không* cần deploy

| Thay đổi | Cách lấy code mới |
|---|---|
| `api/*.py` | Uvicorn `--reload` tự load (mount `./api`) |
| `webapp/src/*` | Vite HMR (mount `./webapp`) |
| `scheduler/*.py` | `make restart` hoặc `docker compose restart scheduler` |
| `tiktok_uploader/*`, `requirements.txt` | **Bắt buộc** `make deploy` hoặc `make rebuild-base && make up` (không mount vào container) |
| Không chắc / vừa merge feature | **`make deploy`** |

### Prod / VPS (không override)

```bash
git pull
make deploy-prod
```

### Thủ công (nếu không dùng make)

**Rule quan trọng**: rebuild service nào → recreate cả `webapp`. Nginx cache DNS upstream IP lúc boot.

```bash
docker compose build
docker compose up -d --force-recreate
```

Chỉ data (cookie, video) trong `./data/` — không cần rebuild: `docker compose restart <service>`.

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

**Lưu ý path**: trong Docker stack working_dir = `/data`, nên `Config.cookies_dir = ./CookiesDir` resolve thành `/data/CookiesDir`. Host path tương ứng = `./data/CookiesDir/`. **KHÔNG** dùng `./CookiesDir/` (path đó chỉ cho native run).

## Expose qua Cloudflare Tunnel

Webapp bind `127.0.0.1:3000` (không ra ngoài). Để truy cập từ xa dùng Cloudflare Tunnel + HTTP Basic Auth.

### Script `restart.sh`

```bash
bash restart.sh
```

Làm theo thứ tự: `make recreate` → start `cloudflared tunnel --url http://localhost:3000` → parse URL tunnel → ghi `HOST=<url>` vào `.env` → `make recreate-api` để api nạp `HOST` mới → giữ `cloudflared` chạy foreground (Ctrl-C để dừng). URL tunnel đổi mỗi lần chạy; script tự cập nhật `.env`.

### Basic Auth

Bắt buộc khi expose. Tạo credential trên host:

```bash
sudo apt install apache2-utils
htpasswd -c .htpasswd <username>          # nhập password khi hỏi
```

- **Prod (nginx)**: `docker-compose.yml` mount `./.htpasswd` → `/etc/nginx/.htpasswd`; `webapp/nginx.conf` bật `auth_basic`. Đổi file → `make recreate-webapp`.
- **Dev (Vite)**: set `BASIC_AUTH_USER` / `BASIC_AUTH_PASS` trong `.env`; `vite.config.ts` thêm middleware basic-auth khi cả hai biến có giá trị (không set → không auth).

`.htpasswd` và `.env` đều gitignored; mẫu xem `.env.example`.

### `HOST` — origin check

API có CSRF origin middleware (`api/middleware/origin.py`) chặn POST/PUT/PATCH/DELETE từ origin lạ (mặc định chỉ `localhost:3000`). Origin tunnel bị chặn → `403 INVALID_ORIGIN`. Set `HOST=<tunnel-url>` (hostname hoặc full URL, tự thêm `https://`) để whitelist. `restart.sh` set tự động; sửa tay thì phải `make recreate-api` (không phải `restart` — `restart` không đọc lại `.env`).

## Production VPS — checklist

Trước khi expose ra Internet:

- [x] Webapp bind `127.0.0.1` (đã sẵn) → đặt Cloudflare Tunnel / Caddy / Traefik trước
- [x] Basic Auth (xem § trên) — tối thiểu khi expose qua tunnel
- [x] HTTPS: Cloudflare Tunnel cấp sẵn cert cho `*.trycloudflare.com`
- [x] `HOST` whitelist origin tunnel cho CSRF middleware
- [ ] CORS API hiện `allow_origins=["*"]` → đổi sang domain thực
- [ ] Auth mạnh hơn (JWT) — Phase 2, xem [11-roadmap](11-roadmap.md)
- [ ] Backup `./data/` định kỳ (cookies + DB)
- [ ] Log rotation `docker compose logs` không xoay tự động — config `docker-compose.yml` thêm `logging` driver `json-file` với `max-size`

## Tự động hóa deploy (gợi ý)

```bash
cd v2/
git pull
make deploy-prod    # VPS: không dùng dev override
# hoặc trên máy dev:
make deploy
```

Cron / CI: gọi `make deploy-prod` sau `git pull`. **Không** chỉ `docker compose up --force-recreate` — sẽ bỏ qua code mới trong image.

Xem thêm bảng lệnh: [09-operations](09-operations.md) § Makefile.

---

**Next:** [04-setup-native](04-setup-native.md)
