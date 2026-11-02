#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — One-line first-time setup for Kaggle GPU-as-a-Service
# Usage: !bash bootstrap.sh ghp_YOUR_GITHUB_TOKEN [comfyui|llm|ml]
#
# This script handles EVERYTHING for first-time setup:
#   1. Clones the repo (private or public) using your GitHub token
#   2. Saves the token to config/auth.env for future use
#   3. Runs install.sh (interactive or non-interactive based on args)
#   4. Starts all services via start.sh
#
# After this, just use: !bash scripts/kgpu <command>
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

err() { echo -e "${RED}✗${NC} $1" >&2; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }

GITHUB_TOKEN="${1:-}"
USE_CASE="${2:-}"
REPO_URL="https://github.com/osumtr-web/kaggle-comfyui-queue.git"
INSTALL_DIR="/kaggle/working/kaggle-comfyui-queue"

# --- Validate token ---
if [ -z "$GITHUB_TOKEN" ]; then
    err "Usage: !bash bootstrap.sh <GITHUB_TOKEN> [comfyui|llm|ml]"
    err ""
    err "Get your token at: https://github.com/settings/tokens"
    err "Required scope: repo (full control of private repositories)"
    err ""
    err "Examples:"
    err "  !bash bootstrap.sh ghp_abc123...           # interactive install"
    err "  !bash bootstrap.sh ghp_abc123... comfyui   # auto-select ComfyUI"
    err "  !bash bootstrap.sh ghp_abc123... llm       # auto-select LLM"
    err "  !bash bootstrap.sh ghp_abc123... ml        # auto-select ML"
    exit 1
fi

# --- Step 1: Clone repo with token ---
info "Cloning repository..."
if [ -d "$INSTALL_DIR" ]; then
    info "Directory exists, updating..."
    cd "$INSTALL_DIR"
    git remote set-url origin "https://${GITHUB_TOKEN}@github.com/osumtr-web/kaggle-comfyui-queue.git" 2>/dev/null || true
    git pull origin master 2>&1 || err "Git pull failed, continuing with existing code"
else
    # Try private clone first, fall back to public
    if git clone "https://${GITHUB_TOKEN}@github.com/osumtr-web/kaggle-comfyui-queue.git" "$INSTALL_DIR" 2>/dev/null; then
        success "Cloned private repo successfully"
    elif git clone "$REPO_URL" "$INSTALL_DIR" 2>/dev/null; then
        success "Cloned public repo (token not needed for this repo)"
    else
        err "Failed to clone repository. Check your token and network."
        exit 1
    fi
fi

cd "$INSTALL_DIR"

# --- Step 2: Save token to auth.env ---
info "Saving GitHub token to config/auth.env..."
if grep -q 'export GITHUB_TOKEN=""' config/auth.env 2>/dev/null; then
    sed -i "s|export GITHUB_TOKEN=\"\"|export GITHUB_TOKEN=\"${GITHUB_TOKEN}\"|" config/auth.env
    success "Token saved to config/auth.env"
elif grep -q 'export GITHUB_TOKEN=' config/auth.env 2>/dev/null; then
    sed -i "s|export GITHUB_TOKEN=.*|export GITHUB_TOKEN=\"${GITHUB_TOKEN}\"|" config/auth.env
    success "Token updated in config/auth.env"
else
    echo "" >> config/auth.env
    echo "# GitHub Personal Access Token (auto-saved by bootstrap.sh)" >> config/auth.env
    echo "export GITHUB_TOKEN=\"${GITHUB_TOKEN}\"" >> config/auth.env
    success "Token appended to config/auth.env"
fi

# --- Step 3: Run installer ---
info "Running installer..."
if [ -n "$USE_CASE" ]; then
    case "$USE_CASE" in
        comfyui|1) INSTALL_ARGS="--case comfyui --non-interactive" ;;
        llm|2)     INSTALL_ARGS="--case llm --non-interactive" ;;
        ml|3)      INSTALL_ARGS="--case ml --non-interactive" ;;
        *)         INSTALL_ARGS="" ;;
    esac
    info "Non-interactive mode: $USE_CASE"
    bash install.sh $INSTALL_ARGS
else
    info "Interactive mode — pick your use case when prompted"
    bash install.sh
fi

# --- Step 4: Start services ---
info "Starting services..."
bash start.sh

echo ""
success "═══════════════════════════════════════════════════"
success " Setup complete! Your GPU server is live."
success "═══════════════════════════════════════════════════"
echo ""
info "Next steps:"
echo "  !bash scripts/kgpu health          # check system"
echo "  !bash scripts/kgpu generate \"prompt\" # generate image"
echo "  !bash scripts/kgpu pull <job_id>   # download result"
echo "  !bash scripts/kgpu help            # all commands"
echo ""
info "Your GitHub token is saved — future installs will auto-clone."