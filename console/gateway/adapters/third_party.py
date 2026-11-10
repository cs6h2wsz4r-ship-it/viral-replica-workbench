"""Third-party OpenAI-compatible and Gemini-native reasoning adapters."""
from __future__ import annotations
import json
import time
from ... import llm
from ..errors import LLMError, EmptyResponse
from .common import reason_body, post


def chat_gemini(s: dict, messages: list[dict], *, max_tokens: int) -> dict:
    """Jimmy /images/understand (Gemini-native contents/parts, wrapped in {code,msg,data})."""
    llm._jimmy_dns_shim()
    body = {"model": s["model"], "contents": [{"role": "user", "parts": llm._to_gemini_parts(messages)}],
            "generationConfig": {"maxOutputTokens": max_tokens}}
    started = time.time()
    data = llm._post(s["base_url"] + "/images/understand", body, s["api_key"], s["timeout"], "三方渠道 Gemini")
    if str(data.get("code")) not in {"0", "200", "20000"}:
        raise LLMError(f"三方渠道 Gemini 返回错误：{data.get('msg') or data.get('message') or data}")
    inner = data.get("data") or {}
    try:
        cand = inner["candidates"][0]
        text = "".join(p.get("text", "") for p in cand["content"]["parts"] if isinstance(p, dict))
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"三方渠道 Gemini 返回格式无法解析：{json.dumps(inner, ensure_ascii=False)[:300]}") from None
    if not text.strip():
        error = EmptyResponse(f"模型返回了空回复（三方渠道 Gemini，finishReason={cand.get('finishReason')}）")
        error.raw_content = text
        raise error
    um = inner.get("usageMetadata") or data.get("usage") or {}
    usage = {"prompt_tokens": um.get("promptTokenCount", um.get("prompt_tokens")),
             "completion_tokens": um.get("candidatesTokenCount", um.get("completion_tokens"))}
    return {"content": text, "usage": usage, "model": inner.get("modelVersion") or s["model"],
            "seconds": round(time.time() - started, 1), "provider": "jimmy"}



def image_preview(*args, **kw):
    from ... import jimmy_bridge
    return jimmy_bridge.image_preview(*args, **kw)


def image_confirm(*args, **kw):
    from ... import jimmy_bridge
    return jimmy_bridge.image_confirm(*args, **kw)


def image_resume(*args, **kw):
    from ... import jimmy_bridge
    return jimmy_bridge.image_resume(*args, **kw)


def video(action, *args, **kw):
    from ... import jimmy_bridge
    return getattr(jimmy_bridge, action)(*args, **kw)
