# Kaggle GPU-as-a-Service Platform

Turn Kaggle's free 32GB GPU into a remote generation server for any HuggingFace model — image, video, LLM, audio, ML — with job queue, watchdog auto-recovery, and ngrok tunnel access.

---

## What This Is

Kaggle is used as a **transparent GPU hardware supplier**. You sign up for a free Kaggle account, run a single notebook, and gain full remote control over installation, git deployment, and service orchestration on a 32GB NVIDIA T4 x2 GPU instance.

This platform supports **ANY HuggingFace model** via a dynamic model registry — not just a curated set. Models are installed on-demand from HuggingFace Hub at runtime:

- **ComfyUI** for diffusion models (image and video generation)
- **Ollama** for large language models (text generation, chat, completion)
- **Extensible custom engines** for audio, classifiers, embeddings, and experimental model types

Key operational features:

- **Persistent SQLite job queue** that survives notebook cell re-runs and process restarts
- **Watchdog daemon** that auto-restarts crashed services within 5 seconds using exponential backoff
- **ngrok HTTPS tunnel** with mandatory basic auth for secure remote access from any device
- **VRAM safety protocol** with two-stage memory checks and automatic OOM recovery
- **Output rotation** that auto-deletes oldest files when disk exceeds 80% or 25GB

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                  KAGGLE NOTEBOOK CELL                    │
│                                                         │
│  ┌─────────────┐    ┌──────────────┐   ┌─────────────┐ │
│  │  ComfyUI     │◄──►│  Queue API   │   │   Watchdog  │ │
│  │  Server      │    │  (FastAPI)   │   │   Daemon    │ │
│  │  :8188       │    │  :8189       │   │             │ │
│  └──────┬───────┘    └──────┬───────┘   └──────┬──────┘ │
│         │                   │                  │        │
│         │              ┌────▼────┐             │        │
│         │              │ SQLite  │◄────────────┘        │
│         │              │ Queue DB│   (health checks)    │
│         │              └─────────┘                      │
│         │                                               │
│  ┌──────▼───────┐                                      │
│  │   ngrok      │ ──► HTTPS tunnel to :8188            │
│  │   Tunnel     │                                      │
│  └──────────────┘                                      │
│                                                         │
│  /kaggle/working/models/  (persistent model cache)      │
│  /kaggle/working/queue.db (persistent job queue)        │
│  /kaggle/working/watchdog.log                           │
└─────────────────────────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| **Kaggle Account(s)** | Phone-verified account(s) with active GPU quota (~30 hrs/week each on T4 x2) |
| **ngrok Account** | Free or paid account — see setup below |
| **ComfyUI Auth** | Password saved as Kaggle Secret `COMFYUI_AUTH_PASSWORD` |
| **Skills** | Basic familiarity with Python, bash, and REST APIs |

### ngrok Setup (Required)

ngrok creates the HTTPS tunnel from Kaggle's isolated container to your laptop/API client. Without it, you cannot access the service remotely.

1. **Create a free account** at https://dashboard.ngrok.com/signup
2. **Get your authtoken** at https://dashboard.ngrok.com/get-started/your-authtoken
3. **Save it in Kaggle**: Open your notebook → **Add-ons → Secrets** → Add secret named `NGROK_AUTHTOKEN` with your token value
4. **(Optional) Connect via API**: If you want programmatic tunnel management instead of CLI:
   ```bash
   # Install ngrok Python SDK
   pip install pyngrok
   
   # Use in code (authtoken auto-reads from NGROK_AUTHTOKEN env var)
   from pyngrok import ngrok
   tunnel = ngrok.connect(8188, auth="kaggle:YOUR_PASSWORD")
   print(tunnel.public_url)
   ```
   The platform uses the CLI binary by default (more reliable in Kaggle), but the SDK works for custom integrations.

**Free tier limits:** 1 agent, random subdomain (URL changes on restart), 40 connections/min. Upgrade to Personal ($8/mo) for static domains and higher limits if running production workloads.

### Multi-Account Kaggle Mode

This platform supports two deployment modes (default: **single**):

| Mode | Description | Use Case |
|------|-------------|----------|
| **Single** (default) | One Kaggle account, one GPU instance, one ngrok tunnel | Personal use, testing, single-user generation |
| **Multi** | Multiple Kaggle accounts orchestrated from a central coordinator | Team/shared GPU pool, higher throughput, redundancy |

