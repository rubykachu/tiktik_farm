# Phase 3 — TODO

Source: `tasks/plan.md`. Mark items done as PRs land.

## Phase 1 — Foundation

- [ ] **T1. Secrets service** — `api/services/secrets.py` + Fernet key file + `.gitignore` + `cryptography` dep + tests.
- [ ] **T2. DB schema** — `LLMModel`, `AppSetting` tables, `ChannelPipeline.caption_ai_prompt` column, `app_settings["llm.enabled"]` seed, migration tests.
- [ ] **T3. LLM service** — `api/services/llm.py` (litellm wrapper), `rewrite_title`, `test_completion`, `litellm` dep, tests.

### Checkpoint A
- [ ] pytest green for T1–T3.
- [ ] API boots on fresh `data/`.
- [ ] Human reviews schema.

## Phase 2 — Backend feature slices

- [ ] **T4. Settings + LLMModel routers** — `/api/settings` + `/api/llm-models` CRUD, `set-default`, `POST /api/settings/llm/test`, schemas, log scrub additions.
- [ ] **T5. Caption `{ai_title}` + pipeline validation** — extend `caption.py`, pipeline schemas, router cross-field checks (prompt, default LLM, enabled flag).
- [ ] **T6. yt-dlp description + worker LLM integration** — `download()` returns `(path, description)`, per-tick `ai_title_by_video` cache, graceful fallback on LLM failure.

### Checkpoint B
- [ ] pytest green for T4–T6.
- [ ] `/docs` shows new endpoints.
- [ ] Smoke: default LLM model + pipeline with prompt → `{ai_title}` in caption.

## Phase 3 — Frontend feature slices

- [ ] **T7. Settings page** — `Settings.tsx`, model CRUD, global toggle, test connection, model_id hint, sidebar entry, route.
- [ ] **T8. Pipeline modal redesign + AI prompt** — widen to `max-w-4xl`, 2-column grid, AI rewrite section, Zod validation.
- [ ] **T9. `/videos` → `/histories` rename + redirect** — rename file, route, sidebar; keep `<Navigate>` for old path.
- [ ] **T10. English copy sweep** — replace all VI strings in webapp + the few VI strings in `api/routers/pipelines.py` run-now messages.

### Checkpoint C
- [ ] `npm run build` clean.
- [ ] Click-through smoke (Accounts, Upload, Schedules, Histories, Settings).
- [ ] Real LLM smoke against `https://www.youtube.com/@themodelcarproject/videos`.

## Phase 4 — Docs

- [ ] **T11. Wiki entry** — `wiki/12-settings.md`, cross-links, PR notes with screenshots.

## Verification gate before PR

- [ ] `pytest -q` all green.
- [ ] `cd webapp && npm run build` clean.
- [ ] `grep -nrE "[À-ỹ]" webapp/src api/routers` empty for user-facing strings.
- [ ] Settings page survives a refresh (state persisted).
- [ ] Existing pipelines created before this branch still load and edit.
- [ ] `data/.secret_key` not committed.
