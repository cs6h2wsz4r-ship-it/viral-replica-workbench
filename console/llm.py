"""Minimal OpenAI-compatible chat client (works for 火山方舟/豆包, DeepSeek, OpenRouter, Jimmy chat...)."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config, media
from .channel_text import user_message


from .gateway.errors import (LLMError, ReasoningExhausted, EmptyResponse, JSONResponseError, CheckedLLMError)
from .gateway.retry import DELAYS as OFFICIAL_RETRY_DELAYS


def _official_error_fields(detail: str) -> dict:
    from .gateway.retry import error_fields
    return error_fields(detail)


def official_retryable(status: int, detail: str) -> bool:
    from .gateway.retry import retryable
    return retryable(status, detail)


def official_error(status: int, detail: str) -> str:
    from .gateway.errors import official_error as implementation
    return implementation(status, detail)


_IMAGE_CACHE = config.ROOT / "runtime/temp/llm-images"
_REQUEST_LIMIT = 3_500_000  # Decimal MB, including base64, text and JSON overhead.


def _local_media_tool(name: str) -> str:
    return str(config.ROOT / "runtime/ffmpeg" / (name + (".exe" if os.name == "nt" else "")))


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith((b"II*\0", b"MM\0*")):
        return "image/tiff"
    raise LLMError("无法识别参考图片格式，请使用 PNG、JPEG 或 WebP 图片")


def _prepare_image(path: Path, long_side: int = 1536, *, force: bool = False) -> Path:
    """Normalize a copy only; cache identity includes source bytes and all encoding parameters."""
    original = path.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    cached = _IMAGE_CACHE / f"{digest}-s{long_side}-q3-white-v1.jpg"
    if cached.is_file() and cached.stat().st_size:
        return cached
    try:
        result = media._run([_local_media_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=width,height", "-of", "json", str(path)], 60)
        stream = json.loads(result.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        if min(width, height) <= 0:
            raise ValueError("invalid dimensions")
        if not force and max(width, height) <= long_side and len(original) <= 1_000_000:
            return path
        scale = min(1, long_side / max(width, height))
        # Even dimensions for JPEG 4:2:0; rounding changes the aspect by at most one pixel.
        width, height = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
        _IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=_IMAGE_CACHE, suffix=".jpg", delete=False) as tmp:
            output = Path(tmp.name)
        try:
            filters = (f"[0:v]scale={width}:{height}:flags=lanczos,format=rgba[fg];"
                       f"color=c=white:s={width}x{height},format=rgba[bg];"
                       "[bg][fg]overlay=shortest=1:format=auto,format=yuvj420p[out]")
            # FFmpeg uses qscale rather than percentage quality; q=3 is approximately JPEG 85.
            media._run([_local_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(path), "-filter_complex", filters, "-map", "[out]", "-frames:v", "1",
                        "-q:v", "3", str(output)], 120)
            if not output.stat().st_size:
                raise ValueError("empty JPEG")
            output.replace(cached)  # Atomic publication: parallel callers never see a partial JPEG.
        finally:
            output.unlink(missing_ok=True)
        return cached
    except (media.MediaError, OSError, ValueError, KeyError, IndexError) as exc:
        raise LLMError(f"参考图片处理失败：{exc}") from None


def image_part(path: Path, caption: str | None = None) -> list[dict]:
    data = _prepare_image(path).read_bytes()
    mime = _image_mime(data)
    b64 = base64.b64encode(data).decode()
    parts = []
    if caption:
        parts.append({"type": "text", "text": caption})
    parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    return parts


def _request_images(body: dict) -> list[tuple[dict, str]]:
    """Return mutable image fields from both OpenAI and Gemini wire formats (never videos)."""
    images = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url" and isinstance(part.get("image_url"), dict):
                    images.append((part["image_url"], "url"))
    for content in body.get("contents", []):
        for part in content.get("parts", []):
            inline = part.get("inline_data")
            if isinstance(inline, dict) and inline.get("mime_type", "").startswith("image/"):
                images.append((inline, "data"))
    return images


def _smaller_inline_image(encoded: str) -> str:
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise LLMError("参考图片编码无效，请重新选择参考图") from None
    _IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=_IMAGE_CACHE) as folder:
        source = Path(folder) / "source"
        source.write_bytes(data)
        return base64.b64encode(_prepare_image(source, 1024, force=True).read_bytes()).decode()


def _request_data(body: dict) -> tuple[bytes, int]:
    """Measure actual UTF-8 wire bytes, then reduce largest images only until it fits."""
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    count = len(_request_images(body))
    # Gemini whole-video analysis has its own existing proxy limit in pipeline;
    # the image-channel limit must not force that path to fall back to frame grids.
    video_only = not count and any(
        part.get("inline_data", {}).get("mime_type", "").startswith("video/")
        for content in body.get("contents", []) for part in content.get("parts", []))
    if len(payload) <= _REQUEST_LIMIT or video_only:
        return payload, count
    # A JSON copy also separates aliased parts, without mutating the caller's retry history.
    prepared = json.loads(payload)
    images = sorted(_request_images(prepared), key=lambda item: len(item[0][item[1]]), reverse=True)
    size, converted = len(payload), {}
    for field, key in images:
        value = field[key]
        if key == "url":
            head, sep, encoded = value.partition(",")
            if not sep or not head.startswith("data:image/") or not head.endswith(";base64"):
                continue  # Remote references are counted, but never fetched locally.
        else:
            encoded = value
        if encoded not in converted:
            converted[encoded] = _smaller_inline_image(encoded)
        smaller = converted[encoded]
        replacement = "data:image/jpeg;base64," + smaller if key == "url" else smaller
        saving = len(value) - len(replacement)
        if key == "data":
            saving += len(field["mime_type"]) - len("image/jpeg")
        if saving > 0:
            field[key] = replacement
            if key == "data":
                field["mime_type"] = "image/jpeg"
            size -= saving
        if size <= _REQUEST_LIMIT:
            break
    payload = json.dumps(prepared, ensure_ascii=False).encode("utf-8")
    if len(payload) > _REQUEST_LIMIT:
        raise LLMError(f"图片太多或太大（约 {len(payload) / 1_000_000:.2f} MB），超过模型渠道的请求上限，请减少参考图",
                       kind='too_large', error_detail=dict(http_status=None, error_type='local_request_limit',
                           message='图片太多或太大，超过模型渠道的请求上限', image_count=count, request_bytes=len(payload)))
    return payload, count


_BUNDLED_CA = config.JIMMY_SCRIPTS.parent / "vendor" / "certifi" / "cacert.pem"


def _ssl_context() -> ssl.SSLContext:
    """python.org builds on macOS often ship without root certs; add the CA bundle vendored in the Jimmy skill."""
    ctx = ssl.create_default_context()
    if _BUNDLED_CA.exists():
        ctx.load_verify_locations(cafile=str(_BUNDLED_CA))
    return ctx


def video_part(path: Path) -> dict:
    """Whole-video input (Gemini via Jimmy only)."""
    return {"type": "video_file", "path": str(path)}


def _post_official(url: str, body: dict, api_key: str, timeout: float) -> dict:
    from .gateway.adapters.official import post
    return post(url, body, api_key, timeout)


def _post(url: str, body: dict, api_key: str, timeout: float, provider: str) -> dict:
    if provider == "openai":
        return _post_official(url, body, api_key, timeout)
    from .gateway.adapters.common import post
    return post(url, body, api_key, timeout, provider)


def _jimmy_dns_shim() -> None:
    """Same per-process DNS shim the Jimmy skill uses for fake-IP proxies (only api/cdn.viraltok.ai)."""
    import sys
    scripts = str(config.JIMMY_SCRIPTS)
    if scripts not in sys.path:
        sys.path.append(scripts)
    try:
        from runtime_dns_patch import install_runtime_dns_patch  # type: ignore
        install_runtime_dns_patch()
    except Exception:
        pass


def _to_gemini_parts(messages: list[dict]) -> list[dict]:
    parts: list[dict] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            prefix = "【系统指令】\n" if m["role"] == "system" else ""
            parts.append({"text": prefix + content})
            continue
        for c in content:
            if c.get("type") == "text":
                parts.append({"text": c["text"]})
            elif c.get("type") == "image_url":
                url = c["image_url"]["url"]
                head, _, data = url.partition(",")
                mime = head[5:].split(";")[0] if head.startswith("data:") else "image/jpeg"
                parts.append({"inline_data": {"mime_type": mime, "data": data}})
            elif c.get("type") == "video_file":
                data = base64.b64encode(Path(c["path"]).read_bytes()).decode()
                parts.append({"inline_data": {"mime_type": "video/mp4", "data": data}})
    return parts


def _chat_jimmy(s: dict, messages: list[dict], *, max_tokens: int) -> dict:
    from .gateway.adapters.third_party import chat_gemini
    return chat_gemini(s, messages, max_tokens=max_tokens)


def chat(provider: str, messages: list[dict], *, temperature: float = 0.4, max_tokens: int = 8000,
         model: str | None = None) -> dict:
    from . import gateway
    return gateway.reason("script", messages, override=provider, _compat=True,
                          temperature=temperature, max_tokens=max_tokens, model=model)


def _chat(provider: str, messages: list[dict], **kw) -> dict:
    from .gateway.reasoning import _chat as implementation
    return implementation(provider, messages, **kw)


def extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise JSONResponseError("模型没有返回 JSON")
    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except ValueError:
        # common slip: trailing commas
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(cleaned)
        except ValueError as exc:
            raise JSONResponseError(f"模型返回的 JSON 不合法：{exc}") from None


def chat_json(provider: str, messages: list[dict], **kw) -> tuple[dict, dict]:
    from . import gateway
    return gateway.reason("script", messages, override=provider, json=True, _compat=True, **kw)


def chat_json_checked(provider: str, messages: list[dict], **kw) -> tuple[dict, dict]:
    from . import gateway
    return gateway.reason_checked("check", messages, override=provider, _compat=True, **kw)
