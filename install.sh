#!/usr/bin/env bash
# =============================================================================
# Kaggle GPU-as-a-Service — Interactive Installer
# Run inside a Kaggle notebook cell: !bash install.sh
# Or with options: !bash install.sh --token GITHUB_TOKEN --mode single
# =============================================================================
set -uo pipefail

# --- Colors & helpers ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

banner() {
  clear 2>/dev/null || true
  echo -e "${CYAN}${BOLD}"
  echo "╔═══════════════════════════════════════════════════════════╗"
  echo "║   🚀 Kaggle GPU-as-a-Service — Interactive Installer   ║"
  echo "║   Turn your free 32GB T4x2 GPU into a remote server    ║"
  echo "╚═══════════════════════════════════════════════════════════╝"
  echo -e "${NC}"
}

info()    { echo -e "${BLUE}ℹ${NC}  $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $1"; }
err()     { echo -e "${RED}✗${NC} $1"; }
heading() { echo -e "\n${BOLD}${CYAN}▶ $1${NC}"; }

# --- Defaults ---
REPO_OWNER="osumtr-web"
REPO_NAME="kaggle-comfyui-queue"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
WORKDIR="/kaggle/working/kaggle-comfyui-queue"
MODE="single"
FLAG="/kaggle/working/.kgpu_installed"
CHOICES_FILE="/kaggle/working/.kgpu_choices"

# Component selections (default: all core)
INSTALL_COMFYUI=1
INSTALL_QUEUE_API=1
INSTALL_QUEUE_CONSUMER=1
INSTALL_WATCHDOG=1
INSTALL_NGROK=1
INSTALL_OLLAMA=0
INSTALL_MODEL_REGISTRY=1

# Model selections
INSTALL_FLUX_DEV=1
INSTALL_MINIMAX_H3=1
INSTALL_SDXL=0
INSTALL_LLAMA=0

# Auth defaults
NGROK_AUTHTOKEN_DEFAULT="1qb36HRiTApsnt5IVQ1bDWAbdk7_5snrnJJ8eBdxbZxhP625f"
COMFYUI_PASS_DEFAULT="MyKagglePass2026!"

# --- Parse CLI args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) GITHUB_TOKEN="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --non-interactive) NON_INTERACTIVE=1; shift ;;
    --skip-comfyui) INSTALL_COMFYUI=0; shift ;;
    --skip-ngrok) INSTALL_NGROK=0; shift ;;
    --ollama) INSTALL_OLLAMA=1; shift ;;
    --help|-h)
      echo "Usage: bash install.sh [options]"
      echo ""
      echo "Options:"
      echo "  --token TOKEN         GitHub token for private repo clone"
      echo "  --mode single|multi   Deployment mode (default: single)"
      echo "  --non-interactive     Use defaults, skip menus"
      echo "  --skip-comfyui        Skip ComfyUI installation"
      echo "  --skip-ngrok          Skip ngrok installation"
      echo "  --ollama              Enable Ollama for LLM serving"
      echo "  --help                Show this help"
      exit 0
      ;;
    *) shift ;;
  esac
done

NON_INTERACTIVE="${NON_INTERACTIVE:-0}"

# --- Idempotency gate ---
if [ -f "$FLAG" ]; then
  banner
  success "Already installed. Found flag at $FLAG"
  echo ""
  info "To reinstall, delete that file and re-run."
  info "To start services: bash $WORKDIR/start.sh"
  exit 0
fi

banner

# =========================================================================
# STEP 1/6: GitHub Repository Access
# =========================================================================
heading "Step 1/6: GitHub Repository Access"

if [ -n "$GITHUB_TOKEN" ]; then
  success "GitHub token detected from environment: ${GITHUB_TOKEN:0:8}..."
else
  if [ "$NON_INTERACTIVE" -eq 1 ]; then
    warn "No GitHub token provided in non-interactive mode. Using public clone."
  else
    echo ""
    info "Your repo: https://github.com/$REPO_OWNER/$REPO_NAME"
    echo ""
    info "If your repo is PRIVATE, you need a GitHub token:"
    info "  1. Go to https://github.com/settings/tokens"
    info "  2. Generate a 'classic' token with 'repo' scope"
    info "  3. Paste it below (or press ENTER for public clone)"
    echo ""
    read -r -p "GitHub token (or ENTER to skip): " GITHUB_TOKEN_INPUT
    GITHUB_TOKEN="${GITHUB_TOKEN_INPUT:-}"
  fi
