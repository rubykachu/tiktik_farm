# farm_tiktik v2 — one environment, one set of commands.
#
# The host machine IS the server. There is no separate dev/prod deployment:
# `make start` builds and runs exactly what the public domain serves.
#
# Day to day the operator only ever needs:
#   make start | stop | restart | update | logs | status | backup
#
# `make help-all` lists the rescue and development targets.

COMPOSE  ?= docker compose
DEV      := $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml
BACKUPS  := backups

GREEN  := \033[1;32m
YELLOW := \033[1;33m
RED    := \033[1;31m
RESET  := \033[0m

.DEFAULT_GOAL := help

# ─── Preflight ─────────────────────────────────────────────────────────────
# .htpasswd is a bind mount: if the file is missing Docker silently creates a
# directory with that name and nginx dies at startup with an error that says
# nothing about the real cause. Fail here instead, with an instruction.

.PHONY: check
check:
	@test -f .env || { \
		printf "$(RED)Thiếu file .env$(RESET)\n"; \
		printf "Chạy: cp .env.example .env  rồi mở ra điền HOST và PUBLIC_BASE_URL\n"; \
		exit 1; }
	@test -f .htpasswd || { \
		printf "$(RED)Thiếu file .htpasswd (mật khẩu đăng nhập trang web)$(RESET)\n"; \
		printf "Chạy: htpasswd -bc .htpasswd admin 'mat-khau-cua-ban'\n"; \
		exit 1; }
	@grep -q '^PUBLIC_BASE_URL=https' .env || { \
		printf "$(RED)PUBLIC_BASE_URL trong .env chưa đặt$(RESET)\n"; \
		printf "Link tải video gửi qua Telegram được ghép từ giá trị này.\n"; \
		exit 1; }

# ─── Everyday ──────────────────────────────────────────────────────────────

.PHONY: start
start: check  ## Bật hệ thống (build lại rồi chạy — dùng sau mọi thay đổi)
	@printf "$(YELLOW)Đang build…$(RESET)\n"
	$(COMPOSE) build
	@printf "$(YELLOW)Đang khởi động…$(RESET)\n"
	$(COMPOSE) up -d --force-recreate --remove-orphans
	@$(MAKE) --no-print-directory status

.PHONY: stop
stop:  ## Tắt hệ thống (dữ liệu giữ nguyên)
	$(COMPOSE) down

.PHONY: restart
restart:  ## Khởi động lại nhanh, không build lại
	$(COMPOSE) restart
	@$(MAKE) --no-print-directory status

.PHONY: update
update:  ## Lấy code mới nhất rồi bật lại
	git pull
	@$(MAKE) --no-print-directory start

.PHONY: logs
logs:  ## Xem log đang chạy (Ctrl-C để thoát)
	$(COMPOSE) logs -f --tail=100

.PHONY: status
status:  ## Xem hệ thống đang chạy hay không
	@$(COMPOSE) ps

.PHONY: backup
backup:  ## Sao lưu dữ liệu + cấu hình ra thư mục backups/
	@mkdir -p $(BACKUPS)
	@stamp=$$(date +%Y%m%d-%H%M); \
	dest=$(BACKUPS)/$$stamp; mkdir -p $$dest; \
	cp -R data/tiktok.db data/CookiesDir $$dest/ 2>/dev/null || true; \
	cp .env .htpasswd $$dest/ 2>/dev/null || true; \
	cp ~/.cloudflared/*.json ~/.cloudflared/config.yml $$dest/ 2>/dev/null || true; \
	printf "$(GREEN)Đã sao lưu vào $$dest$(RESET)\n"; \
	printf "Gồm: cơ sở dữ liệu, cookie, .env, mật khẩu web, khoá tunnel.\n"; \
	printf "$(YELLOW)Chép thư mục này ra ổ khác. Mất khoá tunnel là mất tên miền.$(RESET)\n"

# ─── Rescue ────────────────────────────────────────────────────────────────

.PHONY: rebuild
rebuild:  ## Build lại từ đầu, bỏ qua cache (dùng khi start bị lỗi lạ)
	$(COMPOSE) build --no-cache
	@$(MAKE) --no-print-directory start

.PHONY: reset
reset:  ## Xoá container + image của dự án rồi build lại. Dữ liệu trong data/ giữ nguyên.
	-$(COMPOSE) down --rmi local --remove-orphans
	@$(MAKE) --no-print-directory start

.PHONY: clean-db
clean-db:  ## XOÁ TOÀN BỘ dữ liệu trong cơ sở dữ liệu. Cần thêm EXTRA=yes.
ifneq ($(EXTRA),yes)
	@printf "$(RED)Lệnh này xoá sạch lịch đăng, lịch sử và tài khoản đã lưu.$(RESET)\n"
	@printf "Chạy lại: make clean-db EXTRA=yes\n"
	@exit 1
endif
	rm -f data/tiktok.db
	rm -rf data/douyin_tmp
	$(COMPOSE) restart api scheduler
	@printf "$(GREEN)Đã tạo lại cơ sở dữ liệu rỗng.$(RESET)\n"

# ─── Networking ────────────────────────────────────────────────────────────
# cloudflared chạy trên host, ngoài Docker — xem RUNBOOK.md để tạo tunnel +
# trỏ domain lần đầu. Lệnh này chỉ khởi động lại tunnel đã cấu hình sẵn.

.PHONY: tunnel
tunnel:  ## Chạy cloudflared tunnel (đưa web ra domain public, cần cấu hình theo RUNBOOK.md trước)
	@command -v cloudflared >/dev/null || { \
		printf "$(RED)Chưa cài cloudflared$(RESET)\n"; \
		printf "Chạy: brew install cloudflared — xem RUNBOOK.md, bước Cài cloudflared\n"; \
		exit 1; }
	@test -f $$HOME/.cloudflared/config.yml || { \
		printf "$(RED)Chưa có ~/.cloudflared/config.yml$(RESET)\n"; \
		printf "Xem RUNBOOK.md, mục cấu hình tunnel, để tạo tunnel + trỏ domain trước.\n"; \
		exit 1; }
	cloudflared tunnel run

# ─── Development ───────────────────────────────────────────────────────────

.PHONY: dev
dev:  ## Chạy chế độ lập trình (Vite hot-reload, uvicorn --reload)
	$(DEV) up -d --remove-orphans
	@$(DEV) ps

.PHONY: test
test:  ## Chạy test trong .venv
	. .venv/bin/activate && python -m pytest -q

.PHONY: shell-api
shell-api:  ## Mở shell trong container api
	$(COMPOSE) exec api bash

.PHONY: shell-scheduler
shell-scheduler:  ## Mở shell trong container scheduler
	$(COMPOSE) exec scheduler bash

.PHONY: douyin-update
douyin-update:  ## Cập nhật douyin-downloader trong vendor/
	bash scripts/update-douyin-vendor.sh
	@printf "$(YELLOW)Chạy tiếp: make start$(RESET)\n"

.PHONY: tiktok-signature-update
tiktok-signature-update:  ## Cập nhật tiktok-signature (khi lỗi "Failed to generate signatures")
	bash scripts/update-tiktok-signature.sh
	@printf "$(YELLOW)Chạy tiếp: make start$(RESET)\n"

# ─── Self-doc ──────────────────────────────────────────────────────────────

EVERYDAY := start stop restart update logs status backup

.PHONY: help
help:  ## Danh sách lệnh thường dùng
	@printf "$(GREEN)farm_tiktik — lệnh thường dùng$(RESET)\n\n"
	@awk 'BEGIN { FS = ":.*?## "; split("$(EVERYDAY)", keep, " ") } \
		/^[a-zA-Z0-9_-]+:.*?## / { \
			for (i in keep) if ($$1 == keep[i]) printf "  $(GREEN)%-10s$(RESET) %s\n", $$1, $$2 \
		}' $(MAKEFILE_LIST)
	@printf "\n  Hướng dẫn đầy đủ: RUNBOOK.md\n"
	@printf "  Lệnh nâng cao:    make help-all\n"

.PHONY: help-all
help-all:  ## Danh sách toàn bộ lệnh
	@printf "$(GREEN)farm_tiktik — toàn bộ lệnh$(RESET)\n\n"
	@awk 'BEGIN { FS = ":.*?## " } \
		/^[a-zA-Z0-9_-]+:.*?## / { printf "  $(GREEN)%-24s$(RESET) %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)
