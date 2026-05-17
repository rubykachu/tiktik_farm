# Settings — Cấu hình LLM rewrite caption

Trang Settings (`/settings`) chứa các cấu hình toàn cục. Phase 3 chỉ có một
nhóm cấu hình: **LLM caption rewrite**.

## LLM caption rewrite

Khi bật, các pipeline có biến `{ai_title}` trong `caption_template` sẽ:

1. Lấy YouTube **title** + **description** của video sắp đăng.
2. Gọi model LLM (mặc định) với prompt do pipeline cung cấp
   (`caption_ai_prompt`).
3. Lấy kết quả trả về thay vào `{ai_title}` trước khi upload TikTok.

Nếu LLM bị lỗi (rate limit, key sai, network…), worker **không fail run** —
caption fallback dùng raw YouTube title; lỗi chỉ ghi log.

### Toggle Enable / Disable

Toggle ở góc trên-phải của card. Khi tắt:
- Tất cả pipeline (kể cả pipeline có `{ai_title}` + prompt) sẽ skip LLM và
  dùng title gốc.
- Validate khi tạo / sửa pipeline cũng chặn `{ai_title}` (422
  "Enable LLM in Settings before using {ai_title}").

### Bảng Available models

Mỗi row là một entry LLM (cặp `model_id` + API key). Một entry duy nhất là
**default** tại 1 thời điểm; worker chỉ đọc default.

Cột:
- **Default** — chip `default` hoặc nút "Set default" để gán.
- **Label** — tên hiển thị do user đặt (vd. "Gemini Flash Lite").
- **Model ID** — chuỗi theo format litellm `provider/model`
  (vd. `gemini/gemini-2.0-flash-lite`, `openai/gpt-4o-mini`,
  `xai/grok-3-mini`). Danh sách đầy đủ:
  <https://docs.litellm.ai/docs/providers>.
- **API key** — **masked**, chỉ hiện 4 ký tự đầu + 4 ký tự cuối + dấu `•`.
- **Actions** — Edit / Delete.

### Add model

Click nút **Add model**:

| Field | Yêu cầu |
|---|---|
| Label | Bắt buộc, ≤ 128 ký tự. |
| Model ID | Bắt buộc. Format `provider/model`. |
| API key | Bắt buộc khi tạo mới. Lưu mã hóa, không hiện lại plaintext. |
| Set as default | Optional. Khi tick, các row khác mất `is_default` (atomic). |

**Test connection** — nút bên cạnh API key gọi `litellm.completion` 1 lần
với prompt `"ping"` và `max_tokens=5`. Mục đích chỉ kiểm tra key + model
có hoạt động không (chi phí gần như $0).

### Edit model

- Có thể đổi label, model_id, hoặc API key.
- Nếu để API key trống → giữ key cũ (không xóa).
- Để xóa key thì Delete cả row.

### Delete model

- Xóa entry khỏi DB.
- Nếu xóa entry default mà không có entry khác → pipeline có `{ai_title}`
  sẽ chuyển sang fallback raw title (không crash). Khuyến nghị set default
  khác trước khi xóa.

## Encryption + secret key file

API key trong DB lưu dưới dạng **Fernet ciphertext** (symmetric AES-128
CBC + HMAC). Key dùng để mã hóa nằm tại:

```
data/.secret_key   (mode 600, gitignored)
```

### Hành vi khi thiếu key

- Lần đầu chạy api/scheduler: nếu file không tồn tại, **auto-generate**
  một Fernet key mới, ghi file với mode `0o600`.
- Nếu file bị xóa giữa chừng: tất cả ciphertext trong DB không decode được
  nữa. `decrypt()` trả về `""` (không crash) → pipeline có `{ai_title}`
  fallback dùng raw title. User cần **re-enter API key từ Settings**.

### Backup / restore

Khi backup `data/`, **phải bao gồm cả `data/.secret_key`**. Nếu copy DB
sang máy khác mà thiếu key file → re-enter keys.

### Đổi key (rotation)

Hiện chưa có tooling rotate. Quy trình thủ công:
1. Xóa `data/.secret_key`.
2. Restart api → key mới được tạo.
3. Vào Settings → Edit từng LLM model → paste lại API key.

## Schema DB liên quan

- `app_settings` — key/value. Phase 3 dùng key `llm.enabled` (`"true"`
  hoặc `"false"`).
- `llm_models` — list các LLM endpoint user-managed. `is_default` bool có
  index; chỉ 1 row là `true`.
- `channel_pipelines.caption_ai_prompt` — TEXT nullable. Khi NULL hoặc rỗng
  thì pipeline không gọi LLM.

## Validation `{ai_title}` (server-side)

Khi POST/PATCH `/api/pipelines` mà `caption_template` chứa `{ai_title}`,
API check 3 precondition và trả 422 với 1 trong 3 message:

- `"Add an AI prompt or remove {ai_title} from the caption template"` —
  thiếu `caption_ai_prompt`.
- `"Enable LLM in Settings before using {ai_title}"` —
  `app_settings["llm.enabled"] == "false"`.
- `"Configure a default LLM in Settings before using {ai_title}"` —
  chưa có row `llm_models.is_default = true`.

UI form (PipelineFormModal) cũng validate cùng rule client-side để báo
sớm.

## Cài đặt dependency

Phase 3 thêm 2 package vào `requirements.txt`:

```
cryptography>=42
litellm>=1.50
```

`pip install -r requirements.txt` sau khi pull branch.

## Tham khảo

- LiteLLM providers: <https://docs.litellm.ai/docs/providers>
- Fernet (cryptography): <https://cryptography.io/en/latest/fernet/>
- Phase 3 SPEC: `notebooks/v2/Phase3- SPEC.md`
