#!/usr/bin/env python3
"""
ngrok_launcher.py — Launches ngrok with MANDATORY basic auth for ComfyUI tunneling.

Resolves: Blocker 4.1 (mandatory auth from env), Major 1.3 (URL file + health),
          Major 6.4 (structured JSON logging via JsonFormatter)

Auth credentials MUST come from COMFYUI_AUTH_PASSWORD environment variable
(hardcoded username: kaggle). The launcher refuses
to start without both. Cloudflared fallback removed per Goldfish M-19.
"""

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# --- Structured JSON Logger (Major 6.4) ---
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": "ngrok_launcher",
            "level": record.levelname,
            "event": record.getMessage(),
        }
        if hasattr(record, "url"):
            log_entry["url"] = record.url
        return json.dumps(log_entry)

logger = logging.getLogger("ngrok_launcher")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logger.addHandler(_handler)

file_handler = logging.FileHandler("/kaggle/working/watchdog.log")
file_handler.setFormatter(JsonFormatter())
logger.addHandler(file_handler)

# --- Constants ---
COMFYUI_PORT = 8188
URL_EXTRACT_TIMEOUT_S = 30
PORT_WAIT_TIMEOUT_S = 60
PORT_CHECK_INTERVAL_S = 2
HEALTH_UPDATE_INTERVAL_S = 10
SIGTERM_GRACE_PERIOD_S = 10

NGROK_URL_FILE = "/kaggle/working/ngrok_url.txt"
NGROK_HEALTH_FILE = "/kaggle/working/ngrok_health.json"

NGROK_SEARCH_PATHS = [
    "ngrok",
    "/usr/local/bin/ngrok",
    "/kaggle/working/ngrok",
]


def find_ngrok_binary() -> str:
    """Locate ngrok binary in PATH or common locations."""
    import shutil

    path_result = shutil.which("ngrok")
    if path_result:
        return path_result

    for candidate in NGROK_SEARCH_PATHS:
        p = Path(candidate)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)

    logger.critical(
        "ngrok binary not found. Install: curl -s https://ngrok-agent.s3.amazonaws.com/ngrok-v3-stable-linux-amd64.tgz | tar xz && mv ngrok /usr/local/bin/"
    )
    sys.exit(1)