To enable multi-mode, set `KGPU_MODE=multi` as a Kaggle Secret. See the [Multi-Account Guide](#multi-account-setup) below.

📖 [Kaggle Secrets Documentation](https://www.kaggle.com/docs/secrets)

---

## Quick Start (2 commands)

Paste into a Kaggle notebook cell and run:

```bash
# Step 1: Install (pick your use case interactively)
!git clone https://github.com/osumtr-web/kaggle-comfyui-queue.git /kaggle/working/kaggle-comfyui-queue
!bash /kaggle/working/kaggle-comfyui-queue/install.sh

# Step 2: Start services
!bash /kaggle/working/kaggle-comfyui-queue/start.sh
```

The installer asks **one question**: pick your use case:

| Option | What You Get | Pre-installed Models |
|--------|-------------|---------------------|
| **1) 🎨 ComfyUI** | Image & video generation | FLUX.1-dev + MiniMax H3 |
| **2) 🧠 LLM** | Text generation & chat | Llama 3.2 3B via Ollama |
| **3) 📊 ML** | Classification & embeddings | CLIP + BGE (add more via API) |

Everything else (components, auth, ngrok, directories) is pre-configured. Your ngrok URL appears after `start.sh` completes. Authenticate with `kaggle` / your password.

### Non-interactive / scripted install

```bash
# Skip all prompts, use defaults (ComfyUI mode)
!bash install.sh --non-interactive

# Specify use case + private repo token
!bash install.sh --case llm --token ghp_YOUR_GITHUB_TOKEN
```

---

## Supported Model Types

| `model_type` | Engine | Example Models | Use Case |
|---|---|---|---|
| `diffusion_image` | ComfyUI | FLUX.1-dev, SDXL, SD3 | Text-to-image generation |
| `diffusion_video` | ComfyUI | MiniMax H3, LTX-Video | Text/image-to-video generation |
| `llm` | Ollama | Llama 3, Qwen2, Mistral | Text generation, chat, completion |
| `audio` | ComfyUI | AudioLDM, MusicGen | Text-to-audio/music generation |
| `ml_classifier` | Custom | CLIP, BLIP | Image classification, embedding |
| `embedding` | Custom | BGE, E5 | Text embeddings, RAG |
| `other` | Custom | Any HF model | Experimental / unsupported types |

---

## API Reference

All endpoints require HTTP Basic Auth: `-u kaggle:YOUR_PASSWORD`

Base URL: your ngrok HTTPS endpoint (e.g., `https://abc123.ngrok-free.app`)

### Job Queue Endpoints

#### Submit a Job

```bash
curl -u kaggle:YOUR_PASSWORD -X POST https://URL/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "black-forest-labs/FLUX.1-dev",
    "model_type": "diffusion_image",
    "prompt": "A cyberpunk city at sunset, cinematic lighting",
    "params": {"width": 1024, "height": 1024, "steps": 28}
  }'
```

Response: `{"job_id": "uuid-here", "status": "pending"}`

#### Check Job Status

```bash
curl -u kaggle:YOUR_PASSWORD https://URL/api/jobs/{job_id}
```

Response: `{"job_id": "...", "status": "completed", "result_path": "/kaggle/working/output/...", "created_at": "...", "completed_at": "..."}`

#### List Jobs

```bash
# All jobs
curl -u kaggle:YOUR_PASSWORD https://URL/api/jobs

# Filter by status
curl -u kaggle:YOUR_PASSWORD "https://URL/api/jobs?status=pending"
curl -u kaggle:YOUR_PASSWORD "https://URL/api/jobs?status=running"
curl -u kaggle:YOUR_PASSWORD "https://URL/api/jobs?status=completed"
```

#### Cancel a Job

```bash
curl -u kaggle:YOUR_PASSWORD -X DELETE https://URL/api/jobs/{job_id}
```

#### System Health

```bash
curl -u kaggle:YOUR_PASSWORD https://URL/api/health
```

Response: `{"comfyui": "running", "queue_api": "running", "consumer": "running", "ngrok": "running", "ollama": "stopped", "disk_usage_pct": 42, "vram_free_mb": 28000}`

### Model Registry Endpoints

#### List Installed Models

```bash
curl -u kaggle:YOUR_PASSWORD https://URL/api/models
```

#### Get Model Info

```bash
curl -u kaggle:YOUR_PASSWORD https://URL/api/models/black-forest-labs%2FFLUX.1-dev
```

#### Install a Model from HuggingFace

