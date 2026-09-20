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

.PHONY: start-bg
start-bg: start  ## Bật hệ thống + cloudflared (dùng sau mỗi lần bật WSL)
	@systemctl is-active --quiet cloudflared || sudo systemctl start cloudflared
	@printf "$(GREEN)cloudflared: $$(systemctl is-active cloudflared)$(RESET)\n"

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
# cloudflared chạy trên host, ngoài Docker. tunnel-create làm 1 lần đầu (login
# → create → route → ghi config.yml); tunnel-start/-stop chạy tunnel nền hằng
# ngày dựa trên config.yml đó. Xem RUNBOOK.md, Bước 5.
#
# "tunnel route dns" đọc ~/.cloudflared/config.yml trước khi xét tham số, nên
# trên máy đã có tunnel nó trỏ tên miền vào tunnel CŨ mà vẫn báo thành công.
# Vì vậy route chạy với một config tạm chỉ chứa UUID vừa tạo, và truyền UUID
# thay vì tên tunnel.

.PHONY: tunnel-create
tunnel-create:  ## Tạo tunnel mới + trỏ domain lần đầu (NAME=farm1 DOMAIN=farm1.tenmien.com make tunnel-create)
	@test -n "$(NAME)" || { \
		printf "$(RED)Thiếu NAME$(RESET)\n"; \
		printf "Chạy: NAME=farm1 DOMAIN=farm1.tenmien.com make tunnel-create\n"; \
		exit 1; }
	@test -n "$(DOMAIN)" || { \
		printf "$(RED)Thiếu DOMAIN$(RESET)\n"; \
		printf "Chạy: NAME=farm1 DOMAIN=farm1.tenmien.com make tunnel-create\n"; \
		exit 1; }
	@command -v cloudflared >/dev/null || { \
		printf "$(RED)Chưa cài cloudflared$(RESET)\n"; \
		printf "Chạy: brew install cloudflared — xem RUNBOOK.md, bước Cài cloudflared\n"; \
		exit 1; }
	@test ! -f $$HOME/.cloudflared/config.yml || { \
		printf "$(RED)Máy này đã có tunnel: ~/.cloudflared/config.yml$(RESET)\n"; \
		printf "Mỗi máy chỉ chạy 1 tunnel. Tạo thêm sẽ ghi đè cấu hình đang dùng.\n"; \
		printf "Muốn làm lại từ đầu: đổi tên file đó rồi chạy lại lệnh này.\n"; \
		exit 1; }
	cloudflared tunnel login
	@out=$$(cloudflared tunnel create $(NAME) 2>&1); \
	echo "$$out"; \
	cred=$$(echo "$$out" | grep -oE '/[^ ]*\.cloudflared/[a-f0-9-]+\.json' | head -1); \
	test -n "$$cred" || { \
		printf "$(RED)Không đọc được đường dẫn credentials từ output của 'cloudflared tunnel create'$(RESET)\n"; \
		exit 1; }; \
	uuid=$$(basename $$cred .json); \
	tmpcfg=$$(mktemp); \
	printf "tunnel: %s\n" "$$uuid" > $$tmpcfg; \
	cloudflared --config $$tmpcfg tunnel route dns $$uuid $(DOMAIN); \
	rc=$$?; rm -f $$tmpcfg; test $$rc -eq 0 || exit $$rc; \
	printf "tunnel: %s\ncredentials-file: %s\n\ningress:\n  - hostname: %s\n    service: http://127.0.0.1:2705\n  - service: http_status:404\n" \
		"$$uuid" "$$cred" "$(DOMAIN)" > $$HOME/.cloudflared/config.yml; \
	printf "$(GREEN)Đã tạo tunnel $(NAME), trỏ $(DOMAIN), ghi ~/.cloudflared/config.yml$(RESET)\n"; \
	printf "Chạy tiếp: make tunnel-start\n"

.PHONY: tunnel-start
tunnel-start:  ## Chạy cloudflared tunnel nền (đưa web ra domain public, cần make tunnel-create trước)
	@command -v cloudflared >/dev/null || { \
		printf "$(RED)Chưa cài cloudflared$(RESET)\n"; \
		printf "Chạy: brew install cloudflared — xem RUNBOOK.md, bước Cài cloudflared\n"; \
		exit 1; }
	@test -f $$HOME/.cloudflared/config.yml || { \
		printf "$(RED)Chưa có ~/.cloudflared/config.yml$(RESET)\n"; \
		printf "Chạy: make tunnel-create NAME=... DOMAIN=... để tạo tunnel + trỏ domain trước.\n"; \
		exit 1; }
	@if test -f tunnel.pid && kill -0 $$(cat tunnel.pid) 2>/dev/null; then \
		printf "$(RED)Tunnel đang chạy (PID $$(cat tunnel.pid))$(RESET)\n"; \
		printf "Chạy: make tunnel-stop trước\n"; \
		exit 1; \
	fi
	@nohup cloudflared tunnel run > tunnel.log 2>&1 & echo $$! > tunnel.pid
	@printf "$(GREEN)Tunnel đang chạy nền (PID $$(cat tunnel.pid)), log: tunnel.log$(RESET)\n"

.PHONY: tunnel-stop
tunnel-stop:  ## Dừng tunnel đang chạy nền
	@if test -f tunnel.pid; then \
		kill $$(cat tunnel.pid) 2>/dev/null || true; \
		rm -f tunnel.pid; \
		printf "$(GREEN)Đã dừng tunnel$(RESET)\n"; \
	else \
		printf "$(YELLOW)Không thấy tunnel đang chạy (thiếu tunnel.pid)$(RESET)\n"; \
	fi

.PHONY: tunnel-list
tunnel-list:  ## Xem các tunnel đã tạo trên tài khoản Cloudflare này
	@command -v cloudflared >/dev/null || { \
		printf "$(RED)Chưa cài cloudflared$(RESET)\n"; \
		exit 1; }
	cloudflared tunnel list

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

EVERYDAY := start start-bg stop restart update logs status backup

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
