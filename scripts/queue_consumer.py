#!/usr/bin/env python3
"""
Kaggle ComfyUI Generation Queue Service — Async Job Consumer

Pulls jobs from SQLite, validates VRAM/RAM availability, dispatches to
ComfyUI API, polls for completion, and handles failures with retry logic.

Design references: design_draft.md Sections 5.3, 7.4, 7.5
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
import yaml

# ---------------------------------------------------------------------------
# Model Registry (for LLM dispatch)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from model_registry import ModelRegistry  # noqa: E402

registry = ModelRegistry(base_dir="/kaggle/working/models", auto_scan=False)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_PATH = os.environ.get("QUEUE_DB_PATH", "/kaggle/working/queue.db")
COMFYUI_BASE = os.environ.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
VRAM_MANIFEST_PATH = os.environ.get(
    "VRAM_MANIFEST_PATH", "/kaggle/working/configs/model_vram_manifest.yaml"
)
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "2"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
VRAM_SAFETY_MARGIN_MB = 2048
RAM_THRESHOLD_PCT = 90.0
HEARTBEAT_INTERVAL_S = 30
ORPHAN_HEARTBEAT_AGE_S = 90
COMPLETION_POLL_INTERVAL_S = 5
COMPLETION_MAX_POLLS = 360  # 30 minutes
POST_DISPATCH_OOM_WINDOW_S = 30
SHUTDOWN_STATE_PATH = os.environ.get(
    "SHUTDOWN_STATE_PATH", "/kaggle/working/shutdown_state.json"
)
TRANSIENT_ERRORS = {"oom_during_load", "comfyui_unreachable", "generation_timeout"}
PERMANENT_ERRORS = {"invalid_workflow", "payload_validation_error"}

# SQLite backup configuration (Major 6.2)
SQLITE_BACKUP_INTERVAL_JOBS = int(os.environ.get("SQLITE_BACKUP_INTERVAL_JOBS", "10"))
SQLITE_BACKUP_INTERVAL_SEC = int(os.environ.get("SQLITE_BACKUP_INTERVAL_SEC", "300"))
DB_BACKUP_PATH = os.environ.get("DB_BACKUP_PATH", "/kaggle/working/queue.db.bak")

# ---------------------------------------------------------------------------
# Structured JSON logger
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_obj["exception"] = self.formatException(record.exc_info)
        # Attach extra fields set via LoggerAdapter or extra={}
        for key in ("job_id", "prompt_id", "error_msg", "retry_count"):
            val = getattr(record, key, None)
            if val is not None:
                log_obj[key] = val
        return json.dumps(log_obj)


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("queue_consumer")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


log = _setup_logging()

# ---------------------------------------------------------------------------
# Database helpers (synchronous sqlite3 — fast enough for single-consumer)
# ---------------------------------------------------------------------------


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def recover_orphaned_jobs(conn: sqlite3.Connection) -> int:
    """Reset running jobs whose heartbeat is stale (>90 s)."""
    cutoff = datetime.now(timezone.utc).timestamp() - ORPHAN_HEARTBEAT_AGE_S
    cur = conn.execute(
        """
        UPDATE jobs
           SET status = 'pending',
               retry_count = retry_count + 1,
               started_at = NULL,
               last_heartbeat = NULL
         WHERE status = 'running'
           AND (last_heartbeat IS NULL OR last_heartbeat < ?)
        """,
        (cutoff,),
    )
    conn.commit()
    recovered = cur.rowcount
    if recovered:
        log.info("Recovered %d orphaned job(s)", recovered)
    return recovered


def claim_pending_job(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Atomically select and claim one pending job."""
    row = conn.execute(
        """
        SELECT * FROM jobs
         WHERE status = 'pending'
         ORDER BY priority DESC, created_at ASC
         LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None

    cur = conn.execute(
        """
        UPDATE jobs
           SET status = 'running',
               started_at = CURRENT_TIMESTAMP,
               last_heartbeat = CURRENT_TIMESTAMP
         WHERE id = ? AND status = 'pending'
        """,
        (row["id"],),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None  # another consumer won the race

    # Re-fetch to get updated timestamps
    updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
    return dict(updated)


def update_job(conn: sqlite3.Connection, job_id: str, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)
    conn.commit()


def update_heartbeat(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET last_heartbeat = CURRENT_TIMESTAMP WHERE id = ?", (job_id,)
    )
    conn.commit()


# ---------------------------------------------------------------------------
# VRAM / RAM helpers
# ---------------------------------------------------------------------------


def load_vram_manifest(path: str) -> Dict[str, int]:
    """Return {model_name: vram_mb} from YAML manifest."""
    p = Path(path)
    if not p.exists():
        log.warning("VRAM manifest not found at %s — static check disabled", path)
        return {}
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    manifest: Dict[str, int] = {}
    models = data.get("models", data) if isinstance(data, dict) else {}
    if isinstance(models, dict):
        for name, info in models.items():
            if isinstance(info, dict):
                manifest[name] = int(info.get("vram_mb", 0))
            elif isinstance(info, (int, float)):
                manifest[name] = int(info)
    return manifest


def estimate_workflow_vram(workflow: Any, manifest: Dict[str, int]) -> int:
    """Walk workflow nodes, sum vram_mb for referenced models."""
    total = 0
    seen: set[str] = set()
    if isinstance(workflow, dict):
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs", {})
            if isinstance(inputs, dict):
                ckpt = inputs.get("ckpt_name") or inputs.get("model_name")
                if ckpt and ckpt not in seen:
                    seen.add(ckpt)
                    total += manifest.get(ckpt, 0)
                lora = inputs.get("lora_name")
                if lora and lora not in seen:
                    seen.add(lora)
                    total += manifest.get(lora, 0)
    return total


def get_gpu_free_mb(gpu_index: int = 0) -> Optional[int]:
    """Query nvidia-smi for free VRAM on given GPU. Returns None on error."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free",
                "--format=csv,noheader,nounits",
                "-i",
                str(gpu_index),
            ],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        parts = out.decode().strip().split(",")
        return int(parts[-1].strip())
    except Exception:
        return None


