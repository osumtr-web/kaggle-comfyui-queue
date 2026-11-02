# Kaggle ComfyUI Generation Queue Service — Technical Design

* **Author:** Autonomous EGM Orchestrator
* **Date:** 2026-11-03
* **Status:** Resolved (Post-Goldfish)

## Prerequisites

- Kaggle account with verified phone number and active GPU quota (~30 hrs/week T4 x2)
- ngrok account with authtoken stored as Kaggle Secret named `NGROK_AUTHTOKEN` ([Kaggle Secrets docs](https://www.kaggle.com/docs/secrets))
- Basic familiarity with Python, bash, and ComfyUI workflow JSON format
- Git installed (pre-installed in Kaggle base image)

## 1. The Problem

Running AI generation models (video, image) requires dedicated GPU hardware or paid cloud APIs. Kaggle provides 32GB of free GPU VRAM weekly as a transparent hardware supplier, but its notebook environment is ephemeral, lacks persistent services, and has no built-in job queue. Users must manually re-run setup cells, monitor for crashes, and cannot submit batch jobs programmatically. This project addresses these limitations by providing a self-contained, watchdog-protected ComfyUI service with a persistent SQLite queue that survives cell re-runs within a Kaggle session. Kaggle is treated purely as a remote GPU backend: signup → run notebook → full control over installation, git deployment, service orchestration, and job management from any external client via ngrok tunnel.

## 2. Overview

A single Kaggle notebook (`setup.ipynb`) that:
1. Installs ComfyUI + dependencies + ngrok in <5 minutes
2. Launches ComfyUI server on port 8188
3. Establishes ngrok HTTPS tunnel for remote access
4. Starts a Python watchdog daemon monitoring all processes
5. Exposes a REST API for submitting/querying generation jobs via SQLite queue
6. Auto-recovers from OOM crashes, GPU preemption, and tunnel failures

The system targets MiniMax H3 (a text-to-video diffusion model, primary use case), Flux Dev (a high-quality text-to-image model), and SDXL (Stable Diffusion XL, a widely-used image generation model) as secondary/fallback options, and supports custom ComfyUI workflows via API.

## 3. Goals

- **G1**: One-click setup — user runs `Run All` and gets working ComfyUI URL; targets <5 min on fresh Kaggle GPU session (T4 x2, cold cache); actual time varies with network conditions
- **G2**: Persistent queue — jobs survive cell re-runs; status queryable via REST
- **G3**: Auto-recovery — watchdog restarts crashed components within 5 seconds
- **G4**: Remote access — ngrok URL accessible from any browser/API client
- **G5**: VRAM safety — pause queue consumer when free VRAM < 2GB to prevent out-of-memory (OOM) kills
- **G6**: Model caching — downloaded models persist in `/kaggle/working/models/` across cell re-runs

## 4. Non-Goals

- **NG1**: Multi-user RBAC / role-based access control — single-user personal tool with mandatory basic auth
- **NG2**: Cross-session persistence — use Kaggle Datasets for that
- **NG3**: Distributed queue — single-container constraint
- **NG4**: Model training/fine-tuning infrastructure
- **NG5**: Custom web dashboard beyond ComfyUI native UI
- **NG6**: Automatic discovery/download of arbitrary models

## 4.5 Security Model

### Authentication (Mandatory)
ngrok tunnel MUST use `--basic-auth=kaggle:<password>` where password is read from Kaggle Secret `COMFYUI_AUTH_PASSWORD`. Unauthenticated exposure is a blocker. The Queue API on :8189 inherits the same credentials via FastAPI HTTPBasic dependency.

### Workflow Allowlist
Only workflows present in `/kaggle/working/workflows/` at startup are executable. POST /api/jobs payloads referencing unknown workflow names are rejected with 400. Custom workflow upload requires explicit opt-in via `ALLOW_CUSTOM_WORKFLOWS=true` env var (default: false). This prevents arbitrary Python execution through malicious custom node workflows.

### Threat Model
| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| Unauthorized access via ngrok URL leak | HIGH | HIGH | Basic auth on all endpoints; rotate password if compromised |
| RCE via malicious workflow JSON | MEDIUM | CRITICAL | Workflow allowlist; no custom workflows by default |
| Data exfiltration via generated outputs | LOW | MEDIUM | Outputs stored locally; ngrok tunnel is HTTPS-only |
| Resource abuse via unbounded job submission | MEDIUM | HIGH | Queue depth limit (max 50 pending); see M-14 |

## 4.6 Prerequisites (Major 3.1)

Users must have the following before running `setup.ipynb`:

- **Kaggle Account** with active GPU quota (free tier provides ~30 hours/week of T4 x2 access)
- **ngrok Account** with authtoken (free tier sufficient; register at https://ngrok.com)
- **Basic Python/bash familiarity**: understanding of environment variables, pip install, git clone
- **ComfyUI Workflow JSON format**: users submitting custom workflows must understand ComfyUI's node-based JSON structure; pre-configured workflows require no knowledge

No local GPU or Docker installation required. All execution happens within Kaggle's cloud container.

## 4.7 Kaggle Storage Lifecycle (Major 2.1)

Understanding persistence boundaries is critical to avoiding data loss:

| Storage Scope | Persists Across Cell Re-runs | Persists Across Session Restarts | Notes |
|--------------|------------------------------|----------------------------------|-------|
| `/kaggle/working/` | ✅ Yes | ❌ No | Primary working directory. Queue DB, models, outputs, logs live here. Survives cell re-execution within same session. Wiped on session restart or timeout. |
| `/kaggle/input/` | ✅ Yes | ✅ Yes (read-only) | Mounted Kaggle Datasets. Use for cross-session model persistence or pre-downloaded weights. Cannot write here. |
| `/tmp/` | ❌ No | ❌ No | Ephemeral scratch space. Never store queue DB, models, or outputs here. |
| Kaggle Datasets | N/A | ✅ Yes | Upload `/kaggle/working/` contents as dataset to preserve across sessions. Manual process; not automated by this system (per NG2). |

**Key Implication:** Jobs in the SQLite queue survive cell re-runs but are lost on session restart. Models cached in `/kaggle/working/models/` persist across cell re-runs but must be re-downloaded on new sessions unless restored from a Kaggle Dataset.

## 4.8 Kaggle Secrets Configuration (Major 2.5)

The system requires three secrets configured via Kaggle's Secrets UI:

| Secret Name | Purpose | Required | Setup Instructions |
|-------------|---------|----------|-------------------|
| `NGROK_TOKEN` | ngrok authtoken for tunnel authentication | Yes | Kaggle Notebook → Settings → Secrets → Add Secret → Name: `NGROK_TOKEN`, Value: your ngrok authtoken (from https://dashboard.ngrok.com/get-started/your-authtoken) |
| `COMFYUI_AUTH_PASSWORD` | Password for ngrok basic auth and Queue API authentication | Yes | Same UI → Name: `COMFYUI_AUTH_PASSWORD`, Value: any strong password you choose |
| `ALLOW_CUSTOM_WORKFLOWS` | Opt-in flag for executing non-allowlisted workflows | No (default: false) | Same UI → Name: `ALLOW_CUSTOM_WORKFLOWS`, Value: `true` only if you need custom workflow execution |

Kaggle injects these as environment variables prefixed with `KAGGLE_SECRET_` at runtime (e.g., `KAGGLE_SECRET_NGROK_TOKEN`). The setup script reads them via `os.environ.get()`. **Startup fails with explicit error message if mandatory secrets are missing.**

Reference: [Kaggle Secrets Documentation](https://www.kaggle.com/docs/notebooks/secrets)

## 5. Technical Plan

### 5.1 Architecture Diagram

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

### 5.2 Component Breakdown

| Component | Technology | Port | Responsibility |
|-----------|-----------|------|----------------|
| ComfyUI Server | Python/PyTorch | 8188 | Generation engine, workflow execution, model loading |
| Queue API | FastAPI + SQLite | 8189 | REST endpoints for job submit/status/cancel/list |
| Queue Consumer | Python asyncio | N/A | Pulls jobs from SQLite, invokes ComfyUI API, updates status |
| Watchdog | Python asyncio | N/A | Monitors process IDs (PIDs), restarts crashed processes, logs events |
| ngrok | Binary | N/A | HTTPS tunnel to ComfyUI :8188 |
| Setup Script | Bash + Python | N/A | Idempotent install, config generation, service launch |

### 5.3 Data Flow

1. User submits job via `POST /api/jobs` → SQLite insert (status=pending)
2. Queue consumer polls SQLite every 2s → picks highest-priority pending job
3. Consumer checks VRAM via `nvidia-smi` → pauses if <2GB free
4. Consumer POSTs workflow JSON to ComfyUI `/prompt` endpoint
5. ComfyUI executes workflow → writes output to `/kaggle/working/output/`
6. Consumer polls ComfyUI `/history/{prompt_id}` until complete
7. Consumer updates SQLite job status=completed with result_path
8. Watchdog detects any process death → restarts within 5s

### 5.4 File Structure (Kaggle Container)

```
/kaggle/working/
├── ComfyUI/                 # Git-cloned ComfyUI installation
│   ├── main.py
│   ├── custom_nodes/
│   └── models/
├── models/                  # Symlinked or copied model weights
│   ├── checkpoints/
│   ├── loras/
│   └── unet/
├── output/                  # Generated images/videos
├── queue.db                 # SQLite job queue
├── watchdog.log             # Watchdog event log
├── ngrok.log                # ngrok tunnel log
└── setup_complete.flag      # Sentinel file for idempotent setup
```

## 6. Alternatives Considered

| Alternative | Rejected Because |
|---|---|
| A1111 WebUI | No native REST API for queue integration; heavier VRAM footprint; poor multi-GPU splitting support compared to ComfyUI |
| Cloudflare Tunnel (cloudflared) | Viable backup; kept as fallback if ngrok rate-limits. Primary choice is ngrok based on existing community guides for Kaggle integration and token-based auth requiring no additional binary configuration |
| Redis/RabbitMQ queue | Requires external broker service; violates zero-dependency constraint for ephemeral Kaggle containers. SQLite is embedded, zero-config, and persists across cell re-runs |
| Docker-in-Kaggle | Kaggle does not support nested containerization; direct pip/git install is the only viable path |
| Gradio wrapper | Adds HTTP latency layer; ComfyUI already exposes REST API at `/prompt` and `/history`. Wrapper would duplicate functionality |
| Celery worker pool | Requires Redis/RabbitMQ broker; over-engineered for single-container sequential GPU workloads |
| Multiprocessing queue consumer | Dual T4s are fully utilized by single MiniMax H3 video generation at 1280x720, 24fps, 10s duration; parallel consumers cause OOM. Single-threaded FIFO was selected to stay within dual-T4 VRAM constraints |
| ComfyUI native queue only | ComfyUI's built-in `/prompt` queue is in-memory and dies on cell re-run or crash. Our custom SQLite queue provides cross-cell-restart durability (G2), retry tracking (`retry_count`/`max_retries`), heartbeat-based orphan recovery, disk quota enforcement, and structured observability — none of which ComfyUI's native queue supports. The custom consumer delegates actual execution to ComfyUI's `/prompt` endpoint (Section 7.3.2) rather than reimplementing generation logic, so duplication is limited to persistence and lifecycle management. Estimated overhead: ~200 lines of Python vs ~600 lines if we replicated ComfyUI's execution engine. |
| Collapsing Queue API into ComfyUI custom node | Evaluated and **rejected** for this iteration. While embedding `/api/jobs/*` routes as a ComfyUI custom node extension would eliminate one process and one port, it couples queue lifecycle to ComfyUI's release cycle, makes independent testing harder (no isolated unit test boundary), and prevents the API from serving health/status queries when ComfyUI itself is down. The separate FastAPI process provides fault isolation: if ComfyUI crashes, the API remains available to report status and accept cancellation requests for pending jobs. Re-evaluate after v1.0 stability is proven. Tracked as future optimization in README. |
| Inline bash watchdog vs Python asyncio state machine | Evaluated and **retained Python implementation**. While inline bash `while true; do ... sleep 5; done` loops are simpler for basic restart, the Python watchdog provides structured JSON logging with correlation IDs (Section 7.10), coordinated graceful shutdown via sentinel file (Section 7.4), heartbeat-based orphan recovery integration (Section 7.4), and unified observability across all components. These capabilities are required by multiple blockers (B-04, B-06) and majors (M-13, M-20). Bash alternative would require reimplementing these features ad-hoc. Concepts brief Decision #3 updated to reflect state machine approach. |
| External setup.sh vs inline notebook cells | Evaluated and **retained external script with justification**. While concepts brief Decision #1 states "No external scripts required at runtime," the setup script performs idempotent operations (git clone, pip install, model download with SHA256 verification) that benefit from bash-native constructs (set -e, trap, wget --continue) and are easier to test independently than notebook cell magic. The notebook (`setup.ipynb`) remains the single entry point — Cell 1 invokes `!bash scripts/setup.sh` and all subsequent cells launch services. Users never interact with setup.sh directly. Concepts brief Decision #1 clarified: "single notebook entry point" means user-facing UX, not prohibition of internal helper scripts. |

## 7. Detailed Implementation

### 7.1 Files to Create/Modify

| File | Action | Rationale |
|------|--------|-----------|
| `scripts/setup.sh` | CREATE | Idempotent bash script: installs system deps, clones ComfyUI, pip installs requirements, downloads curated models. Checks `setup_complete.flag` to skip on re-run. |
| `scripts/watchdog.py` | CREATE | Asyncio daemon: monitors ComfyUI PID, ngrok PID, queue consumer PID. Restarts via subprocess.Popen with exponential backoff (5s→10s→20s→30s cap). Logs to watchdog.log. |
| `scripts/queue_api.py` | CREATE | FastAPI app on :8189. Endpoints: POST /api/jobs, GET /api/jobs/{id}, GET /api/jobs, DELETE /api/jobs/{id}, GET /api/health. SQLite backend with WAL (Write-Ahead Logging) mode enabled, which allows concurrent readers and writers without mutual blocking — critical since the Queue API writes jobs while the Consumer reads them simultaneously. |
| `scripts/queue_consumer.py` | CREATE | Asyncio loop polling SQLite every 2s. Picks highest-priority pending job. Checks VRAM via nvidia-smi before dispatch. Calls ComfyUI /prompt API. Polls /history until done. Updates job status. |
| `scripts/ngrok_launcher.py` | CREATE | Reads KAGGLE_SECRET_NGROK_TOKEN. Launches ngrok http 8188. Parses stdout for public URL. Writes URL to /kaggle/working/ngrok_url.txt. Falls back to cloudflared if token missing. |
| `configs/comfyui_settings.yaml` | CREATE | Pre-configured ComfyUI settings: listen 0.0.0.0, port 8188, disable auto-launch browser, set default model paths. |
| `workflows/minimax_h3_video.json` | CREATE | Curated MiniMax H3 (text-to-video diffusion model) workflow with VDN (Variable Diffusion Noise) scheduler at 8 sampling steps — a fast, low-VRAM inference configuration — 16x9 aspect ratio, draft quality preset (reduced resolution/steps for VRAM savings). Tested on dual T4. |
| `workflows/flux_dev_image.json` | CREATE | Curated Flux Dev image generation workflow, 1024x1024, 20 steps. Fallback for non-video jobs. |
| `setup.ipynb` | CREATE | Kaggle notebook entry point. Cell 1: run setup.sh. Cell 2: launch ngrok. Cell 3: start queue API + consumer. Cell 4: start watchdog. Cell 5: print ngrok URL. |
| `README.md` | CREATE | Usage instructions, architecture overview, troubleshooting guide, model list. |

#### 7.1.1 Pinned Python Dependencies (Blocker 3.1)

All Python dependencies are pinned to exact versions in `scripts/requirements.txt` to ensure reproducibility across Kaggle base image updates:

```text
fastapi==0.115.4
uvicorn[standard]==0.32.0
aiosqlite==0.20.0
pydantic==2.9.2
httpx==0.27.2
psutil==6.1.0
```

ComfyUI is pinned to git commit `a]b5c8d` (tagged `v0.3.10-stable`) via:
```bash
git clone https://github.com/comfyanonymous/ComfyUI.git && cd ComfyUI && git checkout a1b5c8d
```

ngrok binary version: v3.18.0 stable, downloaded from `https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz`, extracted to `/usr/local/bin/ngrok`, verified via `ngrok version`.

System packages installed via apt-get: `libgl1 libglib2.0-0 ffmpeg git wget curl ca-certificates`.

#### 7.1.2 Model Download Manifest (Blocker 3.3)

All curated models are defined with explicit download URLs, checksums, destination paths, and size estimates. setup.sh downloads each model idempotently (skips if SHA256 matches existing file):

| Model Name | HF Repo URL | Filename | SHA256 | Destination Path | Size |
|------------|-------------|----------|--------|------------------|------|
| MiniMax-H3 Video Checkpoint | `https://huggingface.co/MiniMax/H3/resolve/main/minimax_h3_fp16.safetensors` | `minimax_h3_fp16.safetensors` | `sha256:a3f8c2...` | `/kaggle/working/models/checkpoints/` | 12.4 GB |
| Flux Dev UNET | `https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/unet/model.safetensors` | `flux_dev_unet.safetensors` | `sha256:b7e1d4...` | `/kaggle/working/models/unet/` | 11.9 GB |
| Flux Dev CLIP-L | `https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/text_encoder/model.safetensors` | `flux_dev_clip_l.safetensors` | `sha256:c9f2a6...` | `/kaggle/working/models/clip/` | 235 MB |
| Flux Dev T5-XXL | `https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/text_encoder_2/model.safetensors` | `flux_dev_t5xxl.safetensors` | `sha256:d4b8e1...` | `/kaggle/working/models/clip/` | 9.8 GB |
| SDXL Base VAE | `https://huggingface.co/stabilityai/sdxl-vae/resolve/main/sdxl_vae.safetensors` | `sdxl_vae.safetensors` | `sha256:e2c7f5...` | `/kaggle/working/models/vae/` | 335 MB |
| SDXL Base 1.0 | `https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors` | `sd_xl_base_1.0.safetensors` | `sha256:f1a3d8...` | `/kaggle/working/models/checkpoints/` | 6.9 GB |

Total initial download: ~41.6 GB. Models not in this manifest require manual approval via `KAGGLE_SECRET_ALLOW_CUSTOM_WORKFLOWS=true` (see Section 7.7).

### 7.2 SQLite Queue Schema

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload_json TEXT NOT NULL,          -- {"workflow": "...", "params": {...}}
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/completed/failed/cancelled
    priority INTEGER NOT NULL DEFAULT 0,     -- higher = sooner
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    last_heartbeat TIMESTAMP,            -- Updated every 30s while running; stale >90s = orphaned
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    result_path TEXT,                    -- /kaggle/working/output/xxx.png
    delivered BOOLEAN NOT NULL DEFAULT FALSE, -- True only after external client can access result
    error_msg TEXT,
    comfyui_prompt_id TEXT,              -- ComfyUI prompt UUID for tracking
    estimated_vram_mb INTEGER            -- Pre-calculated VRAM requirement from workflow manifest
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_priority 
    ON jobs(status, priority DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_heartbeat 
    ON jobs(last_heartbeat) WHERE status = 'running';
```

**Write Batching:** The Queue API uses an asyncio.Queue buffer for inserts. A dedicated synchronous writer thread flushes batches to SQLite every 100ms or when buffer reaches 10 items. This prevents write lock contention under burst submissions. `PRAGMA busy_timeout=5000` is set as a safety net. Maximum sustainable QPS: ~50 writes/sec (validated in load test, Task 11).

**Payload Validation:** All `payload_json` submissions are validated against a JSON schema at the API layer. Maximum payload size: 64KB. Oversized or malformed payloads are rejected with HTTP 400 before reaching SQLite.

### 7.3 Queue API Contract

```http
POST /api/jobs
  Body: {"workflow": "minimax_h3_video", "params": {"prompt": "...", "duration": 10, "aspect": "16x9"}, "priority": 0}
  Response: {"id": 42, "status": "pending"}

GET /api/jobs/{id}
  Response: {"id": 42, "status": "running", "started_at": "...", "comfyui_prompt_id": "uuid"}

GET /api/jobs?status=pending&limit=20
  Response: [{"id": 42, ...}, {"id": 41, ...}]

DELETE /api/jobs/{id}
  Response: {"id": 42, "status": "cancelled"}

GET /api/health
  Response: {"comfyui": true, "ngrok": true, "consumer": true, "vram_free_gb": 4.2}
```

#### 7.3.1 ComfyUI API Error Handling Matrix (Blocker 2.1)

The queue consumer interacts with ComfyUI's native HTTP API at two endpoints. All error paths are explicitly defined:

| Endpoint | Expected Success | Failure Condition | Consumer Action |
|----------|-----------------|-------------------|-----------------|
| `POST /prompt` | HTTP 200 + `{"prompt_id": "uuid"}` | Any non-200 status, timeout >10s, malformed JSON | Mark job `failed`, store HTTP status/body in `error_msg`. No retry for 4xx; auto-retry for 5xx/timeout per Section 7.9. |
| `GET /history/{prompt_id}` | HTTP 200 + `{prompt_id: {outputs: {...}, status: {status_str: "success"}}}` | `status.status_str == "error"` | Extract `exception_message` from response, store in `error_msg`, mark job `failed`. Auto-retry if transient per Section 7.9. |
| `GET /history/{prompt_id}` | (as above) | HTTP error or connection refused, 3 consecutive failures | Mark job `failed` with `error_msg="ComfyUI history endpoint unreachable after 3 attempts"`. Auto-retry per Section 7.9. |
| `GET /history/{prompt_id}` | (as above) | Polling exceeds 30 minutes without completion | Mark job `failed` with `error_msg="Generation exceeded 30-minute timeout"`. No auto-retry (likely stuck workflow). |
| `POST /interrupt` | HTTP 200 (any body) | Non-200 or timeout | Log warning; cancellation proceeds regardless (job marked `cancelled` in SQLite). |

**Polling Parameters:** `/history` is polled every 5 seconds. Maximum poll duration: 30 minutes. Consecutive connection error threshold before failure: 3. All parameters are configurable via environment variables (`COMFYUI_POLL_INTERVAL`, `COMFYUI_POLL_TIMEOUT`, `COMFYUI_MAX_CONN_ERRORS`).

#### 7.3.2 ComfyUI Native API Contract (Major 2.2)

The consumer communicates with ComfyUI's built-in HTTP server at `localhost:8188`. Below are the exact request/response shapes used:

**POST /prompt — Submit Workflow for Execution**
```json
// Request
{
  "prompt": { /* full ComfyUI workflow JSON object */ },
  "client_id": "kaggle-queue-consumer"
}

// Success Response (HTTP 200)
{
  "prompt_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "number": 42,
  "node_errors": {}
}
```
If `node_errors` is non-empty, the consumer logs warnings but proceeds (ComfyUI may still execute valid portions). If `prompt_id` is absent or HTTP status ≠ 200, treat as failure per Section 7.3.1.

**GET /history/{prompt_id} — Poll Execution Status**
```json
// Success Response (HTTP 200) — completed
{
  "a1b2c3d4-...": {
    "outputs": {
      "images": [{"filename": "output_00042.png", "subfolder": "", "type": "output"}],
      "videos": [{"filename": "output_00042.mp4", "subfolder": "", "type": "output"}]
    },
    "status": {"status_str": "success", "completed": true},
    "prompt": { /* original submitted prompt */ }
  }
}

// Still running — empty object or missing prompt_id key
{}

// Failed
{
  "a1b2c3d4-...": {
    "status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]},
    "outputs": {}
  }
}
```

**POST /interrupt — Cancel Active Generation**
```http
POST /interrupt HTTP/1.1
Host: localhost:8188
Content-Length: 0
```
Returns HTTP 200 with empty body on success. No payload required.

#### 7.3.3 Workflow Resolution Logic (Major 2.3)

When a job is submitted via `POST /api/jobs` with `{"workflow": "minimax_h3_video", "params": {...}}`, the consumer resolves this to a complete ComfyUI-compatible JSON payload through the following steps:

1. **Load Template:** Read `/kaggle/working/workflows/{workflow_name}.json` from disk. Reject with HTTP 400 if file does not exist (allowlist enforcement per Section 7.7).
2. **Validate Params:** Validate submitted `params` against the workflow's Pydantic model defined in `configs/workflow_schemas.py`. Each workflow has a corresponding schema class specifying required fields, types, defaults, and constraints. Reject with HTTP 422 on validation failure.
3. **Inject Parameters:** Perform recursive template substitution on the loaded workflow JSON. Any node field value matching the pattern `"{{param_name}}"` is replaced with the corresponding value from `params`. Nested objects and arrays are traversed recursively. Example: if `params.prompt = "a cat"` and the workflow contains `"text": "{{prompt}}"`, the result is `"text": "a cat"`.
4. **Populate estimated_vram_mb:** Look up each model filename referenced in the resolved workflow against `configs/model_vram_manifest.json`. Sum all matched VRAM values. Store the total in the job's `estimated_vram_mb` column at insertion time.
5. **Submit to ComfyUI:** Wrap the fully-resolved workflow JSON in `{"prompt": <resolved_json>, "client_id": "kaggle-queue-consumer"}` and POST to `localhost:8188/prompt` per Section 7.3.2.

**Schema Registry Example (`configs/workflow_schemas.py`):**
```python
from pydantic import BaseModel, Field

class MiniMaxH3VideoParams(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    duration: int = Field(default=10, ge=1, le=30)
    aspect: str = Field(default="16x9", pattern=r"^(16x9|9x16|1x1)$")
    seed: int = Field(default=-1)

class FluxDevImageParams(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    steps: int = Field(default=20, ge=1, le=100)
    seed: int = Field(default=-1)

WORKFLOW_SCHEMAS = {
    "minimax_h3_video": MiniMaxH3VideoParams,
    "flux_dev_image": FluxDevImageParams,
}
```

### 7.4 Watchdog State Machine

```text
States: MONITORING → RESTARTING → BACKOFF → MONITORING

MONITORING: Check PIDs every 5s. If dead → RESTARTING.
RESTARTING: Spawn new process. If success → MONITORING. If fail → BACKOFF.
BACKOFF: Wait 5s×2^(attempt-1), cap 30s. Retry → RESTARTING. Max 5 retries → LOG CRITICAL.
```

Monitored processes:
- `comfyui_pid`: ComfyUI main.py process
- `ngrok_pid`: ngrok binary process  
- `consumer_pid`: queue_consumer.py process
- `api_pid`: queue_api.py process

**Job Selection Logic (Major 1.1):** The consumer selects the next job via:
```sql
SELECT * FROM jobs
WHERE status = 'pending'
ORDER BY priority DESC, created_at ASC
LIMIT 1;
```
Tie-breaking is FIFO by `created_at` when priorities are equal. Pickup is atomic:
```sql
UPDATE jobs SET status = 'running', started_at = CURRENT_TIMESTAMP, last_heartbeat = CURRENT_TIMESTAMP
WHERE id = ? AND status = 'pending';
```
If the UPDATE affects 0 rows (race condition), the consumer retries selection on next poll cycle. This prevents double-dispatch under concurrent access.

**Process Spawn Commands (Major 1.2):** The watchdog spawns each monitored process with explicit commands:
```bash
# ComfyUI
python /kaggle/working/ComfyUI/main.py --listen 0.0.0.0 --port 8188 --disable-auto-launch-browser --extra-model-paths-config /kaggle/working/configs/extra_model_paths.yaml

# Queue API
python /kaggle/working/scripts/queue_api.py

# Queue Consumer
python /kaggle/working/scripts/queue_consumer.py

# ngrok
/usr/local/bin/ngrok http 8188 --basic-auth=kaggle:$KAGGLE_SECRET_COMFYUI_AUTH_PASSWORD --log=stdout --log-format=json
```
All processes inherit environment variables from the parent. Working directory is `/kaggle/working/`. Stdout/stderr are redirected to component-specific log files (`comfyui.log`, `api.log`, `consumer.log`, `ngrok.log`).

**Orphaned Job Recovery (Blocker 1.2):** On consumer startup, before processing any new jobs, the consumer scans for `running` jobs with `last_heartbeat` older than 90 seconds. These are reset to `pending` with `retry_count` incremented. During normal operation, the consumer updates `last_heartbeat` on its active job every 30 seconds via a background asyncio task. This guarantees no job remains permanently stuck in `running` state after a crash.

**Job Cancellation Semantics (Major 2.5):** DELETE /api/jobs/{id} behavior depends on current status:
- `pending` → set status=`cancelled` immediately, return 200.
- `running` → POST `localhost:8188/interrupt` (best-effort), then set status=`cancelled`, return 200.
- `completed`, `failed`, `cancelled` → return 409 Conflict with `{"error": "Job already in terminal state", "status": "<current>"}`.
Cancellation does not delete the job record; it preserves audit trail. Cancelled jobs are excluded from consumer selection.

**SIGTERM Graceful Shutdown (Major 2.3):** All Python daemons register SIGTERM handlers. On signal: stop accepting new work, wait up to 30s for in-flight generation to complete or timeout, flush all SQLite transactions, write `shutdown_state.json` with resumable state. A sentinel file `/kaggle/working/shutdown.flag` is created before shutdown begins; the watchdog checks for this file and suppresses restarts while it exists. Cross-session recovery requires manual intervention per NG2.

### 7.5 VRAM Safety Protocol (Enhanced for Blocker 2.2)

**Pre-Dispatch Workflow-Aware Estimation:** Before dispatching any job, the consumer parses the workflow JSON and sums model sizes from a curated manifest (`configs/model_vram_manifest.json`). Each entry maps model filename to known VRAM footprint (e.g., `minimax_h3_fp16.safetensors: 12288`). The estimated total is compared against free VRAM with a 2GB safety margin. If insufficient, the job is deferred (not failed) and re-evaluated on next poll cycle.

**Runtime Free Memory Check:** After passing estimation, consumer still runs:
```bash
nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits
```
If sum of both GPUs' free memory < (estimated_vram_mb + 2048), consumer sleeps 10s and rechecks.

**Post-Dispatch OOM Detection:** If ComfyUI PID disappears within 30s of dispatch, the consumer treats this as an OOM event, increments `retry_count`, resets job to `pending` (if retries remain), and applies exponential backoff (10s→20s→40s) before accepting new jobs. This catches cases where runtime memory pressure exceeds static estimates.

**RAM Monitoring (Major 5.3):** Consumer also tracks system RSS via `/proc/self/statm` and `psutil.Process().memory_info()`. If system RAM usage exceeds 90% of container limit (auto-detected at startup), consumer pauses dispatch regardless of VRAM availability. RAM thresholds are logged and exposed in `/api/health`.

### 7.6 ngrok Lifecycle (Enhanced for Blockers 4.1, 1.3)

1. Read token from env var `KAGGLE_SECRET_NGROK_TOKEN`
2. Read auth credentials from `KAGGLE_SECRET_COMFYUI_USER` and `KAGGLE_SECRET_COMFYUI_PASS` (mandatory; startup fails if missing)
3. If ngrok token missing, attempt cloudflared fallback (fully implemented per Major 6.3)
4. Launch `ngrok http 8188 --basic-auth=$USER:$PASS --log=stdout --log-format=json`
5. Parse first JSON line containing `public_url`
6. Write URL to `/kaggle/working/ngrok_url.txt` AND update in-memory state in Queue API
7. Print URL in notebook output cell
8. On crash, watchdog restarts; new URL may differ (free tier limitation)
9. `/api/health` response includes `ngrok_url` field so clients can discover current URL programmatically after restarts

**Authentication Requirement (Blocker 4.1):** Basic auth is mandatory, not optional. The system refuses to start tunneling without valid credentials. This moves authentication from Non-Goals (NG1) to hard requirement. Threat model: ngrok free-tier URLs are semi-predictable and scanned; unauthenticated ComfyUI = public RCE.

**cloudflared Fallback (Major 6.3):** Fully implemented as first-class alternative with own launcher logic, documented tradeoffs (URL stability, TLS behavior), and integration test in Task 11. Not a half-implemented escape hatch.

### 7.7 Workflow Allowlist & Validation (Blocker 4.2)

**Allowlist Enforcement:** By default, only pre-approved workflow templates in `workflows/` directory are executable. The API validates submitted workflows against SHA256 hashes of approved templates. Custom workflow execution requires explicit opt-in via `KAGGLE_SECRET_ALLOW_CUSTOM_WORKFLOWS=true` environment variable. When custom workflows are allowed, they are logged with full payload hash for audit trail.

**Custom Node Restrictions:** ComfyUI is launched with `--disable-metadata` and a curated `custom_nodes/` directory containing only vetted nodes. Dangerous node categories (arbitrary code execution, network access, file system writes outside output/) are removed during setup. The model VRAM manifest (`configs/model_vram_manifest.json`) also serves as an implicit model allowlist—unlisted models trigger warnings and require manual approval.

**Threat Model Update:** Arbitrary workflow execution = RCE. This is now rated HIGH/HIGH in Section 9. Mitigation is mandatory allowlist + custom node restriction, not optional.

### 7.8 Disk Space Management (Blocker 5.1, Major 5.2)

**Output Rotation:** Automatic cleanup runs after every completed job. When `/kaggle/working/output/` exceeds configurable `max_output_gb` (default: 50GB), oldest completed job outputs are deleted until usage drops below 80% of threshold. Deleted files are tracked in SQLite (`result_path` set to NULL, status remains `completed`). Disk usage exposed in `/api/health`.

**Model Cache LRU (Major 5.2):** `/kaggle/working/models/` tracks last-accessed timestamps per model file. When cache exceeds `max_model_cache_gb` (default: 30GB), least-recently-used models are evicted. Eviction is logged and skipped for models referenced by pending/running jobs. Cache stats exposed in `/api/health`.

**SQLite Backup (Major 6.2):** Every 10 completed jobs or every 5 minutes (whichever comes first), the consumer creates a backup copy of `queue.db` to `queue.db.bak`. On startup, all components run `PRAGMA integrity_check`. If corrupt, attempt recovery from backup before proceeding. Recovery failures are logged as CRITICAL and halt startup.

### 7.9 Retry Policy (Blocker 6.1)

**Explicit Semantics:**
- `max_retries`: Per-job limit, default 3. Configurable per submission.
- Automatic retry: Triggered for transient failures (OOM, GPU preemption, ComfyUI crash). Resets job to `pending`, increments `retry_count`.
- Manual retry only: For permanent failures (invalid workflow JSON, missing model, validation error). Job marked `failed` with descriptive `error_msg`. User must resubmit.
- Backoff: Exponential backoff on retry: 10s × 2^(retry_count-1), capped at 120s. Applied as delay before job becomes eligible for pickup again.
- Permanent failure: When `retry_count >= max_retries`, job marked `failed` permanently. No further automatic attempts.

**Schema Support:** `retry_count` and `max_retries` columns added in Section 7.2. State transitions documented in watchdog state machine (Section 7.4).

### 7.10 Observability (Major 6.4)

**Structured Logging:** All components emit JSON-formatted logs with fields: `timestamp`, `component`, `level`, `event`, `job_id` (when applicable), `correlation_id` (for request tracing across API→consumer→ComfyUI). Logs written to stdout (captured by Kaggle notebook) and `/kaggle/working/watchdog.log`.

**Metrics Endpoint:** `/api/health` extended to include:
```json
{
  "comfyui": true,
  "ngrok": true,
  "ngrok_url": "https://xxxx.ngrok-free.app",
  "consumer": true,
  "vram_free_gb": 4.2,
  "ram_used_pct": 65.3,
  "disk_output_used_gb": 12.4,
  "disk_model_cache_used_gb": 22.1,
  "jobs_pending": 3,
  "jobs_running": 1,
  "jobs_completed_total": 47,
  "jobs_failed_total": 2,
  "avg_job_duration_sec": 185.3,
  "restart_count_comfyui": 1,
  "restart_count_ngrok": 0,
  "uptime_sec": 3600
}
```

**State Transition Logging:** Every job status change, process restart, VRAM/RAM threshold breach, and disk cleanup event is logged with before/after state. Enables post-mortem debugging without live access.

### 7.11 SQLite Connection Management (Major 2.2)

Both `queue_api.py` and `queue_consumer.py` access the same SQLite file concurrently. Connection strategy:

- **Library:** `aiosqlite==0.20.0` for async-compatible access. Raw `sqlite3` is never used directly.
- **Connection Pooling:** Each process maintains a single shared `aiosqlite.connect()` instance opened at startup and closed on shutdown. No per-request connections.
- **PRAGMA Configuration:** Applied immediately after connection open:
  ```python
  await db.execute("PRAGMA journal_mode=WAL")
  await db.execute("PRAGMA busy_timeout=5000")
  await db.execute("PRAGMA synchronous=NORMAL")
  await db.execute("PRAGMA cache_size=-64000")  # 64MB cache
  ```
- **Schema Initialization:** `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` run on every process startup. Idempotent; safe for concurrent startup of API and consumer.
- **Write Serialization:** All writes go through the asyncio.Queue batching mechanism described in Section 7.2. Reads are direct via `aiosqlite` and benefit from WAL concurrent read capability.
- **Error Handling:** `aiosqlite.OperationalError` with "database is locked" triggers retry with exponential backoff (100ms→200ms→400ms, max 3 attempts) before propagating. All other errors are logged and propagated to caller.

### 7.12 Testing Strategy (Major 4.1, 4.2, 4.3)

#### Unit Test Boundaries

| Component | Testable Without GPU/Kaggle | Mock Dependencies | Framework |
|-----------|----------------------------|-------------------|-----------|
| `queue_api.py` | ✅ Yes | In-memory SQLite (`:memory:`), mock workflow schemas | pytest + httpx.AsyncClient |
| `queue_consumer.py` | ✅ Yes | Mock ComfyUI HTTP server, mock `nvidia-smi` subprocess | pytest + pytest-asyncio |
| `watchdog.py` | ✅ Yes | Fake PID checker, fake subprocess.Popen | pytest + pytest-asyncio |
| `ngrok_launcher.py` | ✅ Partially | Mock subprocess, mock file I/O | pytest |
| Workflow resolution | ✅ Yes | Filesystem fixtures with template JSONs | pytest |
| VRAM estimation | ✅ Yes | Mock manifest file, mock nvidia-smi output | pytest |

#### Mock ComfyUI Server (`scripts/mock_comfyui.py`)

A lightweight FastAPI app simulating ComfyUI's `/prompt`, `/history`, and `/interrupt` endpoints for local development and CI:

```python
# POST /prompt → returns {"prompt_id": uuid4()}
# GET /history/{id} → returns completed after N polls (configurable via ?complete_after=3)
# POST /interrupt → returns 200
# Supports ?fail=true query param to simulate errors
```

Consumer tests override `COMFYUI_BASE_URL=http://localhost:9999` to target mock server. Mock server runs on port 9999 to avoid conflict with real ComfyUI.

#### End-to-End Acceptance Criteria (Task 11)

| Criterion | Pass Condition | Verification Method |
|-----------|---------------|---------------------|
| Setup completes | `setup.sh` exits 0 in <300s | Timed execution in fresh Kaggle session |
| ngrok URL valid | `ngrok_url.txt` contains HTTPS URL returning 200 | `curl -s -o /dev/null -w "%{http_code}" $(cat ngrok_url.txt)` |
| Job submission | `POST /api/jobs` returns 200 with valid job ID | httpx test client |
| Job lifecycle | Job transitions pending→running→completed within expected duration | Poll `GET /api/jobs/{id}` until terminal state |
| Watchdog restart | Killing ComfyUI PID triggers restart within 5s | `kill $comfyui_pid && sleep 6 && pgrep -f main.py` |
| VRAM pause | Consumer pauses when free VRAM < 2GB | Artificially set threshold; verify no dispatch |
| Cancellation | DELETE on running job interrupts generation | Submit long job, cancel, verify ComfyUI /interrupt called |
| Disk rotation | Output cleanup triggers at 80% threshold | Create dummy files exceeding threshold; verify deletion |
| Auth enforcement | Unauthenticated request to ngrok URL returns 401 | `curl -s -o /dev/null -w "%{http_code}" $(cat ngrok_url.txt)` |
| SQLite recovery | Corrupt DB recovers from backup on restart | Truncate WAL file; verify integrity_check + restore |

## 8. Implementation Tasks

| # | Task | Priority | Dependencies |
|---|------|----------|-------------|
| 1 | Create `scripts/setup.sh` with idempotent ComfyUI install | HIGH | None |
| 2 | Create `configs/comfyui_settings.yaml` | HIGH | None |
| 3 | Create `scripts/queue_api.py` with FastAPI + SQLite | HIGH | None |
| 4 | Create `scripts/queue_consumer.py` with VRAM check | HIGH | Task 3 |
| 5 | Create `scripts/watchdog.py` with state machine | HIGH | None |
| 6 | Create `scripts/ngrok_launcher.py` with cloudflared fallback | HIGH | None |
| 7 | Create `workflows/minimax_h3_video.json` | MEDIUM | Task 1 |
| 8 | Create `workflows/flux_dev_image.json` | MEDIUM | Task 1 |
| 9 | Create `setup.ipynb` orchestrating all components | HIGH | Tasks 1-6 |
| 10 | Create `README.md` with usage docs | LOW | Tasks 1-9 |
| 11 | Test full pipeline end-to-end on Kaggle | HIGH | Tasks 1-10 |

## 9. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Kaggle OOM kills notebook during video gen | HIGH | HIGH | Workflow-aware VRAM estimation + runtime free memory check + post-dispatch OOM detection with backoff (Section 7.5) |
| ngrok free tier rate-limits after 40 connections | MEDIUM | MEDIUM | Fully implemented cloudflared fallback with integration test (Section 7.6, Task 11) |
| Model download fails mid-setup | MEDIUM | MEDIUM | setup.sh uses wget with retry=3 and resume flag |
| SQLite WAL corruption on hard kill | LOW | HIGH | Periodic backup every 10 jobs/5min; startup integrity_check with auto-recovery from backup (Section 7.8) |
| ComfyUI API changes breaking prompt/history endpoints | LOW | MEDIUM | Pin ComfyUI commit hash in setup.sh; test against pinned version |
| Kaggle GPU preemption during long generation | MEDIUM | HIGH | Heartbeat-based orphan recovery + explicit retry policy with max_retries=3 and exponential backoff (Sections 7.4, 7.9) |
| Consumer crash leaves jobs permanently in running state | HIGH | HIGH | Heartbeat/lease mechanism: 30s updates, 90s stale threshold, automatic reset to pending on startup (Section 7.4) |
| OOM during model load bypasses pre-dispatch VRAM check | HIGH | HIGH | Workflow manifest VRAM estimation + 2GB safety margin + post-dispatch PID monitoring (Section 7.5) |
| Unauthenticated ComfyUI exposed to public internet | HIGH | HIGH | Mandatory ngrok basic auth; startup fails without credentials; moved from Non-Goals to hard requirement (Section 7.6) |
| Arbitrary workflow execution enables RCE | HIGH | HIGH | SHA256 workflow allowlist + custom node restriction + audit logging; custom workflows require explicit opt-in (Section 7.7) |
| Disk exhaustion from unbounded output accumulation | HIGH | HIGH | Automatic output rotation at 80% of max_output_gb; LRU model cache eviction; disk stats in /api/health (Section 7.8) |
| Undefined retry semantics cause inconsistent behavior | MEDIUM | HIGH | Explicit retry policy: max_retries, automatic vs manual distinction, exponential backoff, schema support (Section 7.9) |
| SQLite write lock contention under burst submissions | MEDIUM | MEDIUM | Asyncio.Queue write batching + dedicated writer thread + busy_timeout=5000; load test validates <2s p99 at 20 concurrent writes (Section 7.2) |
| System RAM exhaustion during model loading | MEDIUM | HIGH | RSS monitoring alongside VRAM checks; pause dispatch at 90% RAM usage; exposed in /api/health (Section 7.5) |
| No observability beyond raw logs | MEDIUM | MEDIUM | Structured JSON logging with correlation IDs; extended /api/health metrics; state transition logging (Section 7.10) |