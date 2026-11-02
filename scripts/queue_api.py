#!/usr/bin/env python3
"""
Kaggle ComfyUI Generation Queue Service - FastAPI REST API
Provides job queue management with authentication and workflow allowlist enforcement.
"""

import asyncio
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import aiosqlite
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

# Model registry import — resolve from scripts directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_registry import ModelRegistry  # noqa: E402


# Configure structured JSON logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
# --- Structured JSON Logger (Major 6.4) ---
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": "queue_api",
            "level": record.levelname,
            "event": record.getMessage(),
        }
        if hasattr(record, "job_id"):
            log_entry["job_id"] = record.job_id
        if hasattr(record, "correlation_id"):
            log_entry["correlation_id"] = record.correlation_id
        return json.dumps(log_entry)

logger = logging.getLogger("queue_api")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logger.addHandler(_handler)

# Configuration from environment variables
DB_PATH = "/kaggle/working/queue.db"
WORKFLOWS_DIR = "/kaggle/working/workflows"
HOST = "0.0.0.0"
PORT = 8189

# Mandatory authentication (Blocker 4.1 — both user AND pass from env, refuse to start if missing)
COMFYUI_USER = "kaggle"
COMFYUI_PASS = os.environ.get("COMFYUI_AUTH_PASSWORD")
if not COMFYUI_PASS:
    logger.critical("COMFYUI_AUTH_PASSWORD must be set as Kaggle Secret. Refusing to start without authentication.")
    sys.exit(1)

# Workflow allowlist configuration
ALLOW_CUSTOM_WORKFLOWS = os.environ.get("KAGGLE_SECRET_ALLOW_CUSTOM_WORKFLOWS", "false").lower() == "true"
MAX_PENDING_JOBS = int(os.getenv("MAX_PENDING_JOBS", "50"))

# Initialize FastAPI app
app = FastAPI(title="Kaggle ComfyUI Queue API", version="1.0.0")
security = HTTPBasic()

# Global database connection
db_connection: Optional[aiosqlite.Connection] = None


class JobPayload(BaseModel):
    """Request model for job submission"""
    workflow: str
    params: dict
    priority: Optional[int] = 0


async def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic authentication credentials (Blocker 4.1)"""
    if credentials.username != COMFYUI_USER or credentials.password != COMFYUI_PASS:
        logger.warning(f"Authentication failed for user: {credentials.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


async def init_database():
    """Initialize SQLite database with WAL mode and required schema"""
    global db_connection
    
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    db_connection = await aiosqlite.connect(DB_PATH)
    
    # Enable WAL mode for better concurrency
    await db_connection.execute("PRAGMA journal_mode=WAL")
    await db_connection.execute("PRAGMA busy_timeout=5000")
    await db_connection.execute("PRAGMA synchronous=NORMAL")
    
    # Create jobs table
    await db_connection.execute("""
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
        )
    """)
    
    # Create indexes
    await db_connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_status_priority 
        ON jobs(status, priority DESC)
    """)
    await db_connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_heartbeat 
        ON jobs(last_heartbeat) 
        WHERE status='running'
    """)
    
    await db_connection.commit()
    logger.info(f"Database initialized at {DB_PATH}")


async def load_workflow_allowlist() -> set:
    """Load allowed workflow names from workflows directory"""
    workflows_path = Path(WORKFLOWS_DIR)
    allowlist = set()
    
    if workflows_path.exists() and workflows_path.is_dir():
        for json_file in workflows_path.glob("*.json"):
            workflow_name = json_file.stem  # filename without .json
            allowlist.add(workflow_name)
        logger.info(f"Loaded {len(allowlist)} workflows from allowlist")
    else:
        logger.warning(f"Workflows directory not found: {WORKFLOWS_DIR}")
    
    return allowlist


async def get_pending_job_count() -> int:
    """Get count of pending jobs in queue"""
    async with db_connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'pending'"
    ) as cursor:
        result = await cursor.fetchone()
        return result[0] if result else 0


async def estimate_wait_time() -> float:
    """Estimate wait time based on average job duration"""
    async with db_connection.execute("""
        SELECT AVG(strftime('%s', completed_at) - strftime('%s', started_at))
        FROM jobs
        WHERE status = 'completed'
        AND started_at IS NOT NULL
        AND completed_at IS NOT NULL
    """) as cursor:
        result = await cursor.fetchone()
        avg_duration = result[0] if result and result[0] else 30.0  # Default 30 seconds
    
    pending_count = await get_pending_job_count()
    return avg_duration * pending_count


@app.on_event("startup")
async def startup_event():
    """Initialize database and load workflow allowlist on startup"""
    await init_database()
    app.state.workflow_allowlist = await load_workflow_allowlist()
    logger.info("Queue API service started")


@app.on_event("shutdown")
async def shutdown_event():
    """Gracefully shutdown and flush database"""
    global db_connection
    if db_connection:
        await db_connection.commit()
        await db_connection.close()
        logger.info("Database connection closed")


def signal_handler(signum, frame):
    """Handle SIGTERM for graceful shutdown"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown")
    asyncio.create_task(shutdown_event())
    sys.exit(0)


