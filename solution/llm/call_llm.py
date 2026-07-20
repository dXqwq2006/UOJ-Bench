"""Shared HTTP adapters for solver model endpoints used by UOJ-Bench."""

from __future__ import annotations

import copy
import math
import os
from typing import Any
from urllib.parse import quote

import requests


__all__ = [
    "assistant_history_message",
    "call_llm",
    "call_llm_details",
    "call_llm_full",
    "generate_messages",
]

TATU_MODELS = {
    "gemini-3.1-pro-preview": "gemini",
    "gpt-5.5": "openai",
    "gpt-5.6-sol": "openai",
    "claude-fable-5": "anthropic",
}

openrouter_url = "https://openrouter.ai/api/v1/chat/completions"


def generate_messages(message):
    if isinstance(message, str):
        return [{"role": "user", "content": message}]
    if not isinstance(message, list):
        raise TypeError("message must be a string or message list")
    return copy.deepcopy(message)


def _redact(value: Any, secret: str | None) -> Any:
    """Return response data safe to persist in benchmark artifacts."""
    if isinstance(value, dict):
        return {key: _redact(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    if isinstance(value, str) and secret:
        return value.replace(secret, "<redacted>")
    return copy.deepcopy(value)


def call_api(message, model, url, api_key):
    """Keep the original OpenRouter request contract."""
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": generate_messages(message)},
    )
    response.raise_for_status()
    return _redact(response.json(), api_key)


def call_openrouter(message, model="openai/gpt-oss-120b"):
    return call_api(message, model, openrouter_url, os.environ.get("OPENROUTER_KEY"))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return min(maximum, max(minimum, value))


def _tatu_temperature() -> float | None:
    raw = os.environ.get("TATU_TEMPERATURE", "").strip()
    if not raw:
        return None
    try:
        temperature = float(raw)
    except ValueError as exc:
        raise ValueError("TATU_TEMPERATURE must be a number") from exc
    if not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise ValueError("TATU_TEMPERATURE must be between 0 and 2")
    return temperature


def _tatu_settings() -> tuple[str, str, int, float | None]:
    key = os.environ.get("TATU_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TATU_API_KEY is required")
    base = os.environ.get("TATU_BASE_URL", "https://maas.tatucloud.com/v1").rstrip("/")
    max_tokens = _env_int("TATU_MAX_OUTPUT_TOKENS", 65536, 1, 65536)
    return key, base, max_tokens, _tatu_temperature()


def _post(url: str, headers: dict[str, str], payload: dict[str, Any], secret: str) -> dict[str, Any]:
    with requests.Session() as session:
        response = session.post(
            url,
            headers=headers,
            json=payload,
            timeout=_env_float("TATU_TIMEOUT_SECONDS", 900, 1, 3600),
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("TATU returned non-object JSON")
    return _redact(data, secret)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            part.get("text", "")
            for part in value
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return "" if value is None else str(value)


def _openai_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = []
    for item in history:
        native = item.get("native_turn")
        if (
            item.get("role") == "assistant"
            and item.get("provider") == "openai"
            and isinstance(native, dict)
        ):
            messages.append(copy.deepcopy(native))
        else:
            messages.append({"role": str(item["role"]), "content": _text(item.get("content"))})
    return messages


def _responses_input(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for item in history:
        native = item.get("native_turn")
        output = native.get("output") if isinstance(native, dict) else None
        if (
            item.get("role") == "assistant"
            and item.get("provider") == "openai-responses"
            and isinstance(output, list)
        ):
            items.extend(copy.deepcopy(output))
        else:
            items.append({"role": str(item["role"]), "content": _text(item.get("content"))})
    return items


def _responses_output_text(raw: dict[str, Any]) -> str:
    parts = []
    output = raw.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "output_text":
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
    if not parts and isinstance(raw.get("output_text"), str):
        parts.append(raw["output_text"])
    if not parts:
        raise RuntimeError("TATU Responses response has no output_text")
    return "".join(parts)


def _responses_reasoning_content(raw: dict[str, Any]) -> str:
    parts = []
    output = raw.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        for key in ("summary", "content"):
            blocks = item.get(key)
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                text = block.get("text") if isinstance(block, dict) else None
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    return "\n\n".join(parts)


def _anthropic_messages(history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    messages = []
    system = []
    for item in history:
        role = item["role"]
        if role in {"system", "developer"}:
            system.append(_text(item.get("content")))
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(f"Anthropic does not support role {role!r}")
        native = item.get("native_turn")
        content = (
            copy.deepcopy(native["content"])
            if role == "assistant"
            and item.get("provider") == "anthropic"
            and isinstance(native, dict)
            and isinstance(native.get("content"), list)
            else [{"type": "text", "text": _text(item.get("content"))}]
        )
        messages.append({"role": role, "content": content})
    return messages, "\n\n".join(filter(None, system))


def _gemini_messages(history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    messages = []
    system = []
    for item in history:
        role = item["role"]
        if role in {"system", "developer"}:
            system.append({"text": _text(item.get("content"))})
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(f"Gemini does not support role {role!r}")
        native = item.get("native_turn")
        parts = (
            copy.deepcopy(native["parts"])
            if role == "assistant"
            and item.get("provider") == "gemini"
            and isinstance(native, dict)
            and isinstance(native.get("parts"), list)
            else [{"text": _text(item.get("content"))}]
        )
        messages.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    return messages, system


def _usage(provider: str, raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    if provider == "openai":
        return copy.deepcopy(raw)
    if provider == "openai-responses":
        prompt = int(raw.get("input_tokens", 0) or 0)
        completion = int(raw.get("output_tokens", 0) or 0)
        usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": int(raw.get("total_tokens", 0) or prompt + completion),
        }
        input_details = raw.get("input_tokens_details")
        if isinstance(input_details, dict):
            usage["prompt_tokens_details"] = copy.deepcopy(input_details)
        output_details = raw.get("output_tokens_details")
        if isinstance(output_details, dict):
            usage["completion_tokens_details"] = copy.deepcopy(output_details)
        return usage
    if provider == "anthropic":
        prompt = int(raw.get("input_tokens", 0) or 0)
        completion = int(raw.get("output_tokens", 0) or 0)
    else:
        prompt = int(raw.get("promptTokenCount", 0) or 0)
        total = int(raw.get("totalTokenCount", 0) or 0)
        if total:
            completion = max(0, total - prompt)
        else:
            completion = int(raw.get("candidatesTokenCount", 0) or 0)
            completion += int(raw.get("thoughtsTokenCount", 0) or 0)
    usage = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
    if provider == "gemini" and "thoughtsTokenCount" in raw:
        usage["reasoning_tokens"] = int(raw.get("thoughtsTokenCount", 0) or 0)
    return usage


def _result(
    model: str,
    provider: str,
    raw: dict[str, Any],
    content: str,
    reasoning: str,
    native_turn: dict[str, Any],
    finish_reason: Any,
    usage: Any,
    request_config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "model": str(raw.get("model") or raw.get("modelVersion") or model),
        "provider": provider,
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": reasoning,
                    "provider": provider,
                    "native_turn": native_turn,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": _usage(provider, usage),
        "request_config": copy.deepcopy(request_config),
        "raw_response": raw,
    }


def _call_openai(history, model, key, base, max_tokens, temperature):
    token_parameter = "max_completion_tokens" if model == "gpt-5.6-sol" else "max_tokens"
    payload = {
        "model": model,
        "messages": _openai_messages(history),
        token_parameter: max_tokens,
        "stream": False,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    requested_effort = os.environ.get("TATU_REASONING_EFFORT", "").strip()
    reasoning_effort = "xhigh" if model == "gpt-5.6-sol" and requested_effort == "max" else requested_effort
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    raw = _post(
        f"{base}/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload,
        key,
    )
    try:
        choice = raw["choices"][0]
        native = copy.deepcopy(choice["message"])
        if not isinstance(native, dict):
            raise TypeError
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("TATU OpenAI response has no assistant message") from exc
    native.setdefault("role", "assistant")
    return _result(
        model,
        "openai",
        raw,
        _text(native.get("content")),
        _text(native.get("reasoning_content")),
        native,
        choice.get("finish_reason"),
        raw.get("usage"),
        {
            "max_output_tokens": max_tokens,
            "max_tokens_parameter": token_parameter,
            "reasoning_effort": reasoning_effort or None,
            "reasoning_effort_requested": requested_effort or None,
            "temperature": temperature,
        },
    )


def _call_openai_responses(history, model, key, base, max_tokens, temperature):
    deployer = os.environ.get("TATU_DEPLOYER", "").strip()
    request_model = model if not deployer or "@" in model else f"{model}@{deployer}"
    requested_effort = os.environ.get("TATU_REASONING_EFFORT", "").strip()
    reasoning_effort = (
        "xhigh"
        if model == "gpt-5.6-sol" and requested_effort == "max"
        else requested_effort
    )
    payload = {
        "model": request_model,
        "input": _responses_input(history),
        "store": False,
        "max_output_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    raw = _post(
        f"{base}/responses",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload,
        key,
    )
    output = raw.get("output")
    if not isinstance(output, list):
        raise RuntimeError("TATU Responses response has no output items")
    incomplete = raw.get("incomplete_details")
    finish_reason = (
        incomplete.get("reason")
        if raw.get("status") == "incomplete" and isinstance(incomplete, dict)
        else raw.get("status")
    )
    return _result(
        model,
        "openai-responses",
        raw,
        _responses_output_text(raw),
        _responses_reasoning_content(raw),
        {"output": copy.deepcopy(output)},
        finish_reason,
        raw.get("usage"),
        {
            "transport": "responses",
            "request_model": request_model,
            "deployer": deployer or None,
            "max_output_tokens": max_tokens,
            "reasoning_effort": reasoning_effort or None,
            "reasoning_effort_requested": requested_effort or None,
            "temperature": temperature,
            "store": False,
        },
    )


def _call_gpt_oss_local(message: Any, base: str) -> dict[str, Any]:
    secret = os.environ.get("GPT_OSS_API_KEY", "").strip()
    key = secret or "local"
    max_tokens = _env_int("GPT_OSS_MAX_OUTPUT_TOKENS", 65536, 1, 65536)
    raw = _post(
        f"{base.rstrip('/')}/chat/completions",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        {
            "model": "gpt-oss-120b",
            "messages": generate_messages(message),
            "max_tokens": max_tokens,
            "stream": False,
        },
        secret,
    )
    try:
        assistant = raw["choices"][0]["message"]
        if not isinstance(assistant, dict):
            raise TypeError
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Local GPT-OSS response has no assistant message") from exc
    assistant.setdefault("role", "assistant")
    assistant["content"] = _text(assistant.get("content"))
    assistant["reasoning_content"] = _text(
        assistant.get("reasoning_content", assistant.get("reasoning"))
    )
    assistant.pop("native_turn", None)
    return raw


def _call_anthropic(history, model, key, base, max_tokens, temperature):
    messages, system = _anthropic_messages(history)
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
    if temperature is not None:
        payload["temperature"] = temperature
    if system:
        payload["system"] = system
    raw = _post(
        f"{base}/messages",
        {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        },
        payload,
        key,
    )
    blocks = raw.get("content")
    if not isinstance(blocks, list):
        raise RuntimeError("TATU Anthropic response has no content blocks")
    content = "".join(
        part.get("text", "")
        for part in blocks
        if isinstance(part, dict) and part.get("type") == "text"
    )
    reasoning = "\n".join(
        part.get("thinking", "")
        for part in blocks
        if isinstance(part, dict) and part.get("type") == "thinking"
    )
    return _result(
        model,
        "anthropic",
        raw,
        content,
        reasoning,
        {"role": "assistant", "content": copy.deepcopy(blocks)},
        raw.get("stop_reason"),
        raw.get("usage"),
        {"max_output_tokens": max_tokens, "temperature": temperature},
    )


def _call_gemini(history, model, key, base, max_tokens, temperature):
    messages, system = _gemini_messages(history)
    thinking_config = {"thinkingLevel": "high", "includeThoughts": True}
    generation_config = {
        "maxOutputTokens": max_tokens,
        "thinkingConfig": thinking_config,
    }
    if temperature is not None:
        generation_config["temperature"] = temperature
    payload = {"contents": messages, "generationConfig": generation_config}
    if system:
        payload["systemInstruction"] = {"parts": system}
    origin = base.rstrip("/")
    for suffix in ("/v1beta", "/v1"):
        if origin.endswith(suffix):
            origin = origin.removesuffix(suffix)
            break
    raw = _post(
        f"{origin}/v1beta/models/{quote(model, safe='')}:generateContent",
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload,
        key,
    )
    try:
        candidate = raw["candidates"][0]
        parts = candidate["content"]["parts"]
        if not isinstance(parts, list):
            raise TypeError
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("TATU Gemini response has no candidate") from exc
    content = "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and part.get("thought") is not True
    )
    reasoning = "\n".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and part.get("thought") is True
    )
    return _result(
        model,
        "gemini",
        raw,
        content,
        reasoning,
        {"role": "model", "parts": copy.deepcopy(parts)},
        candidate.get("finishReason"),
        raw.get("usageMetadata"),
        {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "thinking_config": thinking_config,
        },
    )


def call_llm_full(message, m):
    if m == "gpt-oss-120b":
        base = os.environ.get("GPT_OSS_BASE_URL", "").strip()
        if base:
            return _call_gpt_oss_local(message, base)
        return call_openrouter(message, "openai/" + m)
    try:
        provider = TATU_MODELS[m]
    except KeyError as exc:
        raise ValueError(f"unsupported model: {m}") from exc
    key, base, max_tokens, temperature = _tatu_settings()
    history = generate_messages(message)
    if provider == "openai":
        transport = os.environ.get("TATU_OPENAI_TRANSPORT", "chat-completions").strip()
        if transport == "responses":
            return _call_openai_responses(
                history, m, key, base, max_tokens, temperature
            )
        if transport != "chat-completions":
            raise ValueError(f"unsupported TATU OpenAI transport: {transport}")
        return _call_openai(history, m, key, base, max_tokens, temperature)
    if provider == "anthropic":
        return _call_anthropic(history, m, key, base, max_tokens, temperature)
    return _call_gemini(history, m, key, base, max_tokens, temperature)


def call_llm(message, m):
    result = call_llm_full(message, m)["choices"][0]["message"]
    return str(result["content"])


def call_llm_details(message, m):
    result = call_llm_full(message, m)
    assistant = copy.deepcopy(result["choices"][0]["message"])
    if "raw_response" in result:
        assistant["raw_response"] = copy.deepcopy(result["raw_response"])
    for key in ("model", "request_config"):
        if key in result:
            assistant[key] = copy.deepcopy(result[key])
    return str(assistant["content"]), assistant, copy.deepcopy(result.get("usage", {}))


def assistant_history_message(assistant_message, full_msg):
    """Trim a detailed result to the provider-native fields needed next turn."""
    item = {
        "role": "assistant",
        "content": assistant_message,
        "reasoning_content": str(full_msg.get("reasoning_content") or ""),
    }
    for key in ("provider", "native_turn"):
        if key in full_msg:
            item[key] = copy.deepcopy(full_msg[key])
    return item
