# 09 — Vận hành: log, debug, restart rules

[← Index](README.md) | [← 08-videos](08-videos.md)

## Dev với `docker-compose.override.yml`

File `docker-compose.override.yml` được Compose **tự merge** khi chạy lệnh mặc định trong thư mục `v2/` (cùng project với `docker-compose.yml`).

| Lệnh | Ý nghĩa |
|---|---|
| `docker compose up -d` | **Dev**: Vite HMR (port host `3000` → container `5173`), API `uvicorn --reload`, mount source `webapp/` + `api/` |
| `docker compose -f docker-compose.yml up --build -d` | **Prod-like**: không load override — nginx static webapp, không reload |

Lần đầu dev webapp: container chạy `npm ci` / `npm install` vào volume ẩn `node_modules` (tránh ghi đè deps macOS).

Sửa **scheduler**: mount code có sẵn nhưng **không** auto-reload — sau khi sửa `scheduler/` hoặc `api/` dùng chung:

```bash
docker compose restart scheduler
```

UI dev: http://127.0.0.1:3000 — Vite proxy `/api` → `api:8000`, `/novnc` + `/websockify` → `novnc:6080` (giống nginx prod).

Chi tiết setup: [03-setup-docker](03-setup-docker.md).

## Bảo mật vận hành (Phase 1)

- **Origin middleware**: `POST` / `PUT` / `PATCH` / `DELETE` phải có `Origin` hoặc `Referer` khớp `http://127.0.0.1:3000` hoặc `http://localhost:3000`. `curl` hoặc script automation phải gửi header tương ứng. GET/HEAD/OPTIONS không kiểm tra Origin (CORS vẫn giới hạn ở app).
- **Không có auth đăng nhập**: coi API là **localhost / tin cậy**. Ra Internet → **reverse proxy + HTTPS + basic auth** (hoặc tương đương) trước webapp.
- **Bind**: ưu tiên chỉ lắng nghe `127.0.0.1` trên host; trong Docker giữ API internal, chỉ webapp map `127.0.0.1:3000`.
- **Cookie / path**: username & path cookie file có guard chống path traversal; import JSON có giới hạn kích thước/cấu trúc (xem [05-accounts](05-accounts.md)).
- **Telegram**: URL cố định `api.telegram.org`, httpx `follow_redirects=False`, timeout 10s; token trong log bị redact (`api/logging.py`).

## Xem log

### Docker

```bash
docker compose logs -f api                         # follow
docker compose logs --tail=50 api                  # last 50
docker compose logs --since=5m api                 # 5 phút gần đây
docker compose logs -f api scheduler novnc webapp  # multi-service
docker compose logs api | grep "ERROR\|Traceback"
```

### Native

```bash
tail -f /tmp/v2_api.log
tail -f /tmp/v2_scheduler.log
tail -f /tmp/v2_webapp.log
```

## Operational rule: rebuild → force-recreate webapp

**Nginx (webapp) cache DNS IP upstream tại boot, không TTL.** Nếu recreate `api` hoặc `novnc` → IP đổi → 502 cho đến khi webapp restart.

→ **Mỗi lần** rebuild `api`/`novnc`, force-recreate luôn `webapp`:

```bash
# Sửa code api
docker compose build api
docker compose up -d --force-recreate api webapp

# An toàn nhất: recreate hết
docker compose up -d --force-recreate
```

Đừng over-engineer nginx config với `resolver` directive để auto-heal — operational rule này đơn giản và an toàn hơn.

## Restart không cần rebuild

```bash
# Service khoẻ rồi nhưng cần kick (vd code dùng env mới)
docker compose restart api

# Hoặc stop + start
docker compose stop scheduler && docker compose start scheduler
```

## Debug checklist

### 1. UI không gọi được API (network error / 502)

```bash
docker compose ps              # tất cả containers Up?
curl http://127.0.0.1:8000/health    # API trả 200?
docker compose logs --tail=30 webapp # nginx log có 502/upstream error?
```

Thấy `connect() failed ... Host is unreachable` trong nginx log → DNS cache stale → force-recreate webapp.

### 2. Upload fail (`ok: false`)

```bash
docker compose logs --tail=50 api
```

Tìm phrase:

| Phrase | Nghĩa |
|---|---|
| `tt-target-idc not in cookie` | Bình thường, không lỗi |
| `Published successfully` | Upload OK (server-side đã đăng) |
| `TikTok refused project/create` | Cookie chết → re-login |
| `Failed to generate signatures` | Node sig subprocess fail |
| `Could not get random user agent` | fake_useragent network fail → fallback default UA, vẫn upload được |
| `[-] Could not upload video` | Loop publish hết retry mà fail |
| `KeyError: 'project'` | Bug regress, xem [10-known-bugs](10-known-bugs.md) |

