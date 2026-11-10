"""Official OpenAI reasoning and confirmed image edit transports."""
from __future__ import annotations
import base64
import hashlib
import http.client
import json
import math
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable
from ... import config, llm, store
from ..errors import LLMError, ImageError, diagnostic_detail
from ..retry import DELAYS, classify, retryable
from ..usage import capture_response


def reason_body(settings, messages, *, temperature, max_tokens):
    body = {"model": settings["model"], "messages": messages, "max_completion_tokens": max_tokens}
    if settings.get("reasoning_effort"):
        body["reasoning_effort"] = settings["reasoning_effort"]
    return body


def post(url: str, body: dict, api_key: str, timeout: float) -> dict:
    """Official-only retries; compatibility channels retain their wire format."""
    payload, image_count = llm._request_data(body)
    context = llm._ssl_context()
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
                raise LLMError(f"官方返回的不是 JSON：{raw[:300]}；可选择“改用三方渠道重试”", error_detail=diagnostic_detail(status=200, detail='服务返回非 JSON', image_count=image_count, request_bytes=len(payload))) from None
            capture_response(data, channel="openai", model=body.get("model"))
            if isinstance(data, dict) and data.get("error"):
                if retryable(200, data) and attempt < len(DELAYS):
                    time.sleep(DELAYS[attempt])
                    attempt += 1
                    continue
                kind = classify(200, data)
                message = llm.official_error(401 if kind == 'auth' else 429, json.dumps(data)) if kind in {'auth','quota'} else f"官方返回错误：{data['error']}；可选择“改用三方渠道重试”"
                raise LLMError(message, kind=kind, channel="official", model=body.get("model", ""), technical=json.dumps(data), retryable_elsewhere=True,
                               error_detail=diagnostic_detail(status=200, detail=data, image_count=image_count, request_bytes=len(payload)))
            return data
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            exc.close()
            error = llm._official_error_fields(detail)
            if (not temperature_removed and "temperature" in body and error.get("param") == "temperature"
                    and error.get("code") in {"unsupported_parameter", "unsupported_value"}):
                body = {key: value for key, value in body.items() if key != "temperature"}
                payload, image_count = llm._request_data(body)
                temperature_removed = True
                continue  # Parameter negotiation does not consume a failure or backoff slot.
            if attempt == len(DELAYS) or not llm.official_retryable(exc.code, detail):
                raise LLMError(llm.official_error(exc.code, detail), kind=classify(exc.code, detail), channel="official", model=body.get("model", ""), technical=detail, retryable_elsewhere=True,
                               error_detail=diagnostic_detail(status=exc.code, detail=detail, image_count=image_count, request_bytes=len(payload))) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if attempt == len(DELAYS):
                raise LLMError(f"官方连接失败：{exc}；可选择“改用三方渠道重试”", kind="transient", channel="official", model=body.get("model", ""), retryable_elsewhere=True,
                               error_detail=diagnostic_detail(exc, image_count=image_count, request_bytes=len(payload))) from None
        time.sleep(DELAYS[attempt])
        attempt += 1


def image_size(ratio: str) -> str:
    try:
        width, height = map(float, ratio.split(':'))
        value = width / height
        if not math.isfinite(value) or width <= 0 or height <= 0:
            raise ValueError()
    except (ValueError, ZeroDivisionError, AttributeError):
        raise ImageError('生图比例无效，请使用宽:高格式', kind='invalid_request', channel='official') from None
    choices = [('1024x1536', 2 / 3), ('1024x1024', 1.0), ('1536x1024', 1.5)]
    return min(choices, key=lambda item: abs(item[1] - value))[0]


