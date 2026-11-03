#!/bin/bash
# Watchdog process monitor for ComfyUI on Kaggle.
# Restarts ComfyUI automatically if it crashes or exits unexpectedly.
# This file is embedded into Cell 5 of the generated notebook by
# scripts/generate_notebook.py and deployed to /kaggle/working/watchdog.sh.

while true; do
    echo "🔄 Starting ComfyUI..."
    cd /kaggle/working/ComfyUI
    python main.py --listen 0.0.0.0 --port 8188
    echo "⚠️ ComfyUI exited with code $?. Restarting in 5s..."
    sleep 5
done