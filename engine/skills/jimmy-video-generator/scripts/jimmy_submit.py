from __future__ import annotations

import argparse
import hmac
import json
from pathlib import Path
import socket
import sys
import time
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import quote, urlparse

from config import DEFAULT_ENV_FILE, JIMMY_ORIGIN, load_settings
from seedance_submit import (
    API_CLIENT_USER_AGENT,
    DEFAULT_POLL_INTERVAL,
    MAX_JSON_RESPONSE_BYTES,
    PayloadError,
    PollTimeoutError,
    ResponseBodyTooLargeError,
    SeedanceApiError,
    TaskFailedError,
    _atomic_write_text,
    _create_exclusive_json,
    _download_file,
    _ensure_create_not_started,
    _ensure_dry_run_allowed,
    _is_localhost,
    _is_public_https_url,
    _read_json,
    _require_public_https_urls,
    _split_response,
    _urllib_request_json,
    _validate_task_id,
    _validated_resume_task_id,
    _write_dry_run_artifacts,
    _write_json,
    compute_approval_digest,
    request_sha256,
)


CREATE_PATH = "/seedance/videos"
MINIMAX_CREATE_PATH = "/minimax/videos"
QUERY_PATH = "/videos/{task_id}"
JIMMY_MODEL = "seedance2.0-fast-md"
MINIMAX_H3_MODEL = "minimax-h3"
JIMMY_RESOLUTION = "720p"
DEFAULT_PROMPT_LIMIT = 5000
MINIMAX_H3_PROMPT_LIMIT = 2000
RUNNING = {"pending", "queued", "submitting", "processing", "running", "created", "in_progress"}
COMPLETED = {"completed", "succeeded", "success", "done"}
FAILED = {"failed", "failure", "error", "cancelled", "canceled"}
SUCCESS_CODES = {0, "0", 200, "200", 20000, "20000"}


