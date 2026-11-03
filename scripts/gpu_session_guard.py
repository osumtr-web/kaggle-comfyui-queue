#!/usr/bin/env python3
"""
GPU Session Guard — Auto-stop Kaggle notebook when idle to preserve 30h/week quota.

Monitors queue activity and ComfyUI usage. If no jobs are pending/running AND
no API requests have been made within IDLE_TIMEOUT_SECONDS, gracefully shuts
down all services and stops the Kaggle notebook via the Kaggle API or kernel
shutdown endpoint.

Usage:
    python3 gpu_session_guard.py [--idle-timeout 1800] [--dry-run]

Environment:
    KAGGLE_GPU_AUTO_STOP=1          Enable auto-stop (default: enabled)
    KAGGLE_IDLE_TIMEOUT_SECONDS     Override idle timeout (default: 1800 = 30 min)
    KAGGLE_KERNEL_ID                Kernel ID for API shutdown (optional)
    KAGGLE_API_TOKEN                Kaggle API token for remote shutdown (optional)
"""

from __future__ import annotations

import argparse
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
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path("/kaggle/working")
QUEUE_DB = BASE_DIR / "queue.db"
API_LOG = BASE_DIR / "api.log"
HEALTH_FILE = BASE_DIR / "watchdog_health.json"
NGROK_URL_FILE = BASE_DIR / "ngrok_url.txt"

DEFAULT_IDLE_TIMEOUT = 1800       # 30 minutes of zero activity
POLL_INTERVAL = 60                # Check every 60 seconds
GRACE_PERIOD = 300                # 5 min grace period after last activity before stop
MIN_UPTIME_BEFORE_STOP = 600      # Don't auto-stop if running < 10 min (startup protection)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp":"%(asctime)s","level":"%(levelname)s","component":"gpu_guard","event":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    stream=sys.stdout,
)
log = logging.getLogger("gpu_guard")


# ---------------------------------------------------------------------------
# Activity Detection
# ---------------------------------------------------------------------------

def get_queue_activity(conn: sqlite3.Connection) -> dict:
    """Check pending and running job counts."""
    try:
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'pending'").fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'running'").fetchone()[0]
        return {"pending": pending, "running": running, "db_ok": True}
    except Exception as exc:
        log.warning(f"queue_check_failed: {exc}")
        return {"pending": 0, "running": 0, "db_ok": False}


def get_api_last_request() -> Optional[float]:
    """Parse last request timestamp from API log."""
    try:
        if not API_LOG.exists():
            return None
        # Read last 50 lines looking for request timestamps
        lines = API_LOG.read_text().strip().split("\n")[-50:]
        for line in reversed(lines):
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp")
                if ts:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    return dt.timestamp()
            except (json.JSONDecodeError, ValueError):
                continue
    except Exception:
        pass
    return None


def get_comfyui_active() -> bool:
    """Check if ComfyUI is processing via system_stats endpoint."""
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:8188/system_stats")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            # Check if any execution is active
            devices = data.get("devices", [])
            for dev in devices:
                if dev.get("gpu_utilization", 0) > 5:
                    return True
            return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Shutdown Logic
# ---------------------------------------------------------------------------

