#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — One-line first-time setup for Kaggle GPU-as-a-Service
# Usage: !bash bootstrap.sh ghp_YOUR_GITHUB_TOKEN [comfyui|llm|ml]
#
# Fully non-interactive. No prompts. No stdin reads. Works with curl|bash.
# =============================================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

err() { echo -e "${RED}✗${NC} $1" >&2; }
info() { echo -e "${BLUE}ℹ${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }

GITHUB_TOKEN="${1:-}"
USE_CASE="${2:-comfyui}"
REPO_URL="https://github.com/osumtr-web/kaggle-comfyui-queue.git"
INSTALL_DIR="/kaggle/working/kaggle-comfyui-queue"

echo ""
echo -e "${BOLD}${CYAN}╔═══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║   Kaggle GPU-as-a-Service — Bootstrap            ║${NC}"
echo -e "${BOLD}${CYAN}╚═══════════════════════════════════════════════════╝${NC}"
echo ""

# --- Validate token ---
if [ -z "$GITHUB_TOKEN" ]; then
    err "Usage: !bash bootstrap.sh <GITHUB_TOKEN> [comfyui|llm|ml]"
    err ""
    err "Get your token at: https://github.com/settings/tokens"
    err "Required scope: repo (full control of private repositories)"
    err ""
    err "Examples:"
    err "  !bash bootstrap.sh ghp_abc123... comfyui"
    err "  !bash bootstrap.sh ghp_abc123... llm"
    err "  !bash bootstrap.sh ghp_abc123... ml"
    exit 1
fi

# --- Step 1: Clone repo with token ---
info "Step 1/4: Cloning repository..."
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Directory exists, pulling latest..."
    cd "$INSTALL_DIR"
    git remote set-url origin "https://${GITHUB_TOKEN}@github.com/osumtr-web/kaggle-comfyui-queue.git" 2>/dev/null || true
    git pull origin master 2>&1 || info "Pull failed, continuing with existing code"
else
    rm -rf "$INSTALL_DIR"
    if git clone "https://${GITHUB_TOKEN}@github.com/osumtr-web/kaggle-comfyui-queue.git" "$INSTALL_DIR" 2>&1; then
        success "Cloned private repo successfully"
    elif git clone "$REPO_URL" "$INSTALL_DIR" 2>&1; then
        success "Cloned public repo (token not needed)"
    else
        err "FAILED to clone repository."
        err "Check: 1) Token is valid  2) Network works  3) Repo exists"
        exit 1
    fi
fi

cd "$INSTALL_DIR"
success "Repository ready at $INSTALL_DIR"

# --- Step 2: Save token to LOCAL auth.env (gitignored, never committed) ---
info "Step 2/4: Configuring authentication..."
if [ ! -f config/auth.env ]; then
    if [ -f config/auth.env.example ]; then
        cp config/auth.env.example config/auth.env
        info "Created config/auth.env from template"
    else
        # Fallback: create minimal auth.env if template missing
        cat > config/auth.env << 'AUTHEOF'
#!/usr/bin/env bash
export KGPU_USERNAME="kaggle"
export KGPU_PASSWORD="KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479"
export NGROK_AUTHTOKEN=""
export GITHUB_TOKEN=""
export KGPU_MODE="single"
AUTHEOF
        info "Created minimal config/auth.env"
    fi
fi

# Write token to the local-only file
if grep -q 'export GITHUB_TOKEN=' config/auth.env 2>/dev/null; then
    sed -i "s|export GITHUB_TOKEN=.*|export GITHUB_TOKEN=\"${GITHUB_TOKEN}\"|" config/auth.env
else
    echo "export GITHUB_TOKEN=\"${GITHUB_TOKEN}\"" >> config/auth.env
fi
success "GitHub token saved locally (never pushed to git)"

# --- Step 3: Run installer (always non-interactive) ---
info "Step 3/4: Installing dependencies and models (${USE_CASE})..."
case "$USE_CASE" in
    comfyui|1) INSTALL_ARGS="--case comfyui --non-interactive" ;;
    llm|2)     INSTALL_ARGS="--case llm --non-interactive" ;;
    ml|3)      INSTALL_ARGS="--case ml --non-interactive" ;;
    *)         INSTALL_ARGS="--case comfyui --non-interactive" ;;
esac

if [ -f install.sh ]; then
    bash install.sh $INSTALL_ARGS 2>&1
    if [ $? -ne 0 ]; then
        err "install.sh failed with exit code $?"
        err "Check output above for details"
        exit 1
    fi
    success "Installation complete"
else
    err "install.sh not found in $INSTALL_DIR"
    exit 1
fi

# --- Step 4: Start services ---
info "Step 4/4: Starting services..."
if [ -f start.sh ]; then
    bash start.sh 2>&1
    success "Services started"
else
    err "start.sh not found in $INSTALL_DIR"
    exit 1
fi

echo ""
success "═══════════════════════════════════════════════════"
success " ✅ Setup complete! Your GPU server is live."
success "═══════════════════════════════════════════════════"
echo ""
info "Quick commands:"
echo "  !bash scripts/kgpu health              # check system"
echo "  !bash scripts/kgpu generate \"prompt\"   # generate image"
echo "  !bash scripts/kgpu pull <job_id>       # download result"
echo "  !bash scripts/kgpu help                # all commands"
echo ""
info "Your ngrok URL should appear above. Use it from anywhere."