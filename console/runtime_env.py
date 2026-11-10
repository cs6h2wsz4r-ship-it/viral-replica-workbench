"""Everything the workbench needs lives inside its own folder — on macOS and Windows alike.

viral-replica-console/            (wherever the user unzipped it, e.g. D:\\viral-replica-console)
├── runtime/                      created by install.sh / install.bat — delete the folder to uninstall
│   ├── uv/                       the uv tool used to fetch Python
│   ├── python/                   Python 3.12 itself
│   ├── venv/                     packages (faster-whisper, optional whisperx, static-ffmpeg)
│   ├── ffmpeg/                   ffmpeg + ffprobe
│   ├── models/                   Whisper / alignment / torch models
│   ├── temp/                     temporary files during install and processing
│   └── shim/                     tiny compatibility module for Windows (see below)
├── projects/                     every replication project
└── console.env                   all settings and channel keys (Jimmy key included)

`apply()` points every library's download/cache/temp location at runtime/ so nothing is written to the
user's home directory, AppData or ~/.cache.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
IS_WIN = os.name == "nt"
VENV_PY = RUNTIME / "venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
DIRS = {
    "HF_HOME": RUNTIME / "models" / "huggingface",
    "TORCH_HOME": RUNTIME / "models" / "torch",
    "NLTK_DATA": RUNTIME / "models" / "nltk",
    "XDG_CACHE_HOME": RUNTIME / "temp" / "cache",
    "MPLCONFIGDIR": RUNTIME / "temp" / "matplotlib",
    "UV_CACHE_DIR": RUNTIME / "temp" / "uv",
    "TMPDIR": RUNTIME / "temp",
    "TEMP": RUNTIME / "temp",
    "TMP": RUNTIME / "temp",
}
SHIM_DIR = RUNTIME / "shim"
_SHIM = '''"""Windows compatibility for the vendored skills (os.fchmod only exists on Windows from Python 3.13)."""
import os
if not hasattr(os, "fchmod"):
    os.fchmod = lambda fd, mode: None
'''


def apply() -> dict:
    for key, path in DIRS.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", PIP_NO_CACHE_DIR="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    import tempfile
    tempfile.tempdir = str(DIRS["TEMP"])
    # local ffmpeg first
    ff = RUNTIME / "ffmpeg"
    if ff.is_dir():
        os.environ["PATH"] = str(ff) + os.pathsep + os.environ.get("PATH", "")
    # compatibility shim for child Python processes (skills) and for this process
    SHIM_DIR.mkdir(parents=True, exist_ok=True)
    (SHIM_DIR / "sitecustomize.py").write_text(_SHIM, encoding="utf-8")
    os.environ["PYTHONPATH"] = str(SHIM_DIR) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
    if not hasattr(os, "fchmod"):
        os.fchmod = lambda fd, mode: None  # type: ignore[attr-defined]
    return {k: str(v) for k, v in DIRS.items()}


def _size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def report() -> dict:
    import shutil
    from . import align_models
    hub = DIRS["HF_HOME"] / "hub"
    models = sorted(p.name.replace("models--", "").replace("--", "/") for p in hub.glob("models--*")) if hub.exists() else []
    ffmpeg = shutil.which("ffmpeg") or ""
    return {
        "root": str(ROOT), "runtime": str(RUNTIME), "projects": str(ROOT / "projects"),
        "config": str(ROOT / "console.env"), "platform": "windows" if IS_WIN else sys.platform,
        "venv": VENV_PY.exists(),
        "running_in_venv": Path(sys.prefix).resolve() == (RUNTIME / "venv").resolve(),
        "python": sys.version.split()[0], "models": models, "align_models": align_models.report(),
        "runtime_mb": round(_size(RUNTIME) / 1048576) if RUNTIME.exists() else 0,
        "ffmpeg": ffmpeg, "ffmpeg_local": bool(ffmpeg) and Path(ffmpeg).resolve().is_relative_to(RUNTIME.resolve()),
    }
