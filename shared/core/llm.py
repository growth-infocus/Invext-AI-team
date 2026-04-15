"""shared/core/llm.py — LLM dispatcher with real-time token/latency metrics."""
from __future__ import annotations
import asyncio
import json
import logging
import time
import httpx
from shared.core.config import settings

logger = logging.getLogger(__name__)

# ── Metrics tracking ──────────────────────────────────────────────────────────
# Stores rolling per-model performance in Redis so the planner can use
# real measured throughput instead of hard-coded benchmarks.

async def _record_metrics(model: str, prompt_tokens: int, completion_tokens: int,
                          elapsed_seconds: float) -> None:
    """Record one LLM call's metrics to Redis as a rolling window."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        key = f"llm_metrics:{model}"
        # Store as a Redis list: json records, keep last 50
        record = json.dumps({
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens":      prompt_tokens + completion_tokens,
            "elapsed_s":         round(elapsed_seconds, 3),
            "tok_per_sec":       round((prompt_tokens + completion_tokens) / max(elapsed_seconds, 0.01)),
        })
        pipe = r.pipeline()
        pipe.lpush(key, record)
        pipe.ltrim(key, 0, 49)    # keep last 50 calls
        pipe.expire(key, 86400)   # TTL: 24h
        await pipe.execute()
        await r.aclose()
    except Exception as exc:
        logger.debug(f"[metrics] failed to record: {exc}")


async def get_model_metrics(model: str) -> dict:
    """
    Return averaged performance metrics for a model.
    Used by the planner to calculate realistic task time estimates.
    Returns: {tok_per_sec, avg_latency_s, prompt_tokens_avg, completion_tokens_avg, sample_count}
    """
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        key = f"llm_metrics:{model}"
        records_raw = await r.lrange(key, 0, -1)
        await r.aclose()

        if not records_raw:
            return {}

        records = [json.loads(x) for x in records_raw]
        n = len(records)
        return {
            "sample_count":           n,
            "tok_per_sec":            round(sum(r["tok_per_sec"] for r in records) / n, 1),
            "avg_latency_s":          round(sum(r["elapsed_s"]   for r in records) / n, 2),
            "prompt_tokens_avg":      round(sum(r["prompt_tokens"]     for r in records) / n),
            "completion_tokens_avg":  round(sum(r["completion_tokens"] for r in records) / n),
            "total_tokens_avg":       round(sum(r["total_tokens"]      for r in records) / n),
        }
    except Exception as exc:
        logger.debug(f"[metrics] get failed: {exc}")
        return {}


async def estimate_task_hours(role: str, task_complexity: str = "medium") -> float:
    """
    Estimate how many hours an AI agent needs for a task, based on real
    measured model throughput from Redis metrics.

    complexity: "trivial" | "simple" | "medium" | "complex" | "large"
    Returns estimated hours as a float.
    """
    from shared.core.config import settings as cfg
    provider_attr = f"{role}_provider"
    provider = getattr(cfg, provider_attr, "openrouter")

    # Determine which model this role uses
    model_attr_map = {
        "openai":     cfg.openai_default_model,
        "groq":       cfg.groq_tool_model or cfg.groq_default_model,
        "openrouter": cfg.openrouter_default_model,
        "gemini":     cfg.gemini_default_model,
    }
    model = model_attr_map.get(provider, "unknown")
    metrics = await get_model_metrics(model)

    # Base estimates in ReAct iterations and tokens per iteration
    complexity_profiles = {
        #           react_iters  input_tok  output_tok  tool_calls
        "trivial":  (2,           800,       200,        1),
        "simple":   (4,           1200,      400,        3),
        "medium":   (7,           2000,      600,        6),
        "complex":  (12,          3000,      800,        10),
        "large":    (20,          4000,      1000,       18),
    }
    iters, in_tok, out_tok, n_tools = complexity_profiles.get(
        task_complexity, complexity_profiles["medium"]
    )

    tok_per_sec = metrics.get("tok_per_sec") or 50.0  # fallback if no data yet
    avg_latency = metrics.get("avg_latency_s") or 3.0

    # Time per iteration: LLM call latency + token generation time
    tokens_per_iter = in_tok + out_tok
    time_per_iter_s = avg_latency + (tokens_per_iter / tok_per_sec)

    # Tool call overhead: ~5s per tool call (network + execution)
    tool_overhead_s = n_tools * 5

    total_seconds = (iters * time_per_iter_s) + tool_overhead_s
    hours = total_seconds / 3600

    logger.debug(
        f"[estimate] {role}/{model} {task_complexity}: "
        f"{iters} iters x {time_per_iter_s:.1f}s + {tool_overhead_s}s tools "
        f"= {total_seconds:.0f}s = {hours:.3f}h "
        f"(tok/s={tok_per_sec}, n={metrics.get('sample_count',0)})"
    )
    return round(hours, 2)


async def call_llm(messages, provider="openrouter", model=None,
                   tools=None, temperature=0.3, max_tokens=2048,
                   tool_choice="auto") -> dict:
    """Wrapper with exponential backoff retry, token/latency metrics recording."""
    p = provider.lower()
    if p in ("openrouter", "groq", "openai"):
        return await _call_with_retry(
            _openai_compat, messages, p, model, tools, temperature, max_tokens, tool_choice
        )
    elif p == "gemini":
        return await _call_with_retry(
            _gemini, messages, model, temperature, max_tokens
        )
    return await _call_with_retry(
        _openai_compat, messages, "openrouter", model, tools, temperature, max_tokens, tool_choice
    )


async def _call_with_retry(func, *args, **kwargs):
    """
    Retry logic with exponential backoff.
    For 429s, respects the Retry-After header or uses longer delays.
    Max 4 retries on timeout, 429, or 5xx errors.
    """
    max_retries = 4
    last_exception = None

    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            is_retryable = False
            reason = ""
            delay = 2 ** attempt  # default: 1, 2, 4, 8s

            if isinstance(e, httpx.TimeoutException):
                is_retryable = True
                reason = "timeout"
            elif isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code
                if status == 429 or status >= 500:
                    is_retryable = True
                    reason = f"HTTP {status}"
                    # Respect Retry-After header if present
                    retry_after = e.response.headers.get("retry-after")
                    if retry_after:
                        try:
                            delay = float(retry_after) + 1
                        except ValueError:
                            pass
                    elif status == 429:
                        delay = max(delay, 15)  # at least 15s for rate limits

            if not is_retryable or attempt == max_retries - 1:
                raise

            last_exception = e
            logger.warning(
                f"LLM call failed ({reason}), retry {attempt + 1}/{max_retries} after {delay:.0f}s"
            )
            await asyncio.sleep(delay)
        except Exception:
            raise

    if last_exception:
        raise last_exception


async def _openai_compat(messages, provider, model, tools, temperature, max_tokens,
                         tool_choice="auto"):
    if provider == "groq":
        base, key = "https://api.groq.com/openai/v1", settings.groq_api_key
        model = model or settings.groq_model or settings.groq_default_model
    elif provider == "openai":
        base, key = "https://api.openai.com/v1", settings.openai_api_key
        model = model or settings.openai_default_model
    else:
        base, key = settings.openrouter_base_url, settings.openrouter_api_key
        model = model or settings.openrouter_model or settings.openrouter_default_model

    payload = dict(model=model, messages=messages,
                   temperature=temperature, max_tokens=max_tokens)
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{base}/chat/completions",
                         headers={"Authorization": f"Bearer {key}",
                                  "HTTP-Referer": "https://localhost"},
                         json=payload)
        if not r.is_success:
            print(f"[LLM ERROR] {provider} HTTP {r.status_code}: {r.text[:800]}", flush=True)
        r.raise_for_status()
    elapsed = time.monotonic() - t0

    resp_json = r.json()
    usage = resp_json.get("usage") or {}
    prompt_tok     = usage.get("prompt_tokens", 0)
    completion_tok = usage.get("completion_tokens", 0)

    if prompt_tok or completion_tok:
        asyncio.create_task(_record_metrics(model, prompt_tok, completion_tok, elapsed))
        logger.debug(
            f"[LLM] {provider}/{model} — "
            f"{prompt_tok}+{completion_tok}tok in {elapsed:.2f}s "
            f"({round((prompt_tok+completion_tok)/max(elapsed,0.01))}tok/s)"
        )

    msg = resp_json["choices"][0]["message"]
    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls")}


async def _gemini(messages, model, temperature, max_tokens):
    model = model or settings.gemini_model or settings.gemini_default_model
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
