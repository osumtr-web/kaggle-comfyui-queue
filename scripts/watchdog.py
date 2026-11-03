#!/usr/bin/env python3
"""
Kaggle ComfyUI Generation Queue Service — Watchdog Daemon

Monitors all service processes (comfyui, ngrok, consumer, api), restarts
crashed components with exponential backoff, rotates output files, and
handles graceful shutdown via SIGTERM/SIGINT.

State machine per process: MONITORING → RESTARTING → BACKOFF → MONITORING
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants & Defaults
# ---------------------------------------------------------------------------

BASE_DIR = Path("/kaggle/working")
PIDS_FILE = BASE_DIR / "pids.json"
HEALTH_FILE = BASE_DIR / "watchdog_health.json"
SHUTDOWN_STATE_FILE = BASE_DIR / "shutdown_state.json"
WATCHDOG_LOG_FILE = BASE_DIR / "watchdog.log"
OUTPUT_DIR = BASE_DIR / "output"
CONFIGS_FILE = BASE_DIR / "configs" / "comfyui_settings.yaml"
QUEUE_DB = BASE_DIR / "queue.db"

DEFAULT_POLL_INTERVAL = 5        # seconds
DEFAULT_OUTPUT_MAX_GB = 25
DEFAULT_CLEANUP_THRESHOLD = 80   # percent
CLEANUP_TARGET_PERCENT = 70      # drop to this after cleanup
BACKOFF_BASE = 5                 # seconds
BACKOFF_CAP = 30                 # seconds
MAX_CONSECUTIVE_FAILURES = 5
HEALTH_WRITE_INTERVAL = 10       # seconds
OUTPUT_CHECK_INTERVAL = 60       # seconds
SHUTDOWN_TIMEOUT = 30            # seconds


# ---------------------------------------------------------------------------
# Structured JSON Logger
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line with required fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "component": getattr(record, "component", "watchdog"),
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        if hasattr(record, "pid"):
            entry["pid"] = record.pid
        if hasattr(record, "attempt"):
            entry["attempt"] = record.attempt
        return json.dumps(entry)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("watchdog")
    logger.setLevel(logging.DEBUG)

    formatter = JsonFormatter()

    # File handler
    fh = logging.FileHandler(str(WATCHDOG_LOG_FILE))
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Stdout handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


logger = setup_logging()


def log_event(
    level: int,
    event: str,
    message: str,
    component: str = "watchdog",
    pid: Optional[int] = None,
    attempt: Optional[int] = None,
) -> None:
    extra: Dict[str, Any] = {"event": event, "component": component}
    if pid is not None:
        extra["pid"] = pid
    if attempt is not None:
        extra["attempt"] = attempt
    logger.log(level, message, extra=extra)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class WatchdogConfig:
    poll_interval: float = DEFAULT_POLL_INTERVAL
    output_max_gb: float = DEFAULT_OUTPUT_MAX_GB
    cleanup_threshold: float = DEFAULT_CLEANUP_THRESHOLD

    @classmethod
    def load(cls) -> "WatchdogConfig":
        cfg = cls()

        # Try YAML config
        if CONFIGS_FILE.exists():
            try:
                import yaml  # type: ignore[import-untyped]
                with open(CONFIGS_FILE) as f:
                    data = yaml.safe_load(f) or {}
                wd = data.get("watchdog", {})
                if "poll_interval" in wd:
                    cfg.poll_interval = float(wd["poll_interval"])
                if "output_max_gb" in wd:
                    cfg.output_max_gb = float(wd["output_max_gb"])
                if "cleanup_threshold" in wd:
                    cfg.cleanup_threshold = float(wd["cleanup_threshold"])
            except Exception as exc:
                log_event(logging.WARNING, "config_load", f"Failed to parse YAML config: {exc}")

        # Env overrides
        if v := os.environ.get("WATCHDOG_POLL_INTERVAL"):
            cfg.poll_interval = float(v)
        if v := os.environ.get("OUTPUT_MAX_GB"):
            cfg.output_max_gb = float(v)
        if v := os.environ.get("OUTPUT_CLEANUP_THRESHOLD"):
            cfg.cleanup_threshold = float(v)

        return cfg


# ---------------------------------------------------------------------------
# Process Definitions
# ---------------------------------------------------------------------------

class ProcessState(Enum):
    MONITORING = "monitoring"
    RESTARTING = "restarting"
    BACKOFF = "backoff"


@dataclass
class ProcessSpec:
    name: str
    cmd: List[str]
    cwd: str
    env_extra: Dict[str, str] = field(default_factory=dict)


def build_process_specs() -> Dict[str, ProcessSpec]:
    parent_env = dict(os.environ)

    comfyui_env = dict(parent_env)
    # Ensure PYTHONPATH includes ComfyUI
    existing_pp = comfyui_env.get("PYTHONPATH", "")
    comfyui_env["PYTHONPATH"] = f"/kaggle/working/ComfyUI:{existing_pp}" if existing_pp else "/kaggle/working/ComfyUI"

    ngrok_env = dict(parent_env)
    ngrok_token = os.environ.get("NGROK_AUTHTOKEN", "")
    if ngrok_token:
        ngrok_env["NGROK_AUTHTOKEN"] = ngrok_token

    auth_password = os.environ.get("COMFYUI_AUTH_PASSWORD", "kaggle")

    specs: Dict[str, ProcessSpec] = {
        "comfyui": ProcessSpec(
            name="comfyui",
            cmd=[
                "python3", "/kaggle/working/ComfyUI/main.py",
                "--listen", "0.0.0.0",
                "--port", "8188",
                "--multi-gpu",
                "--fp16-vae",           # T4 does not support bfloat16; force FP16 VAE
                "--lowvram",            # Safety default: offload layers to CPU RAM to prevent OOM on 2×T4
                "--disable-auto-launch-browser",
            ],
            cwd="/kaggle/working/ComfyUI",
            env_extra=comfyui_env,
        ),
        "ngrok": ProcessSpec(
            name="ngrok",
            cmd=[
                "ngrok", "http", "8188",
                f"--basic-auth=kaggle:{auth_password}",
                "--log=stdout",
                "--log-format=json",
            ],
            cwd="/kaggle/working",
            env_extra=ngrok_env,
        ),
        "consumer": ProcessSpec(
            name="consumer",
            cmd=["python3", "/kaggle/working/scripts/queue_consumer.py"],
            cwd="/kaggle/working",
        ),
        "api": ProcessSpec(
            name="api",
            cmd=["python3", "/kaggle/working/scripts/queue_api.py"],
            cwd="/kaggle/working",
        ),
    }
    return specs


# ---------------------------------------------------------------------------
# PID Persistence
# ---------------------------------------------------------------------------

def load_pids() -> Dict[str, Optional[int]]:
    if PIDS_FILE.exists():
        try:
            with open(PIDS_FILE) as f:
                data = json.load(f)
            return {k: data.get(k) for k in ("comfyui", "ngrok", "consumer", "api")}
        except Exception:
            pass
    return {"comfyui": None, "ngrok": None, "consumer": None, "api": None}


def save_pids(pids: Dict[str, Optional[int]]) -> None:
    tmp = PIDS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(pids, f)
    tmp.rename(PIDS_FILE)


def pid_alive(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ---------------------------------------------------------------------------
# Process Manager
# ---------------------------------------------------------------------------

class ProcessManager:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.specs = build_process_specs()
        self.pids: Dict[str, Optional[int]] = load_pids()
        self.processes: Dict[str, Optional[subprocess.Popen]] = {}
        self.states: Dict[str, ProcessState] = {}
        self.consecutive_failures: Dict[str, int] = {}
        self.restart_counts: Dict[str, int] = {}
        self.backoff_until: Dict[str, float] = {}

        for name in self.specs:
            self.states[name] = ProcessState.MONITORING
            self.consecutive_failures[name] = 0
            self.restart_counts[name] = 0
            self.backoff_until[name] = 0.0
            self.processes[name] = None

    # -- Adoption on startup --------------------------------------------------

    def adopt_existing(self) -> None:
        """Check stored PIDs; adopt if alive, else mark for restart."""
        for name, pid in self.pids.items():
            if pid_alive(pid):
                log_event(logging.INFO, "process_started",
                          f"Adopted existing {name} process (PID {pid})",
                          component=name, pid=pid)
                self.states[name] = ProcessState.MONITORING
            else:
                self.pids[name] = None
                log_event(logging.INFO, "process_died",
                          f"Stored PID for {name} not alive, will spawn",
                          component=name)

    # -- Spawn ----------------------------------------------------------------

    def _build_env(self, spec: ProcessSpec) -> Dict[str, str]:
        env = dict(os.environ)
        env.update(spec.env_extra)
        return env

    def spawn(self, name: str) -> bool:
        spec = self.specs[name]
        if self.dry_run:
            log_event(logging.INFO, "restart_attempt",
                      f"[DRY-RUN] Would spawn {name}: {' '.join(spec.cmd)}",
                      component=name)
            return True

        try:
            env = self._build_env(spec)
            proc = subprocess.Popen(
                spec.cmd,
                cwd=spec.cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.processes[name] = proc
            self.pids[name] = proc.pid
            save_pids(self.pids)
            log_event(logging.INFO, "process_started",
                      f"Spawned {name} (PID {proc.pid})",
                      component=name, pid=proc.pid)
            return True
        except Exception as exc:
            log_event(logging.ERROR, "restart_failed",
                      f"Failed to spawn {name}: {exc}",
                      component=name)
            return False

    # -- State Machine Tick ---------------------------------------------------

    async def tick(self) -> None:
        now = time.monotonic()
        for name in list(self.specs.keys()):
            state = self.states[name]

            if state == ProcessState.MONITORING:
                pid = self.pids.get(name)
                if not pid_alive(pid):
                    if pid is not None:
                        log_event(logging.WARNING, "process_died",
                                  f"{name} (PID {pid}) is no longer running",
                                  component=name, pid=pid)
                    self.states[name] = ProcessState.RESTARTING

            elif state == ProcessState.RESTARTING:
                self.restart_counts[name] += 1
                attempt = self.restart_counts[name]
                log_event(logging.INFO, "restart_attempt",
                          f"Restarting {name} (attempt {attempt})",
                          component=name, attempt=attempt)
                success = self.spawn(name)
                if success:
                    self.consecutive_failures[name] = 0
                    self.states[name] = ProcessState.MONITORING
                    log_event(logging.INFO, "restart_success",
                              f"{name} restarted successfully",
                              component=name, pid=self.pids.get(name))
                else:
                    self.consecutive_failures[name] += 1
                    delay = min(BACKOFF_BASE * (2 ** (self.consecutive_failures[name] - 1)), BACKOFF_CAP)
                    self.backoff_until[name] = now + delay
                    self.states[name] = ProcessState.BACKOFF
                    log_event(logging.WARNING, "backoff_wait",
                              f"{name} entering backoff for {delay:.0f}s (failures={self.consecutive_failures[name]})",
                              component=name, attempt=attempt)

            elif state == ProcessState.BACKOFF:
                if self.consecutive_failures[name] >= MAX_CONSECUTIVE_FAILURES:
                    log_event(logging.CRITICAL, "critical_failure",
                              f"{name} has failed {MAX_CONSECUTIVE_FAILURES} consecutive times; continuing to monitor",
                              component=name)
                    # Reset so we keep trying but don't spam CRITICAL
                    self.consecutive_failures[name] = 0

                if now >= self.backoff_until[name]:
                    self.states[name] = ProcessState.RESTARTING

    # -- Health Snapshot ------------------------------------------------------

    def health_snapshot(self) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {}
        for name in self.specs:
            snapshot[f"{name}_alive"] = pid_alive(self.pids.get(name))
        snapshot["restart_counts"] = dict(self.restart_counts)
        snapshot["last_check"] = datetime.now(timezone.utc).isoformat()
        return snapshot

    def write_health(self) -> None:
        try:
            data = self.health_snapshot()
            tmp = HEALTH_FILE.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f)
            tmp.rename(HEALTH_FILE)
        except Exception as exc:
            log_event(logging.ERROR, "health_write", f"Failed to write health file: {exc}")

    # -- Shutdown -------------------------------------------------------------

    async def shutdown(self) -> None:
        log_event(logging.INFO, "shutdown_initiated", "Graceful shutdown started")

        children: List[tuple[str, int]] = []
        for name, pid in self.pids.items():
            if pid_alive(pid):
                children.append((name, pid))
                try:
                    os.kill(pid, signal.SIGTERM)
                    log_event(logging.INFO, "shutdown_signal",
                              f"Sent SIGTERM to {name} (PID {pid})",
                              component=name, pid=pid)
                except OSError:
                    pass

        # Wait up to SHUTDOWN_TIMEOUT
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT
        while time.monotonic() < deadline:
            still_alive = [(n, p) for n, p in children if pid_alive(p)]
            if not still_alive:
                break
            await asyncio.sleep(0.5)

        # SIGKILL survivors
        for name, pid in children:
            if pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                    log_event(logging.WARNING, "shutdown_kill",
                              f"Sent SIGKILL to {name} (PID {pid})",
                              component=name, pid=pid)
                except OSError:
                    pass

        # Write shutdown state
        final_pids = {name: self.pids.get(name) for name in self.specs}
        shutdown_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "final_pids": final_pids,
            "alive_at_shutdown": {name: pid_alive(p) for name, p in final_pids.items()},
        }
        try:
            with open(SHUTDOWN_STATE_FILE, "w") as f:
                json.dump(shutdown_data, f, indent=2)
        except Exception as exc:
            log_event(logging.ERROR, "shutdown_state", f"Failed to write shutdown state: {exc}")

        log_event(logging.INFO, "shutdown_complete", "All children terminated, exiting")


# ---------------------------------------------------------------------------
# Output Rotation
# ---------------------------------------------------------------------------

async def rotate_outputs(config: WatchdogConfig, pm: ProcessManager) -> None:
    """Check disk usage and prune oldest output files if thresholds exceeded."""
    if not OUTPUT_DIR.exists():
        return

    try:
        usage = shutil.disk_usage(str(OUTPUT_DIR))
    except OSError:
        return

    used_pct = (usage.used / usage.total) * 100 if usage.total > 0 else 0
    max_bytes = config.output_max_gb * (1024 ** 3)

    needs_cleanup = (used_pct >= config.cleanup_threshold) or (usage.used >= max_bytes)
    if not needs_cleanup:
        return

    target_used = usage.total * (CLEANUP_TARGET_PERCENT / 100)
    bytes_to_free = usage.used - target_used
    if bytes_to_free <= 0:
        return

    # Collect files sorted by mtime ascending (oldest first)
    files: List[Path] = []
    for entry in OUTPUT_DIR.rglob("*"):
        if entry.is_file():
            files.append(entry)
    files.sort(key=lambda p: p.stat().st_mtime)

    deleted_count = 0
    freed_bytes = 0
    deleted_paths: List[str] = []

    for fp in files:
        if freed_bytes >= bytes_to_free:
            break
        try:
            size = fp.stat().st_size
            fp.unlink()
            freed_bytes += size
            deleted_count += 1
            deleted_paths.append(str(fp))
        except OSError:
            continue

    if deleted_count > 0:
        log_event(logging.INFO, "output_rotation",
                  f"Deleted {deleted_count} files, freed {freed_bytes} bytes",
                  component="rotation")

        # Update SQLite job records
        _nullify_deleted_results(deleted_paths)


def _nullify_deleted_results(deleted_paths: List[str]) -> None:
    """Set result_path=NULL for jobs whose output files were deleted."""
    if not QUEUE_DB.exists():
        return
    try:
        conn = sqlite3.connect(str(QUEUE_DB))
        cursor = conn.cursor()
        for path in deleted_paths:
            cursor.execute(
                "UPDATE jobs SET result_path = NULL WHERE result_path = ?",
                (path,),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        log_event(logging.ERROR, "output_rotation",
                  f"Failed to update SQLite after rotation: {exc}",
                  component="rotation")


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

async def main(dry_run: bool = False) -> None:
    config = WatchdogConfig.load()
    pm = ProcessManager(dry_run=dry_run)

    log_event(logging.INFO, "startup",
              f"Watchdog starting (poll={config.poll_interval}s, "
              f"max_output={config.output_max_gb}GB, "
              f"cleanup_threshold={config.cleanup_threshold}%, "
              f"dry_run={dry_run})")

    # Adopt existing processes
    pm.adopt_existing()

    # Initial spawn for any missing processes
    for name in pm.specs:
        if not pid_alive(pm.pids.get(name)):
            pm.states[name] = ProcessState.RESTARTING

    # Signal handling
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows fallback — shouldn't happen on Kaggle/Linux
            signal.signal(sig, lambda s, f: _signal_handler())

    last_health_write = 0.0
    last_output_check = 0.0
    last_heartbeat_print = 0.0
    HEARTBEAT_PRINT_INTERVAL = 300.0  # 5 minutes — prevents Kaggle 20-min idle timeout

    try:
        while not shutdown_event.is_set():
            now = time.monotonic()

            # Idle-timeout heartbeat: print status every 5 min so Kaggle doesn't kill the kernel
            if now - last_heartbeat_print >= HEARTBEAT_PRINT_INTERVAL:
                alive = {name: pid_alive(pm.pids.get(name)) for name in pm.specs}
                log_event(logging.INFO, "idle_heartbeat",
                          f"[{datetime.now(timezone.utc).isoformat()}] Watchdog heartbeat: {alive}",
                          component="watchdog")
                last_heartbeat_print = now

            # State machine tick
            await pm.tick()

            # Health file every HEALTH_WRITE_INTERVAL
            if now - last_health_write >= HEALTH_WRITE_INTERVAL:
                pm.write_health()
                last_health_write = now

            # Output rotation every OUTPUT_CHECK_INTERVAL
            if now - last_output_check >= OUTPUT_CHECK_INTERVAL:
                await rotate_outputs(config, pm)
                last_output_check = now

            # Sleep until next poll
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=config.poll_interval)
            except asyncio.TimeoutError:
                pass

    finally:
        await pm.shutdown()


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kaggle ComfyUI Queue Watchdog")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log what would be spawned without actually spawning")
    args = parser.parse_args()

    try:
        asyncio.run(main(dry_run=args.dry_run))
    except KeyboardInterrupt:
        pass
    sys.exit(0)