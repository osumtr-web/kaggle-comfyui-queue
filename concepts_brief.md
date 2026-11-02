# Kaggle ComfyUI Generation Queue Service

* **Author:** Autonomous EGM Orchestrator
* **Date:** 2026-11-03

## Overview

A self-contained GPU generation service running on Kaggle's free 32GB VRAM (2x T4) environment. The system installs ComfyUI as the core generation engine, exposes it via ngrok for remote access, runs a watchdog process to maintain service health and auto-restart on crashes, and implements a persistent work queue for batching image/video generation jobs. The architecture is derived from proven Kaggle notebook patterns demonstrated in community tutorials for MiniMax H3 video generation and Fused Turbo LoRA workflows.

## Analysis

**Infrastructure Reality:** Kaggle provides 2x Tesla T4 GPUs (~30GB combined VRAM), 30GB RAM, and ~30 hours/week of free GPU compute. Sessions are ephemeral — any installed software must be reinstalled on each notebook start. This makes the setup script idempotent and fast (<5 minutes to ready state).

**ComfyUI as Engine:** ComfyUI is the correct choice over A1111/Forge because it natively supports multi-GPU splitting, custom node pipelines for video models (MiniMax H3, LTX), and has an API-first architecture that enables queue integration without UI scraping.

**ngrok Exposure:** Kaggle notebooks run in isolated containers with no public IP. ngrok creates a stable HTTPS tunnel to the ComfyUI web server (port 8188). Free tier provides sufficient bandwidth for personal use. Alternative: cloudflared (Cloudflare Tunnel) if ngrok rate-limits.

**Watchdog Necessity:** Kaggle sessions can crash due to OOM, GPU preemption, or idle timeout. A lightweight Python watchdog monitors the ComfyUI process, ngrok tunnel, and queue consumer — restarting any failed component within 5 seconds. Without this, generation jobs silently fail during long unattended runs.

**Queue Architecture:** SQLite-backed FIFO queue stored in `/kaggle/working/queue.db` (persists across cell re-runs within a session). Jobs are JSON payloads with prompt, model, resolution, priority, and callback URL. A single-threaded consumer pulls jobs sequentially to avoid VRAM contention. Multi-worker is out of scope — dual T4s are already fully utilized by single high-res video generations.

**Model Support:** Primary targets are MiniMax H3 (33B video), Fused Turbo INT8 (fast video), Flux Dev/Pro (image), and SDXL (fallback image). Models download to `/kaggle/working/models/` on first use, cached in Kaggle's persistent output directory.

## Goals vs. Non-Goals

* **In-Scope Goals:**
  - One-click Kaggle notebook that installs ComfyUI + dependencies + ngrok + watchdog
  - Persistent SQLite job queue with submit/status/cancel API endpoints
  - Watchdog auto-recovery for ComfyUI, ngrok, and queue consumer processes
  - Pre-configured workflows for MiniMax H3 video and Flux image generation
  - ngrok URL printed in notebook output for immediate remote access
  - GPU memory monitoring with automatic job pausing when VRAM < 2GB free

* **Out-of-Scope Non-Goals:**
  - Multi-user authentication (single-user personal tool)
  - Distributed queue across multiple Kaggle sessions
  - Model training or fine-tuning infrastructure
  - Web dashboard beyond ComfyUI's native UI
  - Automatic model downloading for all possible models (only curated set)
  - Persistent storage across separate Kaggle sessions (use Kaggle Datasets for that)

## Alternatives Weighed

| Alternative | Rejected Because |
|---|---|
| A1111 WebUI | No native API queue, poor multi-GPU support, heavier VRAM overhead |
| Cloudflare Tunnel (cloudflared) | Viable backup, but ngrok has better Kaggle compatibility docs; keep as fallback |
| Redis/RabbitMQ queue | Overkill for single-container ephemeral environment; SQLite is zero-dependency |
| Docker-in-Kaggle | Kaggle doesn't support nested containers; direct pip install is only option |
| Gradio wrapper around ComfyUI | Adds latency and complexity; ComfyUI API is already REST-accessible |
| Celery worker pool | Requires broker service; violates zero-dependency constraint for ephemeral env |

## Key Decisions & Staging for Next Steps

1. **Single notebook entry point:** `setup.ipynb` is the user-facing entry point. Cell 1 invokes `!bash scripts/setup.sh` for idempotent installation. Internal helper scripts are permitted for testability and bash-native constructs; users never interact with them directly. (Updated per M-03/C-01 resolution.)
2. **SQLite queue schema:** `jobs(id, payload_json, status, priority, created_at, started_at, completed_at, last_heartbeat, retry_count, max_retries, result_path, delivered, error_msg, comfyui_prompt_id, estimated_vram_mb)` with indexes on status+priority and heartbeat. Priority field retained for future-proofing despite single-user scope. (Updated per B-04, B-06, B-03 resolutions.)
3. **Watchdog implementation:** Python asyncio state machine (MONITORING→RESTARTING→BACKOFF) with structured JSON logging, coordinated graceful shutdown via sentinel file, and heartbeat-based orphan recovery integration. Bash alternative evaluated and rejected due to observability/recovery requirements. (Updated per M-02/C-02 resolution.)
4. **ngrok auth token:** Stored as Kaggle secret (`KAGGLE_SECRET_NGROK_TOKEN`), never hardcoded. Mandatory basic auth (`COMFYUI_AUTH_PASSWORD`) added as hard requirement. Cloudflared fallback fully implemented as first-class alternative with integration test. (Updated per B-01, M-19/C-03 resolutions.)
5. **Setup time target:** Targets <5 minute installation on fresh Kaggle GPU session (T4 x2, cold cache). Actual time varies with network conditions. Large model downloads deferred to lazy loading if timed dry-run exceeds budget. (Added per M-32/C-05 resolution.)
6. **Project folder structure on local machine:** Mirrors Kaggle layout for development/testing parity. Scripts in `scripts/`, configs in `configs/`, workflows in `workflows/`.
7. **Next phase:** Implementation per resolved design draft. All blockers and majors addressed in `design_draft.md` Sections 4.6–4.8, 6, 7.1.1–7.1.2, 7.3.1–7.3.3, 7.4, 7.11–7.12.