def stop_kaggle_notebook(dry_run: bool = False) -> bool:
    """
    Stop the Kaggle notebook to preserve GPU quota.
    
    Strategy (in order of preference):
    1. Write shutdown marker file → watchdog picks up and kills processes
    2. Kill all service processes directly
    3. Use Kaggle API to stop kernel (if credentials available)
    """
    log.info("initiating_gpu_stop")
    
    if dry_run:
        log.info("dry_run_stop_skipped")
        return True
    
    # Method 1: Signal watchdog to shut down gracefully
    shutdown_marker = BASE_DIR / "gpu_auto_stop.marker"
    shutdown_marker.write_text(datetime.now(timezone.utc).isoformat())
    log.info("shutdown_marker_written")
    
    # Method 2: Direct process cleanup
    services = ["queue_consumer.py", "queue_api.py", "watchdog.py", "ngrok_launcher.py"]
    for svc in services:
        try:
            subprocess.run(["pkill", "-f", svc], capture_output=True, timeout=10)
        except Exception:
            pass
    
    # Stop ComfyUI
    try:
        subprocess.run(["pkill", "-f", "main.py.*--listen"], capture_output=True, timeout=10)
    except Exception:
        pass
    
    # Stop Ollama if running
    try:
        subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True, timeout=10)
    except Exception:
        pass
    
    log.info("all_services_stopped")
    
    # Method 3: Kaggle API shutdown (best effort)
    kernel_id = os.environ.get("KAGGLE_KERNEL_ID")
    api_token = os.environ.get("KAGGLE_API_TOKEN")
    if kernel_id and api_token:
        try:
            import urllib.request
            url = f"https://www.kaggle.com/api/v1/kernels/{kernel_id}/stop"
            req = urllib.request.Request(url, method="POST")
            req.add_header("Authorization", f"Bearer {api_token}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info(f"kaggle_api_stop_response: {resp.status}")
        except Exception as exc:
            log.warning(f"kaggle_api_stop_failed: {exc}")
    
    # Final: exit this guard process (notebook will eventually timeout)
    log.info("gpu_guard_exiting_after_stop")
    return True


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

async def main(idle_timeout: int, dry_run: bool) -> None:
    auto_stop_enabled = os.environ.get("KAGGLE_GPU_AUTO_STOP", "1") == "1"
    
    log.info(f"gpu_guard_started idle_timeout={idle_timeout}s dry_run={dry_run} enabled={auto_stop_enabled}")
    
    if not auto_stop_enabled:
        log.info("auto_stop_disabled_via_env")
        return
    
    start_time = time.monotonic()
    last_activity_time = start_time
    shutdown_event = asyncio.Event()
    
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda s, f: shutdown_event.set())
    
    while not shutdown_event.is_set():
        now = time.monotonic()
        uptime = now - start_time
        
        # Skip auto-stop during startup grace period
        if uptime < MIN_UPTIME_BEFORE_STOP:
            await asyncio.sleep(POLL_INTERVAL)
            continue
        
        # Check all activity sources
        activity_detected = False
        
        # 1. Queue DB activity
        db_conn = None
        try:
            if QUEUE_DB.exists():
                db_conn = sqlite3.connect(str(QUEUE_DB), timeout=5)
                queue_state = get_queue_activity(db_conn)
                if queue_state["pending"] > 0 or queue_state["running"] > 0:
                    activity_detected = True
                    log.debug(f"queue_active pending={queue_state['pending']} running={queue_state['running']}")
        except Exception as exc:
            log.warning(f"queue_check_error: {exc}")
        finally:
            if db_conn:
                db_conn.close()
        
        # 2. API request activity
        api_last = get_api_last_request()
        if api_last and (time.time() - api_last) < idle_timeout:
            activity_detected = True
        
        # 3. ComfyUI GPU utilization
        if get_comfyui_active():
            activity_detected = True
        
        # Update last activity timestamp
        if activity_detected:
            last_activity_time = now
        
        # Calculate idle duration
        idle_seconds = now - last_activity_time
        
        # Log periodic status
        if int(now) % 300 < POLL_INTERVAL:  # Every ~5 min
            log.info(f"idle_status idle={idle_seconds:.0f}s timeout={idle_timeout}s uptime={uptime:.0f}s")
        
        # Trigger auto-stop if idle threshold exceeded
        if idle_seconds >= idle_timeout:
            log.warning(f"idle_threshold_exceeded idle={idle_seconds:.0f}s >= {idle_timeout}s")
            stop_kaggle_notebook(dry_run=dry_run)
            break
        
        # Sleep until next check
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
    
    log.info("gpu_guard_stopped")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPU Session Guard — auto-stop idle Kaggle sessions")
    parser.add_argument("--idle-timeout", type=int, default=int(os.environ.get("KAGGLE_IDLE_TIMEOUT_SECONDS", DEFAULT_IDLE_TIMEOUT)),
                        help=f"Idle timeout in seconds (default: {DEFAULT_IDLE_TIMEOUT})")
    parser.add_argument("--dry-run", action="store_true", help="Log what would happen without stopping")
    args = parser.parse_args()
    
    try:
        asyncio.run(main(args.idle_timeout, args.dry_run))
    except KeyboardInterrupt:
        log.info("gpu_guard_interrupted")