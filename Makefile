# farm_tiktik v2 — Docker Compose convenience targets.
#
# Layer model:
#   docker/base.Dockerfile installs requirements.txt + the tiktok_uploader
#   package. api/scheduler/novnc images all FROM tiktok-base:latest and
#   only COPY their own source. So:
#
#   - You changed code in api/ or scheduler/ only        -> make restart
#   - You changed code in webapp/ (mounted dev volume)   -> nothing (Vite HMR)
#   - You changed requirements.txt or tiktok_uploader/   -> make rebuild-base
#   - You ran `make douyin-update` (vendor changed)       -> make rebuild-base
#   - You added a service or changed Dockerfile         -> make rebuild
#   - You think Docker has gone insane                   -> make nuke && make up
#
# Stop using `docker compose up --recreate` to fix dependency errors. It
# does NOT rebuild images. Use `make rebuild-base` (or `make rebuild`)
# instead when requirements.txt changed.

# Allow overriding the compose project / file from CLI if ever needed.
COMPOSE        ?= docker compose
SERVICES_RUN   := api scheduler novnc webapp

# Pretty output.
GREEN  := \033[1;32m
YELLOW := \033[1;33m
RESET  := \033[0m

.DEFAULT_GOAL := help

# ─── Day-to-day ────────────────────────────────────────────────────────────

.PHONY: deploy
deploy:  ## Build all images + force-recreate containers (safe default after any code change)
	@printf "$(YELLOW)Building all images (tiktok-base + services)…$(RESET)\n"
	$(COMPOSE) build
	@printf "$(YELLOW)Recreating containers from fresh images…$(RESET)\n"
	$(COMPOSE) up -d --force-recreate
	@$(MAKE) --no-print-directory ps

.PHONY: deploy-prod
deploy-prod:  ## Like deploy but prod compose only (no dev override / Vite)
	@printf "$(YELLOW)Building production images…$(RESET)\n"
	$(COMPOSE) -f docker-compose.yml build
	@printf "$(YELLOW)Starting production stack…$(RESET)\n"
	$(COMPOSE) -f docker-compose.yml up -d --force-recreate
	@$(COMPOSE) -f docker-compose.yml ps

.PHONY: up
up:  ## Start the full stack in dev mode (uses docker-compose.override.yml)
	@printf "$(GREEN)Starting stack…$(RESET)\n"
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory ps

.PHONY: down
down:  ## Stop and remove containers (keeps volumes + images)
	$(COMPOSE) down

.PHONY: restart
restart:  ## Restart api + scheduler. Use after editing api/ or scheduler/ code.
	$(COMPOSE) restart api scheduler
	@$(MAKE) --no-print-directory ps

.PHONY: ps
ps:  ## Show container status
	@$(COMPOSE) ps

.PHONY: logs
logs:  ## Tail logs from all services (Ctrl-C to exit)
	$(COMPOSE) logs -f --tail=50

.PHONY: logs-api
logs-api:  ## Tail api logs only
	$(COMPOSE) logs -f --tail=100 api

.PHONY: logs-scheduler
logs-scheduler:  ## Tail scheduler logs only
	$(COMPOSE) logs -f --tail=100 scheduler

.PHONY: logs-webapp
logs-webapp:  ## Tail webapp (Vite) logs only
	$(COMPOSE) logs -f --tail=100 webapp

# ─── Rebuild paths ─────────────────────────────────────────────────────────

.PHONY: rebuild-base
rebuild-base:  ## Rebuild the shared base image (requirements.txt / tiktok_uploader changes)
	@printf "$(YELLOW)Rebuilding tiktok-base (this picks up requirements.txt changes)…$(RESET)\n"
	$(COMPOSE) build --no-cache tiktok-base
	@printf "$(YELLOW)Rebuilding dependent images…$(RESET)\n"
	$(COMPOSE) build api scheduler novnc
	@printf "$(GREEN)Done. Run: make up$(RESET)\n"

.PHONY: rebuild
rebuild:  ## Rebuild every image (full clean build, slowest)
	@printf "$(YELLOW)Full no-cache rebuild…$(RESET)\n"
	$(COMPOSE) build --no-cache
	@printf "$(GREEN)Done. Run: make up$(RESET)\n"

.PHONY: rebuild-app
rebuild-app:  ## Rebuild api + scheduler + webapp prod images (Dockerfile or code copy changed)
	$(COMPOSE) build api scheduler webapp
	@printf "$(GREEN)Done. Run: make up$(RESET)\n"

# ─── Recreate (when images are already correct) ───────────────────────────

.PHONY: recreate
recreate:  ## Recreate all containers from current images (does NOT rebuild)
	$(COMPOSE) up -d --force-recreate
	@$(MAKE) --no-print-directory ps

.PHONY: recreate-api
recreate-api:  ## Recreate just api + scheduler from current images
	$(COMPOSE) up -d --force-recreate api scheduler
	@$(MAKE) --no-print-directory ps

# ─── Webapp dev volume helpers ─────────────────────────────────────────────

.PHONY: webapp-fresh
webapp-fresh:  ## Wipe node_modules in the webapp container and reinstall (use if vite errors after package.json change)
	$(COMPOSE) exec webapp sh -c "rm -rf /app/node_modules /app/.vite && npm install"
	$(COMPOSE) restart webapp

# ─── Vendor ────────────────────────────────────────────────────────────────

.PHONY: douyin-update
douyin-update:  ## Pull latest douyin-downloader into vendor/ (backs up current first)
	bash scripts/update-douyin-vendor.sh

# ─── Testing ───────────────────────────────────────────────────────────────

.PHONY: test
test:  ## Run pytest in the api container
	$(COMPOSE) exec api python -m pytest -q

.PHONY: test-local
test-local:  ## Run pytest in the local .venv (faster iteration)
	. .venv/bin/activate && python -m pytest -q

.PHONY: build-webapp
build-webapp:  ## Run vite build locally to type-check + bundle the frontend
	cd webapp && npm run build

# ─── Shell access ──────────────────────────────────────────────────────────

.PHONY: shell-api
shell-api:  ## Bash into the api container
	$(COMPOSE) exec api bash

.PHONY: shell-scheduler
shell-scheduler:  ## Bash into the scheduler container
	$(COMPOSE) exec scheduler bash

.PHONY: shell-webapp
shell-webapp:  ## Shell into the webapp (Node) container
	$(COMPOSE) exec webapp sh

# ─── Nuclear options ───────────────────────────────────────────────────────

.PHONY: clean
clean: down  ## Stop containers + remove dangling images
	docker image prune -f

.PHONY: nuke
nuke:  ## Stop everything, drop project containers + images. Keeps named volumes.
	@printf "$(YELLOW)Removing project containers + images…$(RESET)\n"
	-$(COMPOSE) down --rmi local --remove-orphans
	@printf "$(GREEN)Cleared. Run: make rebuild-base && make up$(RESET)\n"

.PHONY: nuke-all
nuke-all:  ## Same as nuke but also drops named volumes (deletes data/ SQLite!). Confirm with EXTRA=yes.
ifneq ($(EXTRA),yes)
	@printf "$(YELLOW)This deletes container data. Re-run with EXTRA=yes if you mean it.$(RESET)\n"
	@exit 1
endif
	$(COMPOSE) down --rmi local --remove-orphans --volumes

# ─── Self-doc ──────────────────────────────────────────────────────────────

.PHONY: help
help:  ## Show this help (default target)
	@printf "$(GREEN)farm_tiktik v2 — make targets$(RESET)\n\n"
	@awk 'BEGIN {FS = ":.*?## "} \
		/^[a-zA-Z0-9_-]+:.*?## / { \
			printf "  $(GREEN)%-18s$(RESET) %s\n", $$1, $$2 \
		}' $(MAKEFILE_LIST)
	@printf "\n$(YELLOW)Common recipes:$(RESET)\n"
	@printf "  Unsure / shipped a feature -> make deploy  (build + recreate; always picks up new code)\n"
	@printf "  Edited requirements.txt    -> make deploy  (or make rebuild-base && make up)\n"
	@printf "  Updated Douyin vendor      -> make douyin-update && make rebuild-base && make up\n"
	@printf "  Edited api/*.py (dev)      -> uvicorn --reload (or make restart)\n"
	@printf "  Edited scheduler/*.py      -> make restart  (no auto-reload)\n"
	@printf "  Edited webapp/*.tsx (dev)  -> (nothing — Vite HMR)\n"
	@printf "  VPS prod-like              -> make deploy-prod\n"
	@printf "  Stack is broken            -> make nuke && make deploy\n"
