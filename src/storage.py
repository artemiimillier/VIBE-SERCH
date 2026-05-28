"""KV storage abstraction over Upstash Redis REST (used by Vercel KV)."""

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

KEY_LAST_DIGEST = "vibeserch:last_digest"
KEY_LAST_STATUS = "vibeserch:last_status"

_REQUEST_TIMEOUT = 5.0


def _kv_config() -> tuple[str, str] | None:
    """Return (url, token) or None if KV is not configured."""
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        return None
    return url.rstrip("/"), token


def _kv_command(*args: str) -> Any:
    """Execute a Redis command via Upstash REST. Returns None if KV not configured."""
    cfg = _kv_config()
    if cfg is None:
        logger.warning("KV not configured (set KV_REST_API_URL and KV_REST_API_TOKEN)")
        return None
    url, token = cfg

    try:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=list(args),
            )
            resp.raise_for_status()
            return resp.json().get("result")
    except httpx.HTTPError as exc:
        logger.warning("KV command failed: %s", exc)
        return None


def get_json(key: str) -> dict | None:
    """Read a JSON value from KV. Returns None on miss or error."""
    raw = _kv_command("GET", key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        logger.warning("KV value at %s is not valid JSON", key)
        return None


def set_json(key: str, value: dict) -> bool:
    """Write a JSON value to KV. Returns True on success."""
    result = _kv_command("SET", key, json.dumps(value, ensure_ascii=False))
    return result == "OK"