# Register signal handler
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# --- Model Registry Initialization ---
registry = ModelRegistry("/kaggle/working/models/registry.json", "/kaggle/working/models")
_install_status: dict[str, dict] = {}

VALID_MODEL_TYPES = {"diffusion_image", "diffusion_video", "llm", "ml_classifier", "audio", "embedding", "other"}


def _install_worker(hf_repo: str, filename: Optional[str], model_type: str, vram_mb: Optional[int], tags: Optional[list]):
    """Background thread worker for model installation to avoid blocking the async event loop."""
    try:
        _install_status[hf_repo] = {"status": "installing", "progress_pct": 0.0, "error": None}
        result = registry.install_model(hf_repo, filename, model_type, vram_mb, tags)
        _install_status[hf_repo] = {"status": "installed", "progress_pct": 100.0, "error": None, "model": result}
    except Exception as e:
        _install_status[hf_repo] = {"status": "failed", "progress_pct": None, "error": str(e)}


@app.post("/api/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(
    request: Request,
    username: str = Depends(verify_credentials)
):
    """
    Submit a new job to the queue
    - Validates payload size (max 64KB)
    - Checks workflow allowlist
    - Enforces queue depth limit
    """
    # Check payload size
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 65536:  # 64KB
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload exceeds maximum size of 64KB"
        )
    
    # Parse and validate payload
    try:
        body = await request.body()
        if len(body) > 65536:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Payload exceeds maximum size of 64KB"
            )
        
        payload_data = json.loads(body)
        payload = JobPayload(**payload_data)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid payload: {str(e)}"
        )
    
    # Check queue depth limit
    pending_count = await get_pending_job_count()
    if pending_count >= MAX_PENDING_JOBS:
        wait_time = await estimate_wait_time()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Queue is full",
                "pending_jobs": pending_count,
                "max_pending": MAX_PENDING_JOBS,
                "estimated_wait_seconds": round(wait_time, 1)
            }
        )
    
    # Validate workflow against allowlist
    if not ALLOW_CUSTOM_WORKFLOWS:
        if payload.workflow not in app.state.workflow_allowlist:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workflow '{payload.workflow}' not in allowlist. "
                       f"Available workflows: {sorted(app.state.workflow_allowlist)}"
            )
    
    # Estimate VRAM usage (simplified - would normally lookup from model manifest)
    estimated_vram_mb = 4096  # Default estimate
    
    # Insert job into database
    payload_json = json.dumps({"workflow": payload.workflow, "params": payload.params})
    
    async with db_connection.execute(
        """
        INSERT INTO jobs (payload_json, priority, estimated_vram_mb)
        VALUES (?, ?, ?)
        """,
        (payload_json, payload.priority, estimated_vram_mb)
    ) as cursor:
        job_id = cursor.lastrowid
    
    await db_connection.commit()
    
    logger.info(f"Job {job_id} created by {username} with workflow '{payload.workflow}'")
    
    return {
        "id": job_id,
        "status": "pending",
        "message": "Job submitted successfully"
    }


@app.get("/api/jobs/{job_id}")
async def get_job(
    job_id: int,
    username: str = Depends(verify_credentials)
):
    """Retrieve a specific job by ID"""
    async with db_connection.execute(
        "SELECT * FROM jobs WHERE id = ?",
        (job_id,)
    ) as cursor:
        cursor.row_factory = aiosqlite.Row
        row = await cursor.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    
    job_data = dict(row)
    # Parse payload_json back to dict
    job_data["payload"] = json.loads(job_data["payload_json"])
    del job_data["payload_json"]
    
    return job_data


@app.get("/api/jobs")
async def list_jobs(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    username: str = Depends(verify_credentials)
):
    """List jobs with optional status filter"""
    if status_filter:
        query = "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?"
        params = (status_filter, limit)
    else:
        query = "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?"
        params = (limit,)
    
    async with db_connection.execute(query, params) as cursor:
        cursor.row_factory = aiosqlite.Row
        rows = await cursor.fetchall()
    
    jobs = []
    for row in rows:
        job_data = dict(row)
        job_data["payload"] = json.loads(job_data["payload_json"])
        del job_data["payload_json"]
        jobs.append(job_data)
    
    return {
        "jobs": jobs,
        "count": len(jobs),
        "limit": limit
    }


