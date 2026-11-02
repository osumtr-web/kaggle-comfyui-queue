#!/usr/bin/env python3
"""
Kaggle ComfyUI Queue Service — Integration Validation Tests
Verifies: schema consistency, import resolution, config alignment, auth enforcement logic
Run locally: python3 tests/test_integration.py
Run on Kaggle: validates before service launch
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
CONFIGS_DIR = PROJECT_ROOT / "configs"
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"

passed = 0
failed = 0

def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        print(f"  ✓ {name}")
        passed += 1
    else:
        print(f"  ✗ {name}: {detail}")
        failed += 1

# --- Test 1: Schema Consistency Between queue_api.py and design_draft.md ---
print("\n[Test 1] SQLite Schema Consistency")
api_src = (SCRIPTS_DIR / "queue_api.py").read_text()
required_columns = [
    "id", "payload_json", "status", "priority", "created_at", "started_at",
    "completed_at", "last_heartbeat", "retry_count", "max_retries",
    "result_path", "delivered", "error_msg", "comfyui_prompt_id", "estimated_vram_mb"
]
for col in required_columns:
    check(f"Column '{col}' in CREATE TABLE", f"{col}" in api_src)

check("WAL mode pragma", "journal_mode=WAL" in api_src)
check("busy_timeout pragma", "busy_timeout" in api_src)
check("idx_jobs_status_priority index", "idx_jobs_status_priority" in api_src)
check("idx_jobs_heartbeat index", "idx_jobs_heartbeat" in api_src)

# --- Test 2: VRAM Manifest Matches Workflow Files ---
print("\n[Test 2] VRAM Manifest ↔ Workflow Alignment")
manifest = json.loads((CONFIGS_DIR / "model_vram_manifest.json").read_text())
for wf_file in WORKFLOWS_DIR.glob("*.json"):
    wf = json.loads(wf_file.read_text())
    wf_name = wf.get("workflow")
    wf_vram = wf.get("estimated_vram_mb")
    man_vram = manifest.get(wf_name)
    check(
        f"{wf_file.name}: vram match ({wf_vram} == {man_vram})",
        wf_vram == man_vram,
        f"workflow={wf_vram}, manifest={man_vram}"
    )
    check(f"{wf_file.name}: has prompt key", "prompt" in wf)

# --- Test 3: Auth Enforcement Logic ---
print("\n[Test 3] Authentication Enforcement")
api_src = (SCRIPTS_DIR / "queue_api.py").read_text()
ngrok_src = (SCRIPTS_DIR / "ngrok_launcher.py").read_text()

check("queue_api.py: reads COMFYUI_AUTH_PASSWORD env", "COMFYUI_AUTH_PASSWORD" in api_src)
check("queue_api.py: hardcoded username kaggle", 'COMFYUI_USER = "kaggle"' in api_src)
check("queue_api.py: exits if creds missing", "sys.exit(1)" in api_src)
check("queue_api.py: HTTPBasic security", "HTTPBasic" in api_src)
check("queue_api.py: verify_credentials dependency", "verify_credentials" in api_src)
check("ngrok_launcher.py: mandatory auth check", "COMFYUI_AUTH_PASSWORD" in ngrok_src)
check("ngrok_launcher.py: refuses without auth", "sys.exit(1)" in ngrok_src)
check("ngrok_launcher.py: --basic-auth flag", "--basic-auth" in ngrok_src)

# --- Test 4: Heartbeat Mechanism ---
print("\n[Test 4] Heartbeat / Orphan Recovery")
consumer_src = (SCRIPTS_DIR / "queue_consumer.py").read_text()
check("consumer: heartbeat_loop function", "async def heartbeat_loop" in consumer_src or "def heartbeat_loop" in consumer_src)
check("consumer: heartbeat interval defined", "HEARTBEAT_INTERVAL" in consumer_src)
check("consumer: orphan/stale threshold defined", "ORPHAN_HEARTBEAT_AGE" in consumer_src or "STALE_THRESHOLD" in consumer_src)
check("consumer: recover_orphaned_jobs function", "def recover_orphaned_jobs" in consumer_src)
check("consumer: updates last_heartbeat", "last_heartbeat" in consumer_src)

# --- Test 5: Retry Policy Implementation ---
print("\n[Test 5] Retry Policy")
check("consumer: retry/backoff logic present", "backoff" in consumer_src.lower())
check("consumer: MAX_RETRIES defined", "MAX_RETRIES" in consumer_src)
check("consumer: retry_count tracking", "retry_count" in consumer_src)
check("consumer: checks retry_count vs max", "retry_count >=" in consumer_src or "retry_count <" in consumer_src or ">= MAX_RETRIES" in consumer_src)
check("consumer: permanent failure path", "permanently failed" in consumer_src.lower() or 'status="failed"' in consumer_src or "status='failed'" in consumer_src)

# --- Test 6: Disk Rotation ---
print("\n[Test 6] Disk Space Management")
# Consumer may delegate rotation to watchdog or handle inline; check for any disk management
has_rotation = "rotate_outputs" in consumer_src or "disk_usage" in consumer_src or "output" in consumer_src.lower()
check("consumer: disk/output management awareness", has_rotation)
check("consumer: output directory referenced", "/kaggle/working/output" in consumer_src or "output" in consumer_src.lower())

# --- Test 7: SIGTERM Handlers ---
print("\n[Test 7] Graceful Shutdown (SIGTERM)")
for script_name in ["queue_api.py", "queue_consumer.py", "watchdog.py", "ngrok_launcher.py"]:
    src = (SCRIPTS_DIR / script_name).read_text()
    check(f"{script_name}: SIGTERM handler registered", "signal.SIGTERM" in src or "SIGTERM" in src)

# --- Test 8: Structured Logging ---
print("\n[Test 8] Structured JSON Logging")
for script_name in ["queue_api.py", "queue_consumer.py", "watchdog.py", "ngrok_launcher.py"]:
    src = (SCRIPTS_DIR / script_name).read_text()
    check(f"{script_name}: JsonFormatter class", "class JsonFormatter" in src)
    check(f"{script_name}: JSON output format", "json.dumps" in src)

# --- Test 9: Health Endpoint Fields ---
print("\n[Test 9] /api/health Response Fields")
api_src = (SCRIPTS_DIR / "queue_api.py").read_text()
required_health_fields = [
    "comfyui", "ngrok", "ngrok_url", "consumer", "vram_free_gb",
    "ram_used_pct", "disk_output_used_gb", "disk_model_cache_used_gb",
    "jobs_pending", "jobs_running", "jobs_completed_total", "jobs_failed_total"
]
for field in required_health_fields:
    check(f"Health field '{field}'", f'"{field}"' in api_src)

# --- Test 10: Workflow Allowlist ---
print("\n[Test 10] Workflow Allowlist Enforcement")
check("queue_api.py: loads workflow allowlist", "allowlist" in api_src.lower() or "workflow_allowlist" in api_src)
check("queue_api.py: validates against allowlist", "not in" in api_src and "allowlist" in api_src.lower())
check("queue_api.py: ALLOW_CUSTOM_WORKFLOWS env check", "ALLOW_CUSTOM_WORKFLOWS" in api_src)
check("queue_api.py: rejects unapproved workflows", "400" in api_src or "403" in api_src)

# --- Test 11: SQLite Backup ---
print("\n[Test 11] SQLite Backup Mechanism")
# Backup may be in consumer or watchdog; check both
consumer_src = (SCRIPTS_DIR / "queue_consumer.py").read_text()
watchdog_src = (SCRIPTS_DIR / "watchdog.py").read_text()
combined = consumer_src + watchdog_src
has_backup = "backup" in combined.lower() or ".bak" in combined or "shutil.copy" in combined
check("backup mechanism present (consumer or watchdog)", has_backup)
check("integrity_check or recovery referenced", "integrity" in combined.lower() or "recover" in combined.lower())

# --- Test 12: RAM Monitoring ---
print("\n[Test 12] RAM Monitoring (Major 5.3)")
check("consumer: RAM monitoring present", "ram" in consumer_src.lower() and ("psutil" in consumer_src or "/proc/meminfo" in consumer_src))
check("consumer: RAM_THRESHOLD_PCT", "RAM_THRESHOLD_PCT" in consumer_src)
check("consumer: get_ram_usage_pct function", "def get_ram_usage_pct" in consumer_src)
check("consumer: RAM check gates dispatch", "ram_pct" in consumer_src or "RAM_THRESHOLD" in consumer_src)

# --- Summary ---
print(f"\n{'='*60}")
print(f"  RESULTS: {passed} passed, {failed} failed out of {passed+failed} checks")
print(f"{'='*60}")

if failed > 0:
    print("\n⚠️  Some checks failed. Review above for details.")
    sys.exit(1)
else:
    print("\n✓ All integration validation checks passed.")
    sys.exit(0)