fi

if [ -n "$GITHUB_TOKEN" ]; then
  REPO_URL="https://${GITHUB_TOKEN}@github.com/${REPO_OWNER}/${REPO_NAME}.git"
  success "Will clone with authenticated access."
else
  REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}.git"
  success "Will clone via public HTTPS."
fi

# =========================================================================
# STEP 2/6: Deployment Mode
# =========================================================================
heading "Step 2/6: Deployment Mode"

if [ "$NON_INTERACTIVE" -eq 1 ]; then
  info "Using default mode: $MODE"
else
  echo ""
  echo "  1) Single mode  — One Kaggle account, one GPU (default)"
  echo "     Best for: personal use, testing, single-user generation"
  echo ""
  echo "  2) Multi mode   — Multiple Kaggle accounts orchestrated"
  echo "     Best for: team use, higher throughput, redundancy"
  echo ""
  while true; do
    read -r -p "Select mode [1/2] (default: 1): " mode_choice
    mode_choice="${mode_choice:-1}"
    case "$mode_choice" in
      1) MODE="single"; break ;;
      2) MODE="multi"; break ;;
      *) warn "Please enter 1 or 2." ;;
    esac
  done
fi
success "Deployment mode: $MODE"

# =========================================================================
# STEP 3/6: Components to Install
# =========================================================================
heading "Step 3/6: Components to Install"

toggle() {
  local var="$1"
  local current="${!var}"
  if [ "$current" -eq 1 ]; then
    eval "$var=0"
  else
    eval "$var=1"
  fi
}

if [ "$NON_INTERACTIVE" -eq 0 ]; then
  while true; do
    echo ""
    echo -e "  ${BOLD}Core Services (required):${NC}"
    [ "$INSTALL_COMFYUI" -eq 1 ] && CUI_ICON="${GREEN}[✓]${NC}" || CUI_ICON="[ ]"
    [ "$INSTALL_QUEUE_API" -eq 1 ] && API_ICON="${GREEN}[✓]${NC}" || API_ICON="[ ]"
    [ "$INSTALL_QUEUE_CONSUMER" -eq 1 ] && CON_ICON="${GREEN}[✓]${NC}" || CON_ICON="[ ]"
    [ "$INSTALL_WATCHDOG" -eq 1 ] && WD_ICON="${GREEN}[✓]${NC}" || WD_ICON="[ ]"
    [ "$INSTALL_NGROK" -eq 1 ] && NG_ICON="${GREEN}[✓]${NC}" || NG_ICON="[ ]"

    echo -e "  ${BOLD}Optional Add-ons:${NC}"
    [ "$INSTALL_OLLAMA" -eq 1 ] && OL_ICON="${GREEN}[✓]${NC}" || OL_ICON="[ ]"
    [ "$INSTALL_MODEL_REGISTRY" -eq 1 ] && MR_ICON="${GREEN}[✓]${NC}" || MR_ICON="[ ]"

    echo -e "    $CUI_ICON 1) ComfyUI         — Image & video generation engine"
    echo -e "    $API_ICON 2) Queue API        — REST job queue service"
    echo -e "    $CON_ICON 3) Queue Consumer   — Job dispatcher (ComfyUI/Ollama)"
    echo -e "    $WD_ICON  4) Watchdog         — Auto-recovery daemon"
    echo -e "    $NG_ICON  5) ngrok            — HTTPS tunnel (REQUIRED for remote access)"
    echo -e "    $OL_ICON  6) Ollama           — LLM text generation"
    echo -e "    $MR_ICON  7) Model Registry   — Dynamic HuggingFace model install"
    echo ""
    echo "  Enter number to toggle, or ENTER to confirm."
    read -r -p "> " comp_choice

    case "$comp_choice" in
      1) toggle INSTALL_COMFYUI ;;
      2) toggle INSTALL_QUEUE_API ;;
      3) toggle INSTALL_QUEUE_CONSUMER ;;
      4) toggle INSTALL_WATCHDOG ;;
      5) toggle INSTALL_NGROK ;;
      6) toggle INSTALL_OLLAMA ;;
      7) toggle INSTALL_MODEL_REGISTRY ;;
      "") break ;;
      *) warn "Invalid choice." ;;
    esac
  done
