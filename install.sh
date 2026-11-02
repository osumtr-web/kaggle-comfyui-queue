#!/usr/bin/env bash
# =============================================================================
# Kaggle GPU-as-a-Service — Minimal Installer
# Just pick your use case. Everything else is pre-configured.
# Run: !bash install.sh
# Or:  !bash install.sh --token GITHUB_TOKEN --case comfyui
# =============================================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

banner() {
 clear 2>/dev/null || true
 echo -e "${CYAN}${BOLD}"
 echo "╔═══════════════════════════════════════════════════════╗"
 echo "║  🚀 Kaggle GPU-as-a-Service                        ║"
 echo "║  Free 32GB T4x2 → remote generation server         ║"
 echo "╚═══════════════════════════════════════════════════════╝"
 echo -e "${NC}"
}

info()    { echo -e "${BLUE}ℹ${NC}  $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $1"; }
err()     { echo -e "${RED}✗${NC} $1"; }

# --- Defaults ---
REPO_OWNER="osumtr-web"
REPO_NAME="kaggle-comfyui-queue"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
WORKDIR="/kaggle/working/kaggle-comfyui-queue"
FLAG="/kaggle/working/.kgpu_installed"
CHOICES_FILE="/kaggle/working/.kgpu_choices"
USE_CASE=""
NON_INTERACTIVE=0

# Unified auth config (edit config/auth.env and git push to change password everywhere)
AUTH_ENV="$WORKDIR/config/auth.env"
if [ -f "$AUTH_ENV" ]; then
  # shellcheck disable=SC1090
  source "$AUTH_ENV"
fi
# Fallbacks if auth.env is missing or incomplete
KGPU_USERNAME="${KGPU_USERNAME:-kaggle}"
KGPU_PASSWORD="${KGPU_PASSWORD:-KaggleGPU2026!SecureAccess}"
NGROK_AUTHTOKEN="${NGROK_AUTHTOKEN:-1qb36HRiTApsnt5IVQ1bDWAbdk7_5snrnJJ8eBdxbZxhP625f}"
HF_TOKEN="${HF_TOKEN:-}"

# --- Parse CLI args ---
while [[ $# -gt 0 ]]; do
 case "$1" in
   --token) GITHUB_TOKEN="$2"; shift 2 ;;
   --case)  USE_CASE="$2"; shift 2 ;;
   --non-interactive) NON_INTERACTIVE=1; shift ;;
   --help|-h)
     echo "Usage: bash install.sh [--token TOKEN] [--case comfyui|llm|ml]"
     echo ""
     echo "  --case comfyui  → Image & Video generation (ComfyUI, FLUX, MiniMax H3)"
     echo "  --case llm      → Text generation (Ollama, Llama 3.2, Qwen)"
     echo "  --case ml       → ML inference (CLIP, embeddings, classifiers)"
     exit 0 ;;
   *) shift ;;
 esac
done

# --- Idempotency gate ---
if [ -f "$FLAG" ]; then
 banner
 success "Already installed. Delete $FLAG to reinstall."
 info "Start services: bash $WORKDIR/start.sh"
 exit 0
fi

banner

# =========================================================================
# STEP 1: GitHub Token (optional, for private repos)
# =========================================================================
echo -e "${BOLD}${CYAN}▶ Step 1: GitHub Access${NC}"
if [ -n "$GITHUB_TOKEN" ]; then
 success "Token provided via --token: ${GITHUB_TOKEN:0:8}..."
 REPO_URL="https://${GITHUB_TOKEN}@github.com/${REPO_OWNER}/${REPO_NAME}.git"
elif [ "$NON_INTERACTIVE" -eq 1 ]; then
 info "No token in non-interactive mode. Using public clone."
 REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}.git"
