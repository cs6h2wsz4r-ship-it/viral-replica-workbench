from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from config import DEFAULT_ENV_FILE, JIMMY_ORIGIN, load_settings
from seedance_submit import (
    API_CLIENT_USER_AGENT,
    MAX_JSON_RESPONSE_BYTES,
    ResponseBodyTooLargeError,
    _build_https_opener,
    _read_http_error_body,
    _read_json_bytes,
    _read_limited_body,
    _split_response,
)


JIMMY_UPLOAD_PATH = "/files/upload"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class JimmyUploadError(RuntimeError):
    pass


def _urllib_request_multipart(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
    response_body_limit: int = MAX_JSON_RESPONSE_BYTES,
) -> tuple[int, dict[str, Any]]:
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


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def _clean_filename(name: str) -> str:
    return name.replace("\\", "_").replace('"', "_").strip() or "upload.bin"


def _build_multipart_body(path: Path, boundary: str) -> tuple[bytes, str]:
    filename = _clean_filename(path.name)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("ascii")
    return header + path.read_bytes() + footer, content_type


def _require_https_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise JimmyUploadError("Jimmy upload response did not include url")
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise JimmyUploadError("Jimmy upload response url must be public HTTPS")
    return url.strip()


def _uploaded_data_or_raise(response: dict[str, Any]) -> dict[str, Any]:
    code = response.get("code")
    if code not in {0, "0", 200, "200", 20000, "20000"}:
        message = response.get("msg") or response.get("message") or "Jimmy upload failed"
        raise JimmyUploadError(str(message))
    data = response.get("data")
    if not isinstance(data, dict):
        raise JimmyUploadError("Jimmy upload response data must be an object")
    data = dict(data)
    data["url"] = _require_https_url(data.get("url"))
    return data


class JimmyFileUploader:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = JIMMY_ORIGIN,
        request_multipart: Callable[..., Any] | None = None,
        timeout: float = 60,
        max_bytes: int = MAX_UPLOAD_BYTES,
    ) -> None:
        parsed = urlparse(base_url)
        if (
            base_url != JIMMY_ORIGIN
            or parsed.scheme != "https"
            or parsed.hostname != "api.viraltok.ai"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/api/open-api/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise JimmyUploadError(f"Jimmy base URL must be {JIMMY_ORIGIN}")
        self.api_key = api_key
        self.base_url = base_url
        self.request_multipart = request_multipart or _urllib_request_multipart
        self.timeout = timeout
        self.max_bytes = max_bytes

    def upload(self, local_path: Path) -> dict[str, Any]:
        path = Path(local_path)
        if not path.is_file():
            raise JimmyUploadError(f"Media file not found: {path}")
        size = path.stat().st_size
        if size > self.max_bytes:
            raise JimmyUploadError("Jimmy upload file exceeds 100 MB")
        boundary = f"jimmy-video-generator-{secrets.token_hex(16)}"
        body, content_type = _build_multipart_body(path, boundary)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": API_CLIENT_USER_AGENT,
        }
        try:
            raw_response = self.request_multipart(
                method="POST",
                url=f"{self.base_url}{JIMMY_UPLOAD_PATH}",
                headers=headers,
                body=body,
                timeout=self.timeout,
            )
            status_code, payload = _split_response(raw_response)
        except ResponseBodyTooLargeError as exc:
            raise JimmyUploadError("Jimmy upload response body exceeded safety limit") from exc
        except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JimmyUploadError(f"Jimmy upload transport failed: {exc}") from exc
        if status_code in {401, 403}:
            raise JimmyUploadError(f"Jimmy upload rejected with HTTP {status_code}; check JIMMY_API_KEY")
        if 300 <= status_code < 400:
            raise JimmyUploadError(f"Jimmy upload rejected redirect with HTTP {status_code}")
        if status_code >= 400:
            message = payload.get("msg") or payload.get("message") or "request failed"
            raise JimmyUploadError(f"Jimmy upload failed with HTTP {status_code}: {message}")
        data = _uploaded_data_or_raise(payload)
        data.setdefault("filename", path.name)
        data.setdefault("size", size)
        data.setdefault("mime_type", content_type)
        return data


def publish_media_manifest(
    uploader: JimmyFileUploader,
    media_paths: Iterable[Path],
    output_path: Path,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    paths = [Path(media_path) for media_path in media_paths]
    _atomic_write_json(output_path, manifest)
    for path in paths:
        uploaded = uploader.upload(path)
        manifest.append(
            {
                "filename": str(uploaded.get("filename") or path.name),
                "local_path": str(path),
                "url": uploaded["url"],
                "size": int(uploaded.get("size") or path.stat().st_size),
                "mime_type": str(uploaded.get("mime_type") or "application/octet-stream"),
            }
        )
        _atomic_write_json(output_path, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload media files to Jimmy and write a URL manifest.")
    parser.add_argument("--media", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    settings.require_jimmy()
    uploader = JimmyFileUploader(settings.jimmy_api_key, base_url=settings.jimmy_base_url)
    publish_media_manifest(uploader, args.media, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
