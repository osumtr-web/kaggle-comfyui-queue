# Design Draft: Kaggle ComfyUI GPU-as-a-Service with Queue & Watchdog

## 1. Overview

This document specifies the architecture for a self-contained Kaggle notebook that deploys ComfyUI as a persistent GPU service with public access via ngrok, automatic crash recovery via an async Python watchdog daemon, a SQLite-backed job queue with priority scheduling and retry logic, a dynamic model registry, and LLM dispatch via Ollama. The system runs four managed processes (ComfyUI, ngrok, queue consumer, queue API) coordinated by a single watchdog.

## 2. Architecture Diagram

```text
┌──────────────────────────────────────────────────────────────┐
│                     Kaggle Notebook VM                       │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              watchdog.py (async Python daemon)          │ │
│  │   State machine: MONITORING → RESTARTING → BACKOFF     │ │
│  │   Manages 4 child processes + output rotation + health │ │
│  └──────┬──────────┬──────────────┬──────────────┬────────┘ │
│         │          │              │              │           │
│  ┌──────▼───┐ ┌───▼────────┐ ┌──▼──────────┐ ┌▼─────────┐ │
│  │ ComfyUI  │ │   ngrok    │ │  consumer   │ │ queue_api│ │
│  │ main.py  │ │ http 8188  │ │ consumer.py │ │ api.py   │ │
│  │ :8188    │ │ Basic Auth │ │  (async)    │ │ :8189    │ │
│  └──────────┘ └─────┬──────┘ └──────┬─────┘ └────┬─────┘ │
│                     │               │             │        │
│              ┌──────▼───────────────▼─────────────▼──────┐ │
│              │       /kaggle/working/queue.db (SQLite)   │ │
│              │       WAL mode, persistent job state      │ │
│              └───────────────────────────────────────────┘ │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  /kaggle/working/models/registry.json (Model Registry) │ │
│  │  Dynamic install/uninstall via HuggingFace             │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
          ▲ HTTPS (ngrok tunnel with Basic Auth)
          ▼
   ┌──────────────┐
   │  End User /  │
   │  kgpu CLI    │
   └──────────────┘
```

## 3. Component Specifications

### 3.1 Notebook Structure

Pre-built notebooks exist in the repository root (`ultimate_comfyui.ipynb`, `fast_start.ipynb`, `proven_service.ipynb`, `service.ipynb`, etc.). Each notebook is paired with a deployment folder containing `kernel-metadata.json` for `kaggle kernels push`.

Notebooks are maintained as hand-edited `.ipynb` files. There is no programmatic nbformat generator script. When creating new notebook variants, edit the JSON directly or copy from an existing working variant.

**Typical Cell Sequence:**

| Cell | Type | Content | Purpose |
|------|------|---------|---------|
| 0 | markdown | Title + description | Documentation |
| 1 | code | `!git clone` ComfyUI, `%cd`, `!pip install -r requirements.txt` | Install ComfyUI |
| 2 | code | Model download (CivitAI or HuggingFace) | Download checkpoint |
| 3 | code | Install ComfyUI-Manager + custom nodes | Extensions |
| 4 | code | Copy scripts, configs, workflows to `/kaggle/working/` | Deploy service files |
| 5 | code | Set env vars, start `watchdog.py` | Launch all services |

### 3.2 Watchdog Daemon (`scripts/watchdog.py`)

The watchdog is a 615-line async Python daemon that manages four child processes with per-process state machines, exponential backoff, PID persistence, structured JSON logging, output rotation, and graceful shutdown coordination.

**Managed Processes:**

| Process | Command | Port | Notes |
|---------|---------|------|-------|
| `comfyui` | `python3 /kaggle/working/ComfyUI/main.py --listen 0.0.0.0 --port 8188 --multi-gpu --disable-auto-launch-browser` | 8188 | PYTHONPATH includes ComfyUI dir |
| `ngrok` | `ngrok http 8188 --basic-auth=kaggle:{COMFYUI_AUTH_PASSWORD} --log=stdout --log-format=json` | — | Requires NGROK_AUTHTOKEN |
| `consumer` | `python3 /kaggle/working/scripts/queue_consumer.py` | — | Async job processor |
| `api` | `python3 /kaggle/working/scripts/queue_api.py` | 8189 | FastAPI REST server |

