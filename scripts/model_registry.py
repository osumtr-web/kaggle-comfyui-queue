#!/usr/bin/env python3
"""
Model Registry — Universal GPU Model Management for Kaggle ComfyUI Queue Service.

Provides dynamic discovery, installation, and VRAM estimation for ANY HuggingFace model
(image generation, video generation, LLMs, ML classifiers, audio, embeddings, etc.).

Replaces static curated model lists with a universal model management system.
"""

import argparse
import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Structured JSON Logger
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Emit structured JSON log lines to stdout."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
            "logger": record.name,
        }
        # Merge extra fields attached via `extra=` kwarg
        for key in ("model_id", "hf_repo", "model_type", "vram_mb",
                     "file_size_bytes", "error", "count", "filename"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        return json.dumps(payload, default=str)


def _get_logger() -> logging.Logger:
    logger = logging.getLogger("model_registry")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


log = _get_logger()

# ---------------------------------------------------------------------------
# Constants & Enums
# ---------------------------------------------------------------------------

VALID_MODEL_TYPES = frozenset({
    "diffusion_image",
    "diffusion_video",
    "llm",
    "ml_classifier",
    "audio",
    "embedding",
    "other",
})

MODEL_TYPE_DIRS: dict[str, str] = {
    "diffusion_image": "checkpoints",
    "diffusion_video": "checkpoints",
    "llm": "llm",
    "ml_classifier": "ml",
    "audio": "audio",
    "embedding": "embeddings",
    "other": "other",
}

KNOWN_EXTENSIONS = frozenset({
    ".safetensors", ".gguf", ".bin", ".pt", ".pth", ".onnx", ".ckpt",
})

BASE_MODELS_DIR = Path(os.environ.get(
    "KAGGLE_MODELS_DIR", "/kaggle/working/models"
))
REGISTRY_PATH = BASE_MODELS_DIR / "registry.json"


# ---------------------------------------------------------------------------
# VRAM Estimation Heuristics
# ---------------------------------------------------------------------------

class VramEstimator:
    """Static helper for estimating VRAM requirements from file metadata."""

    @staticmethod
    def estimate(file_path: Optional[str] = None,
                 file_size_bytes: Optional[int] = None,
                 format_hint: Optional[str] = None) -> int:
        """
        Return estimated VRAM in MB.

        Provide either *file_path* (reads size + extension) or explicit
        *file_size_bytes* with an optional *format_hint* (extension string
        like ``".gguf"`` or ``"safetensors"``).
        """
        if file_path is not None:
            p = Path(file_path)
            if not p.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            file_size_bytes = p.stat().st_size
            ext = p.suffix.lower()
        elif file_size_bytes is not None:
            ext = (format_hint or "").lower()
            if not ext.startswith("."):
                ext = f".{ext}"
        else:
            raise ValueError("Provide either file_path or file_size_bytes")

        multiplier = 1.2  # default fp16 safetensors / bin
        if ext == ".gguf":
            # Cannot distinguish Q4 vs Q8 without reading header; assume Q4
            multiplier = 1.5
        elif ext == ".onnx":
            multiplier = 1.1
        # .safetensors, .bin, .pt, .pth, .ckpt → 1.2

        vram_bytes = int(file_size_bytes * multiplier)
        vram_mb = max(1, vram_bytes // (1024 * 1024))

        log.info(
            "vram_estimated",
            extra={
                "event": "vram_estimated",
                "file_size_bytes": file_size_bytes,
                "vram_mb": vram_mb,
                "filename": str(file_path) if file_path else None,
            },
        )
        return vram_mb


# ---------------------------------------------------------------------------
# Thread-safe File Lock Context Manager
# ---------------------------------------------------------------------------

class _FileLock:
    """POSIX advisory file lock for thread/process safety on registry.json."""

    def __init__(self, path: Path):
        self.path = path
        self._fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.path, "a+")  # noqa: SIM115
        fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
        return self._fd

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            self._fd.close()
        return False


# ---------------------------------------------------------------------------
# ModelRegistry
# ---------------------------------------------------------------------------

class ModelRegistry:
    """
    Manages installed models in a JSON registry with thread-safe access.

    Parameters
    ----------
    base_dir : Path | str | None
        Override for the models base directory (default: ``/kaggle/working/models``).
    auto_scan : bool
        Whether to run ``scan_existing()`` on init (default: True).
    """

    def __init__(self, base_dir: Optional[str] = None, auto_scan: bool = True):
        self.base_dir = Path(base_dir) if base_dir else BASE_MODELS_DIR
        self.registry_path = self.base_dir / "registry.json"
        self._ensure_dirs()
        if auto_scan:
            self.scan_existing()

    # -- internal helpers ---------------------------------------------------

    def _ensure_dirs(self) -> None:
        """Create all model subdirectories and the registry file if missing."""
        for subdir in MODEL_TYPE_DIRS.values():
            (self.base_dir / subdir).mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._write_registry([])

    def _read_registry(self) -> list[dict]:
        with _FileLock(self.registry_path) as fd:
            fd.seek(0)
            raw = fd.read().strip()
            if not raw:
                return []
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Corrupt registry.json — resetting to empty list")
                return []

    def _write_registry(self, entries: list[dict]) -> None:
        with _FileLock(self.registry_path) as fd:
            fd.seek(0)
            fd.truncate()
            fd.write(json.dumps(entries, indent=2, default=str))
            fd.flush()

    @staticmethod
    def _make_model_id(hf_repo: str, filename: Optional[str]) -> str:
        """Derive a unique model_id from repo + filename."""
        safe_repo = hf_repo.replace("/", "__")
        if filename:
            stem = Path(filename).stem
            return f"{safe_repo}__{stem}"
        return safe_repo

    @staticmethod
    def _detect_extension(filename: str) -> str:
        return Path(filename).suffix.lower()

    # -- public API ---------------------------------------------------------

    def install_model(
        self,
        hf_repo: str,
        model_type: str,
        filename: Optional[str] = None,
        tags: Optional[list[str]] = None,
        vram_mb: Optional[int] = None,
    ) -> dict:
        """
        Download and register a model from HuggingFace Hub.

        Returns the installed model metadata dict.
        """
        if model_type not in VALID_MODEL_TYPES:
            raise ValueError(
                f"Invalid model_type '{model_type}'. Must be one of: {sorted(VALID_MODEL_TYPES)}"
            )

        log.info(
            "model_install_start",
            extra={"event": "model_install_start", "hf_repo": hf_repo,
                   "model_type": model_type, "model_filename": filename},
        )

        try:
            from huggingface_hub import hf_hub_download, snapshot_download
            from huggingface_hub.utils import EntryNotFoundError, GatedRepoError, RepositoryNotFoundError
        except ImportError as exc:
            raise ImportError(
                "huggingface_hub>=0.26.0 is required. Install via: pip install 'huggingface_hub>=0.26.0'"
            ) from exc

        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        dest_subdir = self.base_dir / MODEL_TYPE_DIRS.get(model_type, "other")
        dest_subdir.mkdir(parents=True, exist_ok=True)

        downloaded_path: Optional[Path] = None
        file_size_bytes = 0

        try:
            if filename:
                local_path = hf_hub_download(
                    repo_id=hf_repo,
                    filename=filename,
                    local_dir=str(dest_subdir),
                    token=token,
                    resume_download=True,
                )
                downloaded_path = Path(local_path)
            else:
                # Download entire repo snapshot
                snapshot_dir = snapshot_download(
                    repo_id=hf_repo,
                    local_dir=str(dest_subdir),
                    token=token,
                    resume_download=True,
                )
                downloaded_path = Path(snapshot_dir)
                # Aggregate size of all files in snapshot
                total = sum(f.stat().st_size for f in downloaded_path.rglob("*") if f.is_file())
                file_size_bytes = total
        except GatedRepoError as exc:
            log.error(
                "model_install_failed",
                extra={"event": "model_install_failed", "hf_repo": hf_repo,
                       "error": "Gated repository — set HF_TOKEN env var"},
            )
            raise PermissionError(
                f"Repository {hf_repo} is gated. Set HF_TOKEN environment variable."
            ) from exc
        except RepositoryNotFoundError as exc:
            log.error(
                "model_install_failed",
                extra={"event": "model_install_failed", "hf_repo": hf_repo,
                       "error": "Repository not found (404)"},
            )
            raise ValueError(f"Repository not found: {hf_repo}") from exc
        except EntryNotFoundError as exc:
            log.error(
                "model_install_failed",
                extra={"event": "model_install_failed", "hf_repo": hf_repo,
                       "filename": filename, "error": "File not found in repo"},
            )
            raise ValueError(
                f"File '{filename}' not found in repository {hf_repo}"
            ) from exc
        except Exception as exc:
            err_msg = str(exc)
            if "429" in err_msg or "rate" in err_msg.lower():
                log.error(
                    "model_install_failed",
                    extra={"event": "model_install_failed", "hf_repo": hf_repo,
                           "error": "Rate limited (429). Retry later."},
                )
                raise ConnectionError(
                    f"HuggingFace rate limit hit for {hf_repo}. Retry later."
                ) from exc
            log.error(
                "model_install_failed",
                extra={"event": "model_install_failed", "hf_repo": hf_repo,
                       "error": err_msg},
            )
            raise ConnectionError(
                f"Failed to download {hf_repo}: {err_msg}"
            ) from exc

        # Compute file size if single-file download
        if downloaded_path and downloaded_path.is_file():
            file_size_bytes = downloaded_path.stat().st_size

        # VRAM estimation
        if vram_mb is None:
            if downloaded_path and downloaded_path.is_file():
                vram_mb = VramEstimator.estimate(file_path=str(downloaded_path))
            else:
                vram_mb = VramEstimator.estimate(file_size_bytes=file_size_bytes)

        model_id = self._make_model_id(hf_repo, filename)
        entry = {
            "model_id": model_id,
            "hf_repo": hf_repo,
            "filename": filename or str(downloaded_path.name) if downloaded_path else "",
            "model_type": model_type,
            "vram_mb": vram_mb,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "file_size_bytes": file_size_bytes,
            "tags": tags or [],
        }

        # Upsert into registry
        entries = self._read_registry()
        entries = [e for e in entries if e.get("model_id") != model_id]
        entries.append(entry)
        self._write_registry(entries)

        log.info(
            "model_install_complete",
            extra={"event": "model_install_complete", "model_id": model_id,
                   "hf_repo": hf_repo, "model_type": model_type,
                   "vram_mb": vram_mb, "file_size_bytes": file_size_bytes},
        )
        return entry

    def list_models(
        self,
        model_type: Optional[str] = None,
        tag: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list[dict]:
        """Return installed models sorted by installed_at descending."""
        entries = self._read_registry()

        if model_type:
            entries = [e for e in entries if e.get("model_type") == model_type]
        if tag:
            entries = [e for e in entries if tag in (e.get("tags") or [])]
        if search:
            needle = search.lower()
            entries = [
                e for e in entries
                if needle in (e.get("model_id") or "").lower()
                or needle in (e.get("hf_repo") or "").lower()
            ]

        entries.sort(key=lambda e: e.get("installed_at", ""), reverse=True)
        return entries

    def get_model(self, model_id: Optional[str] = None,
                  hf_repo: Optional[str] = None) -> Optional[dict]:
        """Lookup a single model by model_id or hf_repo."""
        entries = self._read_registry()
        for e in entries:
            if model_id and e.get("model_id") == model_id:
                return e
            if hf_repo and e.get("hf_repo") == hf_repo:
                return e
        return None

    def uninstall_model(self, model_id: Optional[str] = None,
                        hf_repo: Optional[str] = None) -> bool:
        """Delete model file and remove from registry. Returns True if found."""
        entry = self.get_model(model_id=model_id, hf_repo=hf_repo)
        if entry is None:
            return False

        # Attempt to delete file
        model_type = entry.get("model_type", "other")
        subdir = self.base_dir / MODEL_TYPE_DIRS.get(model_type, "other")
        fname = entry.get("filename", "")
        target = subdir / fname if fname else None

        deleted = False
        if target and target.exists():
            if target.is_file():
                target.unlink()
            elif target.is_dir():
                import shutil
                shutil.rmtree(target, ignore_errors=True)
            deleted = True

        # Remove from registry
        entries = self._read_registry()
        mid = entry.get("model_id")
        entries = [e for e in entries if e.get("model_id") != mid]
        self._write_registry(entries)

        log.info(
            "model_uninstalled",
            extra={"event": "model_uninstalled", "model_id": mid,
                   "hf_repo": entry.get("hf_repo"), "deleted_file": deleted},
        )
        return True

    @staticmethod
    def estimate_vram(file_path: Optional[str] = None,
                      file_size_bytes: Optional[int] = None,
                      format_hint: Optional[str] = None) -> int:
        """Public static VRAM estimation entry point."""
        return VramEstimator.estimate(
            file_path=file_path,
            file_size_bytes=file_size_bytes,
            format_hint=format_hint,
        )

    def scan_existing(self) -> int:
        """
        Scan model directories for files not yet registered.

        Adds discovered files with model_type='other' and estimated VRAM.
        Returns count of newly discovered models.
        """
        existing = self._read_registry()
        known_files: set[str] = set()
        for e in existing:
            mt = e.get("model_type", "other")
            subdir = MODEL_TYPE_DIRS.get(mt, "other")
            fname = e.get("filename", "")
            if fname:
                known_files.add(f"{subdir}/{fname}")

        discovered = 0
        for subdir_name in MODEL_TYPE_DIRS.values():
            dirpath = self.base_dir / subdir_name
            if not dirpath.is_dir():
                continue
            for fpath in dirpath.iterdir():
                if not fpath.is_file():
                    continue
                if fpath.suffix.lower() not in KNOWN_EXTENSIONS:
                    continue
                rel_key = f"{subdir_name}/{fpath.name}"
                if rel_key in known_files:
                    continue

                vram = VramEstimator.estimate(file_path=str(fpath))
                entry = {
                    "model_id": f"discovered__{fpath.stem}",
                    "hf_repo": "",
                    "filename": fpath.name,
                    "model_type": "other",
                    "vram_mb": vram,
                    "installed_at": datetime.fromtimestamp(
                        fpath.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "file_size_bytes": fpath.stat().st_size,
                    "tags": ["auto-discovered"],
                }
                existing.append(entry)
                known_files.add(rel_key)
                discovered += 1

        if discovered > 0:
            self._write_registry(existing)
            log.info(
                "scan_discovered",
                extra={"event": "scan_discovered", "count": discovered},
            )
        return discovered


# ---------------------------------------------------------------------------
# CLI Interface
# ---------------------------------------------------------------------------

def _cli_install(args: argparse.Namespace) -> None:
    reg = ModelRegistry(auto_scan=False)
    result = reg.install_model(
        hf_repo=args.repo,
        model_type=args.type,
        filename=getattr(args, "filename", None),
        tags=args.tag.split(",") if args.tag else None,
        vram_mb=getattr(args, "vram_mb", None),
    )
    print(json.dumps(result, indent=2, default=str))


def _cli_list(args: argparse.Namespace) -> None:
    reg = ModelRegistry(auto_scan=False)
    results = reg.list_models(
        model_type=getattr(args, "type", None),
        tag=getattr(args, "tag", None),
        search=getattr(args, "search", None),
    )
    print(json.dumps(results, indent=2, default=str))


def _cli_info(args: argparse.Namespace) -> None:
    reg = ModelRegistry(auto_scan=False)
    entry = reg.get_model(
        model_id=getattr(args, "model_id", None),
        hf_repo=getattr(args, "repo", None),
    )
    if entry:
        print(json.dumps(entry, indent=2, default=str))
    else:
        print("Model not found.", file=sys.stderr)
        sys.exit(1)


def _cli_uninstall(args: argparse.Namespace) -> None:
    reg = ModelRegistry(auto_scan=False)
    ok = reg.uninstall_model(
        model_id=getattr(args, "model_id", None),
        hf_repo=getattr(args, "repo", None),
    )
    if ok:
        print("Uninstalled successfully.")
    else:
        print("Model not found.", file=sys.stderr)
        sys.exit(1)


def _cli_estimate(args: argparse.Namespace) -> None:
    if args.file:
        mb = ModelRegistry.estimate_vram(file_path=args.file)
    elif args.size:
        mb = ModelRegistry.estimate_vram(
            file_size_bytes=int(args.size),
            format_hint=getattr(args, "format", None),
        )
    else:
        print("Provide --file or --size.", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"vram_mb": mb}))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Model Registry CLI — manage GPU models on Kaggle"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # install
    p_install = sub.add_parser("install", help="Install a model from HuggingFace")
    p_install.add_argument("--repo", required=True, help="HF repo id (e.g. black-forest-labs/FLUX.1-dev)")
    p_install.add_argument("--type", required=True, choices=sorted(VALID_MODEL_TYPES), help="Model type")
    p_install.add_argument("--filename", default=None, help="Specific file within repo")
    p_install.add_argument("--tag", default=None, help="Comma-separated tags")
    p_install.add_argument("--vram-mb", type=int, default=None, dest="vram_mb", help="Override VRAM estimate")
    p_install.set_defaults(func=_cli_install)

    # list
    p_list = sub.add_parser("list", help="List installed models")
    p_list.add_argument("--type", default=None, choices=sorted(VALID_MODEL_TYPES))
    p_list.add_argument("--tag", default=None)
    p_list.add_argument("--search", default=None)
    p_list.set_defaults(func=_cli_list)

    # info
    p_info = sub.add_parser("info", help="Get model info")
    p_info.add_argument("--repo", default=None)
    p_info.add_argument("--model-id", default=None, dest="model_id")
    p_info.set_defaults(func=_cli_info)

    # uninstall
    p_un = sub.add_parser("uninstall", help="Uninstall a model")
    p_un.add_argument("--repo", default=None)
    p_un.add_argument("--model-id", default=None, dest="model_id")
    p_un.set_defaults(func=_cli_uninstall)

    # estimate
    p_est = sub.add_parser("estimate", help="Estimate VRAM for a model file")
    p_est.add_argument("--file", default=None, help="Path to model file")
    p_est.add_argument("--size", default=None, help="File size in bytes")
    p_est.add_argument("--format", default=None, help="Format hint (e.g. .gguf)")
    p_est.set_defaults(func=_cli_estimate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()