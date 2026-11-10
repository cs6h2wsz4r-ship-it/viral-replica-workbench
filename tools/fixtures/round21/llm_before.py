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


class LLMError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(user_message(message))


class ReasoningExhausted(LLMError):
    pass


class EmptyResponse(LLMError):
    pass


class JSONResponseError(LLMError):
    pass


class CheckedLLMError(LLMError):
    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        detail = "；".join(f"第 {i} 次（{a['provider']} / {a['model']}，上限 {a['max_tokens']}）：{a['reason']}"
                           for i, a in enumerate(attempts, 1))
        hint = "；可选择“改用三方渠道重试”" if any(a['provider'] == 'openai' for a in attempts) else ""
        super().__init__(f"模型校验未完成：{detail}{hint}")


OFFICIAL_RETRY_DELAYS = (5, 15, 30)


def _official_error_fields(detail: str) -> dict:
    try:
        data = json.loads(detail)
    except (ValueError, TypeError):
        return {}
    error = data.get("error", {}) if isinstance(data, dict) else {}
    return error if isinstance(error, dict) else {}


def official_retryable(status: int, detail: str) -> bool:
    """Shared HTTP policy for official chat and image generation."""
    quota = _official_error_fields(detail).get("code") == "insufficient_quota"
    return (status == 429 and not quota) or 500 <= status < 600


def official_error(status: int, detail: str) -> str:
    if status in (401, 403):
        message = "官方 key 无效或没有权限，请在设置里检查 OPENAI_API_KEY"
    elif status == 429 and _official_error_fields(detail).get("code") == "insufficient_quota":
        message = "官方额度不足"
    else:
        reason = _official_error_fields(detail).get("message") or detail
        message = f"官方返回 HTTP {status}：{str(reason)[:500]}"
    return user_message(message) + "；可选择“改用三方渠道重试”"


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
        raise LLMError(f"图片太多或太大（约 {len(payload) / 1_000_000:.2f} MB），超过模型渠道的请求上限，请减少参考图")
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
    """Official-only retries; compatibility channels retain their wire format."""
    payload, _ = _request_data(body)
    context = _ssl_context()
    attempt = 0
    temperature_removed = False
    while True:
        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except ValueError:
                raise LLMError(f"官方返回的不是 JSON：{raw[:300]}；可选择“改用三方渠道重试”") from None
            if isinstance(data, dict) and data.get("error"):
                raise LLMError(f"官方返回错误：{data['error']}；可选择“改用三方渠道重试”")
            return data
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            exc.close()
            error = _official_error_fields(detail)
            if (not temperature_removed and "temperature" in body and error.get("param") == "temperature"
                    and error.get("code") in {"unsupported_parameter", "unsupported_value"}):
                body = {key: value for key, value in body.items() if key != "temperature"}
                payload, _ = _request_data(body)
                temperature_removed = True
                continue  # Parameter negotiation does not consume a failure or backoff slot.
            if attempt == len(OFFICIAL_RETRY_DELAYS) or not official_retryable(exc.code, detail):
                raise LLMError(official_error(exc.code, detail)) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if attempt == len(OFFICIAL_RETRY_DELAYS):
                raise LLMError(f"官方连接失败：{exc}；可选择“改用三方渠道重试”") from None
        time.sleep(OFFICIAL_RETRY_DELAYS[attempt])
        attempt += 1


