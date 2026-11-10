#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config import DEFAULT_ENV_FILE, load_settings  # noqa: E402
from jimmy_file_upload import JimmyFileUploader  # noqa: E402
from jimmy_submit import (  # noqa: E402
    COMPLETED,
    FAILED,
    JIMMY_MODEL,
    JIMMY_RESOLUTION,
    RUNNING,
    JimmyClient,
    prompt_limit_for_model,
)
from runtime_dns_patch import install_runtime_dns_patch, write_runtime_dns_patch  # noqa: E402


JOB_SCHEMA_VERSION = "jimmy-video-job.v1"
STATE_SCHEMA_VERSION = "jimmy-video-state.v1"
UPLOAD_SCHEMA_VERSION = "jimmy-upload-map.v1"
RESULTS_SCHEMA_VERSION = "jimmy-video-results.v1"
MAX_FILE_BYTES = 100 * 1024 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_RATIOS = {"16:9", "9:16", "1:1"}
AUDIO_REFERENCE_MODELS = {"seedance2.0-mini", "seedance2.0-mini-video"}
TASK_FIELDS = {
    "id",
    "prompt",
    "duration",
    "ratio",
    "images",
    "reference_videos",
    "reference_audios",
    "model",
    "resolution",
}


class JobValidationError(ValueError):
    """The local immutable Jimmy job manifest is invalid."""


def _fail(path: str, message: str) -> None:
    raise JobValidationError(f"{path}: {message}")


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-empty string")
    return value


def _validate_identifier(value: Any, path: str) -> str:
    text = _required_text(value, path)
    if not ID_RE.fullmatch(text):
        _fail(path, "must use 1..128 ASCII letters, digits, dot, underscore or hyphen")
    return text


def _validate_https_url(value: Any, path: str) -> str:
    url = _required_text(value, path)
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        _fail(path, "must be an absolute HTTPS URL")
    return url


def _validate_media(
    value: Any,
    path: str,
    base_dir: Path,
    *,
    require_files: bool,
) -> dict[str, str]:
    if not isinstance(value, dict):
        _fail(path, "must be a media object")
    source = value.get("source")
    if source == "file":
        if set(value) not in ({"source", "path"}, {"source", "path", "sha256"}):
            _fail(path, "file media must contain source/path and optional sha256")
        raw_path = _required_text(value.get("path"), f"{path}.path")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        candidate = candidate.resolve()
        if require_files:
            if not candidate.is_file():
                _fail(f"{path}.path", "does not exist or is not a regular file")
            try:
                size = candidate.stat().st_size
            except OSError as exc:
                _fail(f"{path}.path", f"cannot be inspected: {exc}")
            if size > MAX_FILE_BYTES:
                _fail(f"{path}.path", "must not exceed 100 MiB")
            if not os.access(candidate, os.R_OK):
                _fail(f"{path}.path", "is not readable")
        normalized = {"source": "file", "path": str(candidate)}
        if "sha256" in value:
            expected_sha = _required_text(value.get("sha256"), f"{path}.sha256")
            if not SHA256_RE.fullmatch(expected_sha):
                _fail(f"{path}.sha256", "must be a lowercase SHA-256 digest")
            normalized["sha256"] = expected_sha
        return normalized
    if source == "url":
        if set(value) != {"source", "url"}:
            _fail(path, "URL media must contain exactly source and url")
        return {
            "source": "url",
            "url": _validate_https_url(value.get("url"), f"{path}.url"),
        }
    _fail(f"{path}.source", "must equal file or url")


