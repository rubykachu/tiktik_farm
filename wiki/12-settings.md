# 12 — Settings: LLM caption rewrite

[← Index](README.md) | [← 11-roadmap](11-roadmap.md)

Trang **Settings** (`/settings`) chứa cấu hình toàn cục cho v2. Mở đầu với một nhóm duy nhất: **LLM caption rewrite**. Trang gồm 3 phần chính:

1. **Toggle global** — bật/tắt toàn bộ tính năng LLM cho mọi pipeline.
2. **Cloud / Local toggle** — chọn backend LLM: cloud API (Gemini, OpenAI…) hoặc Ollama local.
3. **Available models** *(cloud mode only)* — danh sách model LLM do user tự đăng ký. Worker chỉ đọc model được đánh dấu **default**.

---

## 1. Nghiệp vụ — LLM caption rewrite làm gì

Khi một pipeline có biến `{ai_title}` trong `caption_template` **và** field `caption_ai_prompt` đã set, worker sẽ:

1. Lấy YouTube **title** + **description** của video sắp đăng.
2. Gửi prompt + title + description tới model LLM đang là default.
3. Nhận text rewrite về, thay vào `{ai_title}` của caption.
4. Upload TikTok với caption đã render.

Mục tiêu: tránh trùng nội dung khi đăng nhiều account / nhiều kênh, hoặc viết lại tiêu đề ngắn gọn theo style TikTok mà không phải edit thủ công.

**Khi LLM lỗi** (key sai, rate limit, network, response rỗng…), worker **không fail run**. Caption fallback dùng raw YouTube title; lỗi chỉ ghi log. Tiêu chí: 1 hiccup LLM không được làm hỏng cả tick.

**Caching trong 1 tick**: nhiều account đăng cùng 1 video → cache rewrite theo `video_id` trong phạm vi tick. Chỉ cache khi rewrite **thành công** (khác raw title); nếu LLM lỗi/fallback raw title, account kế tiếp sẽ gọi lại — tránh 1 hiccup poison cache cả tick.

---

## 2. Toggle global

Ở góc phải header card "LLM caption rewrite":


| Trạng thái | Hành vi |
|---|---|
| **Enabled** | Pipeline có `{ai_title}` + prompt → gọi LLM. Pipeline không có `{ai_title}` không bị ảnh hưởng. |
| **Disabled** | Mọi pipeline skip LLM dù có prompt. Tạo/sửa pipeline với `{ai_title}` bị chặn 422 ("Enable LLM in Settings before using {ai_title}"). |

Lưu trong bảng `app_settings` với key `llm.enabled` (`"true"` / `"false"`).

---

## 3. Cloud / Local (Ollama) toggle

Nằm trong card "LLM caption rewrite", phía dưới global toggle. Hai nút **Cloud** và **Local (Ollama)**.

| Mode | Backend | Yêu cầu |
|---|---|---|
| **Cloud** | Gọi litellm → provider cloud (Gemini, OpenAI, Azure…) | Phải có ít nhất 1 row trong Available models với `is_default = true`. |
| **Local (Ollama)** | Gọi Ollama trực tiếp qua HTTP | Ollama phải đang chạy trên host. Không cần API key, không cần cấu hình DB. |

Khi chọn **Local**, UI hiển thị read-only:

- **Model**: giá trị `OLLAMA_MODEL` env var (default `llama3.2`).
- **URL**: giá trị `OLLAMA_BASE_URL` env var (default `http://localhost:11434`).
- Note: "Install model with: `ollama pull <model>`"

Lưu trong `app_settings` với key `llm.mode` (`"cloud"` / `"offline"`). Default sau seed: `"cloud"`.

### Cấu hình Ollama (env vars)

Không thay đổi qua UI — cần sửa `.env` rồi restart api/scheduler:

```env
OLLAMA_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434
```

`OLLAMA_BASE_URL` áp dụng cùng SSRF guard như `api_base` của cloud model (xem §7.3). Chỉ loopback được chấp nhận qua HTTP — public host phải dùng HTTPS.

### Hành vi khi Ollama không sẵn sàng

Worker không crash. Nếu Ollama trả lỗi (connection refused, model chưa pull, timeout), caption fallback về raw title và ghi log warning. Tiêu chí giống cloud: 1 hiccup LLM không fail cả tick.

---

## 4. Quản lý models

### Bảng Available models

Mỗi row là một entry `(label, model_id, api_key, [api_base, api_version])`. Tại 1 thời điểm chỉ **một** row có `is_default = true`. Worker đọc row default; nếu không có default mà pipeline cần LLM → fallback raw title.