def _post(url: str, body: dict, api_key: str, timeout: float, provider: str) -> dict:
    if provider == "openai":
        return _post_official(url, body, api_key, timeout)
    payload, image_count = _request_data(body)
    diagnostics = f"（本次请求约 {len(payload) / 1_000_000:.2f} MB，图片 {image_count} 张）"
    req = urllib.request.Request(
        url, data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    delays = (5, 15, 30)
    context = _ssl_context()
    for attempt in range(len(delays) + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except ValueError:
                raise LLMError(f"{provider} 返回的不是 JSON：{raw[:300]}") from None
            if isinstance(data, dict) and "code" in data and not any(k in data for k in ("choices", "candidates")):
                code = str(data.get("code"))
                msg = str(data.get("msg") or data.get("message") or "")
                is_error = code not in {"0", "200", "None"}
                if is_error:
                    permanent = any(word in msg.lower() for word in ("参数", "鉴权", "余额", "unauthorized", "invalid", "balance", "authentication", "insufficient")) or code in {"400", "401", "402", "403"}
                    transient = not permanent and (code.startswith("5") or any(word in msg.lower() for word in ("上游请求失败", "upstream", "timeout", "超时", "繁忙", "busy", "rate limit", "稍后重试")))
                    if transient and provider == "jimmy":
                        if attempt == len(delays):
                            raise LLMError("三方渠道对话接口临时故障（上游请求失败），已自动重试 3 次。这一步只是起草提示词，还没有生图、没有扣生图费，可以稍后再点。")
                        time.sleep(delays[attempt])
                        continue
                    raise LLMError(f"{provider} 返回错误：{msg or code}{diagnostics if '参数' in msg else ''}")
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            exc.close()
            if "参数格式错误" in detail:
                raise LLMError(f"{provider} 返回 HTTP {exc.code}: 参数格式错误{diagnostics}") from None
            if attempt == len(delays) or not (exc.code == 429 or 500 <= exc.code < 600):
                raise LLMError(f"{provider} 返回 HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if attempt == len(delays):
                raise LLMError(f"{provider} 连接失败：{exc}") from None
        time.sleep(delays[attempt])
    try:
        data = json.loads(raw)
    except ValueError:
        raise LLMError(f"{provider} 返回的不是 JSON：{raw[:300]}") from None
    if isinstance(data, dict) and "参数格式错误" in str(data.get("msg") or data.get("message") or ""):
        raise LLMError(f"{provider} 返回错误：参数格式错误{diagnostics}")
    return data


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
    """Jimmy /images/understand (Gemini-native contents/parts, wrapped in {code,msg,data})."""
    _jimmy_dns_shim()
    body = {"model": s["model"], "contents": [{"role": "user", "parts": _to_gemini_parts(messages)}],
            "generationConfig": {"maxOutputTokens": max_tokens}}
    started = time.time()
    data = _post(s["base_url"] + "/images/understand", body, s["api_key"], s["timeout"], "三方渠道 Gemini")
    if str(data.get("code")) not in {"0", "200", "20000"}:
        raise LLMError(f"三方渠道 Gemini 返回错误：{data.get('msg') or data.get('message') or data}")
    inner = data.get("data") or {}
    try:
        cand = inner["candidates"][0]
        text = "".join(p.get("text", "") for p in cand["content"]["parts"] if isinstance(p, dict))
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"三方渠道 Gemini 返回格式无法解析：{json.dumps(inner, ensure_ascii=False)[:300]}") from None
    if not text.strip():
        raise EmptyResponse(f"模型返回了空回复（三方渠道 Gemini，finishReason={cand.get('finishReason')}）")
    um = inner.get("usageMetadata") or data.get("usage") or {}
    usage = {"prompt_tokens": um.get("promptTokenCount", um.get("prompt_tokens")),
             "completion_tokens": um.get("candidatesTokenCount", um.get("completion_tokens"))}
    return {"content": text, "usage": usage, "model": inner.get("modelVersion") or s["model"],
            "seconds": round(time.time() - started, 1), "provider": "jimmy"}


def chat(provider: str, messages: list[dict], *, temperature: float = 0.4, max_tokens: int = 8000,
         model: str | None = None) -> dict:
    try:
        return _chat(provider, messages, temperature=temperature, max_tokens=max_tokens, model=model)
    except (LLMError, ValueError) as exc:
        if provider == "openai" and "改用三方渠道重试" not in str(exc):
            raise type(exc)(str(exc) + "；可选择“改用三方渠道重试”") from None
        raise


def _chat(provider: str, messages: list[dict], *, temperature: float, max_tokens: int,
          model: str | None) -> dict:
    s = config.provider_settings(provider)
    if model:
        s = {**s, "model": model}
    if provider == "jimmy":
        if s["model"].lower().startswith("gemini"):
            return {**_chat_jimmy(s, messages, max_tokens=max_tokens), "requested_model": s["model"]}
        _jimmy_dns_shim()
    if any(isinstance(m["content"], list) and any(c.get("type") == "video_file" for c in m["content"]) for m in messages):
        raise LLMError("只有三方渠道 Gemini 支持直接发送整段视频")
    body = {"model": s["model"], "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if provider == "openai":
        body = {"model": s["model"], "messages": messages, "max_completion_tokens": max_tokens}
        if s.get("reasoning_effort"):
            body["reasoning_effort"] = s["reasoning_effort"]
    started = time.time()
    data = _post(s["base_url"] + "/chat/completions", body, s["api_key"], s["timeout"], provider)
    if isinstance(data, dict) and not data.get("choices") and ("code" in data or "msg" in data):
        raise LLMError(f"{provider} 返回错误：{data.get('msg') or data.get('message') or data.get('code')}")
    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):  # some providers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError(f"{provider} 返回格式无法解析：{json.dumps(data, ensure_ascii=False)[:300]}") from None
    if content is None or (isinstance(content, str) and not content.strip()):
        usage = data.get("usage") or {}
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        if reasoning >= max_tokens * 0.9 or data["choices"][0].get("finish_reason") == "length":
            raise ReasoningExhausted(f"模型（{s['model']}）把 {reasoning} 个 token 全部用于推理，没有输出正文（上限 {max_tokens}）")
        raise EmptyResponse("模型返回了空回复")
    return {"content": content, "usage": data.get("usage") or {}, "model": data.get("model") or s["model"],
            "seconds": round(time.time() - started, 1), "provider": provider, "requested_model": s["model"]}


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
    """Ask for JSON; on a parse failure, ask the model once to repair its own output."""
    result = chat(provider, messages, **kw)
    if not (result.get("content") or "").strip():
        raise EmptyResponse("模型返回了空回复")
    try:
        return extract_json(result["content"]), result
    except JSONResponseError:
        repair = messages + [
            {"role": "assistant", "content": result["content"]},
            {"role": "user", "content": "上面的输出不是合法 JSON。请只输出修正后的完整 JSON 对象，不要任何解释。"},
        ]
        result2 = chat(provider, repair, **kw)
        if not (result2.get("content") or "").strip():
            raise EmptyResponse("模型返回了空回复")
        try:
            return extract_json(result2["content"]), result2
        except JSONResponseError as exc:
            if provider == "openai":
                raise JSONResponseError(str(exc) + "；可选择“改用三方渠道重试”") from None
            raise


def chat_json_checked(provider: str, messages: list[dict], *, analysis: dict | None = None,
                      model: str | None = None, max_tokens: int = 8000, **kw) -> tuple[dict, dict]:
    """Validation-only retry budgets; chat_json retains its nonempty JSON repair.

    Resolve fallback settings lazily so a healthy first call never needs another
    provider's credentials. Attempts describe validation stages, not HTTP retries.
    """
    analysis = analysis or {}
    attempts = []
    initial_model = model
    for index, budget in enumerate((max_tokens, min(max_tokens * 4, 32000), max_tokens * 2)):
        current_provider, current_model = provider, initial_model
        if index == 2:
            # Official failures never authorize a different channel, including
            # old projects whose analysis was recorded on a third-party model.
            if provider == "openai" and analysis.get("provider") != "openai":
                break
            current_provider = analysis.get("provider") or "jimmy"
            current_model = (analysis.get("request_model") or analysis.get("model")
                             or ((analysis.get("steps") or {}).get("overview") or {}).get("output", {}).get("model"))
        try:
            if not current_model:
                current_model = config.provider_settings(current_provider)["model"]
            if index == 0:
                initial_model = current_model
            if index == 2 and current_model == initial_model:
                break
            value, meta = chat_json(current_provider, messages, model=current_model, max_tokens=budget, **kw)
        except (LLMError, ValueError) as exc:
            reason = ("推理占满上限：" if isinstance(exc, ReasoningExhausted) else "") + str(exc)
            attempts.append({"provider": current_provider, "model": current_model or "未能读取模型配置",
                             "max_tokens": budget, "success": False, "reason": reason})
            if current_provider == "openai" and not isinstance(exc, (ReasoningExhausted, EmptyResponse, JSONResponseError)):
                break  # HTTP retries already exhausted, or configuration/authentication needs user action.
            continue
        actual_model = meta.get("model") or current_model
        attempts.append({"provider": current_provider, "model": actual_model,
                         "max_tokens": budget, "success": True, "reason": "完成校验"})
        return value, {**meta, "provider": current_provider, "model": actual_model, "attempts": attempts}
    raise CheckedLLMError(attempts)
