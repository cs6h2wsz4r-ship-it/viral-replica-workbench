"""Reasoning orchestration shared by all gateway channel adapters."""
from __future__ import annotations
import json
import time
from .. import config, llm
from .errors import LLMError, ReasoningExhausted, EmptyResponse, JSONResponseError, CheckedLLMError, classify_error, diagnostic_detail
from .adapters import official, third_party, deepseek, doubao, custom

ADAPTERS = {"openai": official, "jimmy": third_party, "deepseek": deepseek, "doubao": doubao, "custom": custom}

def chat(provider: str, messages: list[dict], *, temperature: float = 0.4, max_tokens: int = 8000,
         model: str | None = None) -> dict:
    try:
        return llm._chat(provider, messages, temperature=temperature, max_tokens=max_tokens, model=model)
    except (LLMError, ValueError) as exc:
        from .errors import channel_name
        if not isinstance(exc, LLMError):
            exc = LLMError(str(exc), kind="auth" if any(word in str(exc) for word in ("还没配置", "还没填")) else "invalid_request")
        exc.channel = channel_name(provider)
        exc.model = exc.model or model or ""
        if provider == "openai":
            exc.retryable_elsewhere = True
            if "改用三方渠道重试" not in str(exc):
                exc.message = str(exc) + "；可选择“改用三方渠道重试”"
                exc.args = (exc.message,)
        raise exc from None


def _chat(provider: str, messages: list[dict], *, temperature: float, max_tokens: int,
          model: str | None) -> dict:
    s = config.provider_settings(provider)
    if model:
        s = {**s, "model": model}
    if provider == "jimmy":
        if s["model"].lower().startswith("gemini"):
            return {**llm._chat_jimmy(s, messages, max_tokens=max_tokens), "requested_model": s["model"]}
        llm._jimmy_dns_shim()
    if any(isinstance(m["content"], list) and any(c.get("type") == "video_file" for c in m["content"]) for m in messages):
        raise LLMError("只有三方渠道 Gemini 支持直接发送整段视频")
    adapter = ADAPTERS.get(provider, custom)
    body = adapter.reason_body(s, messages, temperature=temperature, max_tokens=max_tokens)
    started = time.time()
    data = llm._post(s["base_url"] + "/chat/completions", body, s["api_key"], s["timeout"], provider)
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
            error = ReasoningExhausted(f"模型（{s['model']}）把 {reasoning} 个 token 全部用于推理，没有输出正文（上限 {max_tokens}）", channel=provider, model=s["model"])
        else:
            error = EmptyResponse("模型返回了空回复", channel=provider, model=s["model"])
        error.raw_content = content or ""
        raise error
    return {"content": content, "usage": data.get("usage") or {}, "model": data.get("model") or s["model"],
            "seconds": round(time.time() - started, 1), "provider": provider, "requested_model": s["model"]}


def chat_json(provider: str, messages: list[dict], *, on_response=None, **kw) -> tuple[dict, dict]:
    """Ask for JSON; on a parse failure, ask the model once to repair its own output."""
    def ask(request):
        try:
            result = llm.chat(provider, request, **kw)
        except (EmptyResponse, ReasoningExhausted) as exc:
            # The channel parser rejects empty replies before returning metadata.
            # Preserve that received text before checked retries can replace it.
            if on_response is not None and hasattr(exc, "raw_content"):
                on_response(exc.raw_content)
            raise
        if on_response is not None:
            on_response(result.get("content") or "")
        return result

    result = ask(messages)
    if not (result.get("content") or "").strip():
        raise EmptyResponse("模型返回了空回复")
    try:
        return llm.extract_json(result["content"]), result
    except JSONResponseError:
        repair = messages + [
            {"role": "assistant", "content": result["content"]},
            {"role": "user", "content": "上面的输出不是合法 JSON。请只输出修正后的完整 JSON 对象，不要任何解释。"},
        ]
        if getattr(messages, 'review_budget', False):
            from ..review_request import enforce
            repair = enforce(repair)
        result2 = ask(repair)
        if not (result2.get("content") or "").strip():
            raise EmptyResponse("模型返回了空回复")
        try:
            return llm.extract_json(result2["content"]), result2
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
            value, meta = llm.chat_json(current_provider, messages, model=current_model, max_tokens=budget, **kw)
        except (LLMError, ValueError) as exc:
            reason = ("推理占满上限：" if isinstance(exc, ReasoningExhausted) else "") + str(exc)
            attempts.append({"provider": current_provider, "model": current_model or "未能读取模型配置",
                             "max_tokens": budget, "success": False, "reason": reason, "kind": classify_error(exc),
                             "error_detail": diagnostic_detail(exc)})
            if not isinstance(exc, (ReasoningExhausted, EmptyResponse, JSONResponseError)) and (current_provider == "openai" or classify_error(exc) in {"auth","quota","transient","invalid_request","too_large"}):
                break  # HTTP retries already exhausted, or configuration/authentication needs user action.
            continue
        actual_model = meta.get("model") or current_model
        attempts.append({"provider": current_provider, "model": actual_model,
                         "max_tokens": budget, "success": True, "reason": "完成校验"})
        return value, {**meta, "provider": current_provider, "model": actual_model, "attempts": attempts}
    raise CheckedLLMError(attempts)
