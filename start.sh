#!/usr/bin/env bash
# =============================================================================
# Kaggle GPU-as-a-Service — Start All Services
# Usage: bash start.sh
# =============================================================================
set -euo pipefail

WORKDIR="/kaggle/working/kaggle-comfyui-queue"
cd "$WORKDIR"

echo "🚀 Starting Kaggle GPU-as-a-Service..."

# --- Secret checks ---
if [ -z "${COMFYUI_AUTH_PASSWORD:-}" ]; then
 echo "❌ COMFYUI_AUTH_PASSWORD not set. Add it via Kaggle Secrets."
 exit 1
fi
if [ -z "${NGROK_AUTHTOKEN:-}" ]; then
 echo "❌ NGROK_AUTHTOKEN not set. Add it via Kaggle Secrets."
 exit 1
fi

# --- Kill stale processes ---
for port in 8188 8189; do
 fuser -k ${port}/tcp 2>/dev/null || true
done
pkill -f "ngrok http 8188" 2>/dev/null || true
pkill -f "queue_api.py" 2>/dev/null || true
pkill -f "queue_consumer.py" 2>/dev/null || true
pkill -f "watchdog.py" 2>/dev/null || true
sleep 1

# --- Start ComfyUI (background) ---
echo "🎨 Starting ComfyUI on :8188..."
cd /kaggle/working/ComfyUI
nohup python3 main.py --listen 0.0.0.0 --port 8188 --multi-gpu \
 > /kaggle/working/comfyui.log 2>&1 &
echo "   PID: $!"
cd "$WORKDIR"

# --- Wait for ComfyUI ready ---
echo "⏳ Waiting for ComfyUI..."
for i in $(seq 1 60); do
 if curl -s http://127.0.0.1:8188/system_stats > /dev/null 2>&1; then
 echo "✅ ComfyUI ready."
 break
 fi
 sleep 2
done

# --- Start ngrok (foreground-ish, backgrounded) ---
echo "🌐 Starting ngrok tunnel..."
nohup python3 scripts/ngrok_launcher.py > /kaggle/working/ngrok.log 2>&1 &
NGROK_PID=$!
echo "   PID: $NGROK_PID"

# --- Wait for ngrok URL ---
echo "⏳ Waiting for ngrok URL..."
for i in $(seq 1 30); do
 if [ -f /kaggle/working/ngrok_url.txt ] && [ -s /kaggle/working/ngrok_url.txt ]; then
 URL=$(cat /kaggle/working/ngrok_url.txt)
 echo "✅ ngrok URL: $URL"
 break
 fi
 sleep 2
done

# --- Start Queue API ---
echo "📡 Starting Queue API on :8189..."
nohup python3 scripts/queue_api.py > /kaggle/working/api.log 2>&1 &
echo "   PID: $!"

# --- Start Queue Consumer ---
echo "⚙️  Starting Queue Consumer..."
nohup python3 scripts/queue_consumer.py > /kaggle/working/consumer.log 2>&1 &
echo "   PID: $!"

# --- Start Watchdog ---
echo "👁️  Starting Watchdog..."
nohup python3 scripts/watchdog.py > /kaggle/working/watchdog_stdout.log 2>&1 &
echo "   PID: $!"

sleep 2

echo ""
echo "=============================================="
echo "🚀 ALL SERVICES RUNNING"
echo "=============================================="
if [ -f /kaggle/working/ngrok_url.txt ]; then
 echo "🌐 URL: $(cat /kaggle/working/ngrok_url.txt)"
fi
echo "🔑 Auth: kaggle / [your COMFYUI_AUTH_PASSWORD]"
echo ""
echo "Logs:"
echo " ComfyUI: /kaggle/working/comfyui.log"
echo " ngrok: /kaggle/working/ngrok.log"
echo " API: /kaggle/working/api.log"
echo " Consumer: /kaggle/working/consumer.log"
echo " Watchdog: /kaggle/working/watchdog_stdout.log"
echo ""
echo "Test: curl -u kaggle:PASS https://YOUR-URL.ngrok-free.app/api/health"
echo "=============================================="