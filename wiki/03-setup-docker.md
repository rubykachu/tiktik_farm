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

## Build (lần đầu hoặc khi sửa code)

```bash
cd v2/
docker compose build
```

Build mất ~10-15 phút trên VPS 2-core, ~5 phút trên M4. 5 images sinh ra:

- `tiktok-base:latest`
- `tiktok-api:latest`
- `tiktok-scheduler:latest`
- `tiktok-novnc:latest`
- `tiktok-webapp:latest`

## Up / Down

```bash
docker compose up -d              # start all (detached)
docker compose ps                 # verify all "Up"
docker compose stop               # stop tạm, giữ volume
docker compose down               # stop + remove containers (volume vẫn còn)
docker compose down -v            # ⚠️ NUKE volume (mất DB + cookies)
```

Sau khi up:

| URL | Service |
|---|---|
| http://127.0.0.1:3000 | Webapp UI |
| http://127.0.0.1:8000/docs | Swagger UI (FastAPI auto-doc) |
| http://127.0.0.1:8000/openapi.json | Schema raw |

`6080` + `7900` của noVNC chỉ tồn tại trong compose network — webapp nginx proxy ra `/novnc/`.

## Workflow update code

**Rule quan trọng**: rebuild service nào → recreate cả `webapp`. Vì nginx cache DNS upstream IP ở boot time. Recreate `api`/`novnc` → IP đổi → 502 cho đến khi webapp restart.

```bash
# Sửa code api → rebuild image + force-recreate cả api + webapp
docker compose build api
docker compose up -d --force-recreate api webapp

# Recreate hết stack — an toàn nhất, không cần nhớ
docker compose up -d --force-recreate

# Sửa webapp (React/nginx)
docker compose build webapp
docker compose up -d --force-recreate webapp
```

Code thay đổi nhưng KHÔNG cần rebuild image:
- Chỉ data (cookie, video) — đặt vào `./data/` rồi `docker compose restart` service liên quan
- Config qua env var — sửa `docker-compose.yml` rồi `docker compose up -d <service>`

## Lần đầu setup data

```bash
# 1. (Optional) cookie từ Cookie-Editor JSON hoặc v1 export
./.venv/bin/python scripts/migrate-json-cookie-editor.py \
    <src_dir> ./data/CookiesDir

# 2. Import file mới vào DB qua UI hoặc curl
curl -X POST http://127.0.0.1:8000/api/accounts/import-from-disk

# 3. (Optional) thả video local sẵn vào dir cho CLI dùng
cp my_video.mp4 ./data/VideosDirPath/
```

**Lưu ý path**: trong Docker stack working_dir = `/data`, nên `Config.cookies_dir = ./CookiesDir` resolve thành `/data/CookiesDir`. Host path tương ứng = `./data/CookiesDir/`. **KHÔNG** dùng `./CookiesDir/` (path đó chỉ cho native run).

## Production VPS — checklist

Trước khi expose ra Internet:

- [ ] Đổi `0.0.0.0` → `127.0.0.1` bind cho webapp (đã sẵn) → đặt Caddy/Traefik/Cloudflare Tunnel trước
- [ ] HTTPS via reverse proxy (Cloudflare cert hoặc Let's Encrypt)
- [ ] CORS API hiện `allow_origins=["*"]` → đổi sang domain thực
- [ ] Auth: hiện không có. Phase 2 add JWT (xem [11-roadmap](11-roadmap.md))
- [ ] Backup `./data/` định kỳ (cookies + DB)
- [ ] Log rotation `docker compose logs` không xoay tự động — config `docker-compose.yml` thêm `logging` driver `json-file` với `max-size`

## Tự động hóa deploy (gợi ý)

`Makefile`:
```makefile
.PHONY: deploy redeploy logs

deploy:
	git pull
	docker compose build
	docker compose up -d --force-recreate

redeploy:
	docker compose up -d --force-recreate

logs:
	docker compose logs -f
```

Hoặc cron job pull + redeploy mỗi 5 phút (nếu CI/CD chưa setup).

---

**Next:** [04-setup-native](04-setup-native.md)