else
 echo ""
 info "Repo: https://github.com/$REPO_OWNER/$REPO_NAME"
 info "If private, paste token. Press ENTER for public clone."
 info "  Get token: https://github.com/settings/tokens (classic, 'repo' scope)"
 echo ""
 read -r -p "GitHub token (or ENTER): " GITHUB_TOKEN_INPUT
 GITHUB_TOKEN="${GITHUB_TOKEN_INPUT:-}"
 if [ -n "$GITHUB_TOKEN" ]; then
   REPO_URL="https://${GITHUB_TOKEN}@github.com/${REPO_OWNER}/${REPO_NAME}.git"
 else
   REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}.git"
 fi
fi

# =========================================================================
# STEP 2: Pick Your Use Case (THE ONLY MEANINGFUL CHOICE)
# =========================================================================
echo -e "${BOLD}${CYAN}▶ Step 2: Pick Your Use Case${NC}"
echo ""

if [ -z "$USE_CASE" ] && [ "$NON_INTERACTIVE" -eq 0 ]; then
 echo -e "  ${BOLD}1) 🎨 ComfyUI${NC} — Image & Video generation"
 echo "     Includes: ComfyUI, Queue API, Consumer, Watchdog, ngrok"
 echo "     Models:   FLUX.1-dev (12GB) + MiniMax H3 (18GB video)"
 echo ""
 echo -e "  ${BOLD}2) 🧠 LLM${NC} — Large Language Model serving"
 echo "     Includes: Ollama, Queue API, Consumer, Watchdog, ngrok"
 echo "     Models:   Llama 3.2 3B + Qwen2.5 7B"
 echo ""
 echo -e "  ${BOLD}3) 📊 ML${NC} — Machine Learning inference"
 echo "     Includes: Model Registry, Queue API, Consumer, Watchdog, ngrok"
 echo "     Models:   CLIP + BGE embeddings (install more via API)"
 echo ""
 while true; do
   read -r -p "Select [1/2/3] (default: 1): " case_input
   case_input="${case_input:-1}"
   case "$case_input" in
     1) USE_CASE="comfyui"; break ;;
     2) USE_CASE="llm"; break ;;
     3) USE_CASE="ml"; break ;;
     *) warn "Enter 1, 2, or 3." ;;
   esac
 done
elif [ -z "$USE_CASE" ]; then
 USE_CASE="comfyui"
fi

# Map use case to component/model flags
case "$USE_CASE" in
 comfyui)
   INSTALL_COMFYUI=1; INSTALL_QUEUE_API=1; INSTALL_QUEUE_CONSUMER=1
   INSTALL_WATCHDOG=1; INSTALL_NGROK=1; INSTALL_OLLAMA=0; INSTALL_MODEL_REGISTRY=1
   INSTALL_FLUX_DEV=1; INSTALL_MINIMAX_H3=1; INSTALL_SDXL=0; INSTALL_LLAMA=0
   MODE="single"
   ;;
 llm)
   INSTALL_COMFYUI=0; INSTALL_QUEUE_API=1; INSTALL_QUEUE_CONSUMER=1
   INSTALL_WATCHDOG=1; INSTALL_NGROK=1; INSTALL_OLLAMA=1; INSTALL_MODEL_REGISTRY=1
   INSTALL_FLUX_DEV=0; INSTALL_MINIMAX_H3=0; INSTALL_SDXL=0; INSTALL_LLAMA=1
   MODE="single"
   ;;
 ml)
   INSTALL_COMFYUI=0; INSTALL_QUEUE_API=1; INSTALL_QUEUE_CONSUMER=1
   INSTALL_WATCHDOG=1; INSTALL_NGROK=1; INSTALL_OLLAMA=0; INSTALL_MODEL_REGISTRY=1
   INSTALL_FLUX_DEV=0; INSTALL_MINIMAX_H3=0; INSTALL_SDXL=0; INSTALL_LLAMA=0
   MODE="single"
   ;;
esac

success "Use case: $USE_CASE"