fi

# =========================================================================
# STEP 4/6: Model Downloads
# =========================================================================
heading "Step 4/6: Models to Pre-Download"

if [ "$NON_INTERACTIVE" -eq 0 ]; then
  while true; do
    echo ""
    [ "$INSTALL_FLUX_DEV" -eq 1 ]   && F_ICON="${GREEN}[✓]${NC}" || F_ICON="[ ]"
    [ "$INSTALL_MINIMAX_H3" -eq 1 ] && M_ICON="${GREEN}[✓]${NC}" || M_ICON="[ ]"
    [ "$INSTALL_SDXL" -eq 1 ]      && S_ICON="${GREEN}[✓]${NC}" || S_ICON="[ ]"
    [ "$INSTALL_LLAMA" -eq 1 ]     && L_ICON="${GREEN}[✓]${NC}" || L_ICON="[ ]"

    echo -e "    $F_ICON 1) FLUX.1-dev       (~12GB, image generation)"
    echo -e "    $M_ICON 2) MiniMax H3       (~18GB, video generation)"
    echo -e "    $S_ICON 3) SDXL Base        (~8GB, image generation)"
    if [ "$INSTALL_OLLAMA" -eq 1 ]; then
      echo -e "    $L_ICON 4) Llama 3.2 3B     (~2GB, LLM via Ollama)"
    fi
    echo ""
    echo "  Enter number to toggle, or ENTER to confirm."
    echo "  (You can always install more models later via the API)"
    read -r -p "> " model_choice

    case "$model_choice" in
      1) toggle INSTALL_FLUX_DEV ;;
      2) toggle INSTALL_MINIMAX_H3 ;;
      3) toggle INSTALL_SDXL ;;
      4) [ "$INSTALL_OLLAMA" -eq 1 ] && toggle INSTALL_LLAMA ;;
      "") break ;;
      *) warn "Invalid choice." ;;
    esac
  done
fi

# =========================================================================
# STEP 5/6: Authentication
# =========================================================================
heading "Step 5/6: Authentication Setup"

# Check Kaggle Secrets first
if [ -n "${NGROK_AUTHTOKEN:-}" ]; then
  NGROK_TOKEN="$NGROK_AUTHTOKEN"
  success "NGROK_AUTHTOKEN found in environment."
else
  if [ "$NON_INTERACTIVE" -eq 1 ]; then
    NGROK_TOKEN="$NGROK_AUTHTOKEN_DEFAULT"
    warn "Using embedded ngrok default token."
  else
    echo ""
    info "ngrok authtoken (from https://dashboard.ngrok.com/get-started/your-authtoken)"
    info "Press ENTER to use embedded default: ${NGROK_AUTHTOKEN_DEFAULT:0:8}..."
    read -r -p "ngrok token: " ngrok_input
    NGROK_TOKEN="${ngrok_input:-$NGROK_AUTHTOKEN_DEFAULT}"
  fi
fi

if [ -n "${COMFYUI_AUTH_PASSWORD:-}" ]; then
  COMFYUI_PASS="$COMFYUI_AUTH_PASSWORD"
  success "COMFYUI_AUTH_PASSWORD found in environment."
else
  if [ "$NON_INTERACTIVE" -eq 1 ]; then
    COMFYUI_PASS="$COMFYUI_PASS_DEFAULT"
    warn "Using default ComfyUI password."
  else
    echo ""
    info "ComfyUI access password (username will be 'kaggle')"
    info "Press ENTER to use default: $COMFYUI_PASS_DEFAULT"
    read -r -p "Password: " pass_input
    COMFYUI_PASS="${pass_input:-$COMFYUI_PASS_DEFAULT}"
  fi
