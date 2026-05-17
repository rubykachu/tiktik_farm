# Phase 3 — TODO

Source: `tasks/plan.md`. Status as of 2026-05-17.

## Phase 1 — Foundation

- [x] **T1. Secrets service** — `api/services/secrets.py` + Fernet key file (O_CREAT|O_EXCL bootstrap) + `cryptography` dep + tests.
- [x] **T2. DB schema** — `LLMModel`, `AppSetting` tables, `ChannelPipeline.caption_ai_prompt` column, `app_settings["llm.enabled"]` seed, partial unique index `ux_llm_models_one_default`, migration tests.
- [x] **T3. LLM service** — `api/services/llm.py` (litellm wrapper, telemetry off), `rewrite_title`, `test_completion`, `litellm` dep, tests.

### Checkpoint A
- [x] pytest green for T1–T3 (35 new tests).
- [x] API boots on fresh `data/` (verified by integration tests).

## Phase 2 — Backend feature slices

- [x] **T4. Settings + LLMModel routers** — `/api/settings` + `/api/llm-models` CRUD, `set-default` (atomic transaction), `POST /api/settings/llm/test` (redacted error output), schemas with model_id whitelist + api_key cap, broader log scrub.
- [x] **T5. Caption `{ai_title}` + pipeline validation** — `ALLOWED_VARS` adds `ai_title`, `render_caption` accepts `ai_title=`, pipeline router validates against candidate (post-merge) before any row mutation, three precondition messages.
- [x] **T6. yt-dlp description + worker LLM integration** — `download()` returns `(path, description)`, per-tick `ai_title_by_video` cache only on success, graceful fallback to raw title on `LLMNotConfigured`/`LLMCallFailed`/disabled-globally/no-default.

### Checkpoint B
- [x] pytest green for T4–T6.
- [x] `/docs` lists new endpoints (verified via OpenAPI registration in `api/main.py`).
- [ ] Real smoke pass with a Gemini key against `https://www.youtube.com/@themodelcarproject/videos` (pending operator: needs real API key, deferred to user).

## Phase 3 — Frontend feature slices

- [x] **T7. Settings page** — `Settings.tsx` (parent-keyed modal remount), model CRUD, global toggle, Test connection, model_id hint linking to litellm docs, sidebar + route.
- [x] **T8. Pipeline modal redesign + AI prompt** — `max-w-4xl`, 2-column grid, collapsible AI rewrite section auto-opening for existing prompts, Zod refine matches server-side error string.
- [x] **T9. `/videos` → `/histories` rename + redirect** — `Histories.tsx`, route, sidebar; `<Navigate to="/histories" replace />` keeps the legacy URL alive.
- [x] **T10. English copy sweep** — VI strings replaced inline across `webapp/src` and `api/routers/pipelines.py` run-now messages + `api/routers/telegram.py` + `api/services/telegram.TELEGRAM_TEST_MESSAGE`. Test `test_telegram_api::test_success` updated to match new copy.

### Checkpoint C
- [x] `npm run build` clean.
- [ ] Click-through smoke (pending operator: needs running dev server + browser).
- [ ] Real LLM smoke against `@themodelcarproject` (pending operator: needs real API key).

## Phase 4 — Docs

- [x] **T11. Wiki entry** — `wiki/12-settings.md` with secret key file lifecycle, model CRUD, `{ai_title}` validation rules, cross-linked from `wiki/README.md`.

## Code + Security Review fixes

Both `agent-skills:code-reviewer` and `agent-skills:security-auditor` ran on the branch. Their findings are applied:

- [x] Fernet key file race (Critical) — `O_CREAT|O_EXCL` in `secrets.py`.
- [x] `set_default_llm_model` atomic (Critical) — single transaction + partial unique index.
- [x] PATCH validation rolls back (Critical) — candidate-dict pattern, no row mutation before validation.
- [x] Allowed-vars error message (Important) — derives from `ALLOWED_VARS`.
- [x] Scheduler startup parity (Important) — `seed_app_settings`, `apply_indexes`, `install_global_scrub_filter` all called in `scheduler/main.py`.
- [x] `/llm/test` redacts provider errors (Important) — `scrub_bot_tokens` + 200-char cap.
- [x] LLM cache only on success (Important) — `_rewrite_with_llm` only updates `ai_title_by_video` for non-empty rewrites different from raw title.
- [x] Litellm telemetry/callbacks disabled at import (Important).
- [x] Scrub regex broadened (Important) — Bearer, x-api-key, known provider prefixes.
- [x] `model_id` whitelist + `api_key` max_length (Important).
- [x] Pinned upper bounds for cryptography (<46) and litellm (<2).
- [x] Zod refine string matches server (Important).

Deferred (Suggestion-tier from reviewers, tracked for Phase 4):
- MultiFernet for at-rest key rotation.
- Sentinel for unreadable ciphertexts (UI signal vs silent fallback).
- LLM output URL/at-mention post-filter for hostile YouTube descriptions.
- Per-process rate limit on `/api/settings/llm/test`.

## Verification gate before PR

- [x] `pytest -q` all green (269 tests).
- [x] `cd webapp && npm run build` clean.
- [x] `grep` finds no Vietnamese diacritics in `webapp/src` or `api/routers` user-facing strings.
- [ ] Settings page survives a refresh (pending operator: manual browser check).
- [ ] Existing pipelines created before this branch still load and edit (pending operator: visual confirmation).
- [x] `data/.secret_key` covered by existing `data/` glob in `.gitignore`.

## Pending operator actions before merge

1. Pull branch + `pip install -r requirements.txt` to pick up `cryptography` and `litellm`.
2. Real-key smoke: add a Gemini API key in Settings, set as default, create a pipeline with `caption_ai_prompt`, run-now against `https://www.youtube.com/@themodelcarproject/videos`, confirm `{ai_title}` renders in Histories.
3. Manual click-through across Accounts / Upload / Schedules / Histories / Settings.
4. Decide on push + PR creation (not auto-pushed; branch is local-only).
