#!/usr/bin/env bash
# ============================================================
# AI Agent Team — Single-Source Deployment Script
# Usage:
#   ./deploy.sh              → full stack (Docker only)
#   ./deploy.sh --with-openclaw  → Docker stack + OpenClaw local dev
#   ./deploy.sh --stop       → stop all services
#   ./deploy.sh --restart    → stop + start
#   ./deploy.sh --logs       → tail all logs
#   ./deploy.sh --status     → health check all services
#   ./deploy.sh --clean      → stop + remove volumes (full reset)
# ============================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[deploy]${RESET} $*"; }
ok()   { echo -e "${GREEN}[  ok  ]${RESET} $*"; }
warn() { echo -e "${YELLOW}[ warn ]${RESET} $*"; }
err()  { echo -e "${RED}[ fail ]${RESET} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WITH_OPENCLAW=false
MODE="up"

# ── Parse arguments ───────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --with-openclaw) WITH_OPENCLAW=true ;;
    --stop)          MODE="stop" ;;
    --restart)       MODE="restart" ;;
    --logs)          MODE="logs" ;;
    --status)        MODE="status" ;;
    --clean)         MODE="clean" ;;
    --help|-h)
      echo -e "${BOLD}Usage:${RESET} ./deploy.sh [OPTIONS]"
      echo ""
      echo "  (no args)          Start full Docker stack"
      echo "  --with-openclaw    Also start OpenClaw edge gateway (Cloudflare Worker local dev)"
      echo "  --stop             Stop all services"
      echo "  --restart          Restart all services"
      echo "  --logs             Tail logs from all services"
      echo "  --status           Health check all services"
      echo "  --clean            Full reset — stops services and removes all volumes"
      exit 0
      ;;
  esac
done

# ════════════════════════════════════════════════════════════
# MODE: stop
# ════════════════════════════════════════════════════════════
if [[ "$MODE" == "stop" ]]; then
  log "Stopping all services..."
  cd "$SCRIPT_DIR"
  docker compose down
  ok "All services stopped."
  exit 0
fi

# ════════════════════════════════════════════════════════════
# MODE: clean (full reset)
# ════════════════════════════════════════════════════════════
if [[ "$MODE" == "clean" ]]; then
  warn "This will DELETE all volumes (Redis data, Postgres data, reports, sandbox)."
  read -rp "Are you sure? (yes/no): " confirm
  [[ "$confirm" == "yes" ]] || { log "Aborted."; exit 0; }
  cd "$SCRIPT_DIR"
  docker compose down -v --remove-orphans
  ok "Full clean complete — all volumes removed."
  exit 0
fi

# ════════════════════════════════════════════════════════════
# MODE: logs
# ════════════════════════════════════════════════════════════
if [[ "$MODE" == "logs" ]]; then
  cd "$SCRIPT_DIR"
  docker compose logs -f --tail=50
  exit 0
fi

# ════════════════════════════════════════════════════════════
# MODE: status
# ════════════════════════════════════════════════════════════
if [[ "$MODE" == "status" ]]; then
  log "Checking service health..."
  echo ""
  SERVICES=(
    "gateway:8000:/health"
    "manager:8001:/health"
    "developer:8002:/health"
    "devops:8003:/health"
    "qa:8004:/health"
    "support:8005:/health"
    "docs:8006:/health"
    "design:8007:/health"
    "ux:8008:/health"
    "ui_test:8009:/health"
    "api_test:8010:/health"
    "qa_auto:8011:/health"
    "security:8012:/health"
    "dashboard:3000:/"
  )
  ALL_OK=true
  for svc in "${SERVICES[@]}"; do
    name=$(echo "$svc" | cut -d: -f1)
    port=$(echo "$svc" | cut -d: -f2)
    path=$(echo "$svc" | cut -d: -f3)
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://localhost:${port}${path}" 2>/dev/null || echo "000")
    if [[ "$status" == "200" ]]; then
      ok "  ${name} :${port} → HTTP ${status}"
    else
      echo -e "${RED}[ fail ]${RESET}   ${name} :${port} → HTTP ${status} (not reachable)"
      ALL_OK=false
    fi
  done
  echo ""
  $ALL_OK && ok "All services healthy." || warn "Some services are not reachable. Run './deploy.sh --logs' to investigate."
  exit 0