def wait_for_port(port: int, timeout_s: int = PORT_WAIT_TIMEOUT_S) -> bool:
    """Wait until a TCP port is accepting connections."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", port))
            s.close()
            if result == 0:
                return True
        except OSError:
            pass
        logger.info(f"Waiting for port {port}... ({int(deadline - time.monotonic())}s remaining)")
        time.sleep(PORT_CHECK_INTERVAL_S)
    return False


def get_ngrok_url_from_api() -> str | None:
    """Query ngrok local API (port 4040) for the current public tunnel URL."""
    try:
        req = urllib.request.Request("http://127.0.0.1:4040/api/tunnels")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            tunnels = data.get("tunnels", [])
            for t in tunnels:
                pub = t.get("public_url", "")
                if isinstance(pub, str) and pub.startswith("https://"):
                    return pub
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def write_health(alive: bool, public_url: str, started_at: str) -> None:
    """Write/update the ngrok health JSON file."""
    health = {
        "alive": alive,
        "public_url": public_url,
        "started_at": started_at,
        "last_check": datetime.now(timezone.utc).isoformat(),
    }
    try:
        health_path = Path(NGROK_HEALTH_FILE)
        health_path.parent.mkdir(parents=True, exist_ok=True)
        health_path.write_text(json.dumps(health, indent=2))
    except OSError as e:
        logger.error(f"Health write failed: {e}")


def main() -> None:
    # --- MANDATORY Auth Validation (Blocker 4.1) ---
    comfyui_user = "kaggle"
    comfyui_pass = os.environ.get("COMFYUI_AUTH_PASSWORD")

    if not comfyui_user or not comfyui_pass:
        logger.critical(
            "FATAL: COMFYUI_AUTH_PASSWORD must be set as Kaggle Secret. "
            "Refusing to start tunnel without authentication."
        )
        print(
            "ERROR: COMFYUI_AUTH_PASSWORD is REQUIRED as a Kaggle Secret.\n"
            "Set them in Kaggle Notebook Settings > Secrets before running.",
            file=sys.stderr,
        )
        sys.exit(1)

    authtoken = os.environ.get("NGROK_AUTHTOKEN") or os.environ.get("KAGGLE_SECRET_NGROK_TOKEN")
    if not authtoken:
        logger.critical("NGROK_AUTHTOKEN / KAGGLE_SECRET_NGROK_TOKEN not set")
        print(
            "ERROR: NGROK_AUTHTOKEN or KAGGLE_SECRET_NGROK_TOKEN is required.\n"
            "Get your token at https://dashboard.ngrok.com/get-started/your-authtoken",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Locate ngrok ---
    ngrok_bin = find_ngrok_binary()
    logger.info(f"ngrok starting: binary={ngrok_bin}, port={COMFYUI_PORT}")

    # --- Wait for ComfyUI port ---
    if not wait_for_port(COMFYUI_PORT):
        logger.critical(f"Port {COMFYUI_PORT} not listening after {PORT_WAIT_TIMEOUT_S}s")
        print(
            f"ERROR: Port {COMFYUI_PORT} not listening after {PORT_WAIT_TIMEOUT_S}s.\n"
            "Ensure ComfyUI is running before starting ngrok_launcher.",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info(f"Port {COMFYUI_PORT} ready")

    # --- Authenticate ngrok ---
    auth_result = subprocess.run(
        [ngrok_bin, "config", "add-authtoken", authtoken],
        capture_output=True, text=True,
    )
    if auth_result.returncode != 0:
        logger.error(f"ngrok auth failed: {auth_result.stderr.strip()}")
        sys.exit(1)

    # --- Build ngrok command with MANDATORY basic auth (Blocker 4.1) ---
    cmd = [
        ngrok_bin,
        "http",
        str(COMFYUI_PORT),
        f"--basic-auth={comfyui_user}:{comfyui_pass}",
        "--log=stdout",
        "--log-format=json",
    ]

    started_at = datetime.now(timezone.utc).isoformat()
    public_url = ""
    child_proc = None

    def shutdown_handler(signum, frame):
        """Handle SIGTERM: forward to ngrok child, wait, then SIGKILL."""
        logger.info(f"Shutdown signal received (signal={signum})")
        if child_proc and child_proc.poll() is None:
            child_proc.terminate()
            try:
                child_proc.wait(timeout=SIGTERM_GRACE_PERIOD_S)
            except subprocess.TimeoutExpired:
                logger.warning(f"SIGKILL fallback for PID {child_proc.pid}")
                child_proc.kill()
                child_proc.wait(timeout=5)
        write_health(False, public_url, started_at)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # --- Launch ngrok ---
    try:
        child_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except (FileNotFoundError, OSError) as e:
        logger.critical(f"Failed to launch ngrok: {e}")
        sys.exit(1)

    # --- Parse stdout for URL and forward logs ---
    url_found = False
    launch_time = time.monotonic()
    last_health_update = time.monotonic()

    write_health(True, "", started_at)

    assert child_proc.stdout is not None
    for line in child_proc.stdout:
        line = line.strip()
        if not line:
            continue

        # Forward all ngrok log lines to stderr for watchdog monitoring
        print(line, file=sys.stderr, flush=True)

        # Try to parse as JSON and extract URL
        try:
            log_entry = json.loads(line)
            url_val = log_entry.get("url", "")
            if isinstance(url_val, str) and url_val.startswith("https://"):
                public_url = url_val
                url_found = True
                logger.info(f"Tunnel URL extracted: {public_url}", extra={"url": public_url})

                # Write URL to disk (Major 1.3)
                try:
                    url_path = Path(NGROK_URL_FILE)
                    url_path.parent.mkdir(parents=True, exist_ok=True)
                    url_path.write_text(public_url)
                except OSError as e:
                    logger.error(f"URL file write failed: {e}")

                # Print URL to stdout for notebook cell capture
                print(public_url, flush=True)
                write_health(True, public_url, started_at)
        except (json.JSONDecodeError, ValueError):
            pass

        # Periodic health update + API-based URL fallback
        now_mono = time.monotonic()
        if now_mono - last_health_update >= HEALTH_UPDATE_INTERVAL_S:
            # Try ngrok local API as fallback/refresh for URL
            if not url_found or True:  # Always refresh via API when available
                api_url = get_ngrok_url_from_api()
                if api_url and api_url != public_url:
                    public_url = api_url
                    url_found = True
                    logger.info(f"Tunnel URL from API: {public_url}", extra={"url": public_url})
                    try:
                        Path(NGROK_URL_FILE).write_text(public_url)
                    except OSError as e:
                        logger.error(f"URL file write failed: {e}")
                    print(public_url, flush=True)
            write_health(True, public_url, started_at)
            last_health_update = now_mono

        # Timeout check for URL extraction
        if not url_found and (now_mono - launch_time) > URL_EXTRACT_TIMEOUT_S:
            # Final attempt via ngrok local API before giving up
            api_url = get_ngrok_url_from_api()
            if api_url:
                public_url = api_url
                url_found = True
                logger.info(f"Tunnel URL from API (timeout fallback): {public_url}", extra={"url": public_url})
                try:
                    Path(NGROK_URL_FILE).write_text(public_url)
                except OSError as e:
                    logger.error(f"URL file write failed: {e}")
                print(public_url, flush=True)
                write_health(True, public_url, started_at)
                continue
            logger.critical(f"No public URL extracted within {URL_EXTRACT_TIMEOUT_S}s")
            child_proc.terminate()
            try:
                child_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child_proc.kill()
            write_health(False, "", started_at)
            sys.exit(1)

        # Check if ngrok exited unexpectedly
        retcode = child_proc.poll()
        if retcode is not None:
            if not url_found:
                logger.critical(f"ngrok exited immediately (code {retcode}); likely invalid authtoken")
            else:
                logger.error(f"ngrok exited unexpectedly (code {retcode})")
            write_health(False, public_url, started_at)
            sys.exit(1)

    # If we get here, ngrok stdout closed (process ended)
    retcode = child_proc.wait()
    logger.error(f"ngrok stdout closed (exit_code={retcode})")
    write_health(False, public_url, started_at)
    sys.exit(1 if retcode != 0 else 0)


if __name__ == "__main__":
    main()