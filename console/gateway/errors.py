"""Stable, sanitized gateway failures, also exported through the legacy clients."""
from __future__ import annotations

import re
from ..channel_text import technical_detail, user_message

KINDS = {'auth', 'quota', 'transient', 'invalid_request', 'reasoning_exhausted', 'empty', 'unknown', 'too_large'}


def sanitize(value: object) -> str:
    text = technical_detail(value)
    text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[redacted]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|authorization|access_token|secret)\s*[=:]\s*[\"\']?)[^\s,\"\'}]+', r'\1[redacted]', text)
    return re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)


def diagnostic_message(value):
    """Keep a short explanation, never an echoed request or credential section."""
    text = str(value or '')
    text = re.split(r'(?i)\b(?:proxy-authorization|authorization)\b|\b(?:headers?|cookies?|request|payload|body|messages|contents|inline_data)["\x27]?\s*[=:{]|data:image/', text, maxsplit=1)[0]
    # Embedded JSON/repr is usually an echoed payload; retain only its prose prefix.
    text = re.split(r'[\[{]', text, maxsplit=1)[0]
    text = re.sub(r'https?://\S+', '[redacted-url]', text)
    text = re.sub(r'(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\b["\x27]?\s*[:=]\s*["\x27]?[^\s,;"\x27}]+', '[redacted]', text)
    text = re.sub(r'(?i)bearer\s+\S+', '[redacted]', text)
    text = re.sub(r'[A-Za-z0-9+/=_-]{80,}', '[redacted]', text)
    return sanitize(text).strip()[:200]


def diagnostic_detail(exc=None, *, status=None, detail=None, image_count=None, request_bytes=None):
    """A strict allowlist used from the transport through checked-error wrapping."""
    from .retry import error_fields
    saved = getattr(exc, 'error_detail', {}) or {}
    fields = error_fields(detail) if detail is not None else {}
    envelope = detail if isinstance(detail, dict) else {}
    status = saved.get('http_status', status or getattr(exc, 'code', None))
    if type(status) is not int or not 100 <= status <= 599:
        match = re.search(r'HTTP\s*(\d{3})', str(exc or ''))
        status = int(match.group(1)) if match else None
    kind = saved.get('error_type') or fields.get('type') or fields.get('code') or envelope.get('code') or ''
    kind = str(kind)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', kind) or kind.startswith('sk-'):
        kind = ''
    message = (saved.get('message') or fields.get('message') or envelope.get('msg') or envelope.get('message')
               or (detail if isinstance(detail, str) and not detail.lstrip().startswith(('{', '[')) else '')
               or str(exc or ''))
    return dict(http_status=status, error_type=kind, message=diagnostic_message(message),
                image_count=saved.get('image_count') if type(saved.get('image_count')) is int else image_count,
                request_bytes=saved.get('request_bytes') if type(saved.get('request_bytes')) is int else request_bytes)


def channel_name(channel: str) -> str:
    return {'jimmy': 'third_party', '三方渠道 Gemini': 'third_party', 'openai': 'official'}.get(channel, sanitize(channel))


class GatewayError(RuntimeError):
    def __init__(self, message: str, *, kind: str = 'unknown', channel: str = '',
                 model: str = '', technical: str = '', retryable_elsewhere: bool = False, error_detail=None):
        self.kind = kind if kind in KINDS else 'unknown'
        self.channel = channel_name(channel)
        self.model = sanitize(model)
        self.message = user_message(sanitize(message).replace('third-party', '三方渠道'))
        if not re.search(r'[\u4e00-\u9fff]', self.message):
            self.message = '模型请求失败：' + self.message
        self.technical = sanitize(technical or message)
        self.retryable_elsewhere = bool(retryable_elsewhere)
        if error_detail is not None:
            self.error_detail = error_detail
        super().__init__(self.message)


class LLMError(GatewayError):
    pass


class ReasoningExhausted(LLMError):
    def __init__(self, message: str, **kw):
        super().__init__(message, **{'kind': 'reasoning_exhausted', **kw})


class EmptyResponse(LLMError):
    def __init__(self, message: str, **kw):
        super().__init__(message, **{'kind': 'empty', **kw})


class JSONResponseError(LLMError):
    def __init__(self, message: str, **kw):
        super().__init__(message, **{'kind': 'invalid_request', **kw})


class CheckedLLMError(LLMError):
    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        last = attempts[-1] if attempts else {}
        if (last.get('error_detail') or {}).get('error_type') == 'local_request_limit':
            # No model response exists: changing channels or output token limits
            # cannot fix a shared, local request-size guard.
            super().__init__(last['reason'], kind='too_large', channel=last.get('provider', ''),
                             model=last.get('model', ''), error_detail=last['error_detail'])
            return
        detail = '；'.join(f"第 {i} 次（{a['provider']} / {a['model']}，上限 {a['max_tokens']}）：{a['reason']}"
                          for i, a in enumerate(attempts, 1))
        official = any(a['provider'] == 'openai' for a in attempts)
        hint = '；可选择“改用三方渠道重试”' if official else ''
        super().__init__(f'模型校验未完成：{detail}{hint}', kind=last.get('kind', 'unknown'),
                         channel=last.get('provider', ''), model=last.get('model', ''), retryable_elsewhere=official,
                         error_detail=last.get('error_detail'))


class ImageError(GatewayError):
    pass


def classify_error(exc: object) -> str:
    """Classify legacy failures without exposing their provider diagnostics."""
    from .retry import classify
    text = str(exc)
    detail = getattr(exc, 'error_detail', {}) or {}
    kind = classify(detail.get('http_status') or getattr(exc, 'code', 0) or 0,
                    text + ' ' + str(detail.get('message') or '') + ' ' + str(getattr(exc, 'technical', '')))
    if kind == 'too_large':
        return kind
    if isinstance(exc, GatewayError) and exc.kind != 'unknown':
        return exc.kind
    if kind != 'unknown':
        return kind
    if (isinstance(exc, (TimeoutError, ConnectionError)) or '连接失败' in text
            or re.search(r'(?i)connection (?:reset|aborted|refused|closed)|remote end closed|disconnected|broken pipe|network is unreachable|timed out|HTTP\s*5\d\d', text)):
        return 'transient'
    if '空回复' in text:
        return 'empty'
    if '推理' in text and ('上限' in text or '正文' in text):
        return 'reasoning_exhausted'
    if isinstance(exc, ValueError) or 'JSON' in text or '参数' in text:
        return 'invalid_request'
    return 'unknown'


def official_error(status: int, detail: str) -> str:
    """Round 20 official wording, shared by inference and image adapters."""
    from .retry import error_fields
    if status in (401, 403):
        message = "官方 key 无效或没有权限，请在设置里检查 OPENAI_API_KEY"
    elif status == 429 and error_fields(detail).get("code") == "insufficient_quota":
        message = "官方额度不足"
    else:
        reason = error_fields(detail).get("message") or detail
        message = f"官方返回 HTTP {status}：{str(reason)[:500]}"
    return user_message(message) + "；可选择“改用三方渠道重试”"
