# 🚀 Kaggle One-Click Starter

Your repo is pushed. Here's the 30-second deployment.

## Step 1 — Add 2 Secrets (one-time)

In your Kaggle notebook → **Add-ons → Secrets**:

| Name | Value |
|------|-------|
| `NGROK_AUTHTOKEN` | Get yours at https://dashboard.ngrok.com/get-started/your-authtoken |
| `COMFYUI_AUTH_PASSWORD` | Pick any password, e.g. `MyKagglePass2026!` |

## Step 2 — Create a New Notebook & Paste This Cell

```python
# === KAGGLE ONE-CLICK STARTER ===
# Paste this entire block into Cell 1 of a fresh Kaggle notebook, then hit Run All.

import os, subprocess, sys

# --- Your GitHub repo (change this!) ---
REPO_URL = "https://github.com/YOUR_USERNAME/kaggle-comfyui-queue.git"

# --- Clone or update repo ---
if not os.path.exists("kaggle-comfyui-queue"):
    subprocess.run(["git", "clone", REPO_URL], check=True)
else:
    subprocess.run(["git", "-C", "kaggle-comfyui-queue", "pull"], check=True)

# --- Verify secrets ---
missing = []
if not os.environ.get("NGROK_AUTHTOKEN"): missing.append("NGROK_AUTHTOKEN")
if not os.environ.get("COMFYUI_AUTH_PASSWORD"): missing.append("COMFYUI_AUTH_PASSWORD")
if missing:
    print(f"❌ Missing secrets: {', '.join(missing)}")
    print("   Go to Add-ons → Secrets and add them, then re-run this cell.")
    sys.exit(1)

# --- Launch the real notebook ---
print("✅ Secrets OK. Launching setup.ipynb...")
from IPython.display import display, HTML
display(HTML(f"""
<h2>✅ Ready! Open the full setup notebook:</h2>
<p><a href="/kaggle/input/kaggle-comfyui-queue/setup.ipynb" target="_blank">
   Click here to open setup.ipynb → then Run All
</a></p>
<p>Or paste this into the next cell to run it inline:</p>
<pre>%run /kaggle/input/kaggle-comfyui-queue/setup.ipynb</pre>
"""))
```

## Step 3 — Run All

Cell 1 clones your repo and checks secrets. Once it prints the link, open `setup.ipynb` and click **Run All**.

When Cell 5 prints your ngrok URL, you're live. Use it from your laptop:

```bash
curl -u kaggle:MyKagglePass2026! https://YOUR-NGROK-URL.ngrok-free.app/api/health
```

That's it. 🎉