```bash
curl -u kaggle:YOUR_PASSWORD -X POST https://URL/api/models/install \
  -H "Content-Type: application/json" \
  -d '{"hf_repo": "black-forest-labs/FLUX.1-dev", "model_type": "diffusion_image"}'
```

#### Uninstall a Model

```bash
curl -u kaggle:YOUR_PASSWORD -X DELETE https://URL/api/models/black-forest-labs%2FFLUX.1-dev
```

#### Check Install Progress

```bash
curl -u kaggle:YOUR_PASSWORD https://URL/api/models/black-forest-labs%2FFLUX.1-dev/status
```

Response: `{"model_id": "...", "status": "downloading", "progress_pct": 67, "bytes_downloaded": 8200000000, "bytes_total": 12200000000}`

---

## Dynamic Model Installation

Install **any** HuggingFace model at runtime without modifying code or restarting services.

### Install an LLM (GGUF via Ollama)

```bash
curl -u kaggle:PASS -X POST https://URL/api/models/install \
  -H "Content-Type: application/json" \
  -d '{
    "hf_repo": "bartowski/Llama-3.2-3B-Instruct-GGUF",
    "filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    "model_type": "llm"
  }'
```

### Install a Video Generation Model

```bash
curl -u kaggle:PASS -X POST https://URL/api/models/install \
  -H "Content-Type: application/json" \
  -d '{
    "hf_repo": "MiniMax/H3",
    "model_type": "diffusion_video"
  }'
```

### Install an Image Generation Model

```bash
curl -u kaggle:PASS -X POST https://URL/api/models/install \
  -H "Content-Type: application/json" \
  -d '{
    "hf_repo": "stabilityai/stable-diffusion-xl-base-1.0",
    "model_type": "diffusion_image"
  }'
```

### Monitor Installation Progress

```bash
curl -u kaggle:PASS https://URL/api/models/bartowski%2FLlama-3.2-3B-Instruct-GGUF/status
```

The model registry automatically routes downloads to the correct directory based on `model_type`:
- `diffusion_*` → `/kaggle/working/ComfyUI/models/`
- `llm` → Ollama model store
- `audio`, `ml_classifier`, `embedding`, `other` → `/kaggle/working/models/`

---

## Submitting Generation Jobs

### Image Generation (FLUX.1-dev)

```bash
curl -u kaggle:PASS -X POST https://URL/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "black-forest-labs/FLUX.1-dev",
    "model_type": "diffusion_image",
    "prompt": "A serene Japanese garden with cherry blossoms reflected in a koi pond, golden hour lighting, photorealistic",
    "params": {
      "width": 1024,
      "height": 1024,
      "steps": 28,
      "cfg_scale": 3.5,
      "seed": -1
    }
  }'
```

Expected response:
```json
{"job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "status": "pending"}
```

### Video Generation (MiniMax H3)

```bash
curl -u kaggle:PASS -X POST https://URL/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "MiniMax/H3",
    "model_type": "diffusion_video",
    "prompt": "A timelapse of a blooming lotus flower in slow motion, macro lens, soft natural light",
    "params": {
      "frames": 48,
      "fps": 12,
      "resolution": "720p"
    }
  }'
```

### LLM Text Generation

```bash
curl -u kaggle:PASS -X POST https://URL/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "bartowski/Llama-3.2-3B-Instruct-GGUF",
    "model_type": "llm",
    "prompt": "Write a haiku about debugging code at 3am",
    "params": {
      "max_tokens": 256,
      "temperature": 0.7,
      "system_prompt": "You are a helpful assistant."
    }
  }'
```

Check results with `GET /api/jobs/{job_id}` — completed jobs include `result_path` for file outputs or `result_text` for LLM responses.

---

## Security Model

All endpoints enforce **mandatory HTTP Basic Authentication** via both ngrok tunnel and direct API access. There is no unauthenticated surface.

| Threat | Mitigation |
|--------|-----------|
| Unauthorized access to ngrok tunnel | Basic auth enforced at ngrok level; credentials required before any content loads |
| Arbitrary workflow execution | Workflow allowlist restricts ComfyUI to pre-approved JSON workflows only |
| Credential leakage | Passwords stored as Kaggle Secrets, never hardcoded; ngrok authtoken injected at runtime |
| Multi-user privilege escalation | N/A — single-user personal tool with no RBAC |
| Model supply chain attack | Models downloaded directly from HuggingFace Hub; SHA256 verification available in registry |
| Disk exhaustion DoS | Watchdog auto-rotates outputs at 80% disk or 25GB threshold |