### 3. noVNC 502 hoặc DNS error

- `nodename nor servname provided` = đang chạy native mà chưa override env `NOVNC_CONTROL_URL`. Xem [04-setup-native](04-setup-native.md) section hybrid.
- Docker stack: `docker compose ps novnc` show Up?
- `docker compose logs novnc` có error Chromium? Khả năng OOM, kiểm `/dev/shm` tmpfs (đã setup 512MB trong compose).

### 4. Schedule Single-shot không chạy

```bash
docker compose logs --tail=20 scheduler
```

- `scheduler loop starting` → đã khởi động
- Không thấy log gì sau đó → đang poll; pass 1 ưu tiên single-shot
- Job đến hạn nhưng không run → kiểm `scheduled_for` timezone (UTC trong DB)

### 5. Pipeline không tick / Run now 409

```bash
docker compose logs --tail=80 scheduler
```

- Xác nhận pass 1 không “kẹt” hàng loạt `ScheduledUpload` (pass 2 chỉ chạy khi pass 1 rảnh).
- Kiểm tra `channel_pipelines.state`, `next_fire_at`, `lock_until` (SQLite snippet dưới).
- **409 Run now**: pipeline đang chạy (`lock_until`) hoặc đã xếp sẵn Run now — đợi tick hiện tại xong.
- **low_disk**: free `/tmp` trong container < 1 GB.

### 6. Cookie debug

```bash
docker compose exec api python -c "
import pickle
c = pickle.load(open('/data/CookiesDir/tiktok_session-lmbk37.cookie','rb'))
print('count:', len(c))
print('names:', sorted(x['name'] for x in c))
print('has sessionid:', any(x['name']=='sessionid' for x in c))
print('has tt-target-idc:', any(x['name']=='tt-target-idc' for x in c))
"
```

### 7. SQLite inspect

Container Python (sqlite3 CLI không có sẵn):

```bash
docker compose exec api python -c "
from sqlmodel import Session, select
from api.db import engine
from api.models import Account, ScheduledUpload, ChannelPipeline, PipelineRun, VideoUpload, LoginSession
with Session(engine) as s:
    print('Accounts:')
    for a in s.exec(select(Account)).all():
        print(f'  {a.id} {a.username} valid={a.has_valid_session}')
    print('Schedules (last 10):')
    for sc in s.exec(select(ScheduledUpload).order_by(ScheduledUpload.id.desc())).all()[:10]:
        print(f'  {sc.id} acct={sc.account_id} status={sc.status} when={sc.scheduled_for}')
    print('Pipelines:')
    for p in s.exec(select(ChannelPipeline).order_by(ChannelPipeline.id.desc())).all()[:10]:
        print(f'  {p.id} {p.name!r} state={p.state} next={p.next_fire_at} lock={p.lock_until}')
    print('Recent VideoUpload (5):')
    for v in s.exec(select(VideoUpload).order_by(VideoUpload.id.desc())).all()[:5]:
        print(f'  {v.id} acct={v.account_id} yt={v.youtube_video_id} status={v.status}')
"
```

Hoặc cài sqlite3 vào image base nếu cần thường xuyên:
```dockerfile
RUN apt-get update && apt-get install -y sqlite3
```

## Backup + Restore

Toàn bộ state ở `./data/`:
- `tiktok.db` + WAL files
- `CookiesDir/*.cookie`
- `VideosDirPath/*.mp4`

Backup:
```bash
docker compose stop      # đảm bảo không write
tar czf v2-backup-$(date +%Y%m%d).tar.gz data/
docker compose start
```

Restore:
```bash
docker compose down
rm -rf data/
tar xzf v2-backup-20260515.tar.gz
docker compose up -d
```

## Update code (production)

```bash
git pull
docker compose build      # rebuild image cho service có code mới
docker compose up -d --force-recreate
```

Cookie + DB persist qua volume → không mất gì.

## Reset toàn bộ (cẩn thận)

```bash
docker compose down -v    # XÓA volume!
rm -rf data/              # xóa cookies + DB host-side
docker compose up -d      # fresh start
```

→ Mất hết account + history. Chỉ dùng khi debug từ zero state.

---

**Next:** [10-known-bugs](10-known-bugs.md)