def _multipart(preview: dict) -> tuple[bytes, str]:
    boundary = 'image-' + uuid.uuid4().hex
    chunks = []

    def field(name: str, value: str) -> None:
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

    field('model', preview['model'])
    field('prompt', preview['prompt'])
    for index, reference in enumerate(preview['images'], 1):
        path = Path(reference['path'])
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != reference['sha256']:
            raise ImageError('参考图哈希校验失败，预览已过期，请重新起草', kind='invalid_request', channel='official', model=preview.get('model', ''))
        mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        # Generated names prevent quotes/newlines in local filenames entering headers.
        suffix = mimetypes.guess_extension(mime) or '.bin'
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="reference-{index}{suffix}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
        chunks.extend([content, b'\r\n'])
    field('size', preview['size'])
    field('quality', preview.get('quality') or 'auto')
    field('n', '1')
    chunks.append(f'--{boundary}--\r\n'.encode())
    return b''.join(chunks), 'multipart/form-data; boundary=' + boundary


def image_generate(preview: dict, output: Path, *, before_send: Callable[[], None] | None = None) -> dict:
    """Submit one confirmed edit. Only explicit throttling/server failures retry.

    A timeout or disconnected response has an uncertain charge outcome and never
    resubmits here. Recovery is strictly local and is implemented by the bridge.
    """
    values = config.load()
    key = values.get('OPENAI_API_KEY', '').strip()
    if not key:
        raise ImageError('官方 key 无效或没有权限，请在设置里检查 OPENAI_API_KEY；改用三方渠道重试', kind='auth', channel='official', model=preview.get('model', ''), retryable_elsewhere=True)
    url = (values.get('OPENAI_BASE_URL') or 'https://api.openai.com/v1').rstrip('/') + '/images/edits'
    from ... import openai_image
    body, content_type = openai_image._multipart(preview)
    request = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': content_type, 'Authorization': 'Bearer ' + key})
    payload = None
    for attempt in range(len(DELAYS) + 1):
        if before_send:
            before_send()
        try:
            from ..usage import mark_network_request
            mark_network_request()
            with urllib.request.urlopen(request, timeout=300, context=llm._ssl_context()) as response:
                payload = json.loads(response.read())
            capture_response(payload, channel="official", model=preview.get("model"))
            if isinstance(payload, dict) and payload.get('error'):
                kind = classify(200, payload)
                if kind == 'transient' and attempt < len(DELAYS):
                    time.sleep(DELAYS[attempt])
                    continue
                detail = json.dumps(payload, ensure_ascii=False)
                if kind in {'auth', 'quota'}:
                    message = llm.official_error(401 if kind == 'auth' else 429, detail)
                else:
                    message = f"官方返回错误：{payload['error']}；可选择“改用三方渠道重试”"
                raise ImageError(message, kind=kind, channel='official', model=preview.get('model', ''), retryable_elsewhere=True)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            exc.close()
            if llm.official_retryable(exc.code, detail) and attempt < len(DELAYS):
                time.sleep(DELAYS[attempt])
                continue
            raise ImageError(llm.official_error(exc.code, detail), kind=classify(exc.code, detail), channel="official", model=preview.get("model", ""), technical=detail, retryable_elsewhere=True) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise ImageError('官方生图请求未完成，可能已计费，请检查本地结果；改用三方渠道重试', kind='transient', channel='official', model=preview.get('model', ''), retryable_elsewhere=True) from exc
    try:
        item = payload['data'][0]
        if item.get('b64_json'):
            content = base64.b64decode(item['b64_json'], validate=True)
        elif item.get('url'):
            # Download separately, without forwarding the API credential.
            request = urllib.request.Request(item['url'], method='GET')
            with urllib.request.urlopen(request, timeout=300, context=llm._ssl_context()) as response:
                content = response.read()
        else:
            raise ValueError('missing image')
        if not content:
            raise ValueError('empty image')
    except (KeyError, IndexError, TypeError, ValueError, OSError, urllib.error.URLError) as exc:
        raise ImageError('官方已响应，但图片结果无法读取；请检查结果后再决定是否改用三方渠道重试', kind='empty', channel='official', model=preview.get('model', ''), retryable_elsewhere=True) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix('.tmp')
    temp.write_bytes(content)
    temp.replace(output)
    return {'output_sha256': store.sha256_file(output), 'usage': payload.get('usage') or {}}
