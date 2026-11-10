"""One backoff and classification policy; no automatic channel switching."""
from __future__ import annotations
import json
import re

DELAYS = (5, 15, 30)


def error_fields(detail: str | dict) -> dict:
    try:
        data = json.loads(detail) if isinstance(detail, str) else detail
    except (ValueError, TypeError):
        return {}
    error = data.get('error', {}) if isinstance(data, dict) else {}
    return error if isinstance(error, dict) else {}


def classify(status: int, detail: str | dict = '') -> str:
    fields = error_fields(detail)
    text = str(detail).lower()
    code = str(fields.get('code') or fields.get('type') or '').lower()
    if (status == 413 or re.search(r'http\s*413|(?:request|payload|entity|body|content|image).{0,35}too[ _](?:large|big)|too[ _]many[ _](?:images|pictures)|(?:max(?:imum)?|limit).{0,35}(?:number of images|image count|images allowed)|image_limit_exceeded|(?:request|payload|body).{0,25}exceed.{0,25}(?:size|limit)|(?:request|payload|image).{0,25}(?:size|count).{0,25}(?:exceed|limit)|请求(?:体)?.{0,8}(?:过大|太大)|图片.{0,8}(?:太多|太大|过多)|超过模型渠道的请求上限', text)):
        return 'too_large'
    if any(word in text for word in ('insufficient_quota', 'insufficient_balance', '额度不足', '余额不足', 'balance exhausted', 'quota exceeded')):
        return 'quota'
    if status in (401, 403) or code in {'invalid_api_key', 'authentication_error'} or any(word in text for word in ('unauthorized', 'authentication', '鉴权', '没有权限', 'key 无效')):
        return 'auth'
    if status == 402 or any(word in text for word in ('insufficient', '余额', 'quota')):
        return 'quota'
    if status in (400, 404, 422) or any(word in text for word in ('参数格式错误', 'unsupported_parameter', 'invalid_request', 'invalid parameter')):
        return 'invalid_request'
    if status == 429 or 500 <= status < 600 or code in {'server_error', 'rate_limit_exceeded', 'overloaded_error'}:
        return 'transient'
    if any(word in text for word in ('上游请求失败', 'upstream', 'timeout', '超时', '繁忙', 'busy', 'rate limit', '稍后重试', 'temporarily', 'temporary', 'overloaded')):
        return 'transient'
    return 'unknown'


def retryable(status: int, detail: str | dict = '') -> bool:
    return classify(status, detail) == 'transient'
