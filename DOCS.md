# 📚 Documentation & Architecture Reference

This document contains the detailed technical reference for the Kaggle GPU-as-a-Service platform. For the quick playbook, see [README.md](README.md).

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              KAGGLE NOTEBOOK                 │
│                                             │
│  ComfyUI (:8188) ←→ Queue API (:8189)       │
│       ↕                    ↕                │
│   SQLite Queue ←──── Watchdog               │
│       ↕                                     │
│   ngrok Tunnel → HTTPS access from anywhere │
└─────────────────────────────────────────────┘
```

### Core Components

| Component | Port | Purpose |
|-----------|------|---------|
| **ComfyUI Server** | :8188 | Image/video generation engine |
| **Queue API (FastAPI)** | :8189 | REST API for job submission and status |
| **Queue Consumer** | — | Dispatches jobs to ComfyUI or Ollama |
| **Watchdog** | — | Auto-restarts crashed services, rotates output |
| **ngrok Tunnel** | :443 | HTTPS tunnel with basic auth |
| **SQLite Queue** | — | Persistent job storage at `/kaggle/working/queue.db` |

### Key Features

- **Persistent job queue** — survives cell re-runs and restarts
- **Auto-recovery watchdog** — restarts crashed services in 5 seconds with exponential backoff
- **VRAM safety** — two-stage memory checks before dispatching jobs
- **Output rotation** — auto-deletes old files at 80% disk or 25GB threshold
- **Basic auth** — tunnel is password-protected by default
- **Dynamic URL retrieval** — ngrok URL fetched from local API (port 4040)
- **Orphaned job recovery** — stuck jobs reset after 90s heartbeat timeout

---

## Supported Model Types

| `model_type` | Engine | Example Models | Use Case |
|---|---|---|---|
| `diffusion_image` | ComfyUI | FLUX.1-dev, SDXL, SD3 | Text-to-image generation |
| `diffusion_video` | ComfyUI | MiniMax H3, LTX-Video | Text/image-to-video generation |
| `llm` | Ollama | Llama 3.2, Qwen2, Mistral | Text generation, chat, completion |
| `audio` | ComfyUI | AudioLDM, MusicGen | Text-to-audio/music generation |
| `ml_classifier` | Custom | CLIP, BLIP | Image classification, embedding |
| `embedding` | Custom | BGE, E5 | Text embeddings, RAG |
| `other` | Custom | Any HF model | Experimental / unsupported types |

---

## API Reference

All endpoints require HTTP Basic Auth. The `kgpu` CLI handles this automatically.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/jobs` | Submit a new job |
| GET | `/api/jobs/{id}` | Check job status |
| GET | `/api/jobs` | List all jobs |
| DELETE | `/api/jobs/{id}` | Cancel a job |
| GET | `/api/health` | System health check |
| GET | `/api/models` | List installed models |
| POST | `/api/models/install` | Install from HuggingFace |
| DELETE | `/api/models/{id}` | Uninstall a model |
| GET | `/api/models/{id}/status` | Check install progress |
| GET | `/api/jobs/{id}/artifact` | Download generated file (image/video/audio) with correct MIME type |
| GET | `/api/jobs/{id}/artifact/preview` | Inline image preview (no download prompt; images only) |

### Artifact Serving Details

The `/api/jobs/{id}/artifact` endpoint serves generated files with automatic MIME type detection:

| Extension | MIME Type | Content-Type |
|-----------|-----------|--------------|
| `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` | Image | `image/png`, `image/jpeg`, etc. |
| `.mp4`, `.webm`, `.mov`, `.avi` | Video | `video/mp4`, `video/webm`, etc. |
| `.wav`, `.mp3`, `.flac` | Audio | `audio/wav`, `audio/mpeg`, etc. |
| `.txt`, `.json` | Text | `text/plain`, `application/json` |
| Other | Binary | `application/octet-stream` |

**Behavior:**
- Returns `409 Conflict` if job is not `completed`
- Returns `404 Not Found` if no artifact file exists (e.g., text-only LLM job)
- Sets `Content-Disposition: attachment` header for browser download
- Requires HTTP Basic Auth (same as all other endpoints)

