# Kaggle ComfyUI GPU Service Architecture

## Source Videos
1. **Video 1**: `https://www.youtube.com/watch?v=zgALArNSMR4`
   - *Topic*: ComfyUI Setup, Queue Management, and Watchdog implementation on Kaggle.
   - *Key Components*: 
     - ComfyUI Core Installation
     - Ngrok Tunneling for public access
     - Watchdog script to auto-restart services if they crash
     - Queue system for handling multiple generation requests (Images/Video)

2. **Video 2**: `https://www.youtube.com/watch?v=rEjasJZq1t8`
   - *Topic*: Advanced Queue Management and Multi-Model Support.
   - *Key Components*:
     - Task prioritization
     - Model switching (SDXL, FLUX, etc.)
     - Result storage and retrieval

## Project Structure
```
kaggle-comfyui-queue/
├── bootstrap.sh          # One-line setup script
├── install.sh            # Dependency installer
├── start.sh              # Service starter (ComfyUI + Ngrok + Watchdog)
├── watchdog.sh           # Process monitor
├── queue_manager.py      # Python-based queue logic
├── setup.ipynb           # Kaggle Notebook entry point
├── kernel-metadata.json  # Kaggle CLI metadata
└── config/
    └── auth.env.example  # Token template
```

## Implementation Plan
1. **Bootstrap**: Clone repo, install dependencies, download lightweight model (SDXL Turbo).
2. **Service Start**: Launch ComfyUI on port 8188.
3. **Tunnel**: Use `pyngrok` to expose port 8188.
4. **Watchdog**: Monitor `comfyui` process; restart if exit code != 0.
5. **Queue**: Simple FIFO queue for API requests to prevent GPU overload.