def validate_job(
    data: Any,
    base_dir: Path,
    *,
    require_files: bool = True,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        _fail("$", "must be a JSON object")
    if set(data) != {"schema_version", "job_id", "tasks"}:
        extras = sorted(set(data) - {"schema_version", "job_id", "tasks"})
        missing = sorted({"schema_version", "job_id", "tasks"} - set(data))
        details = []
        if extras:
            details.append(f"unexpected fields: {', '.join(extras)}")
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        _fail("$", "; ".join(details))
    if data.get("schema_version") != JOB_SCHEMA_VERSION:
        _fail("schema_version", f"must equal {JOB_SCHEMA_VERSION}")
    job_id = _validate_identifier(data.get("job_id"), "job_id")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        _fail("tasks", "must be a non-empty array")

    normalized_tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_task in enumerate(tasks):
        task_path = f"tasks[{index}]"
        if not isinstance(raw_task, dict):
            _fail(task_path, "must be an object")
        extras = sorted(set(raw_task) - TASK_FIELDS)
        if extras:
            _fail(task_path, f"contains unexpected fields: {', '.join(extras)}")
        for field in raw_task:
            if raw_task[field] is None:
                _fail(f"{task_path}.{field}", "must not be null; omit the field instead")
        task_id = _validate_identifier(raw_task.get("id"), f"{task_path}.id")
        if task_id in seen_ids:
            _fail(f"{task_path}.id", "is a duplicate task ID")
        seen_ids.add(task_id)

        model = (
            _required_text(raw_task.get("model"), f"{task_path}.model").strip()
            if "model" in raw_task
            else JIMMY_MODEL
        )
        prompt = _required_text(raw_task.get("prompt"), f"{task_path}.prompt").strip()
        prompt_limit = prompt_limit_for_model(model)
        if len(prompt) > prompt_limit:
            _fail(f"{task_path}.prompt", f"must contain 1-{prompt_limit} characters")
        duration = raw_task.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 15:
            _fail(f"{task_path}.duration", "must be an integer from 1 through 15")
        ratio = raw_task.get("ratio")
        if ratio not in ALLOWED_RATIOS:
            _fail(f"{task_path}.ratio", "must be one of 16:9, 9:16, 1:1")
        resolution = raw_task.get("resolution", JIMMY_RESOLUTION)
        if resolution != JIMMY_RESOLUTION:
            _fail(f"{task_path}.resolution", f"must equal {JIMMY_RESOLUTION}")

        images = raw_task.get("images", [])
        if not isinstance(images, list):
            _fail(f"{task_path}.images", "must be an array")
        if len(images) > 4:
            _fail(f"{task_path}.images", "must contain at most 4 images")
        reference_videos = raw_task.get("reference_videos", [])
        if not isinstance(reference_videos, list):
            _fail(f"{task_path}.reference_videos", "must be an array")
        reference_audios = raw_task.get("reference_audios", [])
        if not isinstance(reference_audios, list):
            _fail(f"{task_path}.reference_audios", "must be an array")
        if len(reference_audios) > 1:
            _fail(f"{task_path}.reference_audios", "must contain at most 1 audio reference")
        if reference_audios:
            if model not in AUDIO_REFERENCE_MODELS:
                _fail(
                    f"{task_path}.reference_audios",
                    "model must equal seedance2.0-mini or seedance2.0-mini-video",
                )
            if not images and not reference_videos:
                _fail(
                    f"{task_path}.reference_audios",
                    "requires images or reference_videos; audio-only generation is unsupported",
                )
        normalized_tasks.append(
            {
                "id": task_id,
                "prompt": prompt,
                "duration": duration,
                "ratio": ratio,
                "model": model,
                "resolution": JIMMY_RESOLUTION,
                "images": [
                    _validate_media(
                        image,
                        f"{task_path}.images[{image_index}]",
                        base_dir,
                        require_files=require_files,
                    )
                    for image_index, image in enumerate(images)
                ],
                "reference_videos": [
                    _validate_media(
                        video,
                        f"{task_path}.reference_videos[{video_index}]",
                        base_dir,
                        require_files=require_files,
                    )
                    for video_index, video in enumerate(reference_videos)
                ],
            }
        )
        # Omit absent/empty audio so legacy frozen normalized jobs stay identical.
        if reference_audios:
            normalized_tasks[-1]["reference_audios"] = [
                _validate_media(
                    audio,
                    f"{task_path}.reference_audios[{audio_index}]",
                    base_dir,
                    require_files=require_files,
                )
                for audio_index, audio in enumerate(reference_audios)
            ]

    return {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": job_id,
        "tasks": normalized_tasks,
    }


def load_and_validate_job(job_path: Path, *, require_files: bool = True) -> dict[str, Any]:
    try:
        data = json.loads(Path(job_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(str(job_path), "does not exist")
    except json.JSONDecodeError as exc:
        _fail(str(job_path), f"is not valid JSON: {exc}")
    return validate_job(
        data,
        Path(job_path).resolve().parent,
        require_files=require_files,
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


class AtomicJsonStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise JobValidationError(f"{self.path}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise JobValidationError(f"{self.path}: must contain a JSON object")
        return value

    def save(self, value: dict[str, Any]) -> None:
        _atomic_write_json(self.path, value)


class MediaResolver:
    def __init__(self, uploader: Any, store: AtomicJsonStore) -> None:
        self.uploader = uploader
        self.store = store
        self._memory_urls: dict[str, str] = {}

    def _load_cache(self) -> dict[str, Any]:
        cache = self.store.load()
        if not cache:
            return {"schema_version": UPLOAD_SCHEMA_VERSION, "files": {}}
        if cache.get("schema_version") != UPLOAD_SCHEMA_VERSION:
            raise JobValidationError("uploads.json: unsupported schema_version")
        files = cache.get("files")
        if not isinstance(files, dict):
            raise JobValidationError("uploads.json.files: must be an object")
        return cache

    def resolve_media(self, media: dict[str, str]) -> str:
        if media.get("source") == "url":
            return _validate_https_url(media.get("url"), "media.url")
        if media.get("source") != "file":
            raise JobValidationError("media.source: must equal file or url")
        path = Path(_required_text(media.get("path"), "media.path"))
        if not path.is_file():
            raise JobValidationError(f"media.path: does not exist: {path}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise JobValidationError("media.path: must not exceed 100 MiB")
        digest = sha256_file(path)
        expected_digest = media.get("sha256")
        if expected_digest is not None and expected_digest != digest:
            raise JobValidationError("media.path: bytes do not match the approved sha256")
        if digest in self._memory_urls:
            return self._memory_urls[digest]

        cache = self._load_cache()
        files = cache["files"]
        cached = files.get(digest)
        if isinstance(cached, dict):
            url = _validate_https_url(cached.get("url"), f"uploads.json.files.{digest}.url")
            self._memory_urls[digest] = url
            return url

        uploaded = self.uploader.upload(path)
        if not isinstance(uploaded, dict):
            raise JobValidationError("Jimmy upload response must be an object")
        url = _validate_https_url(uploaded.get("url"), "upload.url")
        record = {
            "sha256": digest,
            "url": url,
            "filename": str(uploaded.get("filename") or path.name),
            "size": int(uploaded.get("size") or size),
            "mime_type": str(uploaded.get("mime_type") or "application/octet-stream"),
        }
        files[digest] = record
        self.store.save(cache)
        self._memory_urls[digest] = url
        return url


def build_jimmy_request(task: dict[str, Any], resolver: MediaResolver) -> dict[str, Any]:
    image_urls = [resolver.resolve_media(image) for image in task.get("images", [])]
    reference_video_urls = [
        resolver.resolve_media(video) for video in task.get("reference_videos", [])
    ]
    reference_audio_urls = [
        resolver.resolve_media(audio) for audio in task.get("reference_audios", [])
    ]
    request_body: dict[str, Any] = {
        "model": task.get("model") or JIMMY_MODEL,
        "prompt": task["prompt"],
        "duration": int(task["duration"]),
        "resolution": JIMMY_RESOLUTION,
        "ratio": task["ratio"],
    }
    if image_urls:
        request_body["images"] = image_urls
    if reference_video_urls:
        request_body["reference_videos"] = reference_video_urls
    if reference_audio_urls:
        request_body["reference_audios"] = reference_audio_urls
    return request_body


def _initial_task_state() -> dict[str, Any]:
    return {
        "phase": "draft",
        "request": None,
        "request_sha256": None,
        "jimmy_task_id": None,
        "status": None,
        "video_url": None,
        "local_path": None,
        "error": None,
    }


def _error_summary(error: Exception) -> dict[str, Any]:
    return {
        "message": str(error),
        "type": type(error).__name__,
    }


def _overall_status(tasks: dict[str, dict[str, Any]]) -> str:
    phases = [task.get("phase") for task in tasks.values()]
    if phases and all(phase == "completed" for phase in phases):
        return "completed"
    if phases and all(phase in {"completed", "failed"} for phase in phases):
        if all(phase == "failed" for phase in phases):
            return "failed"
        return "partial_failed"
    return "running"


def _extract_data(response: dict[str, Any]) -> dict[str, Any]:
    code = response.get("code")
    if code not in (None, 0, "0", 200, "200", 20000, "20000"):
        message = response.get("msg") or response.get("message") or "Jimmy request failed"
        raise RuntimeError(str(message))
    data = response.get("data")
    if data is None:
        return response
    if not isinstance(data, dict):
        raise RuntimeError("Jimmy response data must be an object")
    return data


def _extract_video_url(data: dict[str, Any]) -> str:
    result = data.get("result") or {}
    video_url = data.get("video_url") or data.get("result_url")
    if not video_url and isinstance(result, dict):
        video_url = result.get("video_url") or result.get("result_url")
    if not isinstance(video_url, str) or not video_url.strip():
        raise RuntimeError("completed task did not include video_url")
    return _validate_https_url(video_url.strip(), "video_url")


class JobRunner:
    def __init__(
        self,
        client: Any,
        *,
        uploader: Any | None = None,
        sleep_fn=time.sleep,
        polling_interval: float = 3.0,
    ) -> None:
        self.client = client
        self.uploader = uploader
        self.sleep_fn = sleep_fn
        self.polling_interval = polling_interval

    def _stores(self, run_dir: Path) -> dict[str, AtomicJsonStore]:
        return {
            "job": AtomicJsonStore(run_dir / "job.json"),
            "uploads": AtomicJsonStore(run_dir / "uploads.json"),
            "state": AtomicJsonStore(run_dir / "state.json"),
            "results": AtomicJsonStore(run_dir / "results.json"),
        }

    def _save_state(
        self,
        job: dict[str, Any],
        state: dict[str, Any],
        stores: dict[str, AtomicJsonStore],
        run_dir: Path,
    ) -> None:
        state["status"] = _overall_status(state["tasks"])
        stores["state"].save(state)
        stores["results"].save(
            {
                "schema_version": RESULTS_SCHEMA_VERSION,
                "job_id": job["job_id"],
                "status": state["status"],
                "tasks": [
                    {
                        "id": task["id"],
                        "jimmy_task_id": state["tasks"][task["id"]].get("jimmy_task_id"),
                        "status": state["tasks"][task["id"]].get("phase"),
                        "video_url": state["tasks"][task["id"]].get("video_url"),
                        "local_path": state["tasks"][task["id"]].get("local_path"),
                        "error": state["tasks"][task["id"]].get("error"),
                    }
                    for task in job["tasks"]
                ],
            }
        )

    def _load_or_create_state(
        self,
        job: dict[str, Any],
        stores: dict[str, AtomicJsonStore],
        run_dir: Path,
    ) -> dict[str, Any]:
        frozen = stores["job"].load()
        if frozen:
            if canonical_json(frozen) != canonical_json(job):
                raise JobValidationError(
                    "job.json differs from the immutable job already stored in this run"
                )
        else:
            stores["job"].save(job)

        state = stores["state"].load()
        if not state:
            state = {
                "schema_version": STATE_SCHEMA_VERSION,
                "job_id": job["job_id"],
                "status": "running",
                "tasks": {task["id"]: _initial_task_state() for task in job["tasks"]},
            }
            self._save_state(job, state, stores, run_dir)
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise JobValidationError("state.json: unsupported schema_version")
        if state.get("job_id") != job["job_id"]:
            raise JobValidationError("state.json: job_id does not match job.json")
        expected_ids = {task["id"] for task in job["tasks"]}
        actual_tasks = state.get("tasks")
        if not isinstance(actual_tasks, dict) or set(actual_tasks) != expected_ids:
            raise JobValidationError("state.json: task IDs do not match job.json")
        return state

    def _prepare_tasks(
        self,
        job: dict[str, Any],
        state: dict[str, Any],
        stores: dict[str, AtomicJsonStore],
        run_dir: Path,
    ) -> None:
        if self.uploader is None:
            raise JobValidationError("uploader is required to prepare local media")
        resolver = MediaResolver(self.uploader, stores["uploads"])
        for task in job["tasks"]:
            child = state["tasks"][task["id"]]
            if child.get("phase") != "draft":
                continue
            try:
                request_body = build_jimmy_request(task, resolver)
                child["request"] = request_body
                child["request_sha256"] = hashlib.sha256(
                    canonical_json(request_body).encode("utf-8")
                ).hexdigest()
                child["phase"] = "prepared"
                child["status"] = "prepared"
                child["error"] = None
            except Exception as exc:
                child["phase"] = "failed"
                child["status"] = "failed"
                child["error"] = _error_summary(exc)
            self._save_state(job, state, stores, run_dir)

    def _submit_prepared(
        self,
        job: dict[str, Any],
        state: dict[str, Any],
        stores: dict[str, AtomicJsonStore],
        run_dir: Path,
    ) -> None:
        for task in job["tasks"]:
            child = state["tasks"][task["id"]]
            if child.get("phase") != "prepared":
                continue
            request_body = child.get("request")
            if not isinstance(request_body, dict):
                raise JobValidationError(f"state.tasks.{task['id']}.request must be an object")
            actual_sha = hashlib.sha256(canonical_json(request_body).encode("utf-8")).hexdigest()
            if child.get("request_sha256") != actual_sha:
                raise JobValidationError(
                    f"state.tasks.{task['id']}.request body differs from its saved digest"
                )
            child["phase"] = "create_started"
            child["status"] = "create_started"
            self._save_state(job, state, stores, run_dir)
            try:
                remote_task_id = self.client.create_video(request_body)
            except Exception as exc:
                child["phase"] = "submission_outcome_unknown"
                child["status"] = "submission_outcome_unknown"
                child["error"] = _error_summary(exc)
                self._save_state(job, state, stores, run_dir)
                break
            child["phase"] = "submitted"
            child["status"] = "submitted"
            child["jimmy_task_id"] = remote_task_id
            child["error"] = None
            self._save_state(job, state, stores, run_dir)

    def _poll_existing(
        self,
        job: dict[str, Any],
        state: dict[str, Any],
        stores: dict[str, AtomicJsonStore],
        run_dir: Path,
        *,
        timeout: float,
    ) -> None:
        deadline = time.monotonic() + timeout
        while True:
            active = False
            for task in job["tasks"]:
                child = state["tasks"][task["id"]]
                if child.get("phase") in {"completed", "failed"}:
                    continue
                remote_task_id = child.get("jimmy_task_id")
                if not remote_task_id:
                    continue
                active = True
                try:
                    response = self.client.get_status(str(remote_task_id))
                    data = _extract_data(response)
                    status = str(data.get("status", "")).lower()
                    child["status"] = status
                    if status in COMPLETED:
                        video_url = _extract_video_url(data)
                        output_path = run_dir / "results" / f"video-{task['id']}.mp4"
                        self.client.download_video(video_url, output_path)
                        child["phase"] = "completed"
                        child["video_url"] = video_url
                        child["local_path"] = str(output_path)
                        child["error"] = None
                    elif status in FAILED:
                        message = (
                            data.get("error_message")
                            or data.get("message")
                            or data.get("error")
                            or "Jimmy task failed"
                        )
                        child["phase"] = "failed"
                        child["error"] = {"message": str(message)}
                    elif status in RUNNING:
                        child["phase"] = "running"
                    else:
                        child["phase"] = "failed"
                        child["error"] = {"message": f"unknown Jimmy task status: {status or '<empty>'}"}
                except Exception as exc:
                    child["phase"] = "failed"
                    child["status"] = "failed"
                    child["error"] = _error_summary(exc)
                self._save_state(job, state, stores, run_dir)
            if not active:
                break
            if all(
                state["tasks"][task["id"]].get("phase") in {"completed", "failed"}
                or not state["tasks"][task["id"]].get("jimmy_task_id")
                for task in job["tasks"]
            ):
                break
            if time.monotonic() >= deadline:
                self._save_state(job, state, stores, run_dir)
                break
            self.sleep_fn(self.polling_interval)

    def generate(
        self,
        job_path: Path,
        *,
        output_root: Path,
        poll: bool = True,
        timeout: float = 1800,
    ) -> Path:
        job = load_and_validate_job(Path(job_path))
        run_dir = Path(output_root) / job["job_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        activate_runtime_patch(run_dir)
        stores = self._stores(run_dir)
        state = self._load_or_create_state(job, stores, run_dir)
        self._prepare_tasks(job, state, stores, run_dir)
        self._submit_prepared(job, state, stores, run_dir)
        if poll:
            self._poll_existing(job, state, stores, run_dir, timeout=timeout)
        return run_dir

    def resume(
        self,
        run_dir: Path,
        *,
        poll: bool = True,
        timeout: float = 1800,
    ) -> Path:
        run_dir = Path(run_dir)
        activate_runtime_patch(run_dir)
        job = load_and_validate_job(run_dir / "job.json", require_files=False)
        stores = self._stores(run_dir)
        state = self._load_or_create_state(job, stores, run_dir)
        self._submit_prepared(job, state, stores, run_dir)
        if poll:
            self._poll_existing(job, state, stores, run_dir, timeout=timeout)
        return run_dir

    def recover_task_id(self, run_dir: Path, task_id: str, remote_task_id: str) -> Path:
        run_dir = Path(run_dir)
        job = load_and_validate_job(run_dir / "job.json", require_files=False)
        stores = self._stores(run_dir)
        state = self._load_or_create_state(job, stores, run_dir)
        if task_id not in state["tasks"]:
            raise JobValidationError(f"unknown local task ID: {task_id}")
        child = state["tasks"][task_id]
        existing = child.get("jimmy_task_id")
        if existing and existing != remote_task_id:
            raise JobValidationError("local task already has a different Jimmy task ID")
        child["jimmy_task_id"] = remote_task_id
        child["phase"] = "submitted"
        child["status"] = "submitted"
        child["error"] = None
        self._save_state(job, state, stores, run_dir)
        return run_dir


def activate_runtime_patch(run_dir: Path) -> Path:
    patch_dir = write_runtime_dns_patch(run_dir)
    install_runtime_dns_patch()
    return patch_dir


def print_job_preview(job: dict[str, Any]) -> None:
    print(f"Jimmy job: {job['job_id']}")
    print(f"Schema: {job['schema_version']}")
    print(f"Default model: {JIMMY_MODEL} / {JIMMY_RESOLUTION}")
    for task in job["tasks"]:
        print("")
        print(
            f"[{task['id']}] model={task['model']} duration={task['duration']} "
            f"ratio={task['ratio']} images={len(task.get('images', []))} "
            f"reference_videos={len(task.get('reference_videos', []))}"
            + (
                f" reference_audios={len(task['reference_audios'])}"
                if task.get("reference_audios") else ""
            )
        )
        print(task["prompt"])


def _build_runner(env_file: Path) -> JobRunner:
    settings = load_settings(env_file)
    settings.require_jimmy()
    client = JimmyClient(settings.jimmy_api_key, base_url=settings.jimmy_base_url)
    uploader = JimmyFileUploader(settings.jimmy_api_key, base_url=settings.jimmy_base_url)
    return JobRunner(client, uploader=uploader)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ready video jobs through Jimmy.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview_parser = subparsers.add_parser("preview", help="Display a Jimmy job without remote writes.")
    preview_parser.add_argument("--job", type=Path, required=True)

    generate_parser = subparsers.add_parser("generate", help="Upload media, create tasks, and poll results.")
    generate_parser.add_argument("--job", type=Path, required=True)
    generate_parser.add_argument("--output-root", type=Path, required=True)
    generate_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    generate_parser.add_argument("--no-poll", action="store_true")
    generate_parser.add_argument("--timeout", type=float, default=1800)

    resume_parser = subparsers.add_parser("resume", help="Resume a saved Jimmy run.")
    resume_parser.add_argument("--run", type=Path, required=True)
    resume_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    resume_parser.add_argument("--no-poll", action="store_true")
    resume_parser.add_argument("--timeout", type=float, default=1800)

    recover_parser = subparsers.add_parser("recover-task-id", help="Attach a recovered remote Jimmy task ID.")
    recover_parser.add_argument("--run", type=Path, required=True)
    recover_parser.add_argument("--task-id", required=True)
    recover_parser.add_argument("--remote-task-id", required=True)
    recover_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    upload_parser = subparsers.add_parser("upload", help="Upload one local file to Jimmy.")
    upload_parser.add_argument("file", type=Path)
    upload_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    task_parser = subparsers.add_parser("task", help="Query one Jimmy remote task ID.")
    task_parser.add_argument("task_id")
    task_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)

    args = parser.parse_args()

    if args.command == "preview":
        job = load_and_validate_job(args.job)
        print_job_preview(job)
        return 0
    if args.command == "generate":
        job = load_and_validate_job(args.job)
        print_job_preview(job)
        runner = _build_runner(args.env_file)
        run_dir = runner.generate(
            args.job,
            output_root=args.output_root,
            poll=not args.no_poll,
            timeout=args.timeout,
        )
        print(run_dir)
        return 0
    if args.command == "resume":
        runner = _build_runner(args.env_file)
        run_dir = runner.resume(
            args.run,
            poll=not args.no_poll,
            timeout=args.timeout,
        )
        print(run_dir)
        return 0
    if args.command == "recover-task-id":
        runner = _build_runner(args.env_file)
        run_dir = runner.recover_task_id(args.run, args.task_id, args.remote_task_id)
        print(run_dir)
        return 0
    if args.command == "upload":
        settings = load_settings(args.env_file)
        settings.require_jimmy()
        uploader = JimmyFileUploader(settings.jimmy_api_key, base_url=settings.jimmy_base_url)
        print(json.dumps(uploader.upload(args.file), ensure_ascii=False, indent=2))
        return 0
    if args.command == "task":
        settings = load_settings(args.env_file)
        settings.require_jimmy()
        client = JimmyClient(settings.jimmy_api_key, base_url=settings.jimmy_base_url)
        print(json.dumps(client.get_status(args.task_id), ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
