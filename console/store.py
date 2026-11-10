"""Project storage: one folder per project with an atomically written project.json."""
from __future__ import annotations

import hashlib
import copy
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import config

_locks: dict[str, threading.RLock] = {}
_global = threading.Lock()
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ProjectDeletedError(FileNotFoundError):
    def __init__(self):
        super().__init__("项目已删除")


def _lock(pid: str) -> threading.RLock:
    with _global:
        return _locks.setdefault(pid, threading.RLock())


def project_dir(pid: str) -> Path:
    if not SAFE_ID.fullmatch(pid):
        raise ValueError("非法项目 ID")
    return config.PROJECTS_DIR / pid


def new_id(prefix: str = "") -> str:
    return prefix + time.strftime("%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_sha_cache: dict[Path, tuple[tuple[int, int, int, int], str]] = {}
_sha_cache_lock = threading.Lock()


def sha256_cached(path: Path) -> str:
    """Hash for read-only views and fingerprints, reused while the file's stat is unchanged.

    Every write in this app replaces files (new inode or mtime), so a changed file
    always misses the cache. Payment submission still re-reads with sha256_file.
    """
    path = Path(path).resolve()
    stat = path.stat()
    stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    with _sha_cache_lock:
        cached = _sha_cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    value = sha256_file(path)
    with _sha_cache_lock:
        if len(_sha_cache) > 20000:
            _sha_cache.clear()
        _sha_cache[path] = (stamp, value)
    return value


def create(name: str) -> dict:
    pid = new_id("p")
    data = {
        "id": pid,
        "name": name.strip() or "未命名项目",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": None,
        "assets": [],
        "analysis": {"status": "idle", "steps": {}},
        "breakdown": None,
        "script": None,
        "runs": {},
        "images": {},
        "final": None,
        "log": [],
    }
    d = project_dir(pid)
    for sub in ("source", "assets", "analysis/frames", "packages", "jimmy-runs", "image-runs", "clips", "final"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / "project.json", data)
    return data


def load(pid: str) -> dict:
    path = project_dir(pid) / "project.json"
    with _lock(pid):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProjectDeletedError() from None


def update(pid: str, fn: Callable[[dict], Any]) -> dict:
    """Read-modify-write under the project lock."""
    with _lock(pid):
        data = load(pid)
        fn(data)
        atomic_write_json(project_dir(pid) / "project.json", data)
        return data


def log(pid: str, message: str, level: str = "info") -> None:
    from .channel_text import user_message
    message = user_message(message)
    def add(data: dict) -> None:
        data.setdefault("log", []).append({"t": time.strftime("%H:%M:%S"), "level": level, "msg": message})
        data["log"] = data["log"][-300:]
    try:
        update(pid, add)
    except FileNotFoundError:
        pass


def list_projects() -> list[dict]:
    out = []
    if not config.PROJECTS_DIR.exists():
        return out
    for path in sorted(config.PROJECTS_DIR.glob("*/project.json"), reverse=True):
        if not SAFE_ID.fullmatch(path.parent.name):
            continue  # Hidden directories (including .trash) are never projects.
        try:
            with _lock(path.parent.name):
                data = load(path.parent.name)
                out.append({"id": data["id"], "name": data["name"], "created_at": data.get("created_at"),
                            "has_source": bool(data.get("source")), "analysis": data.get("analysis", {}).get("status")})
                if data.get('derived_from'):
                    derived=data['derived_from'];master_id=derived.get('project_id')
                    out[-1].update(derived_from={key:copy.deepcopy(derived.get(key)) for key in ('project_id','row','row_id','fission_number')},
                                   master_name=(derived.get('master_snapshot') or {}).get('name',''),
                                   master_exists=bool(isinstance(master_id,str) and SAFE_ID.fullmatch(master_id) and (config.PROJECTS_DIR/master_id/'project.json').is_file()))
        except (OSError, ValueError, KeyError):
            continue
    return out


def rel(pid: str, path: Path) -> str:
    """Path relative to the project folder, used for /files URLs."""
    return Path(path).resolve().relative_to(project_dir(pid).resolve()).as_posix()


def append_usage(project: str | dict, record: dict) -> None:
    """Append measured usage atomically; repeated recovery cannot bill twice."""
    def append(data):
        row = {**record, 'billing': dict(record['billing'])}
        resource = row.get('resource')
        if resource and row['billing'].get('amount') is not None:
            paid = sum((previous.get('billing') or {}).get('amount') or 0
                       for previous in data.get('usage', [])
                       if previous.get('resource') == resource and previous.get('kind') == row['kind']
                       and previous.get('channel') == row['channel']
                       and (previous.get('billing') or {}).get('unit') == row['billing']['unit'])
            row['billing']['amount'] = max(0, row['billing']['amount'] - paid)
        data.setdefault('usage', []).append(row)
    if isinstance(project, str):
        update(project, append)
    elif project.get('id') and (project_dir(project['id']) / 'project.json').is_file():
        saved = update(project['id'], append)
        project['usage'] = saved['usage']
    else:
        append(project)
