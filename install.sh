#!/usr/bin/env bash
# =============================================================================
# Kaggle GPU-as-a-Service — One-Command Installer
# Run this inside a Kaggle notebook cell:  !bash install.sh
# Or from terminal:                        bash install.sh
# =============================================================================
set -euo pipefail

REPO="https://github.com/osumtr-web/kaggle-comfyui-queue.git"
WORKDIR="/kaggle/working/kaggle-comfyui-queue"
FLAG="/kaggle/working/.kgpu_installed"

echo "🔧 Kaggle GPU-as-a-Service Installer"
echo "======================================"

# --- Idempotency gate ---
if [ -f "$FLAG" ]; then
  echo "✅ Already installed. Delete $FLAG to reinstall."
  echo ""
  echo "To start services, run:"
  echo "  cd $WORKDIR && python3 scripts/ngrok_launcher.py &"
  echo "  cd $WORKDIR && python3 scripts/queue_api.py &"
  echo "  cd $WORKDIR && python3 scripts/queue_consumer.py &"
  echo "  cd $WORKDIR && python3 scripts/watchdog.py &"
  exit 0
fi

# --- Secret checks ---
if [ -z "${NGROK_AUTHTOKEN:-}" ]; then
  echo "❌ NGROK_AUTHTOKEN not set."
  echo "   Kaggle → Add-ons → Secrets → add NGROK_AUTHTOKEN"
  exit 1
fi
if [ -z "${COMFYUI_AUTH_PASSWORD:-}" ]; then
  echo "❌ COMFYUI_AUTH_PASSWORD not set."
  echo "   Kaggle → Add-ons → Secrets → add COMFYUI_AUTH_PASSWORD"
  exit 1
fi
echo "✅ Secrets verified."

# --- Clone or update repo ---
if [ -d "$WORKDIR/.git" ]; then
  echo "📥 Updating existing repo..."
  git -C "$WORKDIR" pull --ff-only
else
  echo "📥 Cloning repo..."
  rm -rf "$WORKDIR"
  git clone "$REPO" "$WORKDIR"
fi

cd "$WORKDIR"

# --- System deps ---
echo "📦 Installing system packages..."
apt-get update -qq && apt-get install -y -qq libgl1 libglib2.0-0 ffmpeg wget curl > /dev/null 2>&1

# --- Python deps ---
echo "🐍 Installing Python dependencies..."
pip install -q fastapi==0.115.4 uvicorn==0.32.0 aiosqlite==0.20.0 \
  pydantic==2.9.2 huggingface_hub>=0.26.0 psutil==6.1.0 httpx==0.27.2

# --- ComfyUI ---
COMFYUI_DIR="/kaggle/working/ComfyUI"
if [ ! -d "$COMFYUI_DIR" ]; then
  echo "🎨 Installing ComfyUI..."
  git clone https://github.com/comfyanonymous/ComfyUI.git "$COMFYUI_DIR"
  cd "$COMFYUI_DIR"
  git checkout ab2c3d4e5f6 2>/dev/null || true
  pip install -q -r requirements.txt
  cd "$WORKDIR"
else
  echo "🎨 ComfyUI already installed."
fi

# --- Model directories ---
echo "📁 Creating model directories..."
mkdir -p /kaggle/working/models/{checkpoints,loras,unet,vae,clip,llm,ml,audio,embeddings}
mkdir -p /kaggle/working/output
mkdir -p /kaggle/working/workflows

# --- Copy configs & workflows ---
cp -r "$WORKDIR/configs/"* /kaggle/working/configs/ 2>/dev/null || mkdir -p /kaggle/working/configs && cp -r "$WORKDIR/configs/"* /kaggle/working/configs/
cp -r "$WORKDIR/workflows/"* /kaggle/working/workflows/ 2>/dev/null || true

# --- ngrok ---
if ! command -v ngrok &>/dev/null; then
  echo "🌐 Installing ngrok..."
  curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok-v3-stable-linux-amd64.tgz | tar -xz -C /usr/local/bin
fi

# --- Mark complete ---
touch "$FLAG"

echo ""
echo "=============================================="
echo "✅ INSTALLATION COMPLETE"
echo "=============================================="
echo ""
echo "Start all services with ONE command:"
echo ""
echo "  cd $WORKDIR && bash start.sh"
echo ""
echo "Or start individually:"
echo "  python3 scripts/ngrok_launcher.py &"
echo "  python3 scripts/queue_api.py &"
echo "  python3 scripts/queue_consumer.py &"
echo "  python3 scripts/watchdog.py &"
echo ""
echo "Your ngrok URL will appear in ngrok_launcher output."
echo "Auth: kaggle / [your COMFYUI_AUTH_PASSWORD]"
echo "=============================================="