"""REST API routes for the ComfyUI job queue.

Endpoints registered on ComfyUI's aiohttp server:
 POST /api/jobs - Submit a generation job
 GET /api/jobs - List all jobs
 GET /api/jobs/{id} - Get single job status
 GET /api/jobs/{id}/artifact - Download generated image
"""

import json
from aiohttp import web

from .queue_manager import manager


async def handle_submit(request: web.Request) -> web.Response:
    """POST /api/jobs — submit a new generation job."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    prompt = body.get("prompt")
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    params = body.get("params", {})
    job = manager.submit(prompt, params)
    return web.json_response(job.to_dict(), status=201)


async def handle_list_jobs(request: web.Request) -> web.Response:
    """GET /api/jobs — list all jobs, newest first."""
    return web.json_response(manager.list_jobs())


async def handle_get_job(request: web.Request) -> web.Response:
    """GET /api/jobs/{id} — get single job status and metadata."""
    job_id = request.match_info["id"]
    job = manager.get_job(job_id)
    if not job:
        return web.json_response({"error": "Job not found"}, status=404)
    return web.json_response(job)


async def handle_get_artifact(request: web.Request) -> web.Response:
    """GET /api/jobs/{id}/artifact — download the generated image."""
    job_id = request.match_info["id"]
    path = manager.get_artifact_path(job_id)
    if not path:
        return web.json_response(
            {"error": "Artifact not found or job incomplete"}, status=404
        )
    return web.FileResponse(path)


def setup_routes(server) -> None:
    """Register queue API routes on the ComfyUI aiohttp app."""
    app = server.app if hasattr(server, "app") else server
    app.router.add_post("/api/jobs", handle_submit)
    app.router.add_get("/api/jobs", handle_list_jobs)
    app.router.add_get("/api/jobs/{id}", handle_get_job)
    app.router.add_get("/api/jobs/{id}/artifact", handle_get_artifact)