def _validate_base_url(base_url: str, *, allow_test_localhost: bool) -> str:
    try:
        parsed = urlparse(base_url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise PayloadError("Jimmy base URL is invalid") from exc
    has_clean_base_shape = (
        bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )
    if base_url == JIMMY_ORIGIN and has_clean_base_shape:
        return base_url
    if (
        allow_test_localhost
        and has_clean_base_shape
        and not parsed.path
        and _is_localhost(str(hostname))
        and parsed.scheme in {"http", "https"}
    ):
        return base_url.rstrip("/")
    raise PayloadError(f"Jimmy base URL must be {JIMMY_ORIGIN}")


def build_payload(
    prompt: str,
    duration: int,
    ratio: str,
    image_urls: list[str],
    reference_video_urls: list[str],
    *,
    model: str = JIMMY_MODEL,
    resolver: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    normalized_prompt = prompt.strip()
    normalized_model = str(model).strip()
    prompt_limit = prompt_limit_for_model(normalized_model)
    if not normalized_model:
        raise PayloadError("model must be a non-empty Jimmy model name")
    if not normalized_prompt or len(normalized_prompt) > prompt_limit:
        raise PayloadError(f"prompt must contain 1-{prompt_limit} characters")
    if not 1 <= int(duration) <= 15:
        raise PayloadError("duration must be between 1-15 seconds for Jimmy MD")
    if ratio not in {"16:9", "9:16", "1:1"}:
        raise PayloadError("ratio must be one of 16:9, 9:16, 1:1")
    if len(image_urls) > 4:
        raise PayloadError(f"{normalized_model} accepts at most 4 images")
    _require_public_https_urls(image_urls, resolver=resolver)
    _require_public_https_urls(reference_video_urls, resolver=resolver)
    payload: dict[str, Any] = {
        "model": normalized_model,
        "prompt": normalized_prompt,
        "duration": int(duration),
        "resolution": JIMMY_RESOLUTION,
        "ratio": ratio,
    }
    if image_urls:
        payload["images"] = list(image_urls)
    if reference_video_urls:
        payload["reference_videos"] = list(reference_video_urls)
    return payload


def _is_success_code(code: Any) -> bool:
    return code is None or code in SUCCESS_CODES


def _error_message(response: dict[str, Any], fallback: str) -> str:
    direct = response.get("message") or response.get("msg")
    if direct:
        return str(direct)
    nested = response.get("error")
    if isinstance(nested, dict):
        nested_message = nested.get("message") or nested.get("msg")
        if nested_message:
            return str(nested_message)
    if nested:
        return str(nested)
    return fallback


def _data_or_raise(response: dict[str, Any]) -> dict[str, Any]:
    if "code" in response and not _is_success_code(response.get("code")):
        raise SeedanceApiError(_error_message(response, "Jimmy request failed"))
    data = response.get("data")
    if data is None:
        return response
    if not isinstance(data, dict):
        raise SeedanceApiError("Jimmy response data must be an object")
    return data


def parse_task_id(response: dict[str, Any]) -> str:
    data = _data_or_raise(response)
    task_id = data.get("task_id") or data.get("id") or response.get("task_id") or response.get("id")
    if not task_id:
        raise SeedanceApiError("Jimmy response did not include task_id")
    return _validate_task_id(str(task_id))


def prompt_limit_for_model(model: str) -> int:
    if str(model).strip() == MINIMAX_H3_MODEL:
        return MINIMAX_H3_PROMPT_LIMIT
    return DEFAULT_PROMPT_LIMIT


def create_path_for_model(model: str) -> str:
    if str(model).strip() == MINIMAX_H3_MODEL:
        return MINIMAX_CREATE_PATH
    return CREATE_PATH


def build_failure_report(
    message: str,
    *,
    stage: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "raw_error": message,
        "stage": stage,
        "task_id": task_id,
        "code": "jimmy_provider_failure",
        "title": "Jimmy 视频生成失败",
        "user_message": "Jimmy 返回失败或本地提交/查询链路失败，系统不会自动创建新的付费任务。",
        "next_action": "保留原始请求、响应和 task_id；先检查 failure.json/status.json，再决定是否用新 preview 重新确认。",
        "retry_allowed": False,
        "requires_user_confirmation": True,
        "prompt_or_image_change_required": False,
    }


def _validate_run_approval(
    output_dir: Path,
    payload: dict[str, Any],
    approved_digest: str | None,
) -> str:
    if not approved_digest:
        raise PayloadError(
            "a new paid Jimmy request requires explicit user approval of its dry-run preview"
        )
    request_path = output_dir / "request.redacted.json"
    preview_path = output_dir / "approval_preview.json"
    if not request_path.exists() or not preview_path.exists():
        raise PayloadError("a successful dry-run preview is required before paid create")
    if _read_json(request_path) != payload:
        raise PayloadError("request.redacted.json does not match the approved request")
    preview = _read_json(preview_path)
    payload_digest = request_sha256(payload)
    approval_id = str(preview.get("approval_id", ""))
    canonical_output_dir = str(output_dir.expanduser().resolve())
    if not approval_id:
        raise PayloadError("approval_preview.json is missing approval_id")
    if str(preview.get("canonical_output_dir", "")) != canonical_output_dir:
        raise PayloadError("approval preview is bound to a different output directory")
    if not hmac.compare_digest(str(preview.get("payload_sha256", "")), payload_digest):
        raise PayloadError("approval preview payload does not match the current request")
    expected_digest = compute_approval_digest(output_dir, approval_id, payload_digest)
    if not hmac.compare_digest(str(preview.get("approval_digest", "")), expected_digest):
        raise PayloadError("approval preview digest is invalid")
    if not hmac.compare_digest(approved_digest.strip().lower(), expected_digest):
        raise PayloadError("the run-bound approval digest does not match this request")
    return expected_digest


class JimmyClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = JIMMY_ORIGIN,
        request_json: Callable[..., Any] | None = None,
        download: Callable[[str, Path], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        request_timeout: float = 60,
        max_attempts: int = 3,
        resolver: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = _validate_base_url(
            base_url,
            allow_test_localhost=request_json is not None,
        )
        self.request_json = request_json or _urllib_request_json
        self.resolver = resolver or socket.getaddrinfo
        self.download = download or (
            lambda url, path: _download_file(url, path, resolver=self.resolver)
        )
        self.sleep = sleep
        self.clock = clock
        self.request_timeout = request_timeout
        self.max_attempts = max_attempts
        self.last_response: dict[str, Any] = {}
        self.last_status_response: dict[str, Any] = {}

    def create_video(self, payload: dict[str, Any]) -> str:
        response = self._request("POST", create_path_for_model(str(payload.get("model", ""))), payload)
        self.last_response = response
        return parse_task_id(response)

    def get_status(self, task_id: str) -> dict[str, Any]:
        safe_task_id = quote(_validate_task_id(task_id), safe="")
        cache_buster = int(time.time() * 1000)
        response = self._request(
            "GET",
            f"{QUERY_PATH.format(task_id=safe_task_id)}?_t={cache_buster}",
            None,
        )
        self.last_status_response = response
        return response

    def download_video(self, video_url: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not _is_public_https_url(video_url, resolver=self.resolver):
            raise SeedanceApiError("download URL must be a public HTTPS URL")
        try:
            self.download(video_url, output_path)
        except SeedanceApiError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            raise SeedanceApiError(f"video download failed: {exc}") from exc

    def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": API_CLIENT_USER_AGENT,
        }
        delay = 1.0
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_response = self.request_json(
                    method=method,
                    url=url,
                    headers=headers,
                    json_body=json_body,
                    timeout=self.request_timeout,
                )
                status_code, payload = _split_response(raw_response)
            except ResponseBodyTooLargeError as exc:
                if method == "POST":
                    raise SeedanceApiError(
                        "Jimmy create submission outcome unknown because the response body "
                        "exceeded the safety limit; automatic retry is forbidden"
                    ) from exc
                raise SeedanceApiError("Jimmy GET response body exceeded the safety limit") from exc
            except (URLError, TimeoutError) as exc:
                if method == "POST":
                    raise SeedanceApiError(
                        "Jimmy create submission outcome unknown after transport failure; "
                        "automatic retry is forbidden"
                    ) from exc
                if attempt == self.max_attempts:
                    raise SeedanceApiError(f"Jimmy GET transport failed: {exc}") from exc
                self.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if method == "POST":
                    raise SeedanceApiError(
                        "Jimmy create submission outcome unknown after invalid UTF-8/JSON response; "
                        "automatic retry is forbidden"
                    ) from exc
                raise SeedanceApiError("Jimmy GET response contained invalid UTF-8/JSON") from exc
            if status_code == 429 or 500 <= status_code < 600:
                if method == "POST":
                    raise SeedanceApiError(
                        "Jimmy create submission outcome unknown after "
                        f"HTTP {status_code}; automatic retry is forbidden"
                    )
                if attempt == self.max_attempts:
                    raise SeedanceApiError(f"Jimmy request failed with HTTP {status_code}")
                self.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            if 300 <= status_code < 400:
                raise SeedanceApiError(f"Jimmy request rejected redirect with HTTP {status_code}")
            if status_code in {401, 403}:
                raise SeedanceApiError(f"Jimmy request rejected with HTTP {status_code}; check JIMMY_API_KEY")
            if status_code >= 400:
                message = _error_message(payload, "request failed")
                raise SeedanceApiError(f"Jimmy request failed with HTTP {status_code}: {message}")
            return payload
        raise SeedanceApiError("Jimmy request failed")


def poll_task(
    client: JimmyClient,
    task_id: str,
    *,
    timeout: float = 1800,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> str:
    deadline = client.clock() + timeout
    while True:
        response = client.get_status(task_id)
        data = _data_or_raise(response)
        status = str(data.get("status", "")).lower()
        if status in COMPLETED:
            result = data.get("result") or {}
            video_url = data.get("video_url") or data.get("result_url")
            if not video_url and isinstance(result, dict):
                video_url = result.get("video_url") or result.get("result_url")
            if not video_url:
                raise SeedanceApiError("completed task did not include video_url")
            if not _is_public_https_url(str(video_url), resolver=client.resolver):
                raise SeedanceApiError("completed task video_url must be a public HTTPS URL")
            return str(video_url)
        if status in FAILED:
            message = (
                data.get("error_message")
                or data.get("message")
                or data.get("error")
                or response.get("message")
                or "Jimmy task failed"
            )
            raise TaskFailedError(str(message))
        if status not in RUNNING:
            raise SeedanceApiError(f"unknown Jimmy task status: {status or '<empty>'}")
        if client.clock() >= deadline:
            raise PollTimeoutError(f"Jimmy task {task_id} timed out")
        client.sleep(poll_interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit or resume Jimmy Seedance MD 720p video tasks.")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--image-url", action="append", default=[])
    parser.add_argument("--reference-video-url", action="append", default=[])
    parser.add_argument("--duration", type=int)
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--model", default=JIMMY_MODEL)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--poll", action="store_true")
    parser.add_argument("--resume-task-id")
    parser.add_argument("--approved-request-sha256")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.resume_task_id:
        if args.dry_run:
            raise PayloadError("--dry-run cannot be combined with --resume-task-id")
        task_id = _validated_resume_task_id(args.output_dir, args.resume_task_id)
        payload: dict[str, Any] | None = None
        payload_sha256: str | None = None
        approved_digest: str | None = None
    else:
        missing = [
            name
            for name, value in (
                ("--prompt-file", args.prompt_file),
                ("--duration", args.duration),
            )
            if value is None
        ]
        if missing:
            raise PayloadError("create and dry-run require " + ", ".join(missing))
        prompt = args.prompt_file.read_text(encoding="utf-8")
        payload = build_payload(
            prompt,
            args.duration,
            args.ratio,
            args.image_url,
            args.reference_video_url,
            model=args.model,
        )
        payload_sha256 = request_sha256(payload)
        if args.dry_run:
            _write_dry_run_artifacts(args.output_dir, payload)
            return 0
        _ensure_create_not_started(args.output_dir)
        approved_digest = _validate_run_approval(
            args.output_dir,
            payload,
            args.approved_request_sha256,
        )

    settings = load_settings(args.env_file)
    settings.require_jimmy()
    client = JimmyClient(settings.jimmy_api_key, base_url=settings.jimmy_base_url)
    stage = "resume" if args.resume_task_id else "create_prepare"
    task_id: str | None = task_id if args.resume_task_id else None
    try:
        if args.resume_task_id:
            pass
        else:
            assert payload is not None
            assert payload_sha256 is not None
            assert approved_digest is not None
            marker = {
                "payload_sha256": payload_sha256,
                "approval_digest": approved_digest,
                "status": "submission_outcome_ambiguous_until_task_id_is_recorded",
            }
            _create_exclusive_json(args.output_dir / "approval.consumed.json", marker)
            _create_exclusive_json(args.output_dir / "create.started.json", marker)
            stage = "create_post"
            task_id = client.create_video(payload)
            created_task_line = f"CREATED_TASK_ID={task_id}"
            try:
                print(created_task_line, file=sys.stderr, flush=True)
            except (BrokenPipeError, OSError):
                pass
            stage = "post_create_persist"
            try:
                _atomic_write_text(args.output_dir / "task_id.txt", task_id)
                _write_json(args.output_dir / "create_response.json", client.last_response)
            except OSError as exc:
                raise SeedanceApiError(
                    "local post-create persistence failed after Jimmy returned task_id; "
                    f"do not repeat POST; {created_task_line}"
                ) from exc

        if args.poll:
            stage = "resume_poll" if args.resume_task_id else "poll"
            video_url = poll_task(
                client,
                task_id,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
            )
            status_name = "resume_status.json" if args.resume_task_id else "status.json"
            _write_json(args.output_dir / status_name, client.last_status_response)
            stage = "download"
            client.download_video(video_url, args.output_dir / "result.mp4")
        else:
            status_name = "resume_status.json" if args.resume_task_id else "status.json"
            status_value = "resumed" if args.resume_task_id else "created"
            _write_json(
                args.output_dir / status_name,
                {"task_id": task_id, "status": status_value},
            )
    except SeedanceApiError as exc:
        if (
            stage == "create_post"
            and client.last_response
            and not (args.output_dir / "create_response.json").exists()
        ):
            _write_json(args.output_dir / "create_response.json", client.last_response)
        if client.last_status_response:
            status_name = "resume_status.json" if args.resume_task_id else "status.json"
            _write_json(args.output_dir / status_name, client.last_status_response)
        report = build_failure_report(str(exc), stage=stage, task_id=task_id)
        terminal_task_line = f"CREATED_TASK_ID={task_id}" if task_id else ""
        try:
            _write_json(args.output_dir / "failure.json", report)
        except OSError as audit_exc:
            message = (
                f"{terminal_task_line}\n" if terminal_task_line else ""
            ) + (
                f"{report['user_message']}\n下一步：{report['next_action']}\n"
                f"Failure audit could not be written: {audit_exc}"
            )
            raise SystemExit(message) from exc
        message = (
            f"{terminal_task_line}\n" if terminal_task_line else ""
        ) + f"{report['user_message']}\n下一步：{report['next_action']}"
        raise SystemExit(message) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