# =========================================================================
# STEP 3: Auth (uses Kaggle Secrets or embedded defaults)
# =========================================================================
echo -e "${BOLD}${CYAN}▶ Step 3: Authentication${NC}"
# Use unified password from auth.env (or fallback)
export NGROK_AUTHTOKEN="${NGROK_AUTHTOKEN}"
export COMFYUI_AUTH_PASSWORD="$KGPU_PASSWORD"
success "Auth configured (ngrok: ${NGROK_AUTHTOKEN:0:8}..., user: $KGPU_USERNAME, pass: ${KGPU_PASSWORD:0:6}...)"

# =========================================================================
# STEP 4: Review & Confirm
# =========================================================================
echo -e "${BOLD}${CYAN}▶ Step 4: Review${NC}"
echo ""
echo "  Use case:  $USE_CASE"
echo "  Mode:      $MODE"
echo "  Auth:      kaggle / ${KGPU_PASSWORD:0:6}..."
echo ""
case "$USE_CASE" in
 comfyui) echo "  Components: ComfyUI, Queue API, Consumer, Watchdog, ngrok"
          echo "  Models:     FLUX.1-dev (image), MiniMax H3 (video)" ;;
 llm)     echo "  Components: Ollama, Queue API, Consumer, Watchdog, ngrok"
          echo "  Models:     Llama 3.2 3B (text generation)" ;;
 ml)      echo "  Components: Model Registry, Queue API, Consumer, Watchdog, ngrok"
          echo "  Models:     CLIP, BGE (install more via API)" ;;
esac
echo ""

if [ "$NON_INTERACTIVE" -eq 0 ]; then
 read -r -p "Press ENTER to install (Ctrl+C to cancel)..."
fi

# Save choices for start.sh
cat > "$CHOICES_FILE" <<EOF
USE_CASE=$USE_CASE
MODE=$MODE
INSTALL_COMFYUI=$INSTALL_COMFYUI
INSTALL_QUEUE_API=$INSTALL_QUEUE_API
INSTALL_QUEUE_CONSUMER=$INSTALL_QUEUE_CONSUMER
INSTALL_WATCHDOG=$INSTALL_WATCHDOG
INSTALL_NGROK=$INSTALL_NGROK
INSTALL_OLLAMA=$INSTALL_OLLAMA
INSTALL_MODEL_REGISTRY=$INSTALL_MODEL_REGISTRY
INSTALL_FLUX_DEV=$INSTALL_FLUX_DEV
INSTALL_MINIMAX_H3=$INSTALL_MINIMAX_H3
INSTALL_SDXL=$INSTALL_SDXL
INSTALL_LLAMA=$INSTALL_LLAMA
NGROK_TOKEN=$NGROK_AUTHTOKEN
KGPU_USERNAME=$KGPU_USERNAME
KGPU_PASSWORD=$KGPU_PASSWORD
EOF

# =========================================================================
# INSTALLATION
# =========================================================================
echo -e "${BOLD}${CYAN}▶ Installing...${NC}"

# --- Clone or update repo ---
if [ -d "$WORKDIR/.git" ]; then
 info "Updating repo..."
 git -C "$WORKDIR" pull --ff-only 2>/dev/null || true
else
 info "Cloning $REPO_OWNER/$REPO_NAME..."
 rm -rf "$WORKDIR"
 git clone "$REPO_URL" "$WORKDIR" || { err "Clone failed."; exit 1; }
fi
cd "$WORKDIR"
success "Repo ready."

# --- System deps ---
info "System packages..."
apt-get update -qq > /dev/null 2>&1
apt-get install -y -qq libgl1 libglib2.0-0 ffmpeg wget curl > /dev/null 2>&1
success "System packages installed."

# --- Python deps ---
info "Python dependencies..."
PIP_PKGS="fastapi==0.115.4 uvicorn==0.32.0 aiosqlite==0.20.0 pydantic==2.9.2 psutil==6.1.0 httpx==0.27.2"
[ "$INSTALL_MODEL_REGISTRY" -eq 1 ] && PIP_PKGS="$PIP_PKGS huggingface_hub>=0.26.0"
pip install -q $PIP_PKGS
success "Python deps installed."

