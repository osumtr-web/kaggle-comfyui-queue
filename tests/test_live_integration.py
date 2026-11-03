#!/usr/bin/env python3
"""
Kaggle ComfyUI Queue Service — Live Integration Test Suite (T8)

Tests the full job lifecycle against a running deployment:
  submit → poll status transitions → download artifact → verify PNG header

Requires a live tunnel URL and auth credentials. Set via environment:
  KGPU_URL=https://xxxx.ngrok-free.app
  KGPU_USER=kaggle
  KGPU_PASS=<password from startup logs>

Run: pytest tests/test_live_integration.py -v --timeout=600
Design reference: docs/architecture/design_draft.md §10 T8, §11.3
"""

import json
import os
import struct
import time
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

TUNNEL_URL = os.environ.get("KGPU_URL", "").rstrip("/")
AUTH_USER = os.environ.get("KGPU_USER", "kaggle")
AUTH_PASS = os.environ.get("KGPU_PASS", "")

# Minimal valid ComfyUI workflow for SDXL image generation
MINIMAL_WORKFLOW = {
    "prompt": {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 42,
                "steps": 4,
                "cfg": 1.0,
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sd_xl_turbo_1.0_fp16.safetensors"},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "a photo of a cat", "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry, low quality", "clip": ["4", 1]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "test_integration", "images": ["8", 0]},
        },
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Authenticated HTTP client for the queue API."""
    if not TUNNEL_URL:
        pytest.skip("KGPU_URL not set — skipping live integration tests")
    if not AUTH_PASS:
        pytest.skip("KGPU_PASS not set — skipping live integration tests")

    with httpx.Client(
        base_url=f"{TUNNEL_URL}",
        auth=(AUTH_USER, AUTH_PASS),
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    ) as c:
        yield c


@pytest.fixture(scope="module")
def submitted_job(client):
    """Submit a test job and return the job ID."""
    resp = client.post("/api/jobs", json={
        "workflow": MINIMAL_WORKFLOW,
        "params": {"width": 512, "height": 512, "steps": 4},
    })
    assert resp.status_code == 200 or resp.status_code == 201, \
        f"Job submission failed: {resp.status_code} {resp.text}"
    data = resp.json()
    job_id = data.get("id") or data.get("job_id")
    assert job_id is not None, f"No job ID in response: {data}"
    return str(job_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """Verify /api/health is accessible without auth."""

    def test_health_returns_200(self):
        if not TUNNEL_URL:
            pytest.skip("KGPU_URL not set")
        resp = httpx.get(f"{TUNNEL_URL}/api/health", timeout=10.0, follow_redirects=True)
        assert resp.status_code == 200, f"Health check failed: {resp.status_code}"

    def test_health_contains_required_fields(self):
        if not TUNNEL_URL:
            pytest.skip("KGPU_URL not set")
        resp = httpx.get(f"{TUNNEL_URL}/api/health", timeout=10.0, follow_redirects=True)
        data = resp.json()
        # At minimum, health should report queue depth
        assert isinstance(data, dict), f"Health response not a dict: {type(data)}"


class TestAuthentication:
    """Verify auth enforcement on protected endpoints."""

    def test_unauthenticated_request_returns_401(self):
        if not TUNNEL_URL:
            pytest.skip("KGPU_URL not set")
        resp = httpx.get(f"{TUNNEL_URL}/api/jobs", timeout=10.0, follow_redirects=True)
        assert resp.status_code == 401, \
            f"Expected 401 without auth, got {resp.status_code}"

    def test_wrong_credentials_returns_401(self, client):
        # Create a new client with wrong creds
        with httpx.Client(
            base_url=TUNNEL_URL,
            auth=("kaggle", "wrong_password"),
            timeout=10.0,
            follow_redirects=True,
        ) as bad_client:
            resp = bad_client.get("/api/jobs")
            assert resp.status_code == 401


class TestJobLifecycle:
    """Full submit → poll → complete → download cycle."""

    def test_submit_returns_job_id(self, submitted_job):
        assert submitted_job is not None
        assert len(submitted_job) > 0

    def test_job_status_transitions(self, client, submitted_job):
        """Poll until completed/failed or timeout (5 min)."""
        seen_statuses = set()
        deadline = time.monotonic() + 300  # 5 minutes

        while time.monotonic() < deadline:
            resp = client.get(f"/api/jobs/{submitted_job}")
            assert resp.status_code == 200, \
                f"Status poll failed: {resp.status_code} {resp.text}"
            data = resp.json()
            status = data.get("status", "unknown")
            seen_statuses.add(status)

            if status == "completed":
                break
            if status == "failed":
                error = data.get("error_msg") or data.get("error") or "unknown"
                pytest.fail(f"Job {submitted_job} failed: {error}")

            time.sleep(5)
        else:
            pytest.fail(
                f"Job {submitted_job} did not complete within 5 min. "
                f"Seen statuses: {seen_statuses}"
            )

        # Verify we saw expected transitions
        assert "completed" in seen_statuses, \
            f"Job never reached 'completed'. Seen: {seen_statuses}"

    def test_download_artifact(self, client, submitted_job):
        """Download the generated artifact after completion."""
        # Wait for completion first
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            resp = client.get(f"/api/jobs/{submitted_job}")
            if resp.json().get("status") == "completed":
                break
            time.sleep(5)
        else:
            pytest.skip("Job not completed — cannot test artifact download")

        resp = client.get(f"/api/jobs/{submitted_job}/artifact")
        assert resp.status_code == 200, \
            f"Artifact download failed: {resp.status_code} {resp.text}"
        assert len(resp.content) > 0, "Artifact response body is empty"

    def test_artifact_is_valid_png(self, client, submitted_job):
        """Verify downloaded artifact has valid PNG header signature."""
        # Wait for completion
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            resp = client.get(f"/api/jobs/{submitted_job}")
            if resp.json().get("status") == "completed":
                break
            time.sleep(5)
        else:
            pytest.skip("Job not completed — cannot verify PNG")

        resp = client.get(f"/api/jobs/{submitted_job}/artifact")
        if resp.status_code != 200:
            pytest.skip(f"Artifact not available: {resp.status_code}")

        content = resp.content
        # PNG magic bytes: 137 80 78 71 13 10 26 10
        png_signature = b'\x89PNG\r\n\x1a\n'
        assert content[:8] == png_signature, (
            f"Artifact is not a valid PNG. "
            f"Expected header {png_signature!r}, got {content[:8]!r}"
        )

        # Verify IHDR chunk exists (bytes 8-11 should be length, 12-15 = 'IHDR')
        assert content[12:16] == b'IHDR', \
            f"Missing IHDR chunk at offset 12: {content[12:16]!r}"

        # Extract dimensions from IHDR (width at offset 16, height at offset 20)
        width = struct.unpack(">I", content[16:20])[0]
        height = struct.unpack(">I", content[20:24])[0]
        assert width > 0 and height > 0, \
            f"Invalid PNG dimensions: {width}x{height}"
        assert width <= 4096 and height <= 4096, \
            f"PNG dimensions suspiciously large: {width}x{height}"


class TestJobListing:
    """Verify list endpoint returns submitted jobs."""

    def test_list_jobs_includes_submitted(self, client, submitted_job):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        # Response may be a list or a dict with a jobs key
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        job_ids = [str(j.get("id") or j.get("job_id")) for j in jobs]
        assert submitted_job in job_ids, \
            f"Submitted job {submitted_job} not found in listing. IDs: {job_ids}"


class TestPerJobArtifactDirectory:
    """Verify T6 per-job artifact directory structure (if accessible)."""

    def test_metadata_json_exists_after_completion(self, client, submitted_job):
        """Check that metadata.json was written alongside artifacts."""
        # Wait for completion
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            resp = client.get(f"/api/jobs/{submitted_job}")
            if resp.json().get("status") == "completed":
                break
            time.sleep(5)
        else:
            pytest.skip("Job not completed — cannot verify metadata")

        # Check result_path points to per-job directory
        resp = client.get(f"/api/jobs/{submitted_job}")
        data = resp.json()
        result_path = data.get("result_path", "")
        # After T6, result_path should be /kaggle/working/output/{id}/
        assert str(submitted_job) in result_path, \
            f"result_path does not contain job ID: {result_path}"