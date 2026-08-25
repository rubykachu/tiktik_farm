# 09 — Vận hành: log, debug, restart rules

[← Index](README.md) | [← 08-videos](08-videos.md)

## Makefile

Chạy trong `v2/`. `make` = danh sách lệnh thường dùng, `make help-all` = toàn bộ.

| Target | Tác dụng |
|---|---|
| **`start`** | `compose build` + `up -d --force-recreate`. **Dùng sau mọi thay đổi** — code, `.env`, `requirements.txt`. |
| `stop` / `restart` / `status` | Dừng / khởi động lại nhanh / xem trạng thái |
| `update` | `git pull` rồi `start` |
| `logs` | Tail toàn bộ log |
| `backup` | Gói `data/`, `.env`, `.htpasswd`, khoá tunnel vào `backups/<ngày>` |
| `rebuild` | Build `--no-cache` rồi `start` (khi build cache hỏng) |
| `reset` | Xoá container + image của project rồi `start`. `data/` giữ nguyên. |
| `clean-db EXTRA=yes` | Xoá `data/tiktok.db` — mất toàn bộ lịch đăng, lịch sử, tài khoản |
| `dev` | Overlay hot-reload (Vite + `uvicorn --reload`) |
| `test`, `shell-api`, `shell-scheduler` | Pytest trong `.venv`, exec vào container |

**`restart` không nạp code mới và không đọc lại `.env`.** Bản chạy thật không mount
source: `api/`, `scheduler/`, `tiktok_uploader/` được COPY vào image lúc build, webapp
là bundle tĩnh Vite build bên trong image. Sửa gì cũng phải `make start` — build có
cache nên khi không đổi gì chỉ mất ~6 giây.

Hướng dẫn vận hành đầy đủ cho người không chuyên: [RUNBOOK.md](../RUNBOOK.md).

## Chế độ lập trình

`docker-compose.dev.yml` **không** tự merge (đó là lý do nó không tên `override`).
Dùng qua `make dev`:

| Lệnh | Ý nghĩa |
|---|---|
| `make start` | Bản thật: nginx phục vụ bundle tĩnh, không reload |
| `make dev` | Vite HMR (host `2705` → container `5173`), API `uvicorn --reload`, mount `webapp/` + `api/` |

Lần đầu chạy `make dev`: container chạy `npm ci` / `npm install` vào volume ẩn
`node_modules` (tránh ghi đè deps macOS).

Sửa **scheduler**: có mount nhưng **không** auto-reload → `make restart`.

Sửa **`tiktok_uploader/`** hoặc **`requirements.txt`**: không mount ở chế độ nào →
bắt buộc `make start`.

## Bảo mật vận hành (Phase 1)

- **Origin middleware**: `POST` / `PUT` / `PATCH` / `DELETE` phải có `Origin` hoặc `Referer` khớp `http://127.0.0.1:2705` hoặc `http://localhost:2705` (đổi qua `WEBAPP_PORT`). `curl` hoặc script automation phải gửi header tương ứng. GET/HEAD/OPTIONS không kiểm tra Origin (CORS vẫn giới hạn ở app).
- **Không có auth đăng nhập**: coi API là **localhost / tin cậy**. Ra Internet → **reverse proxy + HTTPS + basic auth** (hoặc tương đương) trước webapp.
- **Bind**: chỉ lắng nghe `127.0.0.1` trên host — webapp `127.0.0.1:2705`, api `127.0.0.1:2706`.
- **Cookie / path**: username & path cookie file có guard chống path traversal; import JSON có giới hạn kích thước/cấu trúc (xem [05-accounts](05-accounts.md)).
- **Telegram**: URL cố định `api.telegram.org`, httpx `follow_redirects=False`, timeout 10s; token trong log bị redact (`api/logging.py`).

## Xem log

### Docker

```bash
docker compose logs -f api                         # follow
docker compose logs --tail=50 api                  # last 50
docker compose logs --since=5m api                 # 5 phút gần đây
docker compose logs -f api scheduler webapp  # multi-service
docker compose logs api | grep "ERROR\|Traceback"
```

### Native

```bash
tail -f /tmp/v2_api.log
tail -f /tmp/v2_scheduler.log
tail -f /tmp/v2_webapp.log
```

## Operational rule: rebuild → force-recreate webapp

**Nginx (prod webapp) cache DNS IP upstream tại boot.** Recreate `api` mà không recreate `webapp` → 502.

→ Dùng **`make start`** (build rồi force-recreate cả stack). Hoặc thủ công:

```bash
docker compose build
docker compose up -d --force-recreate
```

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
curl http://127.0.0.1:2706/health    # API trả 200?
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

### 3. Pipeline không tick / Run now 409

```bash
docker compose logs --tail=80 scheduler
```

- Kiểm tra `channel_pipelines.state`, `next_fire_at`, `lock_until` (SQLite snippet dưới).
- **409 Run now**: pipeline đang chạy (`lock_until`) hoặc đã xếp sẵn Run now — đợi tick hiện tại xong.
- **low_disk**: free `PIPELINE_TMP_DIR` (mặc định `/tmp`) trong container < 1 GB. Nếu đã set `PIPELINE_TMP_DIR=/data/pipeline_tmp`, kiểm tra dung lượng volume `/data`.

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
from api.models import Account, ChannelPipeline, PipelineRun, VideoUpload
with Session(engine) as s:
    print('Accounts:')
    for a in s.exec(select(Account)).all():
        print(f'  {a.id} {a.username} valid={a.has_valid_session}')
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

## Scale concurrent pipelines

Mặc định scheduler chạy serial (1 pipeline tại 1 thời điểm). Để tăng throughput, sửa `docker-compose.yml`:

```yaml
scheduler:
  environment:
    - SCHEDULER_CONCURRENCY=4        # số pipeline song song (tối đa 20)
    - PIPELINE_TMP_DIR=/data/pipeline_tmp  # đã set sẵn — để video ra real disk
```

Rồi:

```bash
docker compose restart scheduler   # không cần rebuild
```

**Capacity guide** (với `PIPELINE_TMP_DIR` trỏ sang real disk):

| Server | SCHEDULER_CONCURRENCY an toàn |
|--------|-------------------------------|
| 1 vCPU / 2 GB RAM | 2 |
| 2 vCPU / 4 GB RAM | 4–5 |

> Không set `PIPELINE_TMP_DIR` → video tạm lưu vào `/tmp` (tmpfs = ăn RAM). Mỗi pipeline concurrent cần ~500 MB tmpfs → giảm capacity xuống còn 2–3 ngay cả trên 4 GB RAM.

**Verify đang chạy concurrent:**

```bash
docker compose logs --tail=5 scheduler
# Xem dòng: scheduler loop starting, poll_interval=30s concurrency=3
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

## Update code

```bash
make update      # git pull + build + recreate
```

Cookie + DB nằm trong `./data/` (mount), không bị build đụng tới.

## Reset toàn bộ (cẩn thận)

```bash
make reset                    # xoá container + image, GIỮ data/
make clean-db EXTRA=yes       # xoá DB, giữ cookie
rm -rf data/                  # xoá sạch cookie + DB
```

`make reset` an toàn khi nghi image hỏng. Hai lệnh dưới mất account + history —
chạy `make backup` trước.

---

**Next:** [10-known-bugs](10-known-bugs.md)
