# 04 — Setup Native (dev path)

[← Index](README.md) | [← 03-setup-docker](03-setup-docker.md)

> Chỉ dùng để dev nhanh khi sửa React/FastAPI và muốn hot reload. **noVNC KHÔNG chạy được native trên macOS** (cần Linux Xvfb + chromium).

## Yêu cầu

| Thành phần | Phiên bản |
|---|---|
| Python | 3.9+ (test trên 3.12) |
| Node.js | 18+ (test trên 22) |
| npm | bundled với Node |
| Chrome (browser) | bất kỳ — cho `cli.py login` |

## First-time install

```bash
cd v2/

# 1. Python venv + deps
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 2. Node packages cho tiktok signature subprocess
cd tiktok_uploader/tiktok-signature
npm install
cd ../..

# 3. Playwright Chromium (cho signature subprocess)
node tiktok_uploader/tiktok-signature/node_modules/.bin/playwright install chromium

# 4. Webapp deps
cd webapp
npm install
cd ..

# 5. Tạo dirs
mkdir -p CookiesDir VideosDirPath data
```

## Start (mỗi lần)

3 process độc lập trong background:

```bash
# 1. API
TIKTOK_CONFIG_PATH=./config.txt \
    ./.venv/bin/uvicorn api.main:app \
    --host 127.0.0.1 --port 8000 \
    > /tmp/v2_api.log 2>&1 &

# 2. Scheduler
TIKTOK_CONFIG_PATH=./config.txt LOG_LEVEL=INFO \
    ./.venv/bin/python -m scheduler.main \
    > /tmp/v2_scheduler.log 2>&1 &

# 3. Webapp (Vite — hot reload tự động khi sửa src/)
(cd webapp && ./node_modules/.bin/vite --port 3000 --host 127.0.0.1 \
    > /tmp/v2_webapp.log 2>&1 &)
```

Mở: http://127.0.0.1:3000.

## Stop

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN -t | xargs -r kill
lsof -nP -iTCP:3000 -sTCP:LISTEN -t | xargs -r kill
pkill -f "scheduler.main"
```

## Native cookies path khác Docker

| Mode | Cookie dir |
|---|---|
| Native | `./CookiesDir/` (root project) |
| Docker | `./data/CookiesDir/` (bind mount qua `/data` container) |

→ Cookie native KHÔNG share Docker, và ngược lại. Muốn share, copy thủ công hoặc chạy chỉ 1 mode tại 1 thời điểm.

## Caveat khi sửa Python code native

uvicorn KHÔNG có `--reload` flag → code thay đổi không tự áp dụng. Phải kill + start lại API:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN -t | xargs -r kill
sleep 1
TIKTOK_CONFIG_PATH=./config.txt ./.venv/bin/uvicorn api.main:app \
    --host 127.0.0.1 --port 8000 > /tmp/v2_api.log 2>&1 &
```

Hoặc thêm `--reload` (chỉ dev):
```bash
./.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```

Scheduler tương tự — `pkill -f "scheduler.main"` rồi start lại.

Vite (webapp) tự reload — không cần restart.

## Native + Docker hybrid (cho login noVNC)

Native không có noVNC. Workaround: chạy chỉ noVNC service trong Docker, API native trỏ về:

```bash
# Terminal 1: noVNC trong Docker
docker compose build novnc
docker run --rm -p 127.0.0.1:7900:7900 -p 127.0.0.1:6080:6080 \
    -v "$(pwd)/data:/data" tiktok-novnc:latest

# Terminal 2: API native với env trỏ về localhost
NOVNC_CONTROL_URL=http://127.0.0.1:7900 \
NOVNC_PUBLIC_URL=http://127.0.0.1:6080/vnc.html \
TIKTOK_CONFIG_PATH=./config.txt \
    ./.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 \
    > /tmp/v2_api.log 2>&1 &

# Terminal 3: webapp
(cd webapp && ./node_modules/.bin/vite --port 3000 --host 127.0.0.1 &)
```

Khi đó UI `/login` sẽ load iframe trỏ về `http://127.0.0.1:6080/vnc.html?...` thay vì path nginx proxy.

## Khi nào nên dùng Native?

- Sửa UI React → hot reload nhanh hơn nhiều so với rebuild Docker
- Debug Python — `pdb`/`breakpoint()` work trực tiếp
- Test endpoint nhanh — không cần rebuild image

Khi không nên:
- Test login noVNC (cần Docker)
- Verify production behavior — luôn test Docker stack trước deploy
- Demo cho người khác — Docker stack đảm bảo reproducible

---

**Next:** [05-accounts](05-accounts.md)