**Preview endpoint** (`/api/jobs/{id}/artifact/preview`):
- Only works for image files (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`)
- Returns inline display (no download prompt) for embedding in web UIs
- Returns `415 Unsupported Media Type` for non-image files

**CLI usage:**
```bash
# Auto-name from server result_path
kgpu pull 42

# Custom output filename
kgpu pull 42 my_cyberpunk_city.png

# Download to specific directory
kgpu fetch 42 ./outputs/

# Alias: fetch = pull
kgpu fetch 42 video_output.mp4
```

### Job Request Format

```json
{
  "model_id": "model-name-or-repo",
  "model_type": "diffusion_image|diffusion_video|llm|audio|ml_classifier|embedding|other",
  "prompt": "your prompt here",
  "params": {
    "width": 1024,
    "height": 1024,
    "steps": 28,
    "max_tokens": 256,
    "temperature": 0.7
  }
}
```

### Job Response

```json
{
  "job_id": "uuid-here",
  "status": "pending|running|completed|failed",
  "result_path": "/kaggle/working/output/file.png",
  "result_text": "generated text (for LLM jobs)",
  "created_at": "2026-11-02T...",
  "completed_at": "2026-11-02T..."
}
```

### Direct API Examples (without `kgpu` CLI)

```bash
# Get ngrok URL first
URL=$(curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "import sys,json; print(next(t['public_url'] for t in json.load(sys.stdin)['tunnels'] if t['public_url'].startswith('https://')))")

# Submit image generation job
curl -u kaggle:KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479 -X POST "$URL/api/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "black-forest-labs/FLUX.1-dev",
    "model_type": "diffusion_image",
    "prompt": "A cyberpunk city at sunset, cinematic lighting, 8k",
    "params": {"width": 1024, "height": 1024, "steps": 28}
  }'

# Check job status
curl -u kaggle:KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479 "$URL/api/jobs/{job_id}"

# Install additional model at runtime
curl -u kaggle:KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479 -X POST "$URL/api/models/install" \
  -H "Content-Type: application/json" \
  -d '{"hf_repo": "stabilityai/stable-diffusion-xl-base-1.0", "model_type": "diffusion_image"}'
```

---

## Authentication System

### How It Works

A single password is used for **all** services via `config/auth.env`:

```bash
export KGPU_USERNAME="kaggle"
export KGPU_PASSWORD="KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479"
export NGROK_AUTHTOKEN="1qb36HRiTApsnt5IVQ1bDWAbdk7_5snrnJJ8eBdxbZxhP625f"
export KGPU_MODE="single"
```

### Changing Your Password

1. Edit `config/auth.env` locally
2. `git add config/auth.env && git commit -m "update password"`
3. `git push origin master`
4. Re-run `install.sh` in Kaggle

### Kaggle Secrets Override

If you set Kaggle Secrets, they take priority over embedded defaults:
- `COMFYUI_AUTH_PASSWORD` → overrides `KGPU_PASSWORD`
- `NGROK_AUTHTOKEN` → overrides embedded ngrok token

---

## Dynamic ngrok URL Retrieval

The system uses the ngrok local API (port 4040) to fetch the public URL dynamically:

```bash
curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "..."
```

This eliminates manual copy-paste of URLs. The URL is also persisted to `/kaggle/working/ngrok_url.txt` as a fallback.

### How `kgpu` CLI Uses It

1. Queries `http://127.0.0.1:4040/api/tunnels`
2. Extracts `public_url` from JSON response
3. Falls back to `/kaggle/working/ngrok_url.txt` if API unavailable
4. Combines with credentials from `config/auth.env`

---

## Advanced Usage

### Private GitHub Repo

```bash
!bash install.sh --token ghp_YOUR_GITHUB_TOKEN
```

### Non-Interactive Install (for scripts/CI)

```bash
!bash install.sh --case comfyui --non-interactive
```

### Multi-Account Mode

Orchestrate multiple Kaggle accounts for higher throughput:

```bash
# Set as Kaggle Secret
KGPU_MODE=multi
```

