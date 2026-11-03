#!/usr/bin/env python3
"""Integration test for ComfyUI Queue REST API.

Validates the job lifecycle: submit → pending → running → completed → artifact download.
Runs against the queue_manager module directly (no live ComfyUI server required).

Usage:
    source .venv/bin/activate
    python tests/test_queue_integration.py
"""

import sys
import tempfile
from pathlib import Path

# Add project root to path so custom_nodes package is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from custom_nodes.comfyui_queue.queue_manager import QueueManager


def test_full_job_lifecycle():
    """Submit 3 jobs, process sequentially, verify status transitions and artifacts."""
    # Use a temp dir for artifacts so test doesn't depend on /kaggle/working
    with tempfile.TemporaryDirectory() as tmpdir:
        qm = QueueManager.__new__(QueueManager)
        qm._queue = __import__("collections").deque()
        qm._jobs = {}
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Monkey-patch OUTPUT_DIR for this test instance
        import custom_nodes.comfyui_queue.queue_manager as qm_mod
        original_output_dir = qm_mod.OUTPUT_DIR
        qm_mod.OUTPUT_DIR = output_dir

        try:
            # --- Phase 1: Submit 3 jobs ---
            jobs = []
            for i in range(3):
                job = qm.submit(
                    prompt=f"test prompt {i}",
                    params={"width": 512, "height": 512, "steps": 20, "seed": i},
                )
                jobs.append(job)
                assert job.status == "pending", f"Job {i} should be pending after submit"

            # Verify list returns all 3
            all_jobs = qm.list_jobs()
            assert len(all_jobs) == 3, f"Expected 3 jobs, got {len(all_jobs)}"

            # --- Phase 2: Process sequentially, verify transitions ---
            for i, expected_job in enumerate(jobs):
                next_job = qm.next_pending()
                assert next_job is not None, f"Expected pending job at step {i}"
                assert next_job.id == expected_job.id, (
                    f"FIFO order violated at step {i}: "
                    f"expected {expected_job.id}, got {next_job.id}"
                )
                assert next_job.status == "running", (
                    f"Job {i} should be running after next_pending()"
                )

                # Simulate completion: create a fake PNG artifact
                artifact_path = output_dir / f"{next_job.id}.png"
                artifact_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

                qm.mark_completed(next_job.id, str(artifact_path))

                # Verify final state
                job_dict = qm.get_job(next_job.id)
                assert job_dict["status"] == "completed", (
                    f"Job {i} should be completed after mark_completed"
                )
                assert job_dict["completed_at"] is not None
                assert job_dict["result_path"] == str(artifact_path)

            # --- Phase 3: Verify no more pending jobs ---
            assert qm.next_pending() is None, "Queue should be empty after processing all jobs"

            # --- Phase 4: Verify artifact retrieval ---
            for job in jobs:
                path = qm.get_artifact_path(job.id)
                assert path is not None, f"Artifact path should exist for job {job.id}"
                assert path.exists(), f"Artifact file should exist at {path}"
                content = path.read_bytes()
                assert content[:4] == b"\x89PNG", (
                    f"Artifact for job {job.id} should be valid PNG header"
                )

            # --- Phase 5: Verify failure marking ---
            fail_job = qm.submit(prompt="will fail", params={})
            qm.mark_failed(fail_job.id, "GPU OOM")
            fail_dict = qm.get_job(fail_job.id)
            assert fail_dict["status"] == "failed"
            assert fail_dict["error"] == "GPU OOM"
            assert qm.get_artifact_path(fail_job.id) is None

            print("✅ All integration tests passed:")
            print("   - 3 jobs submitted and processed in FIFO order")
            print("   - Status transitions: pending → running → completed verified")
            print("   - Artifact creation and retrieval verified (valid PNG)")
            print("   - Failure marking verified")
            print("   - Empty queue after full processing verified")

        finally:
            qm_mod.OUTPUT_DIR = original_output_dir


if __name__ == "__main__":
    test_full_job_lifecycle()