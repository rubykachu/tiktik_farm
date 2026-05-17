# Implementation Plan — Phase 3

Source spec: `notebooks/v2/Phase3- SPEC.md` (Phase 3 spec).
Source brief: `notebooks/v2/Phase3.md`.
Branch: `feat/phase3`.

## Overview

Phase 3 ships four user-visible changes against the v2 control panel:

1. **Schedule modal redesign** — widen the pipeline form, regroup fields into a 2-column layout.
2. **LLM caption rewrite** — pipeline-level prompt rewrites the YouTube title+description through litellm; result is injected as `{ai_title}` in the caption template. Provider/model/API-key are user-managed in a new Settings screen and encrypted at rest with Fernet.
3. **Webapp `/videos` → `/histories` rename** — frontend only; API path and `video_uploads` table untouched.
4. **English-only UI copy** — hard inline replacement of all remaining Vietnamese strings.

VPN and per-upload IP tracking are deferred to Phase 4 (blocked by ProtonVPN CLI not supporting headless setups).

## Architecture Decisions

| Decision | Why |
|---|---|
| **Fernet (symmetric) with key file `data/.secret_key`** | Local-first product; OS keyring breaks under headless Docker. Key file mode 600, gitignored. Re-entering keys is the disaster recovery path. |
| **Single `LLMModel` table (label, model_id, api_key, is_default)** | User adds entries with their own labels and litellm-format `provider/model` strings. Avoids a hardcoded dropdown. One row flagged `is_default` is what the worker uses. |
| **Caption variable `{ai_title}` + template stays in charge** | Operator keeps control of formatting and hashtags. LLM only rewrites title using the provided prompt + YouTube description. |
| **Per-tick cache keyed by `video_id`** | Pipelines with multiple accounts call LLM once per video, not N times. |
| **LLM failure degrades silently to raw title** | An LLM outage must not stop scheduled uploads. Worker logs and falls back. |
| **yt-dlp `download()` returns `(path, description)`** | Single `extract_info(download=True)` already pulls full metadata; piggyback on it instead of an extra HTTP round-trip. |
| **`/api/videos` path unchanged** | Multiple webapp/queries depend on it; rename is webapp-only (`/videos` → `/histories`, plus redirect for the old URL). |
| **English copy without i18n framework** | Spec D explicitly defers i18n. Replace inline, use plain words. |

## Dependency Graph

```
secrets.py  ──┐
              ├─→ llm_models table ─→ Settings/LLMModels routers ─→ Settings page UI
DB schema  ──┤                                                              │
              └─→ caption_ai_prompt + {ai_title} validation                  │
                                                                             │
yt-dlp download() → description ──→ caption.render(ai_title=) ──→ worker ────┘
                                                                             │
                                                                pipeline modal redesign (consumes new LLM defaults)
                                                                             │
                                                                histories rename + EN copy sweep
```

Foundation (secrets, schema, llm.py) blocks everything else.
Backend routers block the Settings page.
Worker integration is independent of frontend.
Frontend modal redesign depends on Settings being live (so default LLM exists).

## Task List

### Phase 1: Foundation (sequential)

#### Task 1: Secrets service — Fernet encrypt/decrypt + key bootstrap

**Description:** Create `api/services/secrets.py` exposing `encrypt(plain)`, `decrypt(token)`, and `mask(plain)`. On import, load `data/.secret_key` if present; otherwise generate a new Fernet key, write it with mode 600, and use it. Add `cryptography>=42` to `requirements.txt`. Add `data/.secret_key` to `.gitignore`.

**Acceptance criteria:**
- [ ] `encrypt` then `decrypt` round-trips arbitrary strings.
- [ ] Missing key file is generated automatically on first call, mode 600.
- [ ] `mask("AIzaSyABCDEFGHIJKLMNOP")` returns a string with first 4 + bullets + last 4 chars.
- [ ] `data/.secret_key` is listed in `.gitignore`.

**Verification:**
- [ ] `pytest tests/api/test_secrets.py -q` passes.
- [ ] `grep -F "data/.secret_key" .gitignore` matches.

**Dependencies:** None.

**Files touched:**
- `api/services/secrets.py` (new)
- `tests/api/test_secrets.py` (new)
- `requirements.txt`
- `.gitignore`

**Estimated scope:** S.

---

#### Task 2: DB schema — `LLMModel`, `AppSetting`, `ChannelPipeline.caption_ai_prompt`

**Description:** Add two new SQLModel tables and one additive column.

