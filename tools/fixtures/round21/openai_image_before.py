"""Standard-library OpenAI image edits transport; callers own the confirmation gate."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from . import config, llm, store


class ImageError(RuntimeError):
    pass


def image_size(ratio: str) -> str:
    try:
        width, height = map(float, ratio.split(':'))
        value = width / height
        if not math.isfinite(value) or width <= 0 or height <= 0:
            raise ValueError()
    except (ValueError, ZeroDivisionError, AttributeError):
        raise ImageError('生图比例无效，请使用宽:高格式') from None
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
            raise ImageError('参考图哈希校验失败，预览已过期，请重新起草')
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


def generate(preview: dict, output: Path, *, before_send: Callable[[], None] | None = None) -> dict:
    """Submit one confirmed edit. Only explicit HTTP throttling/server errors retry.

    A timeout or disconnected response has an uncertain charge outcome and never
    resubmits here. Recovery is strictly local and is implemented by the bridge.
    """
    values = config.load()
    key = values.get('OPENAI_API_KEY', '').strip()
    if not key:
        raise ImageError('官方 key 无效或没有权限，请在设置里检查 OPENAI_API_KEY；改用三方渠道重试')
    url = (values.get('OPENAI_BASE_URL') or 'https://api.openai.com/v1').rstrip('/') + '/images/edits'
    body, content_type = _multipart(preview)
    request = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': content_type, 'Authorization': 'Bearer ' + key})
    payload = None
    for attempt in range(len(llm.OFFICIAL_RETRY_DELAYS) + 1):
        if before_send:
            before_send()
        try:
            with urllib.request.urlopen(request, timeout=300, context=llm._ssl_context()) as response:
                payload = json.loads(response.read())
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            if llm.official_retryable(exc.code, detail) and attempt < len(llm.OFFICIAL_RETRY_DELAYS):
                time.sleep(llm.OFFICIAL_RETRY_DELAYS[attempt])
                continue
            raise ImageError(llm.official_error(exc.code, detail)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise ImageError('官方生图请求未完成，可能已计费，请检查本地结果；改用三方渠道重试') from exc
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
        raise ImageError('官方已响应，但图片结果无法读取；请检查结果后再决定是否改用三方渠道重试') from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix('.tmp')
    temp.write_bytes(content)
    temp.replace(output)
    return {'output_sha256': store.sha256_file(output), 'usage': payload.get('usage') or {}}
