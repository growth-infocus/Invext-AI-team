"""shared/core/llm.py — LLM dispatcher (same as monolith but shared)"""
from __future__ import annotations
import asyncio
import json
import logging
import httpx
from shared.core.config import settings

logger = logging.getLogger(__name__)


async def call_llm(messages, provider="openrouter", model=None,
                   tools=None, temperature=0.3, max_tokens=2048) -> dict:
    """Wrapper with exponential backoff retry logic."""
    p = provider.lower()
    if p in ("openrouter", "groq"):
        return await _call_with_retry(
            _openai_compat, messages, p, model, tools, temperature, max_tokens
        )
    elif p == "gemini":
        return await _call_with_retry(
            _gemini, messages, model, temperature, max_tokens
        )
    return await _call_with_retry(
        _openai_compat, messages, "openrouter", model, tools, temperature, max_tokens
    )


async def _call_with_retry(func, *args, **kwargs):
    """
    Retry logic with exponential backoff (1s, 2s, 4s).
    Max 3 retries on timeout, 429, or 5xx errors.
    """
    max_retries = 3
    backoff_delays = [1, 2, 4]  # seconds
    last_exception = None

    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            # Check if it's a retryable error
            is_retryable = False
            reason = ""

            if isinstance(e, httpx.TimeoutException):
                is_retryable = True
                reason = "timeout"
            elif isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code
                if status == 429 or status >= 500:
                    is_retryable = True
                    reason = f"HTTP {status}"

            if not is_retryable or attempt == max_retries - 1:
                raise

            last_exception = e
            delay = backoff_delays[attempt]
            logger.warning(
                f"LLM call failed ({reason}), retry {attempt + 1}/{max_retries} "
                f"after {delay}s"
            )
            await asyncio.sleep(delay)
        except Exception:
            raise

    # Should not reach here, but just in case
    if last_exception:
        raise last_exception


async def _openai_compat(messages, provider, model, tools, temperature, max_tokens):
    if provider == "groq":
        base, key = "https://api.groq.com/openai/v1", settings.groq_api_key
        model = model or settings.groq_default_model
    else:
        base, key = settings.openrouter_base_url, settings.openrouter_api_key
        model = model or settings.openrouter_default_model

    payload = dict(model=model, messages=messages,
                   temperature=temperature, max_tokens=max_tokens)
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{base}/chat/completions",
                         headers={"Authorization": f"Bearer {key}",
                                  "HTTP-Referer": "https://localhost"},
                         json=payload)
        r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls")}


async def _gemini(messages, model, temperature, max_tokens):
    model = model or settings.gemini_default_model
    contents = [{"role": "user" if m["role"] != "assistant" else "model",
                 "parts": [{"text": m["content"]}]} for m in messages]
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={settings.gemini_api_key}")
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(url, json={"contents": contents,
                         "generationConfig": {"temperature": temperature,
                                              "maxOutputTokens": max_tokens}})
        r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return {"content": text, "tool_calls": None}