def get_ram_usage_pct() -> float:
    """Return system RAM usage percentage via /proc/meminfo or psutil."""
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        mem: Dict[str, int] = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                mem[key] = int(parts[1])
        total = mem.get("MemTotal", 1)
        available = mem.get("MemAvailable", mem.get("MemFree", 0))
        return ((total - available) / total) * 100.0
    except Exception:
        pass
    try:
        import psutil  # type: ignore

        return psutil.virtual_memory().percent
    except Exception:
        return 0.0


async def check_vram_runtime(
    required_margin_mb: int = VRAM_SAFETY_MARGIN_MB,
) -> bool:
    """Stage-2 runtime VRAM check with retries."""
    for attempt in range(6):
        free = get_gpu_free_mb(0)
        if free is not None and free >= required_margin_mb:
            return True
        log.warning(
            "GPU free VRAM insufficient (%s MB < %d MB), attempt %d/6",
            free,
            required_margin_mb,
            attempt + 1,
        )
        await asyncio.sleep(10)
    return False


# ---------------------------------------------------------------------------
# ComfyUI interaction
# ---------------------------------------------------------------------------


async def dispatch_to_comfyui(
    session: aiohttp.ClientSession, workflow: Any
) -> Dict[str, Any]:
    """POST workflow to ComfyUI /prompt. Returns parsed JSON response."""
    payload = {"prompt": workflow}
    async with session.post(
        f"{COMFYUI_BASE}/prompt", json=payload, timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {body}")
        return json.loads(body)


async def poll_completion(
    session: aiohttp.ClientSession, prompt_id: str
) -> Dict[str, Any]:
    """Poll /history/{prompt_id} until success/error/timeout."""
    consecutive_errors = 0
    for _ in range(COMPLETION_MAX_POLLS):
        try:
            async with session.get(
                f"{COMFYUI_BASE}/history/{prompt_id}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    consecutive_errors = 0
                    entry = data.get(prompt_id)
                    if entry is not None:
                        status = entry.get("status", {})
                        status_str = status.get("status_str", "")
                        if status_str == "success":
                            outputs = entry.get("outputs", {})
                            images = outputs.get("images", [])
                            filenames = [img.get("filename", "") for img in images]
                            result_path = ""
                            if filenames:
                                result_path = filenames[0]
                            return {
                                "ok": True,
                                "result_path": result_path,
                                "filenames": filenames,
                            }
                        if status_str == "error":
                            exc_msg = status.get(
                                "exception_message", "unknown_comfyui_error"
                            )
                            return {"ok": False, "error_msg": exc_msg}
                else:
                    consecutive_errors += 1
        except (aiohttp.ClientError, asyncio.TimeoutError):
            consecutive_errors += 1

        if consecutive_errors >= 3:
            return {"ok": False, "error_msg": "comfyui_unreachable"}

        await asyncio.sleep(COMPLETION_POLL_INTERVAL_S)

    return {"ok": False, "error_msg": "generation_timeout"}


async def monitor_comfyui_pid(
    pid: int, window_s: int = POST_DISPATCH_OOM_WINDOW_S
) -> bool:
    """Return True if process stays alive for the full window."""
    deadline = time.monotonic() + window_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass  # process exists but we can't signal it
        await asyncio.sleep(1)
    return True


def find_comfyui_pid() -> Optional[int]:
    """Best-effort lookup of ComfyUI main process PID."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "main.py.*--listen"], stderr=subprocess.DEVNULL, timeout=5
        )
        pids = out.decode().strip().splitlines()
        if pids:
            return int(pids[0])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# SQLite Backup (Major 6.2)
# ---------------------------------------------------------------------------

_jobs_since_backup = 0
_last_backup_time = time.monotonic()


def maybe_backup_db(conn: sqlite3.Connection) -> None:
    """Periodically backup queue.db to .bak file."""
    global _jobs_since_backup, _last_backup_time
    import shutil

    now = time.monotonic()
    if _jobs_since_backup >= SQLITE_BACKUP_INTERVAL_JOBS or \
       (now - _last_backup_time) >= SQLITE_BACKUP_INTERVAL_SEC:
        try:
            conn.commit()
            shutil.copy2(DB_PATH, DB_BACKUP_PATH)
            _last_backup_time = now
            _jobs_since_backup = 0
            log.info("SQLite backup created at %s", DB_BACKUP_PATH)
        except Exception as exc:
            log.error("SQLite backup failed: %s", exc)


# ---------------------------------------------------------------------------
# Heartbeat background task
# ---------------------------------------------------------------------------


async def heartbeat_loop(conn: sqlite3.Connection, job_id: str, stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_S)
        except asyncio.TimeoutError:
            pass
        if not stop.is_set():
            try:
                update_heartbeat(conn, job_id)
            except Exception as exc:
                log.warning("Heartbeat update failed for job %s: %s", job_id, exc)


# ---------------------------------------------------------------------------
# Retry / failure handling
# ---------------------------------------------------------------------------


def classify_error(error_msg: str) -> str:
    """Return 'transient' or 'permanent'."""
    msg_lower = error_msg.lower()
    for tag in TRANSIENT_ERRORS:
        if tag in msg_lower:
            return "transient"
    for tag in PERMANENT_ERRORS:
        if tag in msg_lower:
            return "permanent"
    # Default: treat unknown errors as transient so they get retried
    return "transient"


def handle_failure(
    conn: sqlite3.Connection,
    job: Dict[str, Any],
    error_msg: str,
) -> Optional[float]:
    """Mark job failed or re-queue for retry. Returns backoff seconds or None."""
    retry_count = int(job.get("retry_count", 0))
    classification = classify_error(error_msg)

    if classification == "permanent" or retry_count >= MAX_RETRIES:
        update_job(
            conn,
            job["id"],
            status="failed",
            error_msg=error_msg,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        log.info(
            "Job %s permanently failed: %s (retries=%d)",
            job["id"],
            error_msg,
            retry_count,
            extra={"job_id": job["id"], "error_msg": error_msg},
        )
        return None

    # Transient → re-queue with backoff
    new_retry = retry_count + 1
    backoff = min(60 * (2**new_retry), 480)
    update_job(
        conn,
        job["id"],
        status="pending",
        retry_count=new_retry,
        error_msg=error_msg,
        started_at=None,
        last_heartbeat=None,
    )
    log.info(
        "Job %s queued for retry %d/%d, backoff %ds: %s",
        job["id"],
        new_retry,
        MAX_RETRIES,
        backoff,
        error_msg,
        extra={"job_id": job["id"], "retry_count": new_retry, "error_msg": error_msg},
    )
    return backoff


# ---------------------------------------------------------------------------
# SIGTERM / graceful shutdown
# ---------------------------------------------------------------------------


class ShutdownCoordinator:
    def __init__(self):
        self.requested = asyncio.Event()
        self.in_flight_job: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ollama_proc: Optional[subprocess.Popen] = None

    def install(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._on_signal)

    def _on_signal(self):
        log.info("Shutdown signal received")
        self.requested.set()
        # Terminate Ollama if running
        if self._ollama_proc is not None:
            try:
                self._ollama_proc.terminate()
                self._ollama_proc.wait(timeout=5)
                log.info("Ollama process terminated on shutdown")
            except Exception as exc:
                log.warning("Failed to terminate Ollama: %s", exc)
                try:
                    self._ollama_proc.kill()
                except Exception:
                    pass
            self._ollama_proc = None

    async def wait_in_flight(self, timeout_s: float = 30.0):
        if self.in_flight_job is None:
            return
        log.info(
            "Waiting up to %.0fs for in-flight job %s", timeout_s, self.in_flight_job
        )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.in_flight_job is None:
                return
            await asyncio.sleep(1)

    def write_shutdown_state(self, conn: sqlite3.Connection):
        state = {
            "shutdown_ts": datetime.now(timezone.utc).isoformat(),
            "in_flight_job": self.in_flight_job,
        }
        try:
            conn.commit()
            with open(SHUTDOWN_STATE_PATH, "w") as f:
                json.dump(state, f)
            log.info("Wrote shutdown state to %s", SHUTDOWN_STATE_PATH)
        except Exception as exc:
            log.error("Failed to write shutdown state: %s", exc)


# ---------------------------------------------------------------------------
# Ollama lifecycle helpers (LLM dispatch)
# ---------------------------------------------------------------------------

OLLAMA_BASE = "http://127.0.0.1:11434"


async def _ensure_ollama_running(shutdown: ShutdownCoordinator) -> bool:
    """Start Ollama lazily if not already running. Returns True when ready."""
    # Check if already responsive
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{OLLAMA_BASE}/api/tags",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status == 200:
                    return True
    except Exception:
        pass

    # Start ollama serve
    if shutdown._ollama_proc is None or shutdown._ollama_proc.poll() is not None:
        log.info("Starting Ollama server (lazy init)")
        try:
            shutdown._ollama_proc = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            log.info("Ollama process started (pid=%s)", shutdown._ollama_proc.pid)
        except FileNotFoundError:
            log.error("ollama binary not found in PATH")
            return False
        except Exception as exc:
            log.error("Failed to start Ollama: %s", exc)
            return False

    # Poll until ready (up to 30 s)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{OLLAMA_BASE}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    if resp.status == 200:
                        log.info("Ollama server is ready")
                        return True
        except Exception:
            pass
        await asyncio.sleep(1)

    log.error("Ollama did not become ready within 30 s")
    return False


async def _dispatch_llm(
    conn: sqlite3.Connection,
    session: aiohttp.ClientSession,
    job: Dict[str, Any],
    payload: Dict[str, Any],
    shutdown: ShutdownCoordinator,
) -> Optional[float]:
    """Dispatch an LLM generation job via Ollama. Returns backoff or None."""
    job_id = job["id"]

    # --- Extract parameters --------------------------------------------------
    prompt = payload.get("prompt")
    if not prompt:
        return handle_failure(conn, job, "payload_validation_error: missing 'prompt'")

    model_id = payload.get("model_id")
    if not model_id:
        return handle_failure(conn, job, "payload_validation_error: missing 'model_id'")

    max_tokens = int(payload.get("max_tokens", 512))
    temperature = float(payload.get("temperature", 0.7))
    system_prompt = payload.get("system_prompt")

    log.info(
        "llm_dispatch_start",
        extra={"job_id": job_id, "model_id": model_id},
    )

    # --- Registry lookup -----------------------------------------------------
    entry = registry.get_model(model_id=model_id)
    if entry is None:
        # Also try matching by hf_repo
        entry = registry.get_model(hf_repo=model_id)
    if entry is None:
        return handle_failure(conn, job, f"model_not_installed: {model_id}")

    model_file = entry.get("filename", "")
    model_type_dir = {"llm": "llm"}.get(entry.get("model_type"), "llm")
    model_local_path = str(Path("/kaggle/working/models") / model_type_dir / model_file)

    # --- VRAM check for LLM --------------------------------------------------
    estimated_vram = entry.get("vram_mb", 0)
    if estimated_vram <= 0:
        file_size = entry.get("file_size_bytes", 0)
        fmt = Path(model_file).suffix.lower() if model_file else None
        estimated_vram = ModelRegistry.estimate_vram(
            file_size_bytes=file_size, format_hint=fmt
        )
    # Add KV cache overhead
    kv_overhead_mb = 512 if Path(model_file).suffix.lower() == ".gguf" else 1024
    total_vram_needed = estimated_vram + kv_overhead_mb

    gpu_free = get_gpu_free_mb(0)
    if gpu_free is not None and total_vram_needed > 0:
        available = gpu_free - VRAM_SAFETY_MARGIN_MB
        if total_vram_needed > available:
            log.info(
                "Deferring LLM job %s: needs %d MB VRAM, only %d MB available",
                job_id, total_vram_needed, available,
                extra={"job_id": job_id},
            )
            update_job(conn, job_id, status="pending", started_at=None, last_heartbeat=None)
            return None

    if not await check_vram_runtime(required_margin_mb=max(VRAM_SAFETY_MARGIN_MB, total_vram_needed // 2)):
        log.info("Deferring LLM job %s: runtime VRAM check failed", job_id, extra={"job_id": job_id})
        update_job(conn, job_id, status="pending", started_at=None, last_heartbeat=None)
        return None

    # --- Ensure Ollama is running --------------------------------------------
    if not await _ensure_ollama_running(shutdown):
        return handle_failure(conn, job, "ollama_start_failed")

    # --- Pull / load model into Ollama ---------------------------------------
    try:
        async with session.post(
            f"{OLLAMA_BASE}/api/pull",
            json={"name": model_local_path},
            timeout=aiohttp.ClientTimeout(total=600),
        ) as pull_resp:
            if pull_resp.status != 200:
                body = await pull_resp.text()
                return handle_failure(conn, job, f"ollama_pull_failed: {body}")
            # Drain streaming response
            await pull_resp.read()
        log.info("ollama_model_loaded", extra={"job_id": job_id, "model_id": model_id})
    except Exception as exc:
        return handle_failure(conn, job, f"ollama_pull_error: {exc}")

    # --- Generate ------------------------------------------------------------
    gen_payload: Dict[str, Any] = {
        "model": model_local_path,
        "prompt": prompt,
        "options": {"num_predict": max_tokens, "temperature": temperature},
        "stream": False,
    }
    if system_prompt:
        gen_payload["system"] = system_prompt

    t_start = time.monotonic()
    try:
        async with session.post(
            f"{OLLAMA_BASE}/api/generate",
            json=gen_payload,
            timeout=aiohttp.ClientTimeout(total=1800),
        ) as gen_resp:
            body = await gen_resp.text()
            if gen_resp.status != 200:
                log.error(
                    "llm_generation_failed",
                    extra={"job_id": job_id, "error_msg": body},
                )
                return handle_failure(conn, job, f"ollama_generate_failed: {body}")
            result_data = json.loads(body)
    except Exception as exc:
        log.error("llm_generation_failed", extra={"job_id": job_id, "error_msg": str(exc)})
        return handle_failure(conn, job, f"ollama_generate_error: {exc}")

    duration_s = time.monotonic() - t_start
    response_text = result_data.get("response", "")
    tokens_generated = int(result_data.get("eval_count", 0))
    tokens_per_sec = tokens_generated / duration_s if duration_s > 0 else 0.0

    # --- Save output ---------------------------------------------------------
    output_dir = Path("/kaggle/working/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    txt_path = output_dir / f"llm_{job_id}_{ts}.txt"
    meta_path = output_dir / f"llm_{job_id}_{ts}.meta.json"

    txt_path.write_text(response_text, encoding="utf-8")
    meta = {
        "job_id": job_id,
        "model_id": model_id,
        "prompt": prompt,
        "response": response_text,
        "tokens_generated": tokens_generated,
        "duration_seconds": round(duration_s, 2),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Mark complete -------------------------------------------------------
    update_job(
        conn,
        job_id,
        status="completed",
        completed_at=datetime.now(timezone.utc).isoformat(),
        result_path=str(txt_path),
    )
    log.info(
        "llm_generation_complete",
        extra={
            "job_id": job_id,
            "model_id": model_id,
            "tokens_generated": tokens_generated,
            "tokens_per_sec": round(tokens_per_sec, 1),
        },
    )
    global _jobs_since_backup
    _jobs_since_backup += 1
    maybe_backup_db(conn)
    return None


# ---------------------------------------------------------------------------
# Main consumer loop
# ---------------------------------------------------------------------------


async def process_job(
    conn: sqlite3.Connection,
    session: aiohttp.ClientSession,
    job: Dict[str, Any],
    vram_manifest: Dict[str, int],
    shutdown: ShutdownCoordinator,
) -> Optional[float]:
    """Process a single claimed job. Returns backoff seconds on retry, else None."""
    job_id = job["id"]
    shutdown.in_flight_job = job_id
    heartbeat_stop = asyncio.Event()
    hb_task = asyncio.create_task(heartbeat_loop(conn, job_id, heartbeat_stop))

    try:
        # --- Parse payload ---------------------------------------------------
        try:
            payload = json.loads(job["payload_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            return handle_failure(conn, job, f"payload_validation_error: {exc}")

        # --- Detect job type & route -----------------------------------------
        model_type = payload.get("model_type", "diffusion_image")

        if model_type == "llm":
            return await _dispatch_llm(conn, session, job, payload, shutdown)

        # --- Existing ComfyUI path (backward-compatible) ---------------------
        workflow = payload.get("workflow")
        if workflow is None:
            return handle_failure(conn, job, "invalid_workflow: missing 'workflow' key")

        # --- Stage-1 static VRAM check --------------------------------------
        needed_vram = estimate_workflow_vram(workflow, vram_manifest)
        gpu_free = get_gpu_free_mb(0)
        if gpu_free is not None and needed_vram > 0:
            available = gpu_free - VRAM_SAFETY_MARGIN_MB
            if needed_vram > available:
                log.info(
                    "Deferring job %s: needs %d MB VRAM, only %d MB available",
                    job_id,
                    needed_vram,
                    available,
                    extra={"job_id": job_id},
                )
                # Release back to pending without incrementing retry
                update_job(conn, job_id, status="pending", started_at=None, last_heartbeat=None)
                return None

        # --- Stage-2 runtime VRAM check -------------------------------------
        if not await check_vram_runtime():
            log.info("Deferring job %s: runtime VRAM check failed", job_id, extra={"job_id": job_id})
            update_job(conn, job_id, status="pending", started_at=None, last_heartbeat=None)
            return None

        # --- RAM check -------------------------------------------------------
        ram_pct = get_ram_usage_pct()
        if ram_pct > RAM_THRESHOLD_PCT:
            log.warning(
                "RAM usage %.1f%% > %.1f%% threshold, pausing dispatch",
                ram_pct,
                RAM_THRESHOLD_PCT,
            )
            update_job(conn, job_id, status="pending", started_at=None, last_heartbeat=None)
            await asyncio.sleep(30)
            return None

        # --- Dispatch to ComfyUI ---------------------------------------------
        comfyui_pid_before = find_comfyui_pid()
        try:
            result = await dispatch_to_comfyui(session, workflow)
        except Exception as exc:
            return handle_failure(conn, job, f"dispatch_error: {exc}")

        prompt_id = result.get("prompt_id")
        if not prompt_id:
            return handle_failure(conn, job, f"invalid_dispatch_response: {result}")

        update_job(conn, job_id, comfyui_prompt_id=prompt_id)
        log.info(
            "Dispatched job %s → prompt %s",
            job_id,
            prompt_id,
            extra={"job_id": job_id, "prompt_id": prompt_id},
        )

        # --- Post-dispatch OOM detection ------------------------------------
        if comfyui_pid_before is not None:
            alive = await monitor_comfyui_pid(comfyui_pid_before, POST_DISPATCH_OOM_WINDOW_S)
            if not alive:
                return handle_failure(conn, job, "oom_during_load")

        # --- Poll for completion ---------------------------------------------
        completion = await poll_completion(session, prompt_id)
        if completion["ok"]:
            update_job(
                conn,
                job_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
                result_path=completion.get("result_path", ""),
            )
            log.info(
                "Job %s completed successfully",
                job_id,
                extra={"job_id": job_id, "prompt_id": prompt_id},
            )
            _jobs_since_backup += 1
            maybe_backup_db(conn)
            return None
        else:
            return handle_failure(conn, job, completion["error_msg"])

    finally:
        heartbeat_stop.set()
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass
        shutdown.in_flight_job = None


async def main():
    log.info("Queue consumer starting")

    # Load VRAM manifest once
    vram_manifest = load_vram_manifest(VRAM_MANIFEST_PATH)
    log.info("Loaded VRAM manifest with %d entries", len(vram_manifest))

    conn = _connect()
    shutdown = ShutdownCoordinator()
    loop = asyncio.get_running_loop()
    shutdown.install(loop)

    # Recover orphaned jobs before processing
    recover_orphaned_jobs(conn)

    backoff_until: float = 0.0

    async with aiohttp.ClientSession() as session:
        try:
            while not shutdown.requested.is_set():
                now = time.monotonic()
                if now < backoff_until:
                    await asyncio.sleep(min(backoff_until - now, POLL_INTERVAL))
                    continue

                job = claim_pending_job(conn)
                if job is None:
                    try:
                        await asyncio.wait_for(
                            shutdown.requested.wait(), timeout=POLL_INTERVAL
                        )
                    except asyncio.TimeoutError:
                        pass
                    continue

                backoff = await process_job(conn, session, job, vram_manifest, shutdown)
                if backoff is not None and backoff > 0:
                    backoff_until = time.monotonic() + backoff

        except KeyboardInterrupt:
            log.info("KeyboardInterrupt received")
        finally:
            await shutdown.wait_in_flight()
            shutdown.write_shutdown_state(conn)
            conn.close()
            log.info("Queue consumer stopped")


if __name__ == "__main__":
    asyncio.run(main())