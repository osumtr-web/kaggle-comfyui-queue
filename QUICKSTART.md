# 🚀 Quick Start — 2 Commands

## Paste into a Kaggle notebook cell:

```bash
# Step 1: Install (pick your use case)
!git clone https://github.com/osumtr-web/kaggle-comfyui-queue.git /kaggle/working/kaggle-comfyui-queue
!bash /kaggle/working/kaggle-comfyui-queue/install.sh

# Step 2: Start services
!bash /kaggle/working/kaggle-comfyui-queue/start.sh
```

**That's it.** Your ngrok URL appears after `start.sh` completes.

---

## What You'll See

The installer asks **one question**:

| Pick | Use Case | Pre-installed Models |
|------|----------|---------------------|
| **1** | 🎨 ComfyUI — Image & Video | FLUX.1-dev + MiniMax H3 |
| **2** | 🧠 LLM — Text Generation | Llama 3.2 3B (Ollama) |
| **3** | 📊 ML — Classification & Embeddings | CLIP + BGE |

Everything else is automatic: dependencies, auth, ngrok, directories, model downloads.

---

## Auth (Pre-configured)

No secrets to set up. Defaults are embedded:

| Credential | Value | Override |
|------------|-------|----------|
| Username | `kaggle` | — |
| Password | `MyKagglePass2026!` | Set Kaggle Secret `COMFYUI_AUTH_PASSWORD` |
| ngrok Token | Embedded | Set Kaggle Secret `NGROK_AUTHTOKEN` |

Kaggle Secrets override embedded defaults automatically.

---

## Test Your Server

```bash
curl -u kaggle:MyKagglePass2026! https://YOUR-URL.ngrok-free.app/api/health
```

Expected response:
```json
{"comfyui": "running", "queue_api": "running", "consumer": "running", "ngrok": "running"}
```

---

## Private Repo?

```bash
!bash install.sh --token ghp_YOUR_GITHUB_TOKEN
```

Or paste when prompted interactively.

---

## Non-Interactive (CI / Scripts)

```bash
!bash install.sh --case comfyui --non-interactive
!bash start.sh
```

---

## Change Your Setup Later

Delete the install flag and re-run:

```bash
!rm /kaggle/working/.kgpu_installed
!bash /kaggle/working/kaggle-comfyui-queue/install.sh
```

Your previous choices are saved in `/kaggle/working/.kgpu_choices` — `start.sh` reads this file to launch only what you selected.