| Cột | Ý nghĩa |
|---|---|
| Default | Chip `default` hoặc nút **Set default**. |
| Label | Tên hiển thị do user đặt. ≤ 128 ký tự. |
| Model ID | Format litellm `provider/model`. |
| Endpoint | URL `api_base` nếu có (Azure / Ollama / vLLM…), `—` nếu dùng default endpoint của provider. |
| API key | **Masked** — 4 ký tự đầu + bullets + 4 ký tự cuối. Server **không bao giờ** trả plaintext. |
| Actions | Edit / Delete. |

### Add model

Click **Add model** → modal:

| Field | Yêu cầu |
|---|---|
| Label | Bắt buộc. ≤ 128 ký tự. |
| Model ID | Bắt buộc. Format `provider/model`. Provider chữ thường, ngăn cách dấu `/`. |
| API key | Bắt buộc khi tạo mới. ≤ 512 ký tự. Lưu mã hóa, không hiện lại plaintext. |
| API base URL | Bắt buộc nếu provider là `azure/*`. Optional cho provider khác (Ollama / vLLM / custom proxy). |
| API version | Bắt buộc nếu provider là `azure/*`. |
| Set as default | Optional. Khi tick, các row khác mất `is_default` (atomic). |

### Edit model

- Đổi label / model_id / api_base / api_version / api_key.
- **API key trống** → giữ key cũ (không xóa).
- Xóa key thật sự → Delete cả row.
- Đổi `model_id` sang `azure/*` mà chưa có `api_base` + `api_version` → 422 và row giữ nguyên (validate hậu-merge).

### Delete model

- Xóa entry khỏi DB.
- Nếu xóa entry default mà không có row khác → pipeline có `{ai_title}` rơi vào fallback raw title; không crash worker.
- **Khuyến nghị**: set default cho row khác **trước** khi delete.

---

## 5. Provider matrix

| Provider | `model_id` ví dụ | API key shape | `api_base` | `api_version` |
|---|---|---|---|---|
| **Google Gemini** | `gemini/gemini-2.0-flash-lite` | `AIzaSy…` (39 chars) | — | — |
| **OpenAI** | `openai/gpt-4o-mini` | `sk-…` hoặc `sk-proj-…` | — (hoặc proxy URL) | — |
| **xAI (Grok)** | `xai/grok-3-mini` | `xai-…` | — | — |
| **Azure OpenAI / Foundry** | `azure/<deployment-name>` | Azure key | **bắt buộc** `https://<resource>.openai.azure.com/` | **bắt buộc**, vd `2024-08-01-preview` |
| **Ollama (self-host)** | *(dùng Local toggle — xem §3)* | không cần | không cần | — |
| **vLLM / proxy custom** | `openai/<model>` | key của proxy | **bắt buộc** URL của proxy | — |

Format `provider/model` lookup đầy đủ: <https://docs.litellm.ai/docs/providers>.

### Azure-specific

- `model_id` dùng **deployment name** trong Azure Foundry, **không** phải tên model gốc. Ví dụ deployment tên `gpt-5.4` → `model_id = azure/gpt-5.4`.
- `api_base` là **root URL** (`https://<resource>.openai.azure.com/`), **không** thêm `/openai/v1` — litellm tự nối path.
- `api_version` phải khớp version Azure deployment hỗ trợ (vd `2024-08-01-preview`, `2025-02-01-preview`). Sai version → litellm trả 400.

---

## 6. Validate `{ai_title}` (cả UI lẫn API)

Khi POST/PATCH `/api/pipelines` có `caption_template` chứa `{ai_title}`, server check precondition, trả 422 với message tương ứng:

| Điều kiện sai | Error message |
|---|---|
| Template có `{ai_title_hashtag}` mà thiếu `{ai_title}` | `{ai_title_hashtag} requires {ai_title} in the caption template` |
| `caption_ai_prompt` rỗng / null | `Add an AI prompt or remove {ai_title} from the caption template` |
| `app_settings["llm.enabled"]` = `false` | `Enable LLM in Settings before using {ai_title}` |
| Mode = **cloud** và không có row `llm_models.is_default = true` | `Configure a default LLM in Settings before using {ai_title}` |

> **Mode = Local (Ollama)**: precondition thứ 3 không áp dụng — Ollama không cần row trong `llm_models`. Chỉ cần LLM enabled và `caption_ai_prompt` được set.

UI form (`PipelineFormModal`) validate cùng rule client-side qua Zod refinement để báo sớm.

**Khi PATCH**: server tính candidate row sau merge rồi mới validate. Nếu 422 → row giữ nguyên, không có ghi đè bộ phận.

---

## 7. Test connection

Nút **Test connection** trong modal Add/Edit:

- Gọi `POST /api/settings/llm/test` với payload `{model_id, api_key, api_base?, api_version?}`.
- Server encrypt key, gọi `litellm.completion` với prompt `"ping"` và `max_tokens=5`.
- Chi phí: ~$0.00001 trên Gemini Flash, ~$0.00005 trên OpenAI gpt-4o-mini.
- Kết quả hiển thị trong chip duy nhất phía dưới checkbox **Set as default**:
  - ✓ xanh + text "ok" — connect OK.
  - ✗ đỏ + error message ngắn — sai key / sai model_id / sai api_version / SSRF reject…

Mọi error trả về đi qua `scrub_bot_tokens` + cap 200 ký tự để không leak key vào browser dev console.

---

## 8. Bảo mật

### 8.1. Encryption at rest

API key trong DB lưu dưới dạng **Fernet ciphertext** (AES-128-CBC + HMAC-SHA256). Key dùng để mã hóa nằm tại:

```
data/.secret_key   (mode 600, gitignored)
```

#### Vòng đời key file

| Tình huống | Hành vi |
|---|---|
| Lần đầu boot api/scheduler, file không tồn tại | Auto-generate Fernet key, ghi atomic (`O_CREAT|O_EXCL`), mode `0o600`. |
| api + scheduler boot song song lần đầu | Race-safe — chỉ 1 process ghi được file, process còn lại đọc lại file đã ghi. |
| File bị xóa giữa chừng | Mọi ciphertext trong DB không decode được. `decrypt()` trả `""` (không crash) → pipeline có `{ai_title}` rơi vào fallback raw title. User cần **re-enter API key** trong Settings. |

#### Backup / restore

- Backup `data/` **phải bao gồm cả `data/.secret_key`**.
- Copy `tiktok.db` sang máy khác mà thiếu key file → re-enter keys từ Settings.

#### Rotate key (thủ công)

Chưa có tool rotate tự động (không tự re-encrypt). Quy trình:

1. Xóa `data/.secret_key`.
2. Restart api → key mới được sinh.
3. Vào Settings → Edit từng LLM model → paste lại API key.

Backlog: tool `secrets rotate` đọc plaintext bằng key cũ, re-encrypt bằng key mới, swap key file atomic.

### 8.2. Log scrubber

`api/logging.BotTokenScrubFilter` chạy ở root logger handler. Mọi log line + error message trả về client đều đi qua. Các regex pattern hiện scrub:

| Pattern | Ví dụ bị scrub |
|---|---|
| Telegram bot token | `bot123456:abcdef…` |
| `api_key=xxx` key-value | `model=foo api_key=secret-xyz` |
| Authorization header | `Authorization: Bearer …` / `x-api-key: …` |
| Bare provider prefix | `sk-…`, `sk-ant-…`, `sk-proj-…`, `AIza…` |
| JWT 3 segment | `eyJ…eyJ…sig` → `***.***.***` |
| Python bytes literal | `b'<token>'` (httpx "Illegal header value" leak) |
| Long opaque token ≥ 32 ký tự | Azure key, opaque session token |

Hệ quả phụ: git SHA 40-hex và base64 cache key cũng bị scrub. Trade-off chấp nhận được trong error message (log debug giảm tiện nhưng key không leak).

### 8.3. SSRF guard cho `api_base` và `OLLAMA_BASE_URL`

`api_base` (cloud model) và `OLLAMA_BASE_URL` (env var) đều được fed vào `litellm.completion` → server gửi HTTP request tới URL đó. Nếu không validate, attacker write `api_base=http://169.254.169.254/...` (AWS metadata) → response body lọt về client.

`OLLAMA_BASE_URL` được validate tại startup (khi `get_ollama_config()` được gọi lần đầu). Nếu fail → `RuntimeError`, api/scheduler từ chối start.

Server validate `api_base` (cloud) ở cả 3 chỗ (`Create`, `Update`, `Test`):

| URL | Hành vi |
|---|---|
| `https://x.openai.azure.com/` | ✓ accept (public host, https) |
| `https://api.openai.com/v1` | ✓ accept |
| `http://localhost:11434/v1/` | ✓ accept (loopback hostname) |
| `http://127.0.0.1:11434/` | ✓ accept (loopback IP) |
| `http://169.254.169.254/...` | ✗ 422 (link-local — cloud metadata endpoint) |
| `http://10.0.0.5/`, `http://192.168.1.1/`, `http://172.20.0.5/` | ✗ 422 (RFC1918 private) |
| `http://example.com/` | ✗ 422 (public host trên plain http — bắt buộc https) |
| `ftp://example.com/` | ✗ 422 (chỉ chấp nhận http/https) |