fi

export NGROK_AUTHTOKEN="$NGROK_TOKEN"
export COMFYUI_AUTH_PASSWORD="$COMFYUI_PASS"
success "Authentication configured."

# =========================================================================
# STEP 6/6: Review & Confirm
# =========================================================================
heading "Step 6/6: Review Installation Plan"

echo ""
echo "  ┌───────────────────────────────────────────────┐"
echo "  │  Repository:  $REPO_OWNER/$REPO_NAME"
echo "  │  Mode:        $MODE"
echo "  │  Auth:        kaggle / $COMFYUI_PASS"
echo "  └───────────────────────────────────────────────┘"
echo ""
echo -e "  ${BOLD}Components:${NC}"
[ "$INSTALL_COMFYUI" -eq 1 ]        && echo "    ✓ ComfyUI"
[ "$INSTALL_QUEUE_API" -eq 1 ]      && echo "    ✓ Queue API"
[ "$INSTALL_QUEUE_CONSUMER" -eq 1 ] && echo "    ✓ Queue Consumer"
[ "$INSTALL_WATCHDOG" -eq 1 ]       && echo "    ✓ Watchdog"
[ "$INSTALL_NGROK" -eq 1 ]          && echo "    ✓ ngrok"
[ "$INSTALL_OLLAMA" -eq 1 ]         && echo "    ✓ Ollama (LLM)"
[ "$INSTALL_MODEL_REGISTRY" -eq 1 ] && echo "    ✓ Model Registry"
echo ""
echo -e "  ${BOLD}Models to download:${NC}"
[ "$INSTALL_FLUX_DEV" -eq 1 ]   && echo "    ✓ FLUX.1-dev (~12GB)"
[ "$INSTALL_MINIMAX_H3" -eq 1 ] && echo "    ✓ MiniMax H3 (~18GB)"
[ "$INSTALL_SDXL" -eq 1 ]      && echo "    ✓ SDXL Base (~8GB)"
[ "$INSTALL_LLAMA" -eq 1 ]     && echo "    ✓ Llama 3.2 3B (~2GB)"
echo ""

if [ "$NON_INTERACTIVE" -eq 0 ]; then
  read -r -p "Press ENTER to install (Ctrl+C to cancel)..."
fi

# Save choices for start.sh to read
cat > "$CHOICES_FILE" <<EOF
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
NGROK_TOKEN=$NGROK_TOKEN
COMFYUI_PASS=$COMFYUI_PASS
EOF
success "Choices saved to $CHOICES_FILE"

# =========================================================================
# INSTALLATION BEGINS
# =========================================================================
heading "Beginning Installation"

# --- Clone or update repo ---
if [ -d "$WORKDIR/.git" ]; then
  info "Updating existing repo..."
  git -C "$WORKDIR" pull --ff-only || warn "Pull failed; continuing with existing code."
else
  info "Cloning $REPO_OWNER/$REPO_NAME..."
  rm -rf "$WORKDIR"
  if ! git clone "$REPO_URL" "$WORKDIR"; then
    err "Git clone failed. Check your token or internet."
    exit 1
  fi
fi
cd "$WORKDIR"
success "Repo ready at $WORKDIR"

# --- System deps ---
info "Installing system packages..."
apt-get update -qq > /dev/null 2>&1
apt-get install -y -qq libgl1 libglib2.0-0 ffmpeg wget curl > /dev/null 2>&1
success "System packages installed."

# --- Python deps ---
info "Installing Python dependencies..."
PIP_PKGS="fastapi==0.115.4 uvicorn==0.32.0 aiosqlite==0.20.0 pydantic==2.9.2 psutil==6.1.0 httpx==0.27.2"
[ "$INSTALL_MODEL_REGISTRY" -eq 1 ] && PIP_PKGS="$PIP_PKGS huggingface_hub>=0.26.0"
pip install -q $PIP_PKGS
success "Python dependencies installed."

