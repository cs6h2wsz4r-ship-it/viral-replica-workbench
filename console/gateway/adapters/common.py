"""OpenAI-compatible request bytes and transport shared by compatible adapters."""
from __future__ import annotations
import http.client
import json
import time
import urllib.error
import urllib.request
from ... import llm
from ..errors import LLMError, diagnostic_detail
from ..retry import DELAYS, classify, retryable
from ..usage import capture_response


def reason_body(settings, messages, *, temperature, max_tokens):
    return {"model": settings["model"], "messages": messages, "temperature": temperature, "max_tokens": max_tokens}


def post(url: str, body: dict, api_key: str, timeout: float, provider: str) -> dict:
    payload, image_count = llm._request_data(body)
    diagnostics = f"（本次请求约 {len(payload) / 1_000_000:.2f} MB，图片 {image_count} 张）"
    req = urllib.request.Request(
        url, data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    delays = DELAYS
    context = llm._ssl_context()
    for attempt in range(len(delays) + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except ValueError:
                raise LLMError(f"{provider} 返回的不是 JSON：{raw[:300]}", error_detail=diagnostic_detail(status=200, detail='服务返回非 JSON', image_count=image_count, request_bytes=len(payload))) from None
            capture_response(data, channel="jimmy" if provider == "三方渠道 Gemini" else provider, model=body.get("model"))
            envelope = data if isinstance(data, dict) else {}
            code = str(envelope.get("code"))
            nested_error = envelope.get("error")
            is_error = bool(nested_error) or ("code" in envelope and code not in {"0", "200", "20000", "None"}
                                              and not any(k in envelope for k in ("choices", "candidates")))
            if is_error:
                msg = str(envelope.get("msg") or envelope.get("message") or nested_error or code)
                kind = classify(int(code) if code.isdigit() else 200, data)
                if kind == "transient":
                    if attempt < len(delays):
                        time.sleep(delays[attempt])
                        continue
                    if provider in {"jimmy", "三方渠道 Gemini"}:
                        raise LLMError("三方渠道对话接口临时故障（上游请求失败），已自动重试 3 次。这一步只是起草提示词，还没有生图、没有扣生图费，可以稍后再点。",
                                       kind=kind, channel=provider, model=body.get("model", ""), error_detail=diagnostic_detail(status=200, detail=data, image_count=image_count, request_bytes=len(payload)))
                raise LLMError(f"{provider} 返回错误：{msg}{diagnostics if '参数' in msg else ''}",
                               kind=kind, channel=provider, model=body.get("model", ""), error_detail=diagnostic_detail(status=200, detail=data, image_count=image_count, request_bytes=len(payload)))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            exc.close()
            if "参数格式错误" in detail and classify(exc.code, detail) != 'too_large':
                raise LLMError(f"{provider} 返回 HTTP {exc.code}: 参数格式错误{diagnostics}", kind="invalid_request", channel=provider, model=body.get("model", ""), error_detail=diagnostic_detail(status=exc.code, detail=detail, image_count=image_count, request_bytes=len(payload))) from None
            if attempt == len(delays) or not retryable(exc.code, detail):
                raise LLMError(f"{provider} 返回 HTTP {exc.code}: {detail[:500]}", kind=classify(exc.code, detail), channel=provider, model=body.get("model", ""), error_detail=diagnostic_detail(status=exc.code, detail=detail, image_count=image_count, request_bytes=len(payload))) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if attempt == len(delays):
                raise LLMError(f"{provider} 连接失败：{exc}", kind="transient", channel=provider, model=body.get("model", ""), error_detail=diagnostic_detail(exc, image_count=image_count, request_bytes=len(payload))) from None
        time.sleep(delays[attempt])
    try:
        data = json.loads(raw)
    except ValueError:
        raise LLMError(f"{provider} 返回的不是 JSON：{raw[:300]}") from None
    if isinstance(data, dict) and "参数格式错误" in str(data.get("msg") or data.get("message") or ""):
        raise LLMError(f"{provider} 返回错误：参数格式错误{diagnostics}")
    return data