See [Multi-Account Guide](#multi-account-setup) for coordinator setup.

### Install More Models at Runtime

```bash
# Via kgpu CLI (checks what's installed)
!bash scripts/kgpu models

# Via API (install new model)
curl -u kaggle:KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479 -X POST https://YOUR-URL/api/models/install \
  -H "Content-Type: application/json" \
  -d '{"hf_repo": "stabilityai/stable-diffusion-xl-base-1.0", "model_type": "diffusion_image"}'
```

---

## Watchdog & Auto-Recovery

The watchdog daemon monitors all critical processes:

| Monitored Process | Restart Behavior |
|-------------------|-----------------|
| ComfyUI Server | Immediate restart on crash, max 5 retries |
| ngrok Tunnel | Immediate restart on disconnect |
| Queue API (FastAPI) | Immediate restart on crash |
| Queue Consumer | Immediate restart on crash |
| Ollama | Lazy-started on first LLM job; restarted if crashed |

**Restart backoff strategy:** 5s → 10s → 20s → 30s cap, maximum 5 consecutive retries per process. Resets on successful health check.

**Output rotation:** Automatically deletes oldest files in `/kaggle/working/output/` when disk usage exceeds 80% or total output size exceeds 25GB.

**Orphaned job recovery:** Jobs stuck in `running` status are reset to `pending` after 90 seconds of heartbeat timeout.

**Graceful shutdown:** On SIGTERM, the watchdog persists current state to SQLite and stops all managed processes cleanly.

---

## VRAM Safety Protocol

GPU memory is managed through a two-stage safety system:

1. **Static estimation:** Before dispatching a job, the consumer consults `model_vram_manifest.yaml` for pre-computed VRAM requirements based on model type, precision, and quantization level.

2. **Runtime verification:** Immediately before execution, `nvidia-smi` is queried for actual free VRAM. Jobs are queued if available memory is below the estimated requirement + 2GB safety margin.

**OOM recovery:** If a generation fails with CUDA out-of-memory, the consumer automatically retries once with reduced parameters (lower resolution or fewer steps). Persistent OOM marks the job as failed with diagnostic info.

**RAM monitoring:** System RAM is monitored at 90% threshold; model loading is paused if host memory is critically low.

### Model-specific VRAM Heuristics

| Format | VRAM Estimate | Notes |
|--------|--------------|-------|
| fp16 (diffusion) | Model size × 1.2 | Standard half-precision |
| GGUF Q4_K_M | Model size × 0.35 | 4-bit quantized LLM |
| GGUF Q8_0 | Model size × 0.6 | 8-bit quantized LLM |
| ONNX Runtime | Model size × 1.1 | CPU/GPU hybrid inference |

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| **ngrok returns 401** | Wrong password | Use password from `config/auth.env` (default: `KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479`) |
| **Job stuck in "running"** | Consumer crashed mid-job | Wait 90s for heartbeat recovery, or restart Cell 4 |
| **OOM during generation** | Model too large for T4 | Use quantized model (Q4_K_M) or lower resolution |
| **Model install fails 401** | Gated HuggingFace model | Set `HF_TOKEN` env var in Kaggle Secrets |
| **Disk full** | Too many accumulated outputs | Watchdog auto-cleans; manual: `rm /kaggle/working/output/*` |
| **ComfyUI won't start** | Port 8188 already in use | Kill existing process: `fuser -k 8188/tcp` |
| **Ollama not responding** | First LLM job triggers lazy start | Wait ~30s for initial model load |
| **ngrok URL changes every restart** | Free tier limitation | Upgrade to ngrok paid plan for static domains |
| **Job fails with "workflow not allowed"** | Workflow JSON not in allowlist | Add workflow to `workflows/` directory and restart consumer |
| **kgpu can't find URL** | ngrok not running or API unavailable | Ensure ngrok is running; check `/kaggle/working/ngrok.log` |

---

## File Structure

```
kaggle-comfyui-queue/
├── install.sh              # Interactive installer (pick your use case)
├── start.sh                # Launch selected services
├── README.md               # Minimal playbook (this file)
├── DOCS.md                 # Detailed architecture & API reference
├── QUICKSTART.md           # 2-command quick start
├── config/
│   └── auth.env            # Unified password + ngrok token (edit to change)
├── scripts/
│   ├── kgpu                # Plug-and-play CLI (auto-detects URL + auth)
│   ├── queue_api.py        # REST API (FastAPI) on :8189
│   ├── queue_consumer.py   # Job dispatcher (ComfyUI + Ollama)
│   ├── watchdog.py         # Process monitor + output rotation
│   ├── ngrok_launcher.py   # Tunnel with auth + dynamic URL via API
│   └── model_registry.py   # Dynamic HF model management
├── configs/
│   ├── comfyui_settings.yaml
│   └── model_vram_manifest.yaml
├── workflows/
│   ├── minimax_h3_video.json
│   └── flux_dev_image.json
├── goldfish-reports/       # EGM review artifacts
├── concepts_brief.md       # EGM Phase 1 output
└── design_draft.md         # EGM Phase 2+4 resolved design
```

---

## Limitations

- **Single-user only** — no role-based access control or multi-tenant isolation
- **No cross-session persistence** — models and outputs are lost when the Kaggle session ends (use Kaggle Datasets for persistent storage)
- **~30 hrs/week GPU quota** on Kaggle's free tier; sessions auto-stop after 12 hours of inactivity
- **ngrok free tier** — URL changes on every restart, limited to 40 connections/minute
- **No distributed queue** — single container, single GPU; cannot scale horizontally
- **LLM serving via Ollama** — no vLLM batch inference or continuous batching support yet
- **No streaming responses** — LLM jobs return complete text only after generation finishes

---

## License & Credits

**MIT License** — see [LICENSE](LICENSE) for details.

Built with the **EGM (Elephant-Goldfish Model)** autonomous engineering workflow, featuring parallel stateless reviewers and automated design resolution.

Inspired by community Kaggle notebooks for MiniMax H3 video generation and ComfyUI cloud deployment.