fi

# ════════════════════════════════════════════════════════════
# MODE: restart
# ════════════════════════════════════════════════════════════
if [[ "$MODE" == "restart" ]]; then
  log "Restarting all services..."
  cd "$SCRIPT_DIR"
  docker compose down
  MODE="up"   # fall through to startup
fi

# ════════════════════════════════════════════════════════════
# MODE: up — full startup
# ════════════════════════════════════════════════════════════

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║     AI Agent Team — Deployment Script    ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""

cd "$SCRIPT_DIR"

# ── Step 1: Check prerequisites ───────────────────────────────
log "Step 1/5 — Checking prerequisites..."

command -v docker >/dev/null 2>&1 || err "Docker is not installed. Install from https://docs.docker.com/get-docker/"
ok "Docker found: $(docker --version)"

# Support both 'docker compose' (v2) and 'docker-compose' (v1)
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  err "Docker Compose not found. Install from https://docs.docker.com/compose/install/"
fi
ok "Docker Compose found."

if [[ "$WITH_OPENCLAW" == "true" ]]; then
  command -v node >/dev/null 2>&1 || err "Node.js is required for OpenClaw. Install from https://nodejs.org"
  command -v npm  >/dev/null 2>&1 || err "npm is required for OpenClaw."
  ok "Node.js found: $(node --version)"
fi

# ── Step 2: Environment setup ─────────────────────────────────
log "Step 2/5 — Environment setup..."

if [[ ! -f ".env" ]]; then
  if [[ -f ".env.example" ]]; then
    cp .env.example .env
    warn ".env file not found — copied from .env.example."
    warn "Please fill in your API keys in .env before deploying."
    echo ""
    echo -e "${BOLD}Required keys to fill in:${RESET}"
    echo "  OPENROUTER_API_KEY  → https://openrouter.ai  (free tier available)"
    echo "  GROQ_API_KEY        → https://console.groq.com  (free tier available)"
    echo "  GEMINI_API_KEY      → https://aistudio.google.com  (free tier available)"
    echo "  API_SECRET_KEY      → run: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    echo ""
    read -rp "Do you want to open .env now to fill in your keys? (yes/no): " open_env
    if [[ "$open_env" == "yes" ]]; then
      ${EDITOR:-nano} .env
    else
      warn "Skipping .env edit. The stack will start but agents won't work without API keys."
    fi
  else
    err ".env.example not found. Cannot create .env."
  fi
else
  ok ".env file found."
fi

