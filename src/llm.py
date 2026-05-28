"""LLM client with retry logic, cost tracking. Supports Anthropic direct and OpenRouter."""

import json
import logging
import re
import time
from typing import Literal

import httpx
from pydantic import BaseModel

from src.config import get_settings

logger = logging.getLogger(__name__)

_SECRET_PATTERN = re.compile(
    r"(sk-(?:ant-)?[a-zA-Z0-9]{8})[a-zA-Z0-9-]+"
    r"|([a-zA-Z0-9_-]{20,}(?:api|key|token|secret)[a-zA-Z0-9_-]*)",
    re.IGNORECASE,
)


def _mask_secrets(text: str) -> str:
    """Replace anything that looks like an API key with a redacted placeholder."""
    return _SECRET_PATTERN.sub(lambda m: (m.group(1) or m.group(0)[:8]) + "***REDACTED***", text)

MODEL_MAP: dict[str, str] = {
    "fast": "claude-haiku-4-5-20251001",
    "quality": "claude-sonnet-4-6-20250514",
    "best": "claude-opus-4-20250514",
}

OPENROUTER_MODEL_MAP: dict[str, str] = {
    "fast": "anthropic/claude-haiku-4-5",
    "quality": "anthropic/claude-sonnet-4",
    "best": "anthropic/claude-opus-4",
}

PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
}

MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
ANTHROPIC_TIMEOUT = 90.0
OPENROUTER_TIMEOUT = 60.0


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a given model and token counts."""
    clean_model = model.removeprefix("anthropic/")
    prices = PRICING.get(clean_model, PRICING["claude-haiku-4-5-20251001"])
    input_cost = (input_tokens / 1_000_000) * prices["input"]
    output_cost = (output_tokens / 1_000_000) * prices["output"]
    return input_cost + output_cost


def call_llm(
    prompt: str,
    model_tier: Literal["fast", "quality", "best"] = "fast",
    response_format: type[BaseModel] | None = None,
    label: str = "",
) -> str:
    """Call LLM API with retry and cost logging.

    Automatically routes to Anthropic direct or OpenRouter based on config.
    """
    tag = f"[{label}] " if label else ""
    model_name = MODEL_MAP.get(model_tier, model_tier)
    logger.debug(
        "%s>>> PROMPT to %s (%s):\n%s\n<<< END PROMPT",
        tag, model_name, model_tier, _mask_secrets(prompt[:2000]),
    )

    settings = get_settings()
    if settings.use_openrouter:
        response = _call_openrouter(prompt, model_tier)
    else:
        response = _call_anthropic(prompt, model_tier)

    logger.debug(
        "%s>>> AI RESPONSE from %s:\n%s\n<<< END AI RESPONSE",
        tag, model_name, _mask_secrets(response),
    )
    return response


def _call_anthropic(prompt: str, model_tier: str) -> str:
    """Call Anthropic API directly."""
    import anthropic

    settings = get_settings()
    model = MODEL_MAP[model_tier]
    client = anthropic.Anthropic(
        api_key=settings.llm_api_key,
        timeout=ANTHROPIC_TIMEOUT,
    )
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            logger.info("Anthropic call: %s attempt %d/%d", model, attempt + 1, MAX_RETRIES)
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return _process_anthropic_response(response, model)
        except (anthropic.APIError, anthropic.APITimeoutError) as e:
            last_error = e
            _log_retry(attempt, e)

    raise last_error  # type: ignore[misc]


def _process_anthropic_response(response: object, model: str) -> str:
    """Extract text and log cost from an Anthropic API response."""
    input_tokens = response.usage.input_tokens  # type: ignore[attr-defined]
    output_tokens = response.usage.output_tokens  # type: ignore[attr-defined]
    _log_cost(model, input_tokens, output_tokens)
    blocks = [b.text for b in response.content if b.type == "text"]  # type: ignore[attr-defined]
    return "\n".join(blocks)


def _call_openrouter(prompt: str, model_tier: str) -> str:
    """Call OpenRouter API (OpenAI-compatible format)."""
    settings = get_settings()
    model = OPENROUTER_MODEL_MAP[model_tier]
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            logger.info("OpenRouter call: %s attempt %d/%d", model, attempt + 1, MAX_RETRIES)
            return _openrouter_request(settings.llm_api_key, model, prompt)
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
            last_error = e
            _log_retry(attempt, e)

    raise last_error  # type: ignore[misc]


def _openrouter_request(api_key: str, model: str, prompt: str) -> str:
    """Make a single request to OpenRouter."""
    with httpx.Client(timeout=OPENROUTER_TIMEOUT) as client:
        resp = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return _process_openrouter_response(data, model)


def _process_openrouter_response(data: dict, model: str) -> str:
    """Extract text and log cost from OpenRouter response."""
    usage = data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    _log_cost(model, input_tokens, output_tokens)
    return data["choices"][0]["message"]["content"]


def _log_cost(model: str, input_tokens: int, output_tokens: int) -> None:
    """Log token usage and cost."""
    cost = calculate_cost(model, input_tokens, output_tokens)
    logger.info(
        "LLM call: model=%s, input=%d, output=%d, cost=$%.4f",
        model,
        input_tokens,
        output_tokens,
        cost,
    )


def _log_retry(attempt: int, error: Exception) -> None:
    """Log retry attempt with backoff."""
    if attempt < MAX_RETRIES - 1:
        wait = INITIAL_BACKOFF * (2**attempt)
        logger.warning(
            "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
            attempt + 1,
            MAX_RETRIES,
            wait,
            error,
        )
        time.sleep(wait)