```python
class LLMModel(SQLModel, table=True):
    __tablename__ = "llm_models"
    id: Optional[int] = primary_key
    label: str
    model_id: str        # litellm "provider/model"
    api_key_encrypted: str
    is_default: bool = False (indexed)
    created_at, updated_at: datetime

class AppSetting(SQLModel, table=True):
    __tablename__ = "app_settings"
    key: str = primary_key
    value: str
    updated_at: datetime

ChannelPipeline:
    caption_ai_prompt: Optional[str] = None  # additive
```

Extend `api/db_migrations.apply_schema_patches` to add `caption_ai_prompt` column when missing. Tables are created by `init_db()` via `SQLModel.metadata.create_all`. Seed `app_settings` with `("llm.enabled", "true")` on first boot (idempotent: insert if missing).

**Acceptance criteria:**
- [ ] `init_db()` creates `llm_models` and `app_settings` on a fresh DB.
- [ ] On a DB created before this branch, `apply_schema_patches` adds `caption_ai_prompt` without dropping existing rows.
- [ ] `app_settings` has `llm.enabled=true` after startup.
- [ ] Only one row at a time may have `is_default=true` — enforced by helper `set_default_llm_model(id)` that flips others off in the same transaction. (Schema-level uniqueness not added; SQLite can't enforce "exactly one true" easily.)

**Verification:**
- [ ] `pytest tests/api/test_db_migrations.py -q` covers the additive column and the seed.
- [ ] Manual: rm `data/tiktok.db`, restart api, inspect `sqlite3 data/tiktok.db ".schema"`.

**Dependencies:** None (independent of Task 1).

**Files touched:**
- `api/models.py`
- `api/db_migrations.py`
- `tests/api/test_db_migrations.py` (new)

**Estimated scope:** S.

---

#### Task 3: LLM service — `services/llm.py` + tests

**Description:** Create `api/services/llm.py` exposing `rewrite_title(prompt, title, description, *, model_id, api_key_encrypted) -> str`. Decrypts the key, truncates description to 2000 chars, calls `litellm.completion(...)` with a 30-s timeout and `max_tokens=200`, returns the stripped content. Raises `LLMNotConfigured` if the key is missing/unreadable, `LLMCallFailed` if litellm raises. Add `litellm>=1.50` to `requirements.txt`. Also expose `test_completion(model_id, api_key_encrypted) -> tuple[bool, str]` for Settings' "test connection" button.

**Acceptance criteria:**
- [ ] Happy path returns the LLM-rewritten title (litellm patched).
- [ ] Missing key → `LLMNotConfigured`.
- [ ] Litellm raising any `Exception` → `LLMCallFailed` with original message.
- [ ] Long description truncated to 2000 chars before being sent.
- [ ] `test_completion` returns `(True, "ok")` on success, `(False, "<error>")` otherwise.

**Verification:**
- [ ] `pytest tests/services/test_llm.py -q` passes with `litellm.completion` patched (no network).

**Dependencies:** Task 1.

**Files touched:**
- `api/services/llm.py` (new)
- `tests/services/test_llm.py` (new)
- `requirements.txt`

**Estimated scope:** S.

---

### Checkpoint A — Foundation

- [ ] `pytest -q` passes for new tests (T1, T2, T3).
- [ ] `uvicorn api.main:app --port 8000` boots without errors on a fresh `data/` directory.
- [ ] `sqlite3 data/tiktok.db ".schema llm_models"` shows the expected columns.
- [ ] Branch is in a clean state; human reviews schema choices before continuing.

---

### Phase 2: Backend feature slices

#### Task 4: Settings + LLMModel routers

**Description:** Add two routers.

- `api/routers/settings.py` mounted at `/api/settings`:
  - `GET ""` → `{"llm.enabled": bool}` plus any future flags.
  - `PATCH ""` body `{"llm.enabled": bool}` upserts.
  - `POST "/llm/test"` body `{"model_id": str, "api_key": str}` calls `llm.test_completion(model_id, encrypt(api_key))`, returns `{ok, message}`. Used by Settings before saving.

- `api/routers/llm_models.py` mounted at `/api/llm-models`:
  - `GET ""` → list with `api_key` replaced by `api_key_masked` (via `secrets.mask`).
  - `POST ""` → create; body `LLMModelCreate(label, model_id, api_key, set_default=False)`. Encrypts key on the way in.
  - `PATCH "/{id}"` → update any of (label, model_id, api_key). If `api_key` empty/absent → keep existing.
  - `POST "/{id}/set-default"` → flip this row's `is_default=true`, clear others.
  - `DELETE "/{id}"` → 204. If the deleted row was the default, no automatic re-election (UI prompts user).

Schemas live in `api/schemas.py`. Mount routers in `api/main.py`. Extend `install_global_scrub_filter` (in `api/logging.py`) to also scrub `api_key=` patterns.

**Acceptance criteria:**
- [ ] GET endpoints never return plaintext API keys.
- [ ] PATCH/POST with empty `api_key` preserves the existing ciphertext.
- [ ] `set-default` is atomic — exactly one row ends with `is_default=true`.
- [ ] `POST /api/llm-models` with a duplicate `model_id` is allowed (different keys, different labels).
- [ ] OpenAPI `/docs` shows the new endpoints.

**Verification:**
- [ ] `pytest tests/api/test_llm_models_router.py -q` and `tests/api/test_settings_router.py -q` pass.
- [ ] `curl http://localhost:8000/api/llm-models` returns `[]` on a fresh DB.

**Dependencies:** Tasks 1, 2, 3.

**Files touched:**
- `api/routers/settings.py` (new)
- `api/routers/llm_models.py` (new)
- `api/schemas.py`
- `api/main.py`
- `api/logging.py` (scrub additions)
- `tests/api/test_settings_router.py` (new)
- `tests/api/test_llm_models_router.py` (new)

**Estimated scope:** M.

---

#### Task 5: Caption + pipeline validation for `{ai_title}`

**Description:** Extend the caption pipeline so `{ai_title}` is a first-class variable.

- `api/services/caption.py`:
  - Add `"ai_title"` to `ALLOWED_VARS` and the `_RENDER_RE` alternation.
  - `render_caption` accepts `ai_title: str = ""`; when omitted, behaves as before.

- `api/schemas.py`:
  - `PipelineCreate` and `PipelineUpdate` gain `caption_ai_prompt: Optional[str] = None`.
  - New `model_validator` rule: if the (possibly merged) `caption_template` contains `{ai_title}`:
    - require non-empty `caption_ai_prompt`;
    - require `app_settings["llm.enabled"]` truthy;
    - require at least one `LLMModel` with `is_default=true`.

    The DB-dependent checks are too heavy for a Pydantic validator → do them in `api/routers/pipelines.py` (`create_pipeline` and `update_pipeline`), raising `HTTPException(422, …)` with the exact strings from the spec.

- `api/routers/pipelines.py`:
  - Persist `caption_ai_prompt`. Update `_to_read` to expose it.

**Acceptance criteria:**
- [ ] `render_caption("{ai_title} #tag", title="t", ai_title="AI")` returns `"AI #tag"`.
- [ ] Existing pipelines (`caption_ai_prompt=NULL`, no `{ai_title}` in template) keep working.
- [ ] POST/PATCH with `{ai_title}` and empty prompt returns 422 `"Add an AI prompt or remove {ai_title}..."`.
- [ ] POST/PATCH with `{ai_title}` while no default LLM exists returns 422 `"Configure a default LLM in Settings..."`.
- [ ] POST/PATCH with `{ai_title}` while `llm.enabled=false` returns 422 `"Enable LLM in Settings..."`.

**Verification:**
- [ ] `pytest tests/services/test_caption.py tests/api/test_pipelines_validation.py -q` passes.
- [ ] Manual: create a pipeline with `caption_template="{ai_title}"` and confirm each of the three error messages by toggling preconditions.

**Dependencies:** Tasks 2, 4.

**Files touched:**
- `api/services/caption.py`
- `api/schemas.py`
- `api/routers/pipelines.py`
- `tests/services/test_caption.py`
- `tests/api/test_pipelines_validation.py` (new)

**Estimated scope:** M.

---

#### Task 6: yt-dlp description + worker LLM integration

**Description:** Wire the LLM into pipeline ticks.

- `api/services/youtube_channel.py`:
  - Change `download(...)` to return `(path: str, description: str)`. Description comes from the `extract_info` result.
  - If the channel lacks a `shorts` tab (or any subtab the URL targets), let the existing `Exception` flow surface — already handled by `run_pipeline` as a list/download failure.

- `scheduler/pipeline_worker.py`:
  - Maintain `ai_title_by_video: dict[str, str]` reset each tick.
  - When `pipeline.caption_ai_prompt` is truthy AND `app_settings["llm.enabled"]` is truthy AND a default `LLMModel` exists: call `llm.rewrite_title(...)` once per video_id.
  - On `LLMNotConfigured` or `LLMCallFailed`, fall back to the raw `title` and log a warning.
  - Pass `ai_title=...` into `render_caption`.
  - Update existing `download(...)` callsites for the new tuple return.

- Helper `api/services/llm_config.py` (or expose helper from `routers/llm_models.py`) for `get_default_llm()` and `is_llm_enabled()` so the worker doesn't reach into routers.

**Acceptance criteria:**
- [ ] Pipeline without prompt: `ai_title_by_video` stays empty; behavior is unchanged.
- [ ] Pipeline with prompt + default LLM: `llm.rewrite_title` is called once per video_id even with multiple accounts.
- [ ] `LLMCallFailed` does not abort the tick or change the run state; the raw `title` is used as `{ai_title}`.
- [ ] `download()` callers in `pipeline_worker.py` unpack the tuple correctly.
- [ ] Test that targets `https://www.youtube.com/@themodelcarproject/videos` returns at least one entry on a real run (manual smoke).

**Verification:**
- [ ] `pytest tests/scheduler/test_pipeline_worker_llm.py -q` passes (litellm + yt-dlp patched).
- [ ] Manual: run one pipeline with prompt `"Rewrite the title in clickbait style"` against the channel above; check the rendered caption in Histories.

**Dependencies:** Tasks 3, 5.

**Files touched:**
- `api/services/youtube_channel.py`
- `api/services/llm_config.py` (new)
- `scheduler/pipeline_worker.py`
- `tests/scheduler/test_pipeline_worker_llm.py` (new)
- Optional: update `tests/services/test_youtube_channel.py` for the new tuple return.

**Estimated scope:** M.

---

### Checkpoint B — Backend complete

- [ ] All Phase 2 tests pass.
- [ ] OpenAPI `/docs` lists settings, llm-models, and the updated pipelines endpoints.
- [ ] Local smoke: create a default `LLMModel`, create a pipeline with `caption_ai_prompt`, run-now, inspect the rendered caption in `video_uploads`.

---

### Phase 3: Frontend feature slices

#### Task 7: Settings page (vertical slice)

**Description:** New top-level page that owns LLM configuration.

- `webapp/src/api/settings.ts` and `webapp/src/api/llmModels.ts` — typed clients wrapping the new endpoints.
- `webapp/src/pages/Settings.tsx`:
  - Section "LLM caption rewrite" with a global enable toggle (binds to `app_settings["llm.enabled"]`).
  - Models table: columns "Default", "Label", "Model ID", "API Key (masked)", "Actions" (Edit, Delete, Set default).
  - "Add model" button → modal with `label`, `model_id`, `api_key` inputs. Hint text under `model_id`: `"Format: provider/model. Examples: gemini/gemini-2.0-flash-lite, openai/gpt-4o-mini, xai/grok-3-mini. Full list: https://docs.litellm.ai/docs/providers"`.
  - "Test connection" button next to the API-key input → calls `POST /api/settings/llm/test`. Inline status.
- Sidebar gets an entry "Settings" wired to `/settings`. Route added in `App.tsx`.

All copy in English.

**Acceptance criteria:**
- [ ] User can add, edit, delete, and set-default an LLM model from the UI.
- [ ] Toggling "Enable LLM caption rewrite" persists across refresh.
- [ ] "Test connection" shows green ✓ on a valid key (Gemini Flash Lite verified manually) and red ✗ on a bad key.
- [ ] API keys never appear unmasked in the UI after save.

**Verification:**
- [ ] `cd webapp && npm run build` succeeds (type-check + bundle).
- [ ] Manual: walk the flow with a real Gemini API key.

**Dependencies:** Task 4.

**Files touched:**
- `webapp/src/api/settings.ts` (new)
- `webapp/src/api/llmModels.ts` (new)
- `webapp/src/pages/Settings.tsx` (new)
- `webapp/src/components/Sidebar.tsx`
- `webapp/src/App.tsx`

**Estimated scope:** M.

---

#### Task 8: Pipeline modal redesign + AI prompt field

**Description:** Redesign `PipelineFormModal.tsx`.

- Widen container to `max-w-4xl` (~896px).
- Two-column grid on `lg` (sm collapses to one column).
- Section order (top-to-bottom, left-then-right):
  - Column 1 top: **Basic** (name, channel URL).
  - Column 2 top: **Trigger** (interval value + unit, first fire at).
  - Column 1 mid: **Content** (fetch count, upload delay, max duration).
  - Column 2 mid: **Caption** (template textarea + advanced flags).
  - Full-width row: **AI rewrite (optional)** — collapsible. Contains `caption_ai_prompt` textarea + a one-line hint pointing to Settings.
  - Full-width row: **Accounts** (checkbox list).
  - Full-width row: **Telegram** (existing).
  - Sticky footer: Cancel / Save as paused / Save & activate.
- Add Zod refines:
  - `caption_template.includes("{ai_title}") && !caption_ai_prompt` → `"Add an AI prompt or remove {ai_title} from the caption template"`.
  - Optionally a soft warning if the prompt is set but the template lacks `{ai_title}` ("Your prompt won't be used until you add {ai_title}").

**Acceptance criteria:**
- [ ] Modal fits 1280×800 viewport without vertical scroll (best-effort; long Account lists are still expected to scroll internally).
- [ ] Existing pipelines edit/save behaves identically (no regressions in Telegram, accounts, advanced flags).
- [ ] AI prompt textarea round-trips through the API.
- [ ] Client-side validation matches Task 5's server-side errors.

**Verification:**
- [ ] `npm run build` clean.
- [ ] Manual: open create and edit flows, screenshot before/after for the PR.

**Dependencies:** Task 5 (server validation) + Task 7 (default LLM must be configurable from somewhere, even if optional).

**Files touched:**
- `webapp/src/components/PipelineFormModal.tsx`
- `webapp/src/api/pipelines.ts` (add `caption_ai_prompt` field to TS types)

**Estimated scope:** M.

---

#### Task 9: Histories rename + legacy redirect

**Description:** Rename the page only.

- `webapp/src/pages/Videos.tsx` → `webapp/src/pages/Histories.tsx`. Rename the heading from `"Histories"` (already English) — page already has H2 "Histories", just confirm. Keep the `api.get<VideoUploadItem[]>("/videos", ...)` call intact since the API path doesn't change.
- `webapp/src/components/Sidebar.tsx` — change entry `to: "/videos"` to `to: "/histories"`.
- `webapp/src/App.tsx` — add route `/histories → <HistoriesPage />` and keep `/videos` as a `<Navigate to="/histories" replace />`.
- Remove the old `Videos.tsx` file after the rename is complete.

**Acceptance criteria:**
- [ ] Visiting `/videos` redirects to `/histories`.
- [ ] Sidebar item points to `/histories` and is highlighted when active.
- [ ] No backend changes.

**Verification:**
- [ ] `npm run build` clean.
- [ ] Manual: `http://localhost:3000/videos` lands on `/histories`.

**Dependencies:** None (independent of LLM).

**Files touched:**
- `webapp/src/pages/Histories.tsx` (renamed)
- `webapp/src/pages/Videos.tsx` (deleted)
- `webapp/src/App.tsx`
- `webapp/src/components/Sidebar.tsx`

**Estimated scope:** S.

---

#### Task 10: English copy sweep

**Description:** Replace remaining Vietnamese strings with plain English across the webapp.

- Grep target characters with diacritics in `webapp/src/**` (`[À-ỹ]`) and replace each match.
- Common candidates (non-exhaustive): `"Bắt buộc"` → `"Required"`, `"URL không hợp lệ"` → `"Invalid URL"`, `"Lặp mỗi"` → `"Every"`, `"Đơn vị"` → `"Unit"`, `"Bắt đầu từ"` → `"Starting from"`, `"Số video mỗi lần (1-20)"` → `"Videos per run (1-20)"`, `"Nghỉ giữa upload"` → `"Rest between uploads"`, `"Phút nghỉ giữa mỗi lần upload video (0 = không nghỉ)..."` → `"Minutes to wait between uploads (0 = no wait). Helps avoid spam detection."`, `"Bỏ trống = không giới hạn"` → `"Leave empty for no limit"`, `"Tự động phát hiện"` → `"Auto-detect"`, `"Gửi tin test"` → `"Send test"`, `"Đang tải..."` → `"Loading..."`, `"Đang gửi..."` → `"Sending..."`, `"Xóa bot token"` → `"Remove bot token"`, `"Channel URL không đổi được sau khi tạo."` → `"Channel URL cannot change after creation."`, `"Lịch sử upload từ pipeline và các lần chạy."` → `"Upload history per pipeline and run."`, `"Chọn ít nhất 1 account"` → `"Choose at least one account"`, etc.
- Also sweep backend error strings that surface in the UI: `"Pipeline đang tạm dừng..."`, `"Pipeline đang chạy. Đợi tick xong."`, `"Đã xếp Run now..."`, `"Đã xếp lịch chạy ngay"` in `api/routers/pipelines.py` — keep the JSON `code` field unchanged, only translate `message`.

**Acceptance criteria:**
- [ ] `grep -nrE "[À-ỹ]" webapp/src api/routers` returns no matches in user-facing strings (HTML escape sequences inside comments are fine if the comment is dev-only).
- [ ] No string says "test" in two languages.

**Verification:**
- [ ] `npm run build` clean.
- [ ] Manual: click through Accounts, Upload, Schedules, Histories, Settings — confirm English-only.

**Dependencies:** Tasks 7, 8, 9 (so we sweep the final shape, not a moving target).

**Files touched:**
- `webapp/src/components/PipelineFormModal.tsx`
- `webapp/src/pages/*.tsx`
- `api/routers/pipelines.py` (the `run-now` JSON messages)

**Estimated scope:** S.

---

### Checkpoint C — UI complete

- [ ] `npm run build` clean.
- [ ] Click-through smoke: Accounts → Upload → Schedules (modal redesign) → Histories → Settings.
- [ ] Real LLM smoke pass against `https://www.youtube.com/@themodelcarproject/videos`.

---

### Phase 4: Documentation

#### Task 11: Wiki entry + PR notes

**Description:** Document the new Settings page and the secret-key file in `wiki/`. Add `wiki/12-settings.md` covering: enabling LLM rewrite, adding a model, where the secret key lives, what happens if you lose it, how to test a connection. Cross-link from `wiki/README.md` and `wiki/05-pipelines.md` (or whichever pipeline doc exists).

**Acceptance criteria:**
- [ ] New wiki page exists and renders without broken links.
- [ ] PR description summarizes Phase 3 scope, deferred VPN, screenshots of the new modal and Settings.

**Verification:**
- [ ] Manual review of the wiki page.

**Dependencies:** Tasks 7, 8.

**Files touched:**
- `wiki/12-settings.md` (new)
- `wiki/README.md`

**Estimated scope:** XS.

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Lost `data/.secret_key` makes existing rows undecryptable | Med — user must re-enter API keys | Document in wiki. `decrypt()` returns `""` on `InvalidToken` so the worker degrades silently. |
| Litellm Gemini key rotates / model deprecated | Low | Models are user-managed; switching is a UI operation. Documented in wiki. |
| Channel without a `/shorts` tab raises in yt-dlp | Low | Already caught by `run_pipeline`'s `list_top_n` `try/except`. |
| Long description blows token budget | Low | `services/llm.py` truncates to 2000 chars. |
| Default-uniqueness race when two requests `set-default` concurrently | Low (single user, single-process) | Wrap in a single SQLAlchemy transaction; SQLite serializes writes. |
| Modal redesign accidentally drops a field | Med | Snapshot of current modal at the start; checklist of fields in PR description. |
| English sweep misses a string inside a server error | Low | Final grep in Task 10. Backend `pipelines.py:run-now` strings handled. |

## Open Questions (carried from spec)

1. Soft redirect `/videos` → `/histories` for one release, then drop in Phase 4? — Recommend yes; encoded in Task 9.
2. Should "Test connection" be free-form or pinned to a tiny `"ping"` prompt with `max_tokens=5`? — Pinned tiny prompt; cost is trivial. Encoded in Task 3.
3. Decryption failure handling: silent `""` vs. raise? — Silent `""`; matches the LLM-degrades-gracefully policy.

## Parallelization Notes

- Tasks 1, 2, and parts of 3 can be done in parallel by separate agents (no shared files).
- Frontend Task 9 (Histories rename) is independent of LLM work — can be picked up any time after the branch is ready.
- Tasks 4–6 must follow 1–3 in order; T5 and T6 can interleave once T4 is in.
- Task 10 (English sweep) is the last touch on the webapp tree.

## Order Summary

```
T1 ─┐
T2 ─┼─→ T3 ─→ T4 ─→ T5 ─→ T6 ─→ (Checkpoint B)
    │                ↓
    └──────────→ T9 (independent)
                                 ↓
                  T7 ─→ T8 ─→ T10 ─→ (Checkpoint C) ─→ T11 ─→ Done
```
