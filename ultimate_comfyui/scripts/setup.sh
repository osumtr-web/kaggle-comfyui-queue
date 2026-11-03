#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Kaggle ComfyUI Generation Queue Service — Idempotent Setup Script
# Installs ComfyUI, dependencies, and curated models on a fresh Kaggle GPU session.
# Safe to re-run: skips all work if setup_complete.flag exists.
# =============================================================================

BASE_DIR="/kaggle/working"
FLAG_FILE="${BASE_DIR}/setup_complete.flag"
COMFYUI_REPO="https://github.com/comfyanonymous/ComfyUI.git"
COMFYUI_COMMIT="6b904b5fafd062effc1acbc46b1d3196922f7055"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# ---------------------------------------------------------------------------
# 1. Idempotency gate
# ---------------------------------------------------------------------------
if [[ -f "${FLAG_FILE}" ]]; then
    log "Setup already complete (flag file exists). Skipping all installation."
    exit 0
fi

log "Starting Kaggle ComfyUI setup..."

# ---------------------------------------------------------------------------
# 2. System dependencies
# ---------------------------------------------------------------------------
log "Installing system dependencies via apt-get..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    ffmpeg \
    git \
    wget
log "System dependencies installed."

# ---------------------------------------------------------------------------
# 3. Clone ComfyUI at pinned commit
# ---------------------------------------------------------------------------
COMFYUI_DIR="${BASE_DIR}/ComfyUI"
if [[ -d "${COMFYUI_DIR}/.git" ]]; then
    log "ComfyUI repository already cloned. Checking out pinned commit..."
    cd "${COMFYUI_DIR}"
    git fetch --all --quiet
    git checkout "${COMFYUI_COMMIT}" --quiet
else
    log "Cloning ComfyUI repository..."
    git clone "${COMFYUI_REPO}" "${COMFYUI_DIR}"
    cd "${COMFYUI_DIR}"
    git checkout "${COMFYUI_COMMIT}" --quiet
fi
log "ComfyUI checked out at commit ${COMFYUI_COMMIT}."

# ---------------------------------------------------------------------------
# 4. Python dependencies (exact version pins)
# ---------------------------------------------------------------------------
log "Installing pinned Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet \
    fastapi==0.115.4 \
    uvicorn==0.32.0 \
    aiosqlite==0.20.0 \
    pydantic==2.9.2
log "Pinned Python packages installed."

log "Installing ComfyUI requirements.txt..."
pip install --quiet -r "${COMFYUI_DIR}/requirements.txt"
log "ComfyUI requirements installed."

# ---------------------------------------------------------------------------
# 5. Create directory structure
# ---------------------------------------------------------------------------
log "Creating directory structure under ${BASE_DIR}..."
mkdir -p "${BASE_DIR}/models/checkpoints"
mkdir -p "${BASE_DIR}/models/loras"
mkdir -p "${BASE_DIR}/models/unet"
mkdir -p "${BASE_DIR}/models/vae"
mkdir -p "${BASE_DIR}/models/clip"
mkdir -p "${BASE_DIR}/output"
mkdir -p "${BASE_DIR}/workflows"
log "Directory structure created."

# ---------------------------------------------------------------------------
# 6. Download curated models (wget with retry + resume)
# ---------------------------------------------------------------------------
download_model() {
    local url="$1"
    local dest="$2"
    local label="$3"

    if [[ -f "${dest}" ]]; then
        log "${label} already exists at ${dest}. Skipping download."
        return 0
    fi

    log "Downloading ${label}..."
    wget --retry-connrefused --tries=3 -c -q --show-progress -O "${dest}" "${url}"
    log "${label} downloaded to ${dest}."
}

download_model \
    "https://huggingface.co/MiniMax/H3/resolve/main/checkpoint.safetensors" \
    "${BASE_DIR}/models/checkpoints/minimax_h3.safetensors" \
    "MiniMax H3 checkpoint"

download_model \
    "https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors" \
    "${BASE_DIR}/models/unet/flux_dev.safetensors" \
    "Flux Dev UNET"

download_model \
    "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors" \
    "${BASE_DIR}/models/checkpoints/sdxl_base.safetensors" \
    "SDXL Base checkpoint"

# ---------------------------------------------------------------------------
# 7. Copy configs/ and workflows/ from project repo
# ---------------------------------------------------------------------------
if [[ -d "${PROJECT_ROOT}/configs" ]]; then
    log "Copying configs/ from project repo..."
    cp -r "${PROJECT_ROOT}/configs" "${BASE_DIR}/configs"
    log "configs/ copied."
else
    log "WARNING: configs/ not found in project repo at ${PROJECT_ROOT}/configs. Skipping."
fi

if [[ -d "${PROJECT_ROOT}/workflows" ]]; then
    log "Copying workflows/ from project repo..."
    cp -r "${PROJECT_ROOT}/workflows/"* "${BASE_DIR}/workflows/" 2>/dev/null || true
    log "workflows/ copied."
else
    log "WARNING: workflows/ not found in project repo at ${PROJECT_ROOT}/workflows. Skipping."
fi

# ---------------------------------------------------------------------------
# 8. Mark setup complete
# ---------------------------------------------------------------------------
touch "${FLAG_FILE}"
log "Setup complete flag written to ${FLAG_FILE}."
log "=== Kaggle ComfyUI setup finished successfully ==="
exit 0