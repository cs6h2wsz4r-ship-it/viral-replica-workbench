"""Console-only retry policy. Timers never create or submit remote tasks."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

DELAYS = (30, 60, 120, 240, 300)
MAX_AGE = 2 * 60 * 60
WAITING = '三方渠道一直查不到结果，任务可能还在生成'
_timers: dict[tuple, threading.Timer] = {}
_lock = threading.RLock()


def transient(error: object, status: str | None = None) -> bool:
    # The video engine also writes status=failed for a GET exception. Its typed
    # error is the evidence distinguishing that case from a remote failure.
    if not isinstance(error, dict) or not error.get('type'):
        return False
    if str(status or '').lower() in {'error', 'cancelled', 'canceled', 'rejected', 'expired', 'terminated', 'failure'}:
        return False
    return bool(re.search(r'HTTP\s*(?:429|5\d\d)\b|GET transport failed|timed?\s*out|timeout|'
                          r'RemoteDisconnected|Remote end closed connection|Connection[\s_-]*(?:reset|aborted)|'
                          r'IncompleteRead|EOF occurred|'
                          r'invalid\s+(?:UTF-?8|JSON)|query/download failed|download.*fail|failed.*download|'
                          r'下载.*失败|下载.*超时|生图超过.*未完成',
                          str(error['type']) + ' ' + str(error.get('message') or ''), re.I))


def started_at(record: dict, run_dir: Path, *, image: bool = False) -> float:
    for key in ('remote_created_at', 'submitted_at', 'started_at'):
        if isinstance(record.get(key), (int, float)):
            return record[key]
    # Legacy HH:MM:SS lacks a date. The immutable frozen job is written when
    # submission starts, so its mtime survives GET errors and server restarts.
    for name in (('image-job.preview.json', 'image-state.json') if image else ('job.json', 'state.json')):
        if (run_dir / name).is_file():
            return (run_dir / name).stat().st_mtime
    return time.time()


def retry_state(record: dict, start: float) -> dict:
    retry = dict(record.get('query_retry') or {})
    retry.setdefault('started_at', start)
    retry.setdefault('attempts', 0)
    retry['exhausted'] = time.time() - retry['started_at'] >= MAX_AGE
    retry.setdefault('next_at', time.time() + DELAYS[min(retry['attempts'], len(DELAYS)-1)])
    return retry


def cancel(key: tuple) -> None:
    with _lock:
        timer = _timers.pop(key, None)
        if timer:
            timer.cancel()


def schedule(key: tuple, delay: float, callback) -> None:
    with _lock:
        if key in _timers:
            return
        def fire():
            with _lock:
                if _timers.get(key) is not timer:
                    return
                _timers.pop(key, None)
            callback()
        timer = threading.Timer(max(0, delay), fire)
        timer.daemon = True
        _timers[key] = timer
        timer.start()
