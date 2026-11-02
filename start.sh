#!/usr/bin/env bash
# =============================================================================
# Kaggle GPU-as-a-Service — Start Selected Services
# Usage: bash start.sh
# Reads choices from install.sh or uses defaults (all core services).
# =============================================================================
set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

WORKDIR="/kaggle/working/kaggle-comfyui-queue"
CHOICES_FILE="/kaggle/working/.kgpu_choices"

info() { echo -e "${BLUE}ℹ${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
err() { echo -e "${RED}✗${NC} $1"; }

cd "$WORKDIR" || { err "Workdir not found: $WORKDIR. Run install.sh first."; exit 1; }

echo -e "${CYAN}${BOLD}"
echo "╔═══════════════════════════════════════════════════╗"
echo "║ 🚀 Kaggle GPU-as-a-Service — Service Launcher ║"
echo "╚═══════════════════════════════════════════════════╝"
echo -e "${NC}"

# --- Load installation choices ---
INSTALL_COMFYUI=1
INSTALL_QUEUE_API=1
INSTALL_QUEUE_CONSUMER=1
INSTALL_WATCHDOG=1
INSTALL_NGROK=1
INSTALL_OLLAMA=0
MODE="single"
COMFYUI_PASS=""
NGROK_TOKEN=""

if [ -f "$CHOICES_FILE" ]; then
 # shellcheck disable=SC1090
 source "$CHOICES_FILE"
 info "Loaded choices from $CHOICES_FILE"
else
 warn "No choices file found. Starting all core services with defaults."
fi

# --- Load unified auth config ---
AUTH_ENV="$WORKDIR/config/auth.env"
if [ -f "$AUTH_ENV" ]; then
 # shellcheck disable=SC1090
 source "$AUTH_ENV"
fi

# Fallbacks
KGPU_USERNAME="${KGPU_USERNAME:-kaggle}"
KGPU_PASSWORD="${KGPU_PASSWORD:-KaggleGPU2026!SecureAccess}"
NGROK_AUTHTOKEN="${NGROK_AUTHTOKEN:-${NGROK_TOKEN:-1qb36HRiTApsnt5IVQ1bDWAbdk7_5snrnJJ8eBdxbZxhP625f}}"

export COMFYUI_AUTH_PASSWORD="$KGPU_PASSWORD"
export NGROK_AUTHTOKEN

if [ -z "$COMFYUI_AUTH_PASSWORD" ]; then
 err "KGPU_PASSWORD not set. Edit config/auth.env or run install.sh."
 exit 1
fi

if [ "$INSTALL_NGROK" -eq 1 ] && [ -z "$NGROK_AUTHTOKEN" ]; then
 err "NGROK_AUTHTOKEN not set. Edit config/auth.env or run install.sh."
 exit 1
fi

# --- Kill stale processes ---
info "Cleaning up stale processes..."
for port in 8188 8189; do
 fuser -k ${port}/tcp 2>/dev/null || true
done
pkill -f "ngrok http 8188" 2>/dev/null || true
pkill -f "queue_api.py" 2>/dev/null || true
pkill -f "queue_consumer.py" 2>/dev/null || true
pkill -f "watchdog.py" 2>/dev/null || true
pkill -f "ollama serve" 2>/dev/null || true
sleep 1
success "Stale processes cleaned."

# --- Start ComfyUI ---
if [ "$INSTALL_COMFYUI" -eq 1 ]; then
 COMFYUI_DIR="/kaggle/working/ComfyUI"
 if [ -d "$COMFYUI_DIR" ]; then
 info "Starting ComfyUI on :8188..."
 cd "$COMFYUI_DIR"
 nohup python3 main.py --listen 0.0.0.0 --port 8188 --multi-gpu \
 > /kaggle/working/comfyui.log 2>&1 &
 echo " PID: $!"
 cd "$WORKDIR"

 info "Waiting for ComfyUI ready..."
 for i in $(seq 1 60); do
 if curl -s http://127.0.0.1:8188/system_stats > /dev/null 2>&1; then
 success "ComfyUI ready on :8188"
 break
 fi
 sleep 2
 done
 else
 warn "ComfyUI directory not found. Skipping."
 fi
else
 info "ComfyUI skipped (not selected during install)."
fi

# --- Start Ollama ---
if [ "$INSTALL_OLLAMA" -eq 1 ]; then
 if command -v ollama &>/dev/null; then
 info "Starting Ollama server..."
 nohup ollama serve > /kaggle/working/ollama.log 2>&1 &
 echo " PID: $!"
 sleep 2
 success "Ollama started."
 else
 warn "Ollama not installed. Skipping."
 fi
else
 info "Ollama skipped (not selected during install)."
fi

# --- Start ngrok ---
if [ "$INSTALL_NGROK" -eq 1 ]; then
 info "Starting ngrok tunnel..."
 nohup python3 scripts/ngrok_launcher.py > /kaggle/working/ngrok.log 2>&1 &
 NGROK_PID=$!
 echo " PID: $NGROK_PID"

 info "Waiting for ngrok URL..."
 URL=""
 for i in $(seq 1 30); do
 # Primary: query ngrok local API (port 4040) dynamically
 API_URL=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null  | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((t['public_url'] for t in d.get('tunnels',[]) if t.get('public_url','').startswith('https://')),''))" 2>/dev/null || true)
 if [ -n "$API_URL" ]; then
 URL="$API_URL"
 echo "$URL" > /kaggle/working/ngrok_url.txt
 success "ngrok URL (from API): $URL"
 break
 fi
 # Fallback: read from file written by ngrok_launcher.py
 if [ -f /kaggle/working/ngrok_url.txt ] && [ -s /kaggle/working/ngrok_url.txt ]; then
 URL=$(cat /kaggle/working/ngrok_url.txt)
 success "ngrok URL (from file): $URL"
 break
 fi
 sleep 2
 done
 if [ -z "$URL" ]; then
 warn "Could not retrieve ngrok URL. Check /kaggle/working/ngrok.log"
 fi
