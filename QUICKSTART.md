# 🚀 Kaggle GPU-as-a-Service — Quick Start

## 2 Commands. Done.

Paste into a Kaggle notebook cell:

```bash
# Step 1: Clone & install (pick your use case)
!git clone https://github.com/osumtr-web/kaggle-comfyui-queue.git /kaggle/working/kaggle-comfyui-queue
!bash /kaggle/working/kaggle-comfyui-queue/install.sh

# Step 2: Start services
!bash /kaggle/working/kaggle-comfyui-queue/start.sh
```

## What You'll See

The installer asks **one question**:

| Pick | Use Case | Pre-installed |
|------|----------|---------------|
| **1** | 🎨 ComfyUI — Image & Video | FLUX.1-dev + MiniMax H3 |
| **2** | 🧠 LLM — Text Generation | Llama 3.2 3B (Ollama) |
| **3** | 📊 ML — Classification & Embeddings | CLIP + BGE |

Everything else is automatic: components, auth, ngrok, directories, model downloads.

After `start.sh`, your ngrok URL appears. Use it from anywhere:

```bash
curl -u kaggle:MyKagglePass2026! https://YOUR-URL.ngrok-free.app/api/health
```

## Private Repo? Add Your Token

```bash
!bash install.sh --token ghp_YOUR_GITHUB_TOKEN
```

Or paste when prompted interactively.

## Non-Interactive (CI / scripted)

```bash
!bash install.sh --case comfyui --non-interactive
!bash start.sh
```

## Auth Defaults (pre-configured)

| Secret | Default Value | Override |
|--------|--------------|----------|
| `NGROK_AUTHTOKEN` | Embedded in install.sh | Set Kaggle Secret or paste at prompt |
| `COMFYUI_AUTH_PASSWORD` | `MyKagglePass2026!` | Set Kaggle Secret or paste at prompt |

Kaggle Secrets take priority over embedded defaults.

## That's It

No secrets to configure upfront. No component toggles. No model selection menus.
Just pick your use case and go. 🎉