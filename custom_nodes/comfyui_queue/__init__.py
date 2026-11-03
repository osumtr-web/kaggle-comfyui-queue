"""ComfyUI Queue custom node package.

Registers REST API routes for job submission, status tracking,
and artifact retrieval. Integrates with ComfyUI's existing server
via the WEB_DIRECTORY extension point.
"""

from .routes import setup_routes

# ComfyUI discovers custom nodes through NODE_CLASS_MAPPINGS.
# This package has no UI nodes; it only provides API endpoints.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

WEB_DIRECTORY = "."


def init(server):
    """Called by ComfyUI server startup to register queue API routes."""
    setup_routes(server)