# --- ComfyUI ---
COMFYUI_DIR="/kaggle/working/ComfyUI"
if [ "$INSTALL_COMFYUI" -eq 1 ] && [ ! -d "$COMFYUI_DIR" ]; then
 info "Installing ComfyUI..."
 git clone https://github.com/comfyanonymous/ComfyUI.git "$COMFYUI_DIR"
 (cd "$COMFYUI_DIR" && git checkout ab2c3d4e5f6 2>/dev/null || true)
 pip install -q -r "$COMFYUI_DIR/requirements.txt"
 success "ComfyUI installed."
fi

# --- Directories ---
mkdir -p /kaggle/working/models/{checkpoints,loras,unet,vae,clip,llm,ml,audio,embeddings}
mkdir -p /kaggle/working/{output,workflows,configs}
cp -r "$WORKDIR/configs/"* /kaggle/working/configs/ 2>/dev/null || true
cp -r "$WORKDIR/workflows/"* /kaggle/working/workflows/ 2>/dev/null || true

# --- ngrok ---
if [ "$INSTALL_NGROK" -eq 1 ] && ! command -v ngrok &>/dev/null; then
 info "Installing ngrok..."
 curl -fsSL https://bin.ngrok.com/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz | tar -xz -C /usr/local/bin
 success "ngrok installed."
fi

# --- Ollama ---
if [ "$INSTALL_OLLAMA" -eq 1 ] && ! command -v ollama &>/dev/null; then
 info "Installing Ollama..."
 curl -fsSL https://ollama.com/install.sh | sh
 success "Ollama installed."
fi

# --- Model downloads ---
download_model() {
 local url="$1" dest="$2"
 [ -f "$dest" ] && { info "Already have $(basename "$dest")"; return 0; }
 info "Downloading $(basename "$dest")..."
 mkdir -p "$(dirname "$dest")"
 local hf_header=""
 if [ -n "${HF_TOKEN:-}" ]; then
   hf_header="--header=Authorization: Bearer ${HF_TOKEN}"
 fi
 wget --retry-connrefused --tries=3 -c -q --show-progress $hf_header -O "$dest" "$url" || {
   warn "Failed: $(basename "$dest")"; rm -f "$dest"; return 1;
 }
 success "Downloaded: $(basename "$dest")"
}

[ "$INSTALL_FLUX_DEV" -eq 1 ] && download_model \
 "https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors" \
 "/kaggle/working/models/unet/flux1-dev.safetensors"

[ "$INSTALL_MINIMAX_H3" -eq 1 ] && download_model \
 "https://huggingface.co/MiniMax/H3/resolve/main/checkpoint.safetensors" \
 "/kaggle/working/models/checkpoints/minimax_h3.safetensors"

[ "$INSTALL_SDXL" -eq 1 ] && download_model \
 "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" \
 "/kaggle/working/models/checkpoints/sdxl_base.safetensors"

if [ "$INSTALL_LLAMA" -eq 1 ] && command -v ollama &>/dev/null; then
 info "Pulling Llama 3.2 3B via Ollama..."
 ollama pull llama3.2:3b 2>/dev/null || warn "Ollama pull will retry on first use."
fi

# --- Done ---
touch "$FLAG"

echo ""
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════"
echo "  ✅ INSTALLATION COMPLETE  ($USE_CASE mode)"
echo -e "════════════════════════════════════════════════════${NC}"
echo ""
echo "  Start services:  ${CYAN}bash $WORKDIR/start.sh${NC}"
echo "  Your ngrok URL will appear after start."
echo "  Auth: $KGPU_USERNAME / $KGPU_PASSWORD"
echo ""
echo "  To change password: edit config/auth.env → git commit → git push → re-run install.sh"
echo ""
echo "  Test: curl -u kaggle:\$KGPU_PASSWORD https://YOUR-URL.ngrok-free.app/api/health"
echo ""