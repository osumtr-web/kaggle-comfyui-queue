#!/usr/bin/env python3
"""Generate ultimate_comfyui.ipynb programmatically via nbformat.

Produces a 4-cell Kaggle notebook that wraps the existing shell scripts
(install.sh, start.sh) for idempotent deployment. Cells are referenced by
tag metadata (not index) to survive insertion/deletion.

Design reference: docs/architecture/design_draft.md §4.6
"""

import json
import sys
from pathlib import Path

try:
    import nbformat
    from nbformat.v4 import new_markdown_cell, new_code_cell, new_notebook
except ImportError:
    print("ERROR: nbformat is required. Install with: pip install nbformat", file=sys.stderr)
    sys.exit(1)


CELL_DEFINITIONS = [
    {
        "type": "markdown",
        "tag": None,
        "source": (
            "# Kaggle ComfyUI GPU-as-a-Service\n\n"
            "Self-contained notebook deploying ComfyUI as a persistent GPU service "
            "with automatic crash recovery and public access via ngrok.\n\n"
            "**Do not hand-edit this notebook.** Regenerate via `scripts/generate_notebook.py`.\n\n"
            "This notebook clones the project from GitHub, then runs installation and startup scripts."
        ),
    },
    {
        "type": "code",
        "tag": "clone",
        "source": (
            "# Cell tag: clone — Pulls project files from GitHub\n"
            "# Kaggle only uploads the notebook, so we must clone the repo\n"
            "import subprocess\n"
            "import os\n\n"
            "REPO_URL = 'https://github.com/osumtr-web/kaggle-comfyui-queue.git'\n"
            "WORKDIR = '/kaggle/working/kaggle-comfyui-queue'\n\n"
            "if os.path.exists(WORKDIR):\n"
            "    print(f'Repo already exists at {WORKDIR}, skipping clone')\n"
            "else:\n"
            "    print(f'Cloning {REPO_URL}...')\n"
            "    result = subprocess.run(\n"
            "        ['git', 'clone', REPO_URL, WORKDIR],\n"
            "        capture_output=True,\n"
            "        text=True\n"
            "    )\n"
            "    if result.returncode != 0:\n"
            "        print(f'❌ Clone failed: {result.stderr}')\n"
            "        raise SystemExit(1)\n"
            "    print(f'✅ Cloned to {WORKDIR}')\n"
        ),
    },
    {
        "type": "code",
        "tag": "install",
        "source": (
            "# Cell tag: install — Installs ComfyUI, dependencies, and curated models\n"
            "# Wraps install.sh which is idempotent (skips if .kgpu_installed flag exists)\n"
            "!bash /kaggle/working/kaggle-comfyui-queue/install.sh --non-interactive"
        ),
    },
    {
        "type": "code",
        "tag": "model",
        "source": (
            "# Cell tag: model — Downloads additional models via model_registry.py\n"
            "# Moonmaster Krea2 Turbo is already downloaded by install.sh;\n"
            "# this cell demonstrates how to add more models post-install.\n"
            "!python /kaggle/working/kaggle-comfyui-queue/scripts/model_registry.py install \\\n"
            "    --repo stabilityai/sdxl-turbo \\\n"
            "    --filename sd_xl_turbo_1.0_fp16.safetensors \\\n"
            "    --type diffusion_image \\\n"
            "    --tag sdxl,turbo,fallback"
        ),
    },
    {
        "type": "code",
        "tag": "start",
        "source": (
            "# Cell tag: start — Launches all services (ComfyUI, watchdog, queue API, consumer, ngrok)\n"
            "# Wraps scripts/start.sh which handles stale process cleanup, readiness probes,\n"
            "# ngrok URL discovery, and auth credential generation.\n"
            "!bash /kaggle/working/kaggle-comfyui-queue/start.sh"
        ),
    },
    {
        "type": "code",
        "tag": "verify",
        "source": (
            "# Cell tag: verify — Confirms deployment health and prints access URLs\n"
            "import subprocess, json, time\n\n"
            "# Wait for queue API health endpoint\n"
            "for i in range(30):\n"
            "    try:\n"
            "        r = subprocess.run(\n"
            "            ['curl', '-sf', 'http://localhost:8189/api/health'],\n"
            "            capture_output=True, text=True, timeout=5\n"
            "        )\n"
            "        if r.returncode == 0:\n"
            "            health = json.loads(r.stdout)\n"
            "            print(f'✅ Queue API healthy: {json.dumps(health, indent=2)}')\n"
            "            break\n"
            "    except Exception:\n"
            "        pass\n"
            "    time.sleep(2)\n"
            "else:\n"
            "    print('⚠️ Queue API not responding after 60s — check logs')\n\n"
            "# Print ngrok URL\n"
            "try:\n"
            "    with open('/kaggle/working/ngrok_url.txt') as f:\n"
            "        url = f.read().strip()\n"
            "    print(f'🌐 Public URL: {url}')\n"
            "    print(f'🔑 Auth: kaggle:<password printed in startup logs>')\n"
            "except FileNotFoundError:\n"
            "    print('⚠️ ngrok_url.txt not found — ngrok may still be starting')"
        ),
    },
    {
        "type": "code",
        "tag": "gpu-guard",
        "source": (
            "# Cell tag: gpu-guard — Manually stop GPU session to preserve 30h/week quota\n"
            "# The GPU Session Guard runs automatically via start.sh and auto-stops\n"
            "# after 30 min of idle time. Use this cell to stop immediately.\n"
            "import subprocess\n"
            "result = subprocess.run(\n"
            "    ['python3', '/kaggle/working/kaggle-comfyui-queue/scripts/gpu_session_guard.py',\n"
            "     '--idle-timeout', '0'],\n"
            "    capture_output=True, text=True, timeout=30\n"
            ")\n"
            "print(result.stdout)\n"
            "if result.stderr:\n"
            "    print(result.stderr)"
        ),
    },
]


def build_notebook() -> nbformat.NotebookNode:
    """Construct the tagged notebook."""
    nb = new_notebook()
    nb.metadata.kernelspec = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }

    for cell_def in CELL_DEFINITIONS:
        if cell_def["type"] == "markdown":
            cell = new_markdown_cell(cell_def["source"])
        else:
            cell = new_code_cell(cell_def["source"])

        # Attach tag metadata for cell identification
        if cell_def.get("tag"):
            cell.metadata["tags"] = [cell_def["tag"]]

        nb.cells.append(cell)

    return nb


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    output_path = project_root / "ultimate_comfyui.ipynb"

    nb = build_notebook()

    # Validate before writing
    try:
        nbformat.validate(nb)
    except nbformat.ValidationError as e:
        print(f"ERROR: Notebook validation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Write atomically
    tmp_path = output_path.with_suffix(".ipynb.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)
    tmp_path.rename(output_path)

    # Verify written file
    with open(output_path, "r", encoding="utf-8") as f:
        loaded = nbformat.read(f, as_version=4)

    cell_types = [c.cell_type for c in loaded.cells]
    tags = [c.metadata.get("tags", ["—"])[0] if c.metadata.get("tags") else "—" for c in loaded.cells]
    print(f"✅ Generated {output_path}")
    print(f"   Cells: {len(loaded.cells)} ({', '.join(cell_types)})")
    print(f"   Tags:  {', '.join(tags)}")
    print(f"   Validation: passed")


if __name__ == "__main__":
    main()