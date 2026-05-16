# 09 — Vận hành: log, debug, restart rules

[← Index](README.md) | [← 08-videos](08-videos.md)

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

### 4. Schedule không chạy

```bash
docker compose logs --tail=20 scheduler
```

- `scheduler loop starting` → đã khởi động
- Không thấy log gì sau đó → đang poll, chưa có job đến hạn
- Job đến hạn nhưng không run → check `scheduled_for` timezone (UTC trong DB)

### 5. Cookie debug

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

### 6. SQLite inspect

Container Python (sqlite3 CLI không có sẵn):

```bash
docker compose exec api python -c "
from sqlmodel import Session, select
from api.db import engine
from api.models import Account, ScheduledUpload, LoginSession
with Session(engine) as s:
    print('Accounts:')
    for a in s.exec(select(Account)).all():
        print(f'  {a.id} {a.username} valid={a.has_valid_session}')
    print('Schedules:')
    for sc in s.exec(select(ScheduledUpload).order_by(ScheduledUpload.id.desc())).all()[:10]:
        print(f'  {sc.id} acct={sc.account_id} status={sc.status} when={sc.scheduled_for}')
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