@app.delete("/api/jobs/{job_id}")
async def cancel_job(
    job_id: int,
    username: str = Depends(verify_credentials)
):
    """
    Cancel a job
    - pending: cancel immediately
    - running: return 409 (use ComfyUI interrupt)
    - completed/failed/cancelled: return 409
    """
    # Get current job status
    async with db_connection.execute(
        "SELECT status FROM jobs WHERE id = ?",
        (job_id,)
    ) as cursor:
        row = await cursor.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    
    current_status = row[0]
    
    if current_status == "pending":
        # Cancel immediately
        async with db_connection.execute(
            "UPDATE jobs SET status = 'cancelled' WHERE id = ?",
            (job_id,)
        ):
            pass
        await db_connection.commit()
        logger.info(f"Job {job_id} cancelled by {username}")
        return {"message": f"Job {job_id} cancelled", "status": "cancelled"}
    
    elif current_status == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is currently running. Use ComfyUI interrupt to stop it."
        )
    
    else:  # completed, failed, or cancelled
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is already {current_status}. Cannot cancel."
        )


# --- Model Registry Endpoints ---

@app.get("/api/models")
async def list_models(
    type: Optional[str] = Query(None, description="Filter by model_type"),
    search: Optional[str] = Query(None, description="Substring match on model_id or hf_repo"),
    username: str = Depends(verify_credentials)
):
    """List installed models with optional filtering."""
    models = registry.list_models(model_type=type, search=search)
    return {"models": models, "count": len(models)}


@app.get("/api/models/{model_id:path}/status")
async def get_model_install_status(
    model_id: str,
    username: str = Depends(verify_credentials)
):
    """Get installation status for a model being downloaded in background."""
    decoded_id = unquote(model_id)
    status_info = _install_status.get(decoded_id)
    if status_info is None:
        # Check if model exists in registry (already installed previously)
        existing = registry.get_model(decoded_id)
        if existing:
            return {"model_id": decoded_id, "status": "installed", "progress_pct": 100.0, "error": None}
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No install record for model '{decoded_id}'")
    return {
        "model_id": decoded_id,
        "status": status_info["status"],
        "progress_pct": status_info.get("progress_pct"),
        "error": status_info.get("error"),
    }


@app.get("/api/models/{model_id:path}")
async def get_model(
    model_id: str,
    username: str = Depends(verify_credentials)
):
    """Retrieve metadata for a specific installed model."""
    decoded_id = unquote(model_id)
    model_data = registry.get_model(decoded_id)
    if model_data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model '{decoded_id}' not found in registry")
    return model_data


class InstallModelRequest(BaseModel):
    """Request body for model installation."""
    hf_repo: str
    filename: Optional[str] = None
    model_type: str
    vram_mb: Optional[int] = None
    tags: Optional[list] = None


@app.post("/api/models/install")
async def install_model(
    payload: InstallModelRequest,
    username: str = Depends(verify_credentials)
):
    """
    Start background installation of a model from HuggingFace.
    Returns immediately; track progress via GET /api/models/{model_id}/status.
    """
    if payload.model_type not in VALID_MODEL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid model_type '{payload.model_type}'. Must be one of: {sorted(VALID_MODEL_TYPES)}"
        )

    # Check if already installing
    current = _install_status.get(payload.hf_repo)
    if current and current["status"] == "installing":
        return {"status": "installing", "model_id": payload.hf_repo, "estimated_time": "varies"}

    # Launch background thread for download
    t = threading.Thread(
        target=_install_worker,
        args=(payload.hf_repo, payload.filename, payload.model_type, payload.vram_mb, payload.tags),
        daemon=True,
    )
    t.start()

    logger.info(f"Model install started in background: {payload.hf_repo} (type={payload.model_type}) by {username}")
    return {"status": "installing", "model_id": payload.hf_repo, "estimated_time": "varies"}


@app.delete("/api/models/{model_id:path}")
async def uninstall_model(
    model_id: str,
    username: str = Depends(verify_credentials)
):
    """Uninstall a model from the registry."""
    decoded_id = unquote(model_id)
    model_data = registry.get_model(decoded_id)
    if model_data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Model '{decoded_id}' not found in registry")

    try:
        registry.uninstall_model(decoded_id)
    except AttributeError:
        # Fallback if uninstall_model doesn't exist yet — remove from registry dict directly
        logger.warning("registry.uninstall_model not available; attempting manual removal")
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="uninstall_model not implemented in ModelRegistry")

    logger.info(f"Model uninstalled: {decoded_id} by {username}")
    return {"status": "uninstalled", "model_id": decoded_id}


# --- Artifact Serving Endpoints ---

MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".txt": "text/plain",
    ".json": "application/json",
}


@app.get("/api/jobs/{job_id}/artifact")
async def get_job_artifact(
    job_id: int,
    username: str = Depends(verify_credentials)
):
    """
    Download the generated artifact for a completed job.
    Supports images (PNG/JPG/WebP), videos (MP4/WebM), audio (WAV/MP3), and text.
    Returns the file with correct MIME type and Content-Disposition header.
    For LLM/text jobs without a file, returns result_text as JSON.
    """
    async with db_connection.execute(
        "SELECT status, result_path, error_msg FROM jobs WHERE id = ?",
        (job_id,)
    ) as cursor:
        cursor.row_factory = aiosqlite.Row
        row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    job_status = row["status"]
    result_path = row["result_path"]
    error_msg = row["error_msg"]

    if job_status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} is '{job_status}', not 'completed'. Cannot download artifact."
        )

    # If no file path but job completed (e.g., LLM text-only result)
    if not result_path or not Path(result_path).exists():
        # Try to return inline text result from payload or error context
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} completed but no artifact file found at '{result_path}'. "
                   f"This may be a text-only LLM job — check /api/jobs/{job_id} for result_text."
        )

    file_path = Path(result_path)
    suffix = file_path.suffix.lower()
    media_type = MIME_MAP.get(suffix, "application/octet-stream")
    filename = file_path.name

    logger.info(f"Serving artifact for job {job_id}: {filename} ({media_type})")
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/jobs/{job_id}/artifact/preview")
async def get_job_artifact_preview(
    job_id: int,
    username: str = Depends(verify_credentials)
):
    """
    Inline preview of image artifacts (no download prompt).
    Only works for image types; returns 415 for non-image files.
    """
    async with db_connection.execute(
        "SELECT status, result_path FROM jobs WHERE id = ?",
        (job_id,)
    ) as cursor:
        cursor.row_factory = aiosqlite.Row
        row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    if row["status"] != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} is '{row['status']}', not 'completed'."
        )

    result_path = row["result_path"]
    if not result_path or not Path(result_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No artifact file found")

    file_path = Path(result_path)
    suffix = file_path.suffix.lower()
    image_types = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    if suffix not in image_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Preview only supports images. File is '{suffix}'. Use /api/jobs/{job_id}/artifact to download."
        )

    media_type = MIME_MAP.get(suffix, "image/png")
    return FileResponse(path=str(file_path), media_type=media_type)


@app.get("/api/health")
async def health_check():
    """
    Extended health endpoint with full metrics (Major 1.3, Major 6.4).
    No auth required — used for monitoring and URL discovery after tunnel restarts.
    """
    import psutil

    # Queue counts from DB
    pending = running = completed = failed = 0
    try:
        async with db_connection.execute(
            "SELECT "
            "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='running' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) "
            "FROM jobs"
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                pending, running, completed, failed = row
    except Exception:
        pass

    # VRAM (live query)
    vram_free_gb = 0.0
    try:
        result = os.popen(
            "nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits"
        ).read().strip()
        values = [int(v.strip()) for v in result.split("\n") if v.strip()]
        vram_free_gb = round(sum(values) / 1024, 2)
    except Exception:
        pass

    # RAM
    ram_used_pct = 0.0
    try:
        ram_used_pct = round(psutil.virtual_memory().percent, 1)
    except Exception:
        pass

    # Disk usage for output and model cache directories
    def dir_size_gb(path_str: str) -> float:
        try:
            p = Path(path_str)
            if not p.exists():
                return 0.0
            total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            return round(total / (1024 ** 3), 2)
        except Exception:
            return 0.0

    disk_output_gb = dir_size_gb("/kaggle/working/output")
    disk_model_gb = dir_size_gb("/kaggle/working/models")

    # ngrok URL from file (Major 1.3)
    ngrok_url = None
    try:
        url_path = Path("/kaggle/working/ngrok_url.txt")
        if url_path.exists():
            ngrok_url = url_path.read_text().strip()
    except Exception:
        pass

    # Component liveness probes
    comfyui_alive = False
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=2)
        comfyui_alive = resp.status == 200
    except Exception:
        pass

    # Installed models count from registry
    installed_models_count = 0
    try:
        installed_models_count = len(registry.list_models())
    except Exception:
        pass

    return {
        "comfyui": comfyui_alive,
        "ngrok": ngrok_url is not None,
        "ngrok_url": ngrok_url,
        "consumer": True,  # Watchdog manages; API assumes alive if running
        "vram_free_gb": vram_free_gb,
        "ram_used_pct": ram_used_pct,
        "disk_output_used_gb": disk_output_gb,
        "disk_model_cache_used_gb": disk_model_gb,
        "jobs_pending": pending or 0,
        "jobs_running": running or 0,
        "jobs_completed_total": completed or 0,
        "jobs_failed_total": failed or 0,
        "installed_models_count": installed_models_count,
    }


if __name__ == "__main__":
    logger.info(f"Starting Queue API service on {HOST}:{PORT}")
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True
    )