> ⚠️ **This is a single-user personal tool.** There is no multi-user RBAC, audit logging, or tenant isolation. Do not expose to untrusted users.

---

## Watchdog & Auto-Recovery

The watchdog daemon runs continuously and monitors all critical processes:

| Monitored Process | Restart Behavior |
|-------------------|-----------------|
| ComfyUI Server | Immediate restart on crash, max 5 retries |
| ngrok Tunnel | Immediate restart on disconnect |
| Queue API (FastAPI) | Immediate restart on crash |
| Queue Consumer | Immediate restart on crash |
| Ollama | Lazy-started on first LLM job; restarted if crashed during serving |

**Restart backoff strategy:** 5s → 10s → 20s → 30s cap, maximum 5 consecutive retries per process. Resets on successful health check.

**Output rotation:** Automatically deletes oldest files in `/kaggle/working/output/` when disk usage exceeds 80% or total output size exceeds 25GB.

**Orphaned job recovery:** Jobs stuck in `running` status are reset to `pending` after 90 seconds of heartbeat timeout, ensuring no job is permanently lost due to consumer crashes.

**Graceful shutdown:** On SIGTERM, the watchdog persists current state to SQLite and stops all managed processes cleanly.

---

## VRAM Safety Protocol

GPU memory is managed through a two-stage safety system:

1. **Static estimation:** Before dispatching a job, the consumer consults `model_vram_manifest.yaml` for pre-computed VRAM requirements based on model type, precision, and quantization level.

2. **Runtime verification:** Immediately before execution, `nvidia-smi` is queried for actual free VRAM. Jobs are queued if available memory is below the estimated requirement + 2GB safety margin.

**OOM recovery:** If a generation fails with CUDA out-of-memory, the consumer automatically retries once with reduced parameters (lower resolution or fewer steps). Persistent OOM marks the job as failed with diagnostic info.

**RAM monitoring:** System RAM is monitored at 90% threshold; model loading is paused if host memory is critically low.

**Model-specific heuristics:**

| Format | VRAM Estimate | Notes |
|--------|--------------|-------|
| fp16 (diffusion) | Model size × 1.2 | Standard half-precision |
| GGUF Q4_K_M | Model size × 0.35 | 4-bit quantized LLM |
| GGUF Q8_0 | Model size × 0.6 | 8-bit quantized LLM |
| ONNX Runtime | Model size × 1.1 | CPU/GPU hybrid inference |

---

## File Structure

```
kaggle-comfyui-queue/
├── setup.ipynb              # One-click entry point (6 cells)
├── scripts/
│   ├── setup.sh             # Idempotent install (also inlined in notebook)
│   ├── queue_api.py         # FastAPI REST API (:8189)
│   ├── queue_consumer.py    # Job dispatcher (ComfyUI + Ollama)
│   ├── watchdog.py          # Process monitor + output rotation
│   ├── ngrok_launcher.py    # Tunnel with basic auth
│   └── model_registry.py    # Dynamic HF model management
├── configs/
│   ├── comfyui_settings.yaml
│   └── model_vram_manifest.yaml
├── workflows/
│   ├── minimax_h3_video.json
│   └── flux_dev_image.json
├── goldfish-reports/        # EGM review artifacts
├── concepts_brief.md        # EGM Phase 1 output
└── design_draft.md          # EGM Phase 2+4 resolved design
```

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| ngrok URL returns 401 | Wrong password | Verify `COMFYUI_AUTH_PASSWORD` secret matches your browser input |
| Job stuck in "running" | Consumer crashed mid-job | Wait 90s for heartbeat recovery, or restart Cell 4 |
| OOM during generation | Model too large for T4 | Use quantized model (Q4_K_M) or lower resolution |
| Model install fails 401 | Gated HuggingFace model | Set `HF_TOKEN` env var in Kaggle Secrets |
| Disk full | Too many accumulated outputs | Watchdog auto-cleans; manual: `rm /kaggle/working/output/*` |
| ComfyUI won't start | Port 8188 already in use | Kill existing process: `fuser -k 8188/tcp` |
| Ollama not responding | First LLM job triggers lazy start | Wait ~30s for initial model load |
| ngrok URL changes every restart | Free tier limitation | Upgrade to ngrok paid plan for static domains |
| Job fails with "workflow not allowed" | Workflow JSON not in allowlist | Add workflow to `workflows/` directory and restart consumer |

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