# Warn if placeholder keys are still present
MISSING_KEYS=()
for key in OPENROUTER_API_KEY GROQ_API_KEY GEMINI_API_KEY API_SECRET_KEY; do
  val=$(grep "^${key}=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
  if [[ -z "$val" || "$val" == *"REPLACE_ME"* || "$val" == *"REPLACE_WITH"* ]]; then
    MISSING_KEYS+=("$key")
  fi
done

if [[ ${#MISSING_KEYS[@]} -gt 0 ]]; then
  warn "The following keys still have placeholder values in .env:"
  for k in "${MISSING_KEYS[@]}"; do echo "    → $k"; done
  warn "Services will start but LLM calls will fail until these are set."
  echo ""
fi

# ── Step 3: Fix dashboard API URL if needed ───────────────────
log "Step 3/5 — Checking dashboard configuration..."

# Detect if running on a remote server (non-localhost)
DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

if [[ "$DETECTED_IP" != "127.0.0.1" && "$DETECTED_IP" != "localhost" && -n "$DETECTED_IP" ]]; then
  # Check if dashboard still has localhost hardcoded
  if grep -q "const API_URL = 'http://localhost:8000'" dashboard/index.html 2>/dev/null; then
    warn "Dashboard is hardcoded to localhost:8000 but server IP is ${DETECTED_IP}."
    echo ""
    read -rp "Update dashboard to use http://${DETECTED_IP}:8000 ? (yes/no): " fix_url
    if [[ "$fix_url" == "yes" ]]; then
      sed -i "s|const API_URL = 'http://localhost:8000'|const API_URL = 'http://${DETECTED_IP}:8000'|g" dashboard/index.html
      sed -i "s|fetch('http://localhost:8000|fetch('http://${DETECTED_IP}:8000|g" dashboard/index.html
      ok "Dashboard API URL updated to http://${DETECTED_IP}:8000"
    else
      warn "Dashboard URL not updated. Use SSH tunnel to access UI: ssh -L 3000:localhost:3000 -L 8000:localhost:8000 user@${DETECTED_IP}"
    fi
  else
    ok "Dashboard API URL already configured."
  fi
else
  ok "Running locally — dashboard will connect to localhost:8000."
fi

# ── Step 4: Build & start Docker stack ────────────────────────
log "Step 4/5 — Building and starting Docker stack..."
echo ""

$COMPOSE up --build -d

echo ""
log "Waiting for infrastructure (Redis + Postgres) to be healthy..."

MAX_WAIT=60
ELAPSED=0
while [[ $ELAPSED -lt $MAX_WAIT ]]; do
  REDIS_OK=$($COMPOSE ps redis 2>/dev/null | grep -c "healthy" || echo 0)
  POSTGRES_OK=$($COMPOSE ps postgres 2>/dev/null | grep -c "healthy" || echo 0)
  if [[ "$REDIS_OK" -ge 1 && "$POSTGRES_OK" -ge 1 ]]; then
    ok "Redis and Postgres are healthy."
    break
  fi
  echo -n "."
  sleep 3
  ELAPSED=$((ELAPSED + 3))
done

if [[ $ELAPSED -ge $MAX_WAIT ]]; then
  warn "Timeout waiting for infrastructure. Check logs: ./deploy.sh --logs"
fi

# ── Step 5: OpenClaw (optional) ────────────────────────────────
if [[ "$WITH_OPENCLAW" == "true" ]]; then
  log "Step 5/5 — Setting up OpenClaw edge gateway..."

  cd openclaw

  if [[ ! -f ".env" ]]; then
    cp .env.example .env
    warn "openclaw/.env created from template. Fill in Twilio/Mailgun/Teams credentials."
  else
    ok "openclaw/.env found."
  fi

  log "Installing npm dependencies..."
  npm install --silent

  ok "OpenClaw installed. Starting local dev server..."
  echo ""
  warn "OpenClaw will run in the foreground. Press Ctrl+C to stop."
  echo ""
  npm run dev
  cd "$SCRIPT_DIR"
else
  log "Step 5/5 — OpenClaw skipped (use --with-openclaw to enable)."
fi

# ── Summary ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║            Deployment Complete ✓                 ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "${BOLD}Services running:${RESET}"
echo -e "  Dashboard       →  ${CYAN}http://localhost:3000${RESET}"
echo -e "  API Gateway     →  ${CYAN}http://localhost:8000${RESET}"
echo -e "  API Docs        →  ${CYAN}http://localhost:8000/docs${RESET}   (Swagger UI)"
echo ""
echo -e "${BOLD}Agent endpoints:${RESET}"
echo -e "  Manager   :8001   Developer :8002   DevOps    :8003"
echo -e "  QA        :8004   Support   :8005   Docs      :8006"
echo -e "  Design    :8007   UX        :8008   UI Test   :8009"
echo -e "  API Test  :8010   QA Auto   :8011   Security  :8012"
echo ""
echo -e "${BOLD}Quick commands:${RESET}"
echo -e "  ./deploy.sh --status    → health check all services"
echo -e "  ./deploy.sh --logs      → tail logs"
echo -e "  ./deploy.sh --stop      → stop everything"
echo -e "  ./deploy.sh --restart   → restart everything"
echo -e "  ./deploy.sh --clean     → full reset (deletes all data)"
echo ""
echo -e "${BOLD}Test it:${RESET}"
echo -e "  ${CYAN}curl http://localhost:8000/health${RESET}"
echo -e "  ${CYAN}curl -X POST http://localhost:8000/goal -H 'Content-Type: application/json' -d '{\"goal\": \"Hello team\"}'${RESET}"
echo ""