else
 info "ngrok skipped (not selected during install)."
fi

# --- Start Queue API ---
if [ "$INSTALL_QUEUE_API" -eq 1 ]; then
 info "Starting Queue API on :8189..."
 nohup python3 scripts/queue_api.py > /kaggle/working/api.log 2>&1 &
 echo " PID: $!"
 success "Queue API started."
else
 info "Queue API skipped (not selected during install)."
fi

# --- Start Queue Consumer ---
if [ "$INSTALL_QUEUE_CONSUMER" -eq 1 ]; then
 info "Starting Queue Consumer..."
 nohup python3 scripts/queue_consumer.py > /kaggle/working/consumer.log 2>&1 &
 echo " PID: $!"
 success "Queue Consumer started."
else
 info "Queue Consumer skipped (not selected during install)."
fi

# --- Start Watchdog ---
if [ "$INSTALL_WATCHDOG" -eq 1 ]; then
 info "Starting Watchdog..."
 nohup python3 scripts/watchdog.py > /kaggle/working/watchdog_stdout.log 2>&1 &
 echo " PID: $!"
 success "Watchdog started."
else
 info "Watchdog skipped (not selected during install)."
fi

sleep 2

# --- Summary ---
echo ""
echo -e "${GREEN}${BOLD}=============================================="
echo " 🚀 ALL SELECTED SERVICES RUNNING"
echo -e "==============================================${NC}"
echo ""
echo " Mode: $MODE"
if [ -f /kaggle/working/ngrok_url.txt ] && [ "$INSTALL_NGROK" -eq 1 ]; then
 echo " 🌐 URL: $(cat /kaggle/working/ngrok_url.txt)"
fi
echo " 🔑 Auth: $KGPU_USERNAME / $KGPU_PASSWORD"
echo ""
echo -e " ${BOLD}Active services:${NC}"
[ "$INSTALL_COMFYUI" -eq 1 ] && echo " ✓ ComfyUI (:8188)"
[ "$INSTALL_OLLAMA" -eq 1 ] && echo " ✓ Ollama (LLM)"
[ "$INSTALL_NGROK" -eq 1 ] && echo " ✓ ngrok (HTTPS tunnel)"
[ "$INSTALL_QUEUE_API" -eq 1 ] && echo " ✓ Queue API (:8189)"
[ "$INSTALL_QUEUE_CONSUMER" -eq 1 ] && echo " ✓ Queue Consumer"
[ "$INSTALL_WATCHDOG" -eq 1 ] && echo " ✓ Watchdog"
echo ""
echo -e " ${BOLD}Logs:${NC}"
echo " ComfyUI: /kaggle/working/comfyui.log"
[ "$INSTALL_OLLAMA" -eq 1 ] && echo " Ollama: /kaggle/working/ollama.log"
echo " ngrok: /kaggle/working/ngrok.log"
echo " API: /kaggle/working/api.log"
echo " Consumer: /kaggle/working/consumer.log"
echo " Watchdog: /kaggle/working/watchdog_stdout.log"
echo ""
if [ "$INSTALL_NGROK" -eq 1 ] && [ -f /kaggle/working/ngrok_url.txt ]; then
 echo " Test: curl -u kaggle:$COMFYUI_AUTH_PASSWORD $(cat /kaggle/working/ngrok_url.txt)/api/health"
fi
echo ""
echo -e "${GREEN}${BOLD}==============================================${NC}"