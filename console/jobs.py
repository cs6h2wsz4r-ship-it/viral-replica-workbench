"""Tiny background job runner. One running job per (project, key)."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

from . import store
from .channel_text import user_message, technical_detail

_jobs: dict[tuple[str, str], dict] = {}
_lock = threading.Lock()


def start(pid: str, key: str, fn: Callable[[], None], label: str) -> dict:
    with _lock:
        cur = _jobs.get((pid, key))
        if cur and cur["state"] == "running":
            raise RuntimeError(f"「{cur['label']}」还在运行，请稍等")
        job = {"key": key, "label": label, "state": "running", "error": None, "started": time.time()}
        _jobs[(pid, key)] = job

    from .gateway.usage import wrap_worker
    fn = wrap_worker(fn)

    def runner() -> None:
        finished_state = "done"
        try:
            fn()
        except Exception as exc:  # surfaced to the UI
            finished_state = "error"
            job["error"] = user_message(exc)
            job.setdefault('previous_errors', []).append(technical_detail(exc))
            store.log(pid, f"{label}失败：{user_message(exc)}", "error")
            traceback.print_exc()
        finally:
            # Only a live worker completion may advance batch queues/assembly.
            # Importing modules, dashboard reads and reload checks do not call this.
            try:
                from . import fission_dashboard
                fission_dashboard.task_finished(pid, key)
            except Exception as exc:
                store.log(pid, '批量任务后续处理未完成：' + user_message(exc), 'error')
            finally:
                job["state"] = finished_state
                job["ended"] = time.time()

    threading.Thread(target=runner, name=f"{pid}:{key}", daemon=True).start()
    return job


def snapshot(pid: str) -> dict:
    with _lock:
        return {key: dict(job) for (p, key), job in _jobs.items() if p == pid}


def any_running() -> bool:
    with _lock:
        return any(j["state"] == "running" for j in _jobs.values())
