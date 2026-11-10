from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from runtime_dns_patch import install_runtime_dns_patch


install_runtime_dns_patch()
BUNDLED_CA_BUNDLE = Path(__file__).resolve().parents[1] / "vendor" / "certifi" / "cacert.pem"

try:
    import certifi
except ImportError:  # pragma: no cover - depends on local Python packaging.
    certifi = None


DEFAULT_POLL_INTERVAL = 30
MAX_JSON_RESPONSE_BYTES = 1024 * 1024
API_CLIENT_USER_AGENT = "jimmy-video-generator/1.0"
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class PayloadError(ValueError):
    pass


class SeedanceApiError(RuntimeError):
    pass


class TaskFailedError(SeedanceApiError):
    pass


class PollTimeoutError(SeedanceApiError):
    pass


class ResponseBodyTooLargeError(RuntimeError):
    pass


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _ssl_context() -> ssl.SSLContext:
    if BUNDLED_CA_BUNDLE.is_file():
        return ssl.create_default_context(cafile=str(BUNDLED_CA_BUNDLE))
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def _build_https_opener() -> Any:
    return build_opener(_NoRedirectHandler(), HTTPSHandler(context=_ssl_context()))


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _is_localhost(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    return normalized == "localhost" or normalized.endswith(".localhost")


def _is_public_https_url(
    url: str,
    *,
    resolver: Callable[..., Any] | None = None,
) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or _is_localhost(hostname)
        ):
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            active_resolver = resolver or socket.getaddrinfo
            try:
                resolved = active_resolver(
                    hostname,
                    parsed.port or 443,
                    type=socket.SOCK_STREAM,
                )
                addresses = [
                    ipaddress.ip_address(str(item[4][0]).split("%", 1)[0])
                    for item in resolved
                ]
            except (OSError, ValueError, IndexError, TypeError):
                return False
            return bool(addresses) and all(address.is_global for address in addresses)
        return address.is_global
    except ValueError:
        return False


def _require_public_https_urls(
    urls: list[str],
    *,
    resolver: Callable[..., Any] | None = None,
) -> None:
    for url in urls:
        if not _is_public_https_url(url, resolver=resolver):
            raise PayloadError(
                "media URLs must be public HTTPS URLs without private hosts or userinfo"
            )


def _validate_task_id(task_id: str) -> str:
    normalized = str(task_id)
    if not TASK_ID_PATTERN.fullmatch(normalized):
        raise SeedanceApiError("task_id contains unsafe characters or has invalid length")
    return normalized


def request_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def compute_approval_digest(
    output_dir: Path,
    approval_id: str,
    payload_sha256: str,
) -> str:
    binding = {
        "canonical_output_dir": str(output_dir.expanduser().resolve()),
        "approval_id": approval_id,
        "payload_sha256": payload_sha256,
    }
    canonical = json.dumps(
        binding,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _split_response(raw_response: Any) -> tuple[int, dict[str, Any]]:
    if isinstance(raw_response, tuple) and len(raw_response) == 2:
        status_code, payload = raw_response
        return int(status_code), dict(payload)
    return 200, dict(raw_response)


def _read_json_bytes(payload: bytes) -> dict[str, Any]:
    if not payload:
        return {}
    return json.loads(payload.decode("utf-8"))


def _read_http_error_body(payload: bytes) -> dict[str, Any]:
    try:
        return _read_json_bytes(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        text = payload.decode("utf-8", errors="replace").strip()
        return {
            "message": text[:4096] or "HTTP error response had an empty body",
            "body_format": "text",
        }


def _read_limited_body(stream: Any, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = stream.read(limit + 1 - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > limit:
        raise ResponseBodyTooLargeError(
            f"Jimmy JSON response body exceeds {limit}-byte limit"
        )
    return b"".join(chunks)


def _urllib_request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None,
    timeout: float,
    response_body_limit: int = MAX_JSON_RESPONSE_BYTES,
) -> tuple[int, dict[str, Any]]:
    body = None
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers=headers, method=method)
    opener = _build_https_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, _read_json_bytes(
                _read_limited_body(response, response_body_limit)
            )
    except HTTPError as exc:
        return exc.code, _read_http_error_body(
            _read_limited_body(exc, response_body_limit)
        )


def _download_file(
    url: str,
    output_path: Path,
    *,
    resolver: Callable[..., Any] | None = None,
    max_bytes: int = 512 * 1024 * 1024,
) -> None:
    if not _is_public_https_url(url, resolver=resolver):
        raise SeedanceApiError("download URL must be a public HTTPS URL")
    request = Request(
        url,
        headers={"User-Agent": API_CLIENT_USER_AGENT},
        method="GET",
    )
    opener = _build_https_opener()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle, opener.open(request, timeout=120) as response:
            headers = getattr(response, "headers", {})
            content_length = headers.get("Content-Length") if headers else None
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except (TypeError, ValueError) as exc:
                    raise SeedanceApiError("download Content-Length is invalid") from exc
                if declared_size > max_bytes:
                    raise SeedanceApiError("video download exceeds size limit")
            total_bytes = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise SeedanceApiError("video download exceeds size limit")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
        os.chmod(output_path, 0o600)
        _fsync_directory(output_path.parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def _write_json(path: Path, data: Any) -> None:
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
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
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


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, text: str) -> None:
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
            handle.write(text)
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


def _create_exclusive_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _ensure_create_not_started(output_dir: Path) -> None:
    markers = ("create.started.json", "task_id.txt", "approval.consumed.json")
    existing = [name for name in markers if (output_dir / name).exists()]
    if existing:
        raise PayloadError(
            "create already started for this output directory; resume the existing task"
        )


def _ensure_dry_run_allowed(output_dir: Path) -> None:
    if (output_dir / "create.started.json").exists() or (output_dir / "task_id.txt").exists():
        raise PayloadError("dry-run is not allowed after create has started")


def _write_dry_run_artifacts(output_dir: Path, payload: dict[str, Any]) -> None:
    _ensure_dry_run_allowed(output_dir)
    payload_sha = request_sha256(payload)
    approval_id = hashlib.sha256(os.urandom(32)).hexdigest()
    preview = {
        "approval_id": approval_id,
        "canonical_output_dir": str(output_dir.expanduser().resolve()),
        "payload_sha256": payload_sha,
        "approval_digest": compute_approval_digest(output_dir, approval_id, payload_sha),
    }
    _write_json(output_dir / "request.redacted.json", payload)
    _write_json(output_dir / "approval_preview.json", preview)


def _validated_resume_task_id(output_dir: Path, supplied_task_id: str) -> str:
    task_id = _validate_task_id(supplied_task_id)
    saved_path = output_dir / "task_id.txt"
    if saved_path.exists():
        saved_task_id = saved_path.read_text(encoding="utf-8").strip()
        if not hmac.compare_digest(saved_task_id, task_id):
            raise PayloadError("supplied task ID does not match saved task_id.txt")
    return task_id
