# 🚀 Kaggle GPU-as-a-Service — Playbook

**Turn your free Kaggle 32GB GPU into a remote generation server in 60 seconds.**

> 📖 For architecture details, API reference, troubleshooting, and advanced configuration, see [DOCS.md](DOCS.md).

---

## Setup (2 commands)

Paste into a Kaggle notebook cell:

```bash
!git clone https://github.com/osumtr-web/kaggle-comfyui-queue.git /kaggle/working/kaggle-comfyui-queue
!bash /kaggle/working/kaggle-comfyui-queue/install.sh
```

Pick your use case when prompted:

| # | Use Case | What's Included |
|---|----------|-----------------|
| 1 | 🎨 **ComfyUI** — Images & Video | FLUX.1-dev + MiniMax H3 |
| 2 | 🧠 **LLM** — Text Generation | Llama 3.2 3B (Ollama) |
| 3 | 📊 **ML** — Classification | CLIP + BGE embeddings |

Then start everything:

```bash
!bash /kaggle/working/kaggle-comfyui-queue/start.sh
```

Your ngrok URL appears automatically. You're live. ✅

---

## Daily Usage (`kgpu` CLI)

The `kgpu` command handles auth and URL detection for you. No passwords to copy.

### Check Health

```bash
!bash scripts/kgpu health
```

### Generate an Image

```bash
!bash scripts/kgpu generate "a cyberpunk city at sunset, cinematic lighting"
```

### Generate Text (LLM mode)

```bash
!bash scripts/kgpu generate "explain quantum computing simply" llm llama3.2:3b
```

### Track Jobs

```bash
!bash scripts/kgpu jobs              # all jobs
!bash scripts/kgpu jobs pending      # filter by status
!bash scripts/kgpu job <job_id>      # specific job
```

### Pull Generated Artifacts

Download images, videos, audio, or any generated file directly:

```bash
!bash scripts/kgpu pull 42                    # auto-names from server (e.g., flux_001.png)
!bash scripts/kgpu pull 42 my_image.png       # custom output filename
!bash scripts/kgpu fetch 42 ./outputs/        # alias for pull, download to directory
```

The CLI checks job completion status automatically before downloading. For text-only LLM jobs without a file artifact, use `kgpu job <id>` to view the inline result.

### List Models

```bash
!bash scripts/kgpu models
```

---

## Changing Your Password

Edit one file, push, re-run:

```bash
# On your laptop:
nano config/auth.env          # change KGPU_PASSWORD line
git add config/auth.env && git commit -m "update password" && git push

# In Kaggle:
!bash install.sh              # picks up new password automatically
```

Default password format: `KAG-<UUID>` (e.g., `KAG-f47ac10b-58cc-4372-a567-0e02b2c3d479`)

---

## Quick Reference

| Task | Command |
|------|---------|
| Install | `!bash install.sh` |
| Start services | `!bash start.sh` |
| Health check | `!bash scripts/kgpu health` |
| Generate image | `!bash scripts/kgpu generate "prompt"` |
| Generate text | `!bash scripts/kgpu generate "prompt" llm llama3.2:3b` |
| List jobs | `!bash scripts/kgpu jobs` |
| Check job | `!bash scripts/kgpu job <id>` |
| List models | `!bash scripts/kgpu models` |
| Pull artifact | `!bash scripts/kgpu pull <job_id> [output]` |
| Change password | Edit `config/auth.env` → push → re-run `install.sh` |
| Reconfigure | `!rm .kgpu_installed && !bash install.sh` |

---

## Private Repo?

```bash
!bash install.sh --token ghp_YOUR_GITHUB_TOKEN
```

## Non-Interactive (CI / Scripts)

```bash
!bash install.sh --case comfyui --non-interactive
!bash start.sh
```

---

## Need More Details?

→ **[DOCS.md](DOCS.md)** — Architecture, API reference, watchdog behavior, VRAM safety, troubleshooting, file structure, limitations.

---

MIT License · Built with [EGM](https://github.com/egm) autonomous engineering workflow