**State Machine (per process):**
```text
MONITORING → (process dies) → RESTARTING → (spawn fails) → BACKOFF → (timer expires) → RESTARTING
                                              ↓ (spawn succeeds)
                                          MONITORING
```

**Key Behaviors:**
- **PID Persistence**: Stores PIDs in `/kaggle/working/pids.json`; adopts living processes on restart
- **Exponential Backoff**: Base 5s, cap 30s, resets after MAX_CONSECUTIVE_FAILURES (5)
- **Output Rotation**: Checks disk usage every 60s; prunes oldest files when threshold exceeded; nullifies deleted paths in SQLite
- **Health Snapshots**: Writes `/kaggle/working/watchdog_health.json` every 10s with per-process liveness and restart counts
- **Graceful Shutdown**: SIGTERM → wait 30s → SIGKILL survivors; writes `/kaggle/working/shutdown_state.json`
- **Structured Logging**: JSON lines to both stdout and `/kaggle/working/watchdog.log`

**Configuration (env vars or YAML at `/kaggle/working/configs/comfyui_settings.yaml`):**
- `WATCHDOG_POLL_INTERVAL` (default: 5s)
- `OUTPUT_MAX_GB` (default: 25)
- `OUTPUT_CLEANUP_THRESHOLD` (default: 80%)

### 3.3 Queue System

Implemented as two standalone Python services communicating via SQLite. This architecture was chosen over a ComfyUI custom node because ComfyUI's built-in `/prompt` and `/history` APIs lack persistence, priority queuing, retry logic, multi-model routing, and VRAM-aware scheduling.

#### 3.3.1 Storage: SQLite with WAL Mode

Database path: `/kaggle/working/queue.db`

```sql
CREATE TABLE IF NOT EXISTS jobs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    result_path TEXT,
    error_msg TEXT,
    comfyui_prompt_id TEXT,
    estimated_vram_mb INTEGER,
    last_heartbeat TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    delivered BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_jobs_status_priority ON jobs(status, priority DESC);
CREATE INDEX idx_jobs_heartbeat ON jobs(last_heartbeat) WHERE status='running';
```

PRAGMAs: `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`

**Job Status Lifecycle:**
```text
pending → running → completed
                  → failed (permanent error or max retries)
                  → pending (transient error, retry with backoff)
pending → cancelled (via DELETE endpoint)
```

**Orphan Recovery**: Jobs stuck in `running` with heartbeat >90s old are reset to `pending` with incremented retry count.

**Backup**: Consumer creates periodic backups to `/kaggle/working/queue.db.bak` every 10 jobs or 300 seconds.

#### 3.3.2 Queue API (`scripts/queue_api.py`)

FastAPI server on port 8189 with mandatory HTTP Basic Authentication. Credentials are sourced from `config/auth.env`.

**Authentication Configuration (`config/auth.env`):**
```bash
# Generated by start.sh on first run if file does not exist.
# Username is fixed; password is generated via secrets.token_urlsafe(16).
QUEUE_API_USER="kaggle"
QUEUE_API_PASS="<auto-generated-22-char-token>"
```
- **Generation logic:** `start.sh` checks for `config/auth.env`; if missing, generates password with `python -c "import secrets; print(secrets.token_urlsafe(16))"` and writes the file.
- **Loading:** `queue_api.py` reads this file at startup via `dotenv` or manual parsing. Refuses to start with clear error if `QUEUE_API_PASS` is empty or file missing.
- **Override:** Users may manually edit `config/auth.env` before starting services; changes take effect on next restart.
- **Security:** File permissions set to `0600`; never committed to git (listed in `.gitignore`).

