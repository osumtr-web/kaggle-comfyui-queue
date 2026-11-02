# 🚀 Quick Start — 2 Commands + Plug-and-Play CLI

## Step 1: Install (pick your use case)

```bash
!git clone https://github.com/osumtr-web/kaggle-comfyui-queue.git /kaggle/working/kaggle-comfyui-queue
!bash /kaggle/working/kaggle-comfyui-queue/install.sh
```

The installer asks **one question**:

| Pick | Use Case | Pre-installed Models |
|------|----------|---------------------|
| **1** | 🎨 ComfyUI — Image & Video | FLUX.1-dev + MiniMax H3 |
| **2** | 🧠 LLM — Text Generation | Llama 3.2 3B (Ollama) |
| **3** | 📊 ML — Classification & Embeddings | CLIP + BGE |

Everything else is automatic: dependencies, auth, ngrok, directories, model downloads.

## Step 2: Start services

```bash
!bash /kaggle/working/kaggle-comfyui-queue/start.sh
```

Your ngrok URL is retrieved **dynamically** via the ngrok local API (port 4040) — no manual copy-paste needed.

---

## Step 3: Use the `kgpu` CLI (zero config)

No passwords. No URLs. Everything auto-detected.

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

### All Available Commands

| Command | Description |
|---------|-------------|
| `kgpu health` | System health check |
| `kgpu jobs [status]` | List jobs (optional: pending/running/completed) |
| `kgpu job <id>` | Check specific job status |
| `kgpu models` | List installed models |
| `kgpu generate "prompt" [type] [model]` | Submit generation job |

---

## Auth (One Password Everywhere)

A single strong password is used for **all** services — ComfyUI, ngrok tunnel, Queue API, every endpoint.

| Credential | Default Value | How to Change |
|------------|---------------|---------------|
| Username | `kaggle` | Edit `config/auth.env` |
| Password | `KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479` | Edit `config/auth.env` → commit → push → re-run `install.sh` |
| ngrok Token | Embedded in `config/auth.env` | Edit `config/auth.env` → commit → push → re-run `install.sh` |

**To change your password:** edit `config/auth.env`, `git commit`, `git push`, then re-run `install.sh`. One file controls everything.

---

## Private Repo?

```bash
!bash install.sh --token ghp_YOUR_GITHUB_TOKEN
```

Or paste when prompted interactively.

## Non-Interactive (CI / Scripts)

```bash
!bash install.sh --case comfyui --non-interactive
!bash start.sh
```

## Change Your Setup Later

Delete the install flag and re-run:

```bash
!rm /kaggle/working/.kgpu_installed
!bash /kaggle/working/kaggle-comfyui-queue/install.sh
```

Your previous choices are saved in `/kaggle/working/.kgpu_choices` — `start.sh` reads this file to launch only what you selected.