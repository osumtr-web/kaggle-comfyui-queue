# Minimax H3 Video Generator on Kaggle — Setup Guide

Based on transcript from: https://www.youtube.com/watch?v=zgALArNSMR4

## What This Is

Run Minimax H3 (open-source AI video generator) for free on Kaggle with dual T4 GPUs.
Generates up to 15-second videos with synchronized audio in ~16 minutes.

## Prerequisites

1. **Kaggle account** with phone number verified (Settings → Phone Verification)
   - Without verification, GPU access is blocked
2. **Internet enabled** in notebook settings (Settings → Internet → On)
3. **GPU accelerator**: Select `GPU T4 x2` in notebook settings

## Setup Steps

### Step 1: Open the Notebook
- Go to the Kaggle notebook link (provided in video description)
- Click **"Copy and Edit"** to create your own copy

### Step 2: Configure Settings
1. Open **Settings** panel (right sidebar)
2. Under **Accelerator**, select **GPU T4 x2**
3. Verify **Internet** is toggled ON
4. Close settings

### Step 3: Run All Cells
- Click **"Run All"** at the top
- The notebook will:
  - Detect both T4 GPUs (~30GB VRAM total)
  - Install ComfyUI and dependencies
  - Download models (~50GB)
- **Wait 15–30 minutes** for installation to complete

### Step 4: Handle Stalls
If a cell stops mid-execution:
- Check if "Cancel Run" changed back to "Run All" at the top
- If so, click **"Run All"** again — it resumes without re-downloading

### Step 5: Launch the Studio
- Scroll to **Step 6** when you see the green "Studio Ready" box
- Click **"Open H3 Studio"**
- If blocked by antivirus/DNS: copy the URL, open in new tab, ensure `/h3` + key is present

## Usage Tips

| Setting | Recommendation |
|---------|---------------|
| Model Selection | Use `--model flux` (Image) or `--model minimax` (Video). **Only one per run.** |
| Duration 15s | Use **Draft** or **Standard** quality only |
| Best quality | Max **12 seconds** at High quality |
| Prompt language | English works best |
| Audio description | Add `audio: [description]` line to prompt |
| Speed mode | **VDN 8-step** (fastest reliable option) |

## Memory Limits

- **15s @ Draft/Standard**: Works reliably
- **12s @ High**: Tested limit
- **15s @ High**: May crash Kaggle RAM — retry with "Run All" if it happens
- Vertical (9:16) @ High 15s: Likely to OOM — use 12s instead

## Important Warnings

1. **Download videos before stopping** — files exist only in the current session
2. **Stop the cell** (square button) when done — GPU timer runs while notebook is active
3. **Weekly limit**: ~30 hours of GPU time per Kaggle account
4. **License**: Read Minimax H3 community license before commercial use
5. **Don't share the studio URL** — it contains your personal access key
6. **Don't pass off AI video as real footage**

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Can't select GPU T4 x2 | Verify phone number in Kaggle settings |
| Cell stops unexpectedly | Click "Run All" again to resume |
| "Access Denied" on studio link | Copy URL → new tab → ensure `/h3?key=...` is intact |
| Generation seems stuck | Check notebook top bar for "Cancel Run" vs "Run All" |
| Out of memory crash | Reduce duration or quality; retry with "Run All" |

## Project Structure

```
kaggle-comfyui-queue/
├── SETUP_GUIDE.md          ← This file
├── transcripts/
│   ├── zgALArNSMR4.txt     ← Main setup tutorial transcript
│   └── zgALArNSMR4.en.vtt  ← Raw VTT subtitle file
├── install.sh              ← Automated installer
├── start.sh                ← Service launcher
├── scripts/
│   ├── ngrok_launcher.py   ← Tunnel manager with diagnostics
│   ├── queue_api.py        ← Job queue REST API
│   ├── queue_consumer.py   ← GPU job worker
│   └── watchdog.py         ← Process health monitor
├── config/
│   └── auth.env            ← Credentials (ngrok token, password)
└── workflows/              ← ComfyUI workflow JSONs
```