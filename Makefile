# ============================================================
# AI Agent Team — Makefile
# Wraps deploy.sh for convenience
# Usage: make <target>
# ============================================================

.PHONY: help up down restart logs status clean openclaw build shell-gateway shell-redis

# Default target
help:
	@echo ""
	@echo "  AI Agent Team — Available Commands"
	@echo "  ═══════════════════════════════════"
	@echo "  make up            Start full stack (build + launch)"
	@echo "  make down          Stop all services"
	@echo "  make restart       Restart all services"
	@echo "  make logs          Tail logs from all services"
	@echo "  make status        Health check all services"
	@echo "  make clean         Full reset (removes all volumes + data)"
	@echo "  make openclaw      Start stack + OpenClaw edge gateway"
	@echo ""
	@echo "  make build         Rebuild Docker images without starting"
	@echo "  make shell-gateway Open a shell in the gateway container"
	@echo "  make shell-redis   Open a Redis CLI session"
	@echo "  make ps            Show running containers"
	@echo ""

## ── Core lifecycle ───────────────────────────────────────────

up:
	@chmod +x deploy.sh && ./deploy.sh

down:
	@chmod +x deploy.sh && ./deploy.sh --stop

restart:
	@chmod +x deploy.sh && ./deploy.sh --restart

logs:
	@docker compose logs -f --tail=50

status:
	@chmod +x deploy.sh && ./deploy.sh --status

clean:
	@chmod +x deploy.sh && ./deploy.sh --clean

openclaw:
	@chmod +x deploy.sh && ./deploy.sh --with-openclaw

## ── Docker helpers ───────────────────────────────────────────

build:
	@docker compose build

ps:
	@docker compose ps

shell-gateway:
	@docker compose exec gateway /bin/bash

shell-redis:
	@docker compose exec redis redis-cli

## ── API shortcuts ────────────────────────────────────────────

health:
	@curl -s http://localhost:8000/health | python3 -m json.tool

goal:
	@read -p "Goal: " g; \
	curl -s -X POST http://localhost:8000/goal \
	  -H "Content-Type: application/json" \
	  -d "{\"goal\": \"$$g\"}" | python3 -m json.tool

tasks:
	@curl -s http://localhost:8000/tasks | python3 -m json.tool

tickets:
	@curl -s http://localhost:8000/tickets | python3 -m json.tool
