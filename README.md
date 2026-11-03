# Kaggle ComfyUI GPU-as-a-Service

Self-contained Kaggle notebook deploying ComfyUI as a persistent GPU service with automatic crash recovery via async watchdog, public access via ngrok, and a REST job queue with SQLite persistence for image/video generation on free Tesla T4 GPUs.

## Quick Start

### Prerequisites

1. **Kaggle Account**: Create at [kaggle.com](https://www.kaggle.com), enable GPU access (Settings → Phone Verification).
2. **Kaggle CLI**: `pip install kaggle`. Download API token from [kaggle.com/settings/account](https://www.kaggle.com/settings/account) → "Create New Token". Place `kaggle.json` at `~/.kaggle/kaggle.json` with `chmod 600 ~/.kaggle/kaggle.json`.
3. **Verify**: `kaggle kernels list --mine` should return your kernels without auth errors.
4. **Python 3.10+** with `nbformat`: `pip install nbformat` (for notebook generation only).

### Deploy

1. **Generate the notebook** (do not hand-edit `.ipynb` files):
   ```bash
   python scripts/generate_notebook.py
   ```
2. **Push to Kaggle**:
   ```bash
   cd ultimate_comfyui
   kaggle kernels push
   ```
3. **Wait for startup** (≤15 min). Check logs for the ngrok URL and auth credentials:
   ```bash
   kaggle kernels logs -u <your-username>/ultimate-comfyui
   ```
4. **Open the ngrok URL**. Authenticate with HTTP Basic Auth using credentials printed in startup logs (`kaggle:<password>`).

### Submit Jobs

```bash
# Set tunnel URL and credentials
export KGPU_URL="https://xxxx.ngrok-free.app"
export KGPU_USER="kaggle"
export KGPU_PASS="<password from logs>"

# Submit a job
kgpu submit --prompt "a photo of a cat" --width 1024 --height 1024

# Poll status
kgpu status <job_id>

# Wait and download
kgpu wait <job_id> && kgpu download <job_id> -o output.png
```

## Architecture

| Component | Path | Purpose |
|-----------|------|---------|
| Async Watchdog | `scripts/watchdog.py` | Manages 4 processes (ComfyUI, ngrok, consumer, API) with health checks, exponential backoff, PID persistence |
| Queue API | `scripts/queue_api.py` | FastAPI REST server on port 8189 with HTTP Basic Auth, SQLite WAL persistence |
| Queue Consumer | `scripts/queue_consumer.py` | Async worker with VRAM/RAM pre-checks, heartbeat orphan recovery, retry classification |
| ngrok Launcher | `scripts/ngrok_launcher.py` | Binary management, URL extraction, stable subdomain support |
| Model Registry | `scripts/model_registry.py` | Dynamic HuggingFace download, VRAM estimation, type classification |
| Notebook Generator | `scripts/generate_notebook.py` | Programmatic `.ipynb` generation via `nbformat` wrapping shell scripts |
| Install Script | `install.sh` | Idempotent setup: clones ComfyUI at pinned commit, downloads models, creates directories |
| Start Script | `start.sh` | Service launcher with stale process cleanup, readiness probes, ngrok URL discovery |

See `docs/architecture/design_draft.md` for full component specifications, data models, risk register, and glossary.

## Queue API

All endpoints except `/api/health` require HTTP Basic Auth (`kaggle:<password>`).

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/jobs` | Yes | Submit generation job (`{"workflow": {...}, "params": {...}}`) |
| GET | `/api/jobs` | Yes | List all jobs with status |
| GET | `/api/jobs/{id}` | Yes | Get single job status and metadata |
| GET | `/api/jobs/{id}/artifact` | Yes | Download generated image/video |
| GET | `/api/health` | No | Health metrics (queue depth, GPU status, disk usage) |
| GET | `/api/models` | Yes | List installed models |
| POST | `/api/models/install` | Yes | Install model from HuggingFace |

Job statuses: `pending` → `running` → `completed` | `failed`

## Testing

```bash
# Static integration tests (no Kaggle runtime required)
source .venv/bin/activate
python tests/test_integration.py

# Live integration tests (requires running deployment)
export KGPU_URL="https://xxxx.ngrok-free.app"
export KGPU_USER="kaggle"
export KGPU_PASS="<password>"
pytest tests/test_live_integration.py -v --timeout=600
```

## Troubleshooting

### ngrok Setup & Authentication

**Problem**: ngrok URL not appearing in logs after 15 minutes.
- Verify `NGROK_AUTHTOKEN` is set as a Kaggle Secret (Settings → Secrets → Add Secret).
- Check `kaggle kernels logs` for `ngrok_launcher.py` errors.
- Fallback: Cloudflare Tunnel activates automatically if ngrok fails.

**Problem**: 401 Unauthorized when accessing API.
- Credentials are generated at startup and printed **once** in kernel logs. Search logs for `COMFYUI_AUTH_PASSWORD`.
- Username is always `kaggle`. Password is randomly generated per session.
- If you missed the password, restart the kernel to regenerate.

**Problem**: ngrok subdomain changes unexpectedly.
- Free tier assigns random subdomains. Configure a stable subdomain in your [ngrok dashboard](https://dashboard.ngrok.com) (paid plan).
- After any kernel restart, re-copy the URL from logs.

### GPU OOM Recovery

**Symptom**: ComfyUI crashes during model loading; watchdog logs show `oom_during_load` or exit code 137.

**Automatic recovery**: The watchdog detects OOM kills via `dmesg` and exit code analysis, then restarts ComfyUI with `--lowvram` flag enabled. This reduces peak VRAM by offloading layers to CPU RAM at the cost of 3–5× slower inference.

**Manual intervention**: If auto-recovery fails repeatedly:
1. Reduce image resolution in workflow params (e.g., 512×512 instead of 1024×1024).
2. Use SDXL-Turbo (4 steps) instead of full SDXL (20+ steps) to reduce peak memory.
3. Check `/kaggle/working/.watchdog_health.json` for restart counts and failure reasons.

**T4 bfloat16 incompatibility**: NVIDIA T4 does NOT support bfloat16. All models load in FP16 (`torch.float16`). The watchdog enforces `--fp16-vae` by default. Do not remove this flag.

### Idle Timeout Prevention

**Problem**: Kernel dies after ~20 minutes of inactivity despite running services.

**Cause**: Kaggle kills notebooks that produce no stdout output for 20 consecutive minutes.

**Solution**: Both `watchdog.py` and `queue_consumer.py` print heartbeat messages every 5 minutes to prevent idle timeout. Verify heartbeats appear in logs:
```bash
kaggle kernels logs | grep -i heartbeat
```

If heartbeats stop, the process itself has crashed. Check watchdog health file:
```bash
cat /kaggle/working/.watchdog_health.json
```

### Session Persistence

**Problem**: Queue state (jobs, artifacts) lost after kernel restart.

**Cause**: `/kaggle/working/` is wiped on kernel restart. SQLite queue database survives watchdog restarts but NOT kernel/session restarts.

**Prevention**: Before ending a session, click **"Save Version"** in the Kaggle notebook UI to persist `/kaggle/working/` contents. On next run, restore from saved version.

**Artifacts**: Generated images/videos are stored at `/kaggle/working/output/{job_id}/` with `metadata.json`. Download important artifacts before ending sessions.

### Model Download Failures

**CivitAI 429 / geo-block**: `model_registry.py` automatically falls back to HuggingFace mirrors. No manual intervention required.

**HuggingFace rate limit (429)**: Wait 5–10 minutes and retry. Set `HF_TOKEN` env var for higher rate limits on gated models.

**Corrupted download**: Delete the partial file and re-run `install.sh` — it is idempotent and will re-download missing files.

### GPU Quota Preservation (30h/week)

**Problem**: GPU quota exhausted before end of week; kernel auto-stops unexpectedly.

**Cause**: Kaggle free tier provides ~30 hours/week of T4 GPU access. Leaving sessions running idle burns quota.

**Solution**: The **GPU Session Guard** (`scripts/gpu_session_guard.py`) runs automatically via `start.sh` and monitors queue activity, API requests, and ComfyUI GPU utilization. After 30 minutes of zero activity, it gracefully stops all services to preserve quota.

**Manual stop**: Run the `gpu-guard` notebook cell or execute directly:
```bash
python3 /kaggle/working/kaggle-comfyui-queue/scripts/gpu_session_guard.py --idle-timeout 0
```

**Configuration**:
- `KAGGLE_GPU_AUTO_STOP=0` — Disable auto-stop entirely (not recommended).
- `KAGGLE_IDLE_TIMEOUT_SECONDS=3600` — Change idle threshold (default: 1800 = 30 min).

**Verify guard is running**:
```bash
cat /kaggle/working/gpu_guard.log | tail -5
```

### Notebook JSON Corruption

**Never hand-edit `.ipynb` files.** Always regenerate via `python scripts/generate_notebook.py`. Hand-edited JSON causes whitespace corruption that breaks Kaggle's parser with opaque `IndentationError` messages.

## Infrastructure Economics

| Metric | Estimate | Notes |
|--------|----------|-------|
| GPU quota (free tier) | 30 hours/week | 2× NVIDIA T4 |
| Setup time | 10–15 min | Model download dominant |
| Image generation (1024×1024, 20 steps) | ~8s | Without `--lowvram` |
| Image generation (1024×1024, 20 steps) | ~25s | With `--lowvram` (OOM-safe default) |
| Video generation (15s, VDN 8-step) | ~16 min | Per MiniMax H3 benchmarks |
| Throughput (images/hour) | ~360 | Without `--lowvram` |
| Storage burn rate | ~2 MB/job | 1024×1024 PNG |
| Max jobs/session (12h) | ~4,320 | Images only, no `--lowvram` |
| Quota exhaustion (sustained) | ~7 days | At 360 jobs/hour, 30h/week |
| **Idle auto-stop threshold** | **30 min** | GPU Session Guard default |
| **Quota saved by auto-stop** | **~2–4 h/week** | Prevents idle burn during breaks |

### GPU Quota Preservation Strategy

Kaggle's free tier provides **30 hours/week** of T4 GPU access. To maximize productive usage:

1. **Auto-stop is enabled by default**: The GPU Session Guard stops all services after 30 minutes of zero queue/API activity. This prevents burning quota during lunch breaks, meetings, or overnight.
2. **Batch jobs when possible**: Submit multiple jobs in rapid succession rather than one-at-a-time with long pauses between submissions.
3. **Use `--lowvram` for large batches**: Slower per-image but avoids OOM crashes that waste setup/restart time.
4. **Monitor quota**: Check remaining GPU hours at [kaggle.com/settings/account](https://www.kaggle.com/settings/account) → "GPU Hours".
5. **Save versions before stopping**: Click "Save Version" in the Kaggle UI to persist `/kaggle/working/` contents across sessions.

## File Structure

```
kaggle-comfyui-queue/
├── docs/
│   └── architecture/
│       ├── concepts_brief.md      # Feature requirements
│       └── design_draft.md        # Full architecture spec + glossary
├── scripts/
│   ├── generate_notebook.py       # nbformat generator (tagged cells)
│   ├── watchdog.py                # Async process supervisor (615 lines)
│   ├── queue_api.py               # FastAPI REST API (772 lines)
│   ├── queue_consumer.py          # Async job worker (955 lines)
│   ├── ngrok_launcher.py          # Tunnel binary manager
│   ├── model_registry.py          # Universal model management
│   ├── gpu_session_guard.py       # Auto-stop idle sessions (30h quota)
│   └── setup.sh                   # Idempotent installer
├── configs/
│   └── model_vram_manifest.json   # VRAM estimates per workflow
├── workflows/                     # Allowlisted ComfyUI workflows
├── tests/
│   ├── test_integration.py        # Static validation (no runtime)
│   └── test_live_integration.py   # pytest lifecycle tests (live tunnel)
├── install.sh                     # Top-level installer
├── start.sh                       # Service launcher
├── requirements.txt               # Pinned Python dependencies
├── ultimate_comfyui.ipynb         # Generated notebook (DO NOT EDIT)
├── ultimate_comfyui/              # Kaggle deployment folder
│   ├── kernel-metadata.json
│   └── ultimate_comfyui.ipynb
└── README.md                      # This file
```

## Known Limitations

### T4: Live Kaggle Runtime Verification (Requires Manual Execution)
Automated verification of kernel startup and tunnel URL appearance in logs requires a live Kaggle GPU environment. This task cannot be validated in local development and must be performed manually:

1. Push the notebook to Kaggle: `cd ultimate_comfyui && kaggle kernels push`
2. Wait for kernel to reach `RUNNING` status (<15 min): `kaggle kernels status`
3. Check logs for tunnel URL: `kaggle kernels logs -u jalilososum/comfyui-ultimate-localtunnel`
4. Verify the URL is accessible and displays ComfyUI interface

All other tasks (T1–T3, T5–T8) have been implemented and verified locally with passing integration tests.

## License

MIT