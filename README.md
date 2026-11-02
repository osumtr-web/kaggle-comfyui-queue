# 🚀 Kaggle GPU-as-a-Service

**Turn your free Kaggle 32GB GPU into a remote generation server in 60 seconds.**

ComfyUI (images/video), Ollama (LLMs), or ML models — all accessible via REST API from your laptop, phone, or any device.

---

## What You Get

| Use Case | Engine | Models | Best For |
|----------|--------|--------|----------|
| **🎨 ComfyUI** | ComfyUI | FLUX.1-dev + MiniMax H3 | Image & video generation |
| **🧠 LLM** | Ollama | Llama 3.2 3B | Text generation, chat, coding |
| **📊 ML** | Custom | CLIP + BGE | Classification, embeddings, RAG |

Each option includes: Queue API, Queue Consumer, Watchdog (auto-recovery), and ngrok HTTPS tunnel.

---

## Quick Start

**Just paste this in a Kaggle notebook cell:**

```bash
!git clone https://github.com/osumtr-web/kaggle-comfyui-queue.git /kaggle/working/kaggle-comfyui-queue
!bash /kaggle/working/kaggle-comfyui-queue/install.sh
```

That's it. The installer:
- Asks **one question**: which use case? (ComfyUI / LLM / ML)
- Clones the repo
- Installs all dependencies
- Downloads your models
- Configures auth and ngrok

**Then start services:**

```bash
!bash /kaggle/working/kaggle-comfyui-queue/start.sh
```

Your ngrok URL is retrieved **dynamically** via the ngrok local API (port 4040) — no manual copy-paste needed. The URL also persists to `/kaggle/working/ngrok_url.txt` as a fallback.

---

## Plug-and-Play CLI (`kgpu`)

No more copying passwords or URLs. The `kgpu` command auto-detects everything:

```bash
# Check system health
!bash scripts/kgpu health

# Generate an image
!bash scripts/kgpu generate "a cyberpunk city at sunset, cinematic lighting"

# Generate text (LLM mode)
!bash scripts/kgpu generate "write a haiku about debugging" llm llama3.2:3b

# List all jobs
!bash scripts/kgpu jobs

# Check a specific job
!bash scripts/kgpu job <job_id>

# List installed models
!bash scripts/kgpu models
```

The CLI reads credentials from `config/auth.env` and the ngrok URL from the local API automatically. Zero configuration needed.

### Available Commands

| Command | Description |
|---------|-------------|
| `kgpu health` | System health check |
| `kgpu jobs [status]` | List jobs (optional filter: pending/running/completed) |
| `kgpu job <id>` | Check specific job status |
| `kgpu models` | List installed models |
| `kgpu generate "prompt" [type] [model]` | Submit generation job |

---

## Authentication (One Password Everywhere)

A single strong password is used for **all** services — ComfyUI, ngrok tunnel, Queue API, every endpoint.

| Credential | Default Value | How to Change |
|------------|---------------|---------------|
| Username | `kaggle` | Edit `config/auth.env` |
| Password | `KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479` | Edit `config/auth.env` → commit → push → re-run `install.sh` |
| ngrok Token | Embedded in `config/auth.env` | Edit `config/auth.env` → commit → push → re-run `install.sh` |

**To change your password:** edit `config/auth.env`, `git commit`, `git push`, then re-run `install.sh`. One file controls everything.

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

**Key features:**
- **Persistent job queue** — survives cell re-runs and restarts
- **Auto-recovery watchdog** — restarts crashed services in 5 seconds
- **VRAM safety** — checks GPU memory before dispatching jobs
- **Output rotation** — auto-deletes old files at 80% disk usage
- **Basic auth** — your tunnel is password-protected by default
- **Dynamic URL retrieval** — ngrok URL fetched from local API (port 4040)

---

## Advanced Usage

### Private GitHub Repo

If you forked and made your repo private:

```bash
!bash install.sh --token ghp_YOUR_GITHUB_TOKEN
```

### Non-Interactive Install (for scripts/CI)

```bash
!bash install.sh --case comfyui --non-interactive
```

### Install More Models at Runtime

```bash
!bash scripts/kgpu models  # see what's installed
# Or use the API directly:
curl -u kaggle:KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479 -X POST https://YOUR-URL/api/models/install \
  -H "Content-Type: application/json" \
  -d '{"hf_repo": "stabilityai/stable-diffusion-xl-base-1.0", "model_type": "diffusion_image"}'
```

### Multi-Account Mode

Orchestrate multiple Kaggle accounts for higher throughput:

```bash
# Set as Kaggle Secret
KGPU_MODE=multi
```

---

## API Reference

For direct API access (the `kgpu` CLI wraps these endpoints):

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
    "max_tokens": 256
  }
}
```

### Job Response

```json
{
  "job_id": "uuid-here",
  "status": "pending|running|completed|failed",
  "result_path": "/kaggle/working/output/file.png",
  "created_at": "2026-11-02T...",
  "completed_at": "2026-11-02T..."
}
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| **ngrok returns 401** | Use password from `config/auth.env` (default: `KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479`) |
| **Job stuck in "running"** | Wait 90s for watchdog to recover, or re-run `start.sh` |
| **OOM error** | Model too large — use smaller model or lower resolution |
| **Model install fails** | Check internet; for gated HF models, set `HF_TOKEN` in Kaggle Secrets |
| **Disk full** | Watchdog auto-cleans; manual: `rm /kaggle/working/output/*` |
| **ngrok URL changes** | Free tier limitation — upgrade for static domains |
| **kgpu can't find URL** | Ensure ngrok is running; check `/kaggle/working/ngrok.log` |

---

## File Structure

```
kaggle-comfyui-queue/
├── install.sh          # Interactive installer (pick your use case)
├── start.sh            # Launch selected services
├── QUICKSTART.md       # 2-command quick start
├── config/
│   └── auth.env        # Unified password + ngrok token (edit to change)
├── scripts/
│   ├── kgpu            # Plug-and-play CLI (auto-detects URL + auth)
│   ├── queue_api.py    # REST API (FastAPI)
│   ├── queue_consumer.py # Job dispatcher
│   ├── watchdog.py     # Auto-recovery daemon
│   ├── ngrok_launcher.py # Tunnel with auth + dynamic URL via API
│   └── model_registry.py # Dynamic model installer
├── configs/            # ComfyUI settings, VRAM manifest
└── workflows/          # Pre-approved ComfyUI workflows
```

---

## Limitations

- **Single-user tool** — no multi-tenant RBAC or user management
- **~30 hrs/week GPU quota** on Kaggle free tier
- **12-hour session timeout** — notebook auto-stops after inactivity
- **ngrok free tier** — URL changes on restart, 40 connections/min
- **No streaming** — LLM jobs return complete text only

---

## License

MIT — see [LICENSE](LICENSE) for details.

Built with the [EGM (Elephant-Goldfish Model)](https://github.com/egm) autonomous engineering workflow.