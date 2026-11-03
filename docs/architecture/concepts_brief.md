# Concepts Brief: Kaggle ComfyUI GPU-as-a-Service with Queue & Watchdog

## 1. Goal
Build a self-contained, reproducible Kaggle notebook that deploys ComfyUI as a persistent GPU service with:
- Public access via LocalTunnel (no ngrok token required)
- Automatic watchdog restart on crash
- Job queue for image/video generation requests
- Proven model stack (Moonmaster Krea2 Turbo + ComfyUI-Manager)

## 2. Constraints
- **Platform**: Kaggle Notebooks (free tier, 2x Tesla T4, ~30GB VRAM, 30h/week GPU quota)
- **No external auth**: LocalTunnel over ngrok to avoid token management
- **Zero indentation errors**: Notebook must be generated programmatically via `nbformat`
- **Model size**: Must fit in Kaggle's ephemeral storage (~20GB usable after system)
- **Startup time**: <15 minutes from kernel start to public URL

## 3. Core Components

### 3.1 Service Layer
- **ComfyUI**: Latest stable from `comfyanonymous/ComfyUI`
- **Model**: Moonmaster Krea2 Turbo (`civitai.com/api/download/models/3355423`)
- **Extensions**: ComfyUI-Manager for node management
- **Port**: 8188 (standard ComfyUI)

### 3.2 Tunnel Layer
- **Tool**: `localtunnel` (npm package, no auth)
- **IP Verification**: `ipv4.icanhazip.com` for interstitial bypass
- **Persistence**: Runs in same cell as ComfyUI via `&` backgrounding

### 3.3 Watchdog Layer
- **Mechanism**: Shell-level process monitoring (not Python threading)
- **Restart policy**: Immediate restart on non-zero exit
- **Logging**: Stdout/stderr captured in Kaggle cell output

### 3.4 Queue Layer (Phase 2)
- **Type**: In-memory FIFO (Python `queue.Queue`)
- **API**: REST endpoint `/api/jobs` for submit/status/pull
- **Concurrency**: Single-worker (Kaggle T4 cannot parallelize SDXL)
- **Artifact storage**: `/kaggle/working/output/` with job-ID naming

## 4. Non-Goals (YAGNI)
- Multi-GPU scaling (Kaggle free tier limits to 2x T4)
- Persistent storage across sessions (Kaggle resets VMs)
- User authentication (public tunnel is intentional for personal use)
- Video generation in Phase 1 (image-only first, video adds 3x complexity)

## 5. Success Criteria
- [ ] Notebook runs without indentation or syntax errors
- [ ] Public URL appears in cell output within 15 minutes
- [ ] ComfyUI UI loads and generates an image with Moonmaster model
- [ ] Killing `python main.py` triggers automatic restart within 10 seconds
- [ ] Queue accepts 3+ jobs and processes them sequentially without loss

## 6. Risks & Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| CivitAI download blocked | High | Mirror model to HuggingFace fallback |
| LocalTunnel rate-limited | Medium | Cache IP verification; retry with exponential backoff |
| Kaggle OOM during model load | High | Use fp16 weights; disable xformers if needed |
| Notebook JSON corruption | Critical | Generate exclusively via `nbformat`; never hand-edit |
| Queue lost on kernel restart | Low | Document ephemeral nature; add SQLite persistence in Phase 2 |

## 7. Open Questions
1. Should the queue API be Flask/FastAPI or pure ComfyUI custom node?
2. Can we pre-cache the model in a Kaggle Dataset to skip download?
3. Is there a way to persist LocalTunnel subdomain across restarts?

## 8. Implementation Tasks (for Phase 5)
- [ ] Generate base notebook via `nbformat` with 6 cells (clone, model, manager, tunnel, ip, run)
- [ ] Add watchdog shell wrapper script (`watchdog.sh`)
- [ ] Implement `/api/jobs` REST endpoint as ComfyUI custom node
- [ ] Write integration test: submit job → wait → pull artifact → verify PNG
- [ ] Document setup in `README.md` with troubleshooting section