# --- ComfyUI ---
COMFYUI_DIR="/kaggle/working/ComfyUI"
if [ "$INSTALL_COMFYUI" -eq 1 ]; then
  if [ ! -d "$COMFYUI_DIR" ]; then
    info "Installing ComfyUI..."
    git clone https://github.com/comfyanonymous/ComfyUI.git "$COMFYUI_DIR"
    (cd "$COMFYUI_DIR" && git checkout ab2c3d4e5f6 2>/dev/null || true)
    pip install -q -r "$COMFYUI_DIR/requirements.txt"
    success "ComfyUI installed."
  else
    info "ComfyUI already present."
  fi
fi

# --- Directory structure ---
info "Creating directory structure..."
mkdir -p /kaggle/working/models/{checkpoints,loras,unet,vae,clip,llm,ml,audio,embeddings}
mkdir -p /kaggle/working/output
mkdir -p /kaggle/working/workflows
mkdir -p /kaggle/working/configs
cp -r "$WORKDIR/configs/"* /kaggle/working/configs/ 2>/dev/null || true
cp -r "$WORKDIR/workflows/"* /kaggle/working/workflows/ 2>/dev/null || true
success "Directories ready."

# --- ngrok ---
if [ "$INSTALL_NGROK" -eq 1 ]; then
  if ! command -v ngrok &>/dev/null; then
    info "Installing ngrok..."
    curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok-v3-stable-linux-amd64.tgz | tar -xz -C /usr/local/bin
    success "ngrok installed."
  else
    info "ngrok already present."
  fi
fi

# --- Ollama ---
if [ "$INSTALL_OLLAMA" -eq 1 ]; then
  if ! command -v ollama &>/dev/null; then
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    success "Ollama installed."
  else
    info "Ollama already present."
  fi
fi

# --- Model downloads ---
download_model() {
  local url="$1"
  local dest="$2"
  if [ -f "$dest" ]; then
    info "Already downloaded: $(basename "$dest")"
    return 0
  fi
  info "Downloading $(basename "$dest")..."
  mkdir -p "$(dirname "$dest")"
  wget --retry-connrefused --tries=3 -c -q --show-progress -O "$dest" "$url" || {
    warn "Download failed for $(basename "$dest")"
    rm -f "$dest"
    return 1
  }
  success "Downloaded: $(basename "$dest")"
}

if [ "$INSTALL_FLUX_DEV" -eq 1 ] && [ "$INSTALL_COMFYUI" -eq 1 ]; then
  download_model \
    "https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors" \
    "/kaggle/working/models/unet/flux1-dev.safetensors"
fi

if [ "$INSTALL_MINIMAX_H3" -eq 1 ] && [ "$INSTALL_COMFYUI" -eq 1 ]; then
  download_model \
    "https://huggingface.co/MiniMax/H3/resolve/main/checkpoint.safetensors" \
    "/kaggle/working/models/checkpoints/minimax_h3.safetensors"
fi

if [ "$INSTALL_SDXL" -eq 1 ] && [ "$INSTALL_COMFYUI" -eq 1 ]; then
  download_model \
    "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" \
    "/kaggle/working/models/checkpoints/sdxl_base.safetensors"
fi

if [ "$INSTALL_LLAMA" -eq 1 ] && [ "$INSTALL_OLLAMA" -eq 1 ]; then
  info "Pulling Llama 3.2 3B via Ollama..."
  ollama pull llama3.2:3b || warn "Ollama pull failed; will retry on first use."
fi

# --- Mark complete ---
touch "$FLAG"

echo ""
echo -e "${GREEN}${BOLD}=============================================="
echo "  ✅ INSTALLATION COMPLETE"
echo -e "==============================================${NC}"
echo ""
echo "  Start all selected services with ONE command:"
echo ""
echo -e "    ${CYAN}bash $WORKDIR/start.sh${NC}"
echo ""
if [ "$INSTALL_NGROK" -eq 1 ]; then
  echo "  Your ngrok URL will appear after start.sh runs."
  echo "  Auth: kaggle / $COMFYUI_PASS"
fi
echo ""
echo "  Logs: /kaggle/working/*.log"
echo "  Choices: $CHOICES_FILE"
echo ""
echo -e "${GREEN}${BOLD}==============================================${NC}"