Logic: scheme phải là `http` hoặc `https`. Nếu host parse được thành IP và rơi vào private/loopback/link-local/multicast/reserved → reject (trừ loopback). Nếu host là FQDN → bắt buộc `https` trừ khi là `localhost`.

### 8.4. Model ID whitelist

`model_id` phải khớp regex `^[a-z0-9_\-]+/[A-Za-z0-9._:\-]+$`. Provider segment lowercase + chỉ alphanumeric/underscore/hyphen — chặn các provider routing-style mà litellm hỗ trợ có thể tạo SSRF surface ngoài ý muốn.

---

## 9. Schema DB liên quan

```sql
-- app_settings: key/value store toàn cục
CREATE TABLE app_settings (
  key VARCHAR(64) PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
-- Seed: ("llm.enabled", "true"), ("llm.mode", "cloud")

-- llm_models: user-managed endpoints
CREATE TABLE llm_models (
  id INTEGER PRIMARY KEY,
  label VARCHAR(128) NOT NULL,
  model_id VARCHAR(128) NOT NULL,
  api_key_encrypted TEXT NOT NULL,   -- Fernet ciphertext
  api_base VARCHAR(256),             -- nullable
  api_version VARCHAR(32),           -- nullable
  is_default BOOLEAN NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
CREATE UNIQUE INDEX ux_llm_models_one_default
  ON llm_models (is_default) WHERE is_default = 1;

-- channel_pipelines: thêm 1 cột nullable
ALTER TABLE channel_pipelines ADD COLUMN caption_ai_prompt TEXT;
```

Partial unique index `ux_llm_models_one_default` enforce invariant **"tối đa 1 row default tại 1 thời điểm"** ở mức DB. Hai writer race set-default sẽ có đúng 1 row thắng; row còn lại nhận `IntegrityError` thay vì để DB sai trạng thái.

---

## 10. Cài đặt + deploy

Thêm 2 package vào `requirements.txt`:

```
cryptography>=42,<46
litellm>=1.85,<2
```

Trên môi trường Docker:

```bash
make rebuild-base   # rebuild tiktok-base để pickup requirements.txt mới
make up
```

Trên môi trường native (dev):

```bash
pip install -r requirements.txt
```

Migration `apply_schema_patches` chạy tự động lúc api/scheduler boot — thêm `caption_ai_prompt`, `api_base`, `api_version` nếu chưa có. Idempotent, an toàn rerun. Seed `llm.mode = "cloud"` cũng được thêm tự động (idempotent, không ghi đè giá trị hiện tại).

### Cài Ollama (nếu dùng Local mode)

Ollama chạy trên **host machine** (không phải container). Cài một lần:

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2
# ollama serve   # không cần nếu systemd service đã start tự động
```

Sau đó set env vars trong `.env`:

```env
OLLAMA_MODEL=llama3.2
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

Recreate api + scheduler để pickup env mới (`make recreate-api` — `restart` không đọc lại `.env`). Vào Settings → chuyển sang **Local (Ollama)**.

#### Docker reach Ollama trên host

`docker-compose.yml` đã khai báo `extra_hosts: host.docker.internal:host-gateway` cho api + scheduler, nên `http://host.docker.internal:11434` resolve về host trên cả macOS (Docker Desktop) lẫn Linux/WSL (docker engine native, gateway `172.17.0.1`).

Nhưng route tới host **chưa đủ** — Ollama mặc định bind `127.0.0.1`, chỉ nghe loopback của host, container không vào được (`[Errno 111] Connection refused`, api trả `502`).

Phải cho Ollama bind mọi interface qua `OLLAMA_HOST=0.0.0.0`. Với systemd service (cách `install.sh` cài):

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

> `systemctl edit ollama` mở editor để tạo cùng file override này; nếu editor không lưu được (hay gặp trên WSL) thì dùng `tee` như trên.

Verify theo thứ tự:

```bash
ss -tlnp | grep 11434                                    # phải thấy 0.0.0.0:11434, không còn 127.0.0.1
docker compose exec api curl -sS http://host.docker.internal:11434   # phải in "Ollama is running"
```

SSRF guard chấp nhận `host.docker.internal` và IP bridge (loopback/link-local của Docker network) — nếu `GET /api/settings` báo lỗi base URL thì xem [§8.3](#83-ssrf-guard-cho-api_base-và-ollama_base_url).

---

## 11. Tham khảo

- LiteLLM providers: <https://docs.litellm.ai/docs/providers>
- Ollama model library: <https://ollama.com/library>
- Azure OpenAI versions: <https://learn.microsoft.com/en-us/azure/ai-services/openai/api-version-deprecation>
- Fernet (cryptography): <https://cryptography.io/en/latest/fernet/>

---

**Next:** [Index](README.md)