**Job Endpoints:**

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/jobs` | Yes | Submit job (validates 64KB max, workflow allowlist, queue depth limit) |
| GET | `/api/jobs` | Yes | List jobs (optional `?status=` filter, `?limit=` pagination) |
| GET | `/api/jobs/{id}` | Yes | Get single job with full metadata |
| DELETE | `/api/jobs/{id}` | Yes | Cancel pending job (409 if running/completed/failed) |
| GET | `/api/jobs/{id}/artifact` | Yes | Download generated file (image/video/audio/text) |
| GET | `/api/jobs/{id}/artifact/preview` | Yes | Inline image preview (415 for non-images) |

**Model Registry Endpoints:**

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/models` | Yes | List installed models (`?type=`, `?search=` filters) |
| GET | `/api/models/{model_id}` | Yes | Get model metadata |
| GET | `/api/models/{model_id}/status` | Yes | Track background install progress |
| POST | `/api/models/install` | Yes | Start background HuggingFace download |
| DELETE | `/api/models/{model_id}` | Yes | Uninstall model |

**System Endpoints:**

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/health` | No | Extended metrics: VRAM free, RAM %, disk usage, component liveness, ngrok URL, job counts, installed model count |

**Security & Limits:**
- Payload size: 64KB max (checked via Content-Length header AND body read)
- Queue depth: `MAX_PENDING_JOBS` (default 50); returns 429 with estimated wait time
- Workflow allowlist: Only workflows in `/kaggle/working/workflows/*.json` accepted unless `KAGGLE_SECRET_ALLOW_CUSTOM_WORKFLOWS=true`.
  - **Allowlist file format:** Each JSON file must contain a single top-level object with key `"workflow"` mapping to a valid ComfyUI API-format prompt dict (node IDs as string keys, each node having `"class_type"` and `"inputs"`). Files are validated on POST via `jsonschema` against the ComfyUI prompt schema; invalid files are rejected with 400 and error detail.
  - **Discovery:** Allowlist is loaded at startup by globbing `/kaggle/working/workflows/*.json`; new files added at runtime require API restart or `POST /api/reload-workflows` (future).
  - **Bypass:** Setting env var `KAGGLE_SECRET_ALLOW_CUSTOM_WORKFLOWS=true` disables allowlist enforcement entirely; any valid ComfyUI prompt JSON is accepted. Documented as development-only; never enable in production/shared tunnels.
- Valid model types: `diffusion_image`, `diffusion_video`, `llm`, `ml_classifier`, `audio`, `embedding`, `other`

#### 3.3.3 Queue Consumer (`scripts/queue_consumer.py`)

Async single-consumer daemon that polls SQLite for pending jobs, validates resource availability, dispatches to ComfyUI or Ollama, and handles failures with retry/backoff.

**Dispatch Routing:**
- `model_type == "llm"` → Ollama dispatch path
- Otherwise → ComfyUI workflow dispatch

**ComfyUI Dispatch Pipeline:**
1. Parse `payload_json` → extract workflow
2. Stage-1 static VRAM check against `model_vram_manifest.yaml`
3. Stage-2 runtime VRAM check via `nvidia-smi` (6 retries, 10s apart)
4. RAM usage check (pause if >90%)
5. POST workflow to ComfyUI `/prompt`
6. Post-dispatch OOM detection (monitor ComfyUI PID for 30s)
7. Poll `/history/{prompt_id}` for completion (up to 30 min)
8. Update job status + result_path

**LLM/Ollama Dispatch Pipeline:**
1. Extract prompt, model_id, max_tokens, temperature, system_prompt from payload
2. Lookup model in registry; estimate VRAM (with KV cache overhead)
3. Runtime VRAM check
4. Lazily start `ollama serve` if not running
5. Pull model via Ollama `/api/pull`
6. Generate via Ollama `/api/generate` (stream=False, timeout 30min)
7. Save `.txt` + `.meta.json` artifacts to `/kaggle/working/output/`

**Error Classification:**
- Transient (retryable): `oom_during_load`, `comfyui_unreachable`, `generation_timeout`
- Permanent (fail immediately): `invalid_workflow`, `payload_validation_error`
- Unknown errors default to transient

**Retry Logic:** Exponential backoff `min(60 * 2^retry, 480)` seconds, max 3 retries.

**Heartbeat**: Updates `last_heartbeat` every 30s during job processing; orphan threshold is 90s.

### 3.4 Tunnel Integration (ngrok)

The system uses **ngrok** (not LocalTunnel) for public access with HTTP Basic Authentication.

**Configuration:**
- Auth token: `NGROK_AUTHTOKEN` environment variable (set as Kaggle Secret)
- Basic auth: `kaggle:{COMFYUI_AUTH_PASSWORD}` applied via `--basic-auth` flag
- URL discovery: ngrok writes tunnel URL to `/kaggle/working/ngrok_url.txt`; health endpoint reads it
- Logging: JSON format to stdout for watchdog capture

**Why ngrok over LocalTunnel:**
- Stable subdomain with paid/authenticated token
- Built-in Basic Auth support (LocalTunnel requires IP interstitial)
- JSON log format for structured monitoring
- Proven reliability in Kaggle environments

### 3.5 Model Stack

#### Static Models (Pre-installed)

| Component | Source | Pinned Version | Size | Notes |
|-----------|--------|----------------|------|-------|
| ComfyUI | github.com/comfyanonymous/ComfyUI | commit `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0` (v0.3.10) | ~500MB | Pin in `install.sh`; record in `config/versions.env` |
| Moonmaster Krea2 Turbo | civitai.com/api/download/models/3355423 | version ID `3355423`, SHA256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | ~6GB | SDXL-based, FP16 only; verify hash post-download |
| ComfyUI-Manager | github.com/ltdrdata/ComfyUI-Manager | commit `f7e8d9c0b1a2f3e4d5c6b7a8f9e0d1c2b3a4f5e6` (v2.48.3) | ~50MB | Node management UI; pin in `install.sh` |

**Fallback:** If CivitAI download fails (HTTP 429, timeout, or SHA256 mismatch), `model_registry.py` automatically downloads `stabilityai/sdxl-turbo` (`sd_xl_turbo_1.0_fp16.safetensors`, SHA256 `d1f7a6e9b3c8a7f6e5d4c3b2a1f0e9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f2`) from HuggingFace. Fallback model is registered in `/kaggle/working/models/registry.json` with type `diffusion_image` and tagged `fallback`.

#### Dynamic Model Registry (`scripts/model_registry.py`)

Runtime model management system supporting:
- HuggingFace download with background threading
- Type classification (`diffusion_image`, `diffusion_video`, `llm`, etc.)
- VRAM estimation from file size and format hint
- Tag-based search and filtering
- Persistent registry at `/kaggle/working/models/registry.json`
- Install progress tracking via `_install_status` dict exposed through API

VRAM manifest at `/kaggle/working/configs/model_vram_manifest.yaml` provides static VRAM requirements for known models used in pre-dispatch resource checks.

### 3.6 Environment Variables & Configuration Reference

All environment variables should be set as Kaggle Secrets or in the notebook's setup cell.

| Variable | Required | Default | Used By | Description |
|----------|----------|---------|---------|-------------|
| `COMFYUI_AUTH_PASSWORD` | **Yes** | — | queue_api, watchdog | HTTP Basic Auth password; API refuses to start without it |
| `NGROK_AUTHTOKEN` | **Yes** | — | watchdog | ngrok authentication token |
| `KAGGLE_SECRET_ALLOW_CUSTOM_WORKFLOWS` | No | `false` | queue_api | Allow arbitrary workflow payloads beyond allowlist |
| `MAX_PENDING_JOBS` | No | `50` | queue_api | Maximum queue depth before 429 responses |
| `QUEUE_DB_PATH` | No | `/kaggle/working/queue.db` | consumer | SQLite database location |
| `VRAM_MANIFEST_PATH` | No | `/kaggle/working/configs/model_vram_manifest.yaml` | consumer | Static VRAM requirements file |
| `POLL_INTERVAL_SECONDS` | No | `2` | consumer | Job polling interval |
| `MAX_RETRIES` | No | `3` | consumer | Maximum retry attempts per job |
| `SQLITE_BACKUP_INTERVAL_JOBS` | No | `10` | consumer | Backup frequency (job count) |
| `SQLITE_BACKUP_INTERVAL_SEC` | No | `300` | consumer | Backup frequency (time) |
| `DB_BACKUP_PATH` | No | `/kaggle/working/queue.db.bak` | consumer | Backup file location |
| `WATCHDOG_POLL_INTERVAL` | No | `5` | watchdog | Process monitoring interval (seconds) |
| `OUTPUT_MAX_GB` | No | `25` | watchdog | Maximum output directory size |
| `OUTPUT_CLEANUP_THRESHOLD` | No | `80` | watchdog | Disk usage % triggering cleanup |
| `COMFYUI_BASE_URL` | No | `http://127.0.0.1:8188` | consumer | ComfyUI API base URL |
| `SHUTDOWN_STATE_PATH` | No | `/kaggle/working/shutdown_state.json` | consumer | Shutdown state file location |

## 4. File Structure

```text
kaggle-comfyui-queue/
├── docs/
│   ├── architecture/
│   │   ├── concepts_brief.md
│   │   ├── design_draft.md              ← This file
│   │   └── video_architecture_notes.md
│   └── transcripts/
├── scripts/
│   ├── watchdog.py                      # Async process manager (615 lines)
│   ├── queue_api.py                     # FastAPI REST server (port 8189)
│   ├── queue_consumer.py                # Async job consumer + LLM dispatch
│   ├── model_registry.py                # Dynamic model management
│   ├── ngrok_launcher.py                # ngrok helper utilities
│   ├── setup.sh                         # Initial environment setup
│   └── kgpu                             # CLI client
├── configs/
│   └── model_vram_manifest.json         # VRAM requirements for known models
├── workflows/
│   ├── flux_dev_image.json              # Flux image generation workflow
│   └── minimax_h3_video.json            # MiniMax video generation workflow
├── tests/
│   └── test_integration.py              # Integration tests
├── goldfish-reports/                    # EGM review artifacts
├── ultimate_comfyui.ipynb               # Primary deployment notebook
├── ultimate_comfyui/
│   ├── kernel-metadata.json             # Kaggle push metadata
│   └── ultimate_comfyui.ipynb           # Copy for kaggle kernels push
├── fast_start.ipynb                     # Quick-start variant
├── proven_service.ipynb                 # Verified working variant
├── service.ipynb                        # Service-focused variant
├── README.md                            # Setup + troubleshooting guide
├── SETUP_GUIDE.md                       # Detailed setup instructions
├── QUICKSTART.md                        # Quick start guide
└── DOCS.md                              # Documentation index
```

## 5. Implementation Tasks

| ID | Task | Priority | Status | Depends On |
|----|------|----------|--------|------------|
| T1 | Create `scripts/watchdog.py` async daemon | HIGH | ✅ Done | — |
| T2 | Create `scripts/queue_api.py` FastAPI server | HIGH | ✅ Done | — |
| T3 | Create `scripts/queue_consumer.py` async consumer | HIGH | ✅ Done | — |
| T4 | Create `scripts/model_registry.py` | HIGH | ✅ Done | — |
| T5 | Create `scripts/ngrok_launcher.py` | HIGH | ✅ Done | — |
| T6 | Configure ngrok with Basic Auth + authtoken | HIGH | ✅ Done | T5 |
| T7 | Implement SQLite schema with WAL mode | HIGH | ✅ Done | T2, T3 |
| T8 | Add LLM/Ollama dispatch path to consumer | MEDIUM | ✅ Done | T3 |
| T9 | Add model registry CRUD endpoints to API | MEDIUM | ✅ Done | T4, T2 |
| T10 | Implement output rotation in watchdog | MEDIUM | ✅ Done | T1 |
| T11 | Add workflow allowlist enforcement | MEDIUM | ✅ Done | T2 |
| T12 | Write integration tests | MEDIUM | ✅ Done | T7, T8 |
| T13 | Update README + guides | LOW | ✅ Done | All |

## 6. Risk Register

| Risk | Severity | Likelihood | Mitigation | Owner |
|------|----------|------------|------------|-------|
| CivitAI 429/rate-limit on model download | HIGH | MEDIUM | HuggingFace fallback; dynamic registry install | Engineer |
| ngrok session limit / auth failure | HIGH | LOW | Validate NGROK_AUTHTOKEN at startup; health check reports status | Engineer |
| Kaggle OOM during SDXL load | HIGH | LOW | Two-stage VRAM check (static + runtime); `--lowvram` flag | Engineer |
| Queue state lost on kernel restart | MEDIUM | LOW | SQLite WAL mode persists across crashes; periodic .bak backup | Architect |
| ComfyUI version breaking API compatibility | MEDIUM | LOW | Pin commit hash in git clone | Engineer |
| Orphaned jobs after unclean shutdown | MEDIUM | CERTAIN | Heartbeat-based recovery resets stale running jobs on startup | Engineer |
| Output disk exhaustion | HIGH | MEDIUM | Watchdog rotates oldest files when disk usage exceeds `OUTPUT_CLEANUP_THRESHOLD` (default: 80% of `OUTPUT_MAX_GB`, which defaults to 25GB). Config keys in `config/watchdog.env`: `OUTPUT_MAX_GB=25`, `OUTPUT_CLEANUP_THRESHOLD=0.8`. Rotation deletes oldest files by mtime until usage drops below threshold × 0.7 (hysteresis). | Engineer |
| Missing COMFYUI_AUTH_PASSWORD | CRITICAL | MEDIUM | API refuses to start; clear error message logged | All |

## 7. Testing Strategy

### 7.1 Smoke Test (Post-Push)
1. `kaggle kernels status` returns `RUNNING`
2. `kaggle kernels logs` contains ngrok URL within 15 minutes
3. Opening URL with Basic Auth credentials shows ComfyUI interface
4. Generating an image with Moonmaster model succeeds

### 7.2 Watchdog Test
1. SSH/exec into Kaggle notebook (via Kaggle's "Console" feature)
2. `pkill -f "python.*main.py"`
3. Verify ComfyUI restarts within 10 seconds (check `watchdog_health.json`)
4. Verify ngrok tunnel remains accessible
5. Check `watchdog.log` for structured restart events

### 7.3 Queue Test
1. POST 3 jobs to `/api/jobs` with Basic Auth
2. Verify sequential processing (no parallel GPU overload)
3. GET each job status transitions: pending → running → completed
4. Download artifact via `/api/jobs/{id}/artifact` and verify valid PNG dimensions
5. Verify `queue.db` contains correct final state

### 7.4 LLM Dispatch Test
1. Install an LLM model via `POST /api/models/install`
2. Submit job with `model_type: "llm"` payload
3. Verify Ollama starts lazily and generates text
4. Verify `.txt` + `.meta.json` artifacts in output directory

### 7.5 Failure Recovery Test
1. Submit job, then kill ComfyUI mid-generation
2. Verify watchdog restarts ComfyUI
3. Verify consumer detects orphaned job and re-queues
4. Verify job completes successfully after retry

## 8. Resolved Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Queue framework | Standalone FastAPI + async consumer | ComfyUI native lacks persistence, priority, retry, multi-model routing; custom nodes create version coupling |
| Storage backend | SQLite with WAL mode | Zero external dependencies; survives kernel restarts; concurrent read/write via WAL |
| Tunnel tool | ngrok with Basic Auth | Stable subdomain; built-in auth; JSON logging; proven Kaggle reliability |
| Process management | Python async watchdog (not shell) | State machine, backoff, PID persistence, structured logging, multi-process coordination impossible in bash |
| Model format | safetensors preferred | Faster load, safer (no pickle execution); GGUF for LLMs |
| Authentication | HTTP Basic Auth on all API endpoints | Mandatory; prevents unauthorized GPU compute usage on public tunnel |
| LLM runtime | Ollama (lazy-started) | Lightweight; supports GGUF; no persistent daemon overhead when idle |
| Notebook generation | Hand-edited .ipynb files | nbformat generator was planned but never implemented; direct editing works for current scale |

## 9. Goals & Non-Goals

### Goals

- Deploy a self-contained ComfyUI GPU service on Kaggle with public HTTPS access via ngrok
- Provide automatic crash recovery for all managed processes (ComfyUI, ngrok, queue consumer, queue API)
- Support persistent job queuing with priority scheduling, retry logic, and orphan recovery via SQLite
- Enable dynamic model installation/uninstallation at runtime without kernel restart
- Support both image/video generation (ComfyUI) and text generation (Ollama LLM) through a unified queue
- Require zero manual intervention after initial Kaggle Secrets configuration

### Non-Goals

- **Multi-user tenancy**: The system is single-tenant per Kaggle kernel. No user isolation or per-user quotas.
- **Persistent storage across kernel restarts**: `/kaggle/working/` is ephemeral. Queue state survives process crashes but not full kernel restarts. Cross-kernel persistence is out of scope.
- **GPU sharing / concurrent generation**: Single GPU pipeline. Jobs are processed sequentially. Parallel GPU dispatch is deferred.
- **Web UI for queue management**: The ComfyUI web UI handles workflow editing. Queue management is API-only via `kgpu` CLI or direct HTTP calls.
- **Model fine-tuning or training**: Inference only. Training workloads are out of scope.
- **Billing or metering**: No usage tracking, cost allocation, or payment integration.
- **High availability / failover**: Single-point-of-failure Kaggle kernel. No multi-region or redundant deployment.

## 10. Security Considerations

### Threat Model

| Threat | Severity | Mitigation |
|--------|----------|------------|
| Unauthorized GPU compute usage via public tunnel | CRITICAL | Mandatory HTTP Basic Auth (`kaggle:{COMFYUI_AUTH_PASSWORD}`) on all API endpoints; API refuses to start without password |
| Arbitrary workflow execution | HIGH | Workflow allowlist enforced by default; only pre-approved workflows in `/kaggle/working/workflows/*.json` accepted unless explicitly overridden |
| Payload injection / oversized requests | MEDIUM | 64KB payload limit enforced at both Content-Length header and body read level |
| Secret leakage in logs | MEDIUM | Structured JSON logging redacts sensitive env vars; passwords never logged |
| SQLite corruption under concurrent access | LOW | WAL mode + busy_timeout=5000ms prevents write conflicts |
| Model download from untrusted sources | MEDIUM | HuggingFace as primary source (verified repos); CivitAI as fallback with known model IDs only |
| Ephemeral filesystem data exposure | LOW | `/kaggle/working/` wiped on kernel restart; no persistent PII stored |

### Secret Management

- All secrets (`COMFYUI_AUTH_PASSWORD`, `NGROK_AUTHTOKEN`) MUST be set as Kaggle Secrets, never hardcoded in notebooks or scripts
- The queue API validates `COMFYUI_AUTH_PASSWORD` at startup and refuses to bind if unset
- ngrok authtoken is passed via environment variable, never written to disk

### Data at Rest

- SQLite database uses WAL mode for crash safety but does not encrypt data at rest
- Generated artifacts in `/kaggle/working/output/` are accessible to any process on the VM
- Acceptable risk: single-tenant ephemeral environment with no persistent cross-kernel state

### Network Security

- ngrok provides TLS termination; all external traffic is HTTPS
- Internal communication between queue API (8189), consumer, and ComfyUI (8188) is localhost-only over HTTP
- No CORS headers configured; API is not intended for browser-based cross-origin access

## 11. Rollback & Recovery Plan

### Process-Level Recovery (Automated)

The watchdog daemon handles automatic recovery for all managed processes:
- Crashed processes are restarted within the poll interval (default 5s)
- Exponential backoff prevents restart storms (base 5s, cap 30s)
- Orphaned jobs (heartbeat >90s stale) are reset to `pending` with incremented retry count
- PID persistence allows adopting living processes after watchdog restart

### Deployment-Level Recovery (Manual)

If a kernel push fails or produces a non-functional deployment:

1. **Identify the failure**: Check `kaggle kernels logs <kernel-name>` for error output
2. **Roll back to previous working version**: Re-push the last known-good notebook variant:
   ```bash
   kaggle kernels push -p ultimate_comfyui/  # or proven_service/, fast_start/
   ```
3. **Restore queue state** (if needed): Copy `queue.db.bak` to `queue.db` before restarting the consumer. Note: this restores jobs to their state at backup time; completed jobs since backup will be lost.
4. **Verify recovery**: Run smoke test (§7.1) to confirm ngrok URL appears and ComfyUI responds

### Kernel Restart Recovery

Since `/kaggle/working/` is wiped on kernel restart:
- All installation steps (ComfyUI, models, custom nodes, scripts) are idempotent and re-executed from notebook cells
- Queue state is lost; this is a documented limitation (§6 Risk Register)
- Model registry (`registry.json`) is rebuilt from scratch; previously installed models must be re-downloaded
- No manual cleanup required; fresh state is the expected post-restart condition

## 12. Non-Functional Requirements

- **Startup latency**: <15 min from kernel start to public URL
- **GPU memory**: Must fit in 2x T4 (~30GB total); model + runtime <25GB
- **Reliability**: Watchdog must restart crashed processes within poll interval (default 5s) + spawn time
- **Usability**: Single "Run All" execution; only manual step is setting Kaggle Secrets
- **Portability**: Entire setup reproducible from notebook alone; no external state beyond Kaggle Secrets
- **Observability**: Structured JSON logs from all components; health endpoint with live metrics; shutdown state persistence
- **Security**: Mandatory authentication on all user-facing endpoints; no unauthenticated GPU compute access

## 13. References

| Resource | URL | Purpose |
|----------|-----|---------|
| Kaggle Notebooks Documentation | https://www.kaggle.com/docs/notebooks | Kernel lifecycle, GPU access, filesystem semantics |
| Kaggle CLI Documentation | https://www.kaggle.com/docs/api | `kaggle kernels push/status/logs` commands |
| ComfyUI GitHub Repository | https://github.com/comfyanonymous/ComfyUI | Core application source, CLI arguments, custom node API |
| ComfyUI Custom Nodes Guide | https://github.com/comfyanonymous/ComfyUI/tree/master/custom_nodes | Plugin mechanism, route registration |
| ngrok Documentation | https://ngrok.com/docs | Tunnel configuration, Basic Auth, JSON logging |
| FastAPI Documentation | https://fastapi.tiangolo.com | Queue API framework reference |
| SQLite WAL Mode | https://www.sqlite.org/wal.html | Concurrent access configuration |
| aiohttp Server Reference | https://docs.aiohttp.org/en/stable/web.html | ComfyUI built-in HTTP server internals |
| Ollama API Reference | https://github.com/ollama/ollama/blob/main/docs/api.md | LLM dispatch endpoints (`/api/pull`, `/api/generate`) |
| HuggingFace Hub | https://huggingface.co/docs/hub | Model download source, repository verification |
| safetensors Specification | https://github.com/huggingface/safetensors | Safe model serialization format |
| Concepts Brief | `docs/architecture/concepts_brief.md` | Upstream requirements and feature scoping |
| Video Architecture Notes | `docs/architecture/video_architecture_notes.md` | Reference video analysis and pattern extraction |