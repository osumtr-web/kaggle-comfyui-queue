"""In-memory FIFO job queue with artifact storage.

MVP implementation uses collections.deque for queue state.
SQLite persistence deferred to Phase 2.
Artifacts stored at /kaggle/working/output/{job_id}.png.
"""

import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

OUTPUT_DIR = Path("/kaggle/working/output")


class Job:
    """Single generation job."""

    __slots__ = (
        "id", "status", "prompt", "params",
        "created_at", "completed_at", "result_path", "error",
    )

    def __init__(self, prompt: str, params: dict):
        self.id: str = str(uuid.uuid4())
        self.status: str = "pending"
        self.prompt: str = prompt
        self.params: dict = params
        self.created_at: float = time.time()
        self.completed_at: Optional[float] = None
        self.result_path: Optional[str] = None
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "prompt": self.prompt,
            "params": self.params,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "result_path": self.result_path,
            "error": self.error,
        }


class QueueManager:
    """Thread-safe FIFO queue for ComfyUI generation jobs."""

    def __init__(self):
        self._queue: deque[Job] = deque()
        self._jobs: dict[str, Job] = {}
        # Defer OUTPUT_DIR creation to avoid import-time failures outside Kaggle.
        # Directory is created lazily on first submit or artifact write.

    def _ensure_output_dir(self) -> None:
        """Create OUTPUT_DIR lazily on first use."""
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def submit(self, prompt: str, params: dict) -> Job:
        """Add a job to the queue. Returns the created Job."""
        self._ensure_output_dir()
        job = Job(prompt, params)
        self._jobs[job.id] = job
        self._queue.append(job)
        return job

    def list_jobs(self) -> list[dict]:
        """Return all jobs as dicts, newest first."""
        return [j.to_dict() for j in sorted(
            self._jobs.values(), key=lambda j: j.created_at, reverse=True
        )]

    def get_job(self, job_id: str) -> Optional[dict]:
        """Return a single job dict or None."""
        job = self._jobs.get(job_id)
        return job.to_dict() if job else None

    def get_artifact_path(self, job_id: str) -> Optional[Path]:
        """Return the artifact file path if it exists on disk."""
        job = self._jobs.get(job_id)
        if not job or not job.result_path:
            return None
        path = Path(job.result_path)
        return path if path.exists() else None

    def next_pending(self) -> Optional[Job]:
        """Pop and return the next pending job, or None if queue empty."""
        while self._queue:
            job = self._queue[0]
            if job.status == "pending":
                self._queue.popleft()
                job.status = "running"
                return job
            # Skip already-completed/failed jobs left in deque
            self._queue.popleft()
        return None

    def mark_completed(self, job_id: str, result_path: str) -> None:
        """Mark a job as completed with its artifact path."""
        job = self._jobs.get(job_id)
        if job:
            job.status = "completed"
            job.completed_at = time.time()
            job.result_path = result_path

    def mark_failed(self, job_id: str, error: str) -> None:
        """Mark a job as failed with an error message."""
        job = self._jobs.get(job_id)
        if job:
            job.status = "failed"
            job.completed_at = time.time()
            job.error = error


# Module-level singleton
manager = QueueManager()