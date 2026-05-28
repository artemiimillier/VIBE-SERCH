"""FastAPI web app - serves the UI and exposes KV-backed status endpoints.

Designed for Vercel serverless: no in-memory state, no background scheduler.
The pipeline runs only via Vercel Cron hitting GET /api/cron.
"""

import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src import storage
from src.config import get_settings
from src.models import DailyDigest, MethodCard

logger = logging.getLogger(__name__)

app = FastAPI(title="VIBE-SERCH")

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Serve the main page."""
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


@app.get("/api/cron/status")
async def get_cron_status() -> JSONResponse:
    """Return current cron job status (from KV) for the UI."""
    settings = get_settings()
    stored = storage.get_json(storage.KEY_LAST_STATUS) or {}

    payload = {
        "schedule": f"{settings.cron_hour:02d}:{settings.cron_minute:02d}",
        "timezone": settings.cron_timezone,
        "next_run": _next_run_iso(settings.cron_hour, settings.cron_minute),
        "is_running": False,
        "last_run": stored.get("last_run"),
        "last_result": stored.get("last_result", ""),
        "last_duration": stored.get("last_duration", 0.0),
        "cards_count": stored.get("cards_count", 0),
        "error": stored.get("error", ""),
        "has_digest": stored.get("has_digest", False),
    }
    return JSONResponse(payload)


@app.get("/api/cron/digest")
async def get_cron_digest() -> JSONResponse:
    """Return the last digest produced by the cron job (from KV)."""
    digest = storage.get_json(storage.KEY_LAST_DIGEST)
    return JSONResponse({"digest": digest})


@app.get("/api/cron")
async def run_cron_job(request: Request) -> JSONResponse:
    """Run the full pipeline as a Vercel Cron handler.

    Protected by CRON_SECRET when set: Vercel sends Authorization: Bearer <secret>.
    Returns a summary of the run.
    """
    _authorize_cron(request)

    t0 = time.time()
    logger.info("Cron job started")

    try:
        digest = _run_pipeline()
    except Exception as exc:
        elapsed = time.time() - t0
        logger.exception("Cron job failed after %.1fs", elapsed)
        storage.set_json(
            storage.KEY_LAST_STATUS,
            {
                "last_run": datetime.now(UTC).isoformat(),
                "last_result": "error",
                "last_duration": round(elapsed, 1),
                "cards_count": 0,
                "error": str(exc),
                "has_digest": False,
            },
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    elapsed = time.time() - t0
    digest_dict = _digest_to_dict(digest, elapsed)

    storage.set_json(storage.KEY_LAST_DIGEST, digest_dict)
    storage.set_json(
        storage.KEY_LAST_STATUS,
        {
            "last_run": datetime.now(UTC).isoformat(),
            "last_result": "success",
            "last_duration": round(elapsed, 1),
            "cards_count": len(digest.cards),
            "error": "",
            "has_digest": True,
        },
    )

    logger.info("Cron job completed in %.1fs, %d cards", elapsed, len(digest.cards))
    return JSONResponse(
        {
            "ok": True,
            "elapsed_seconds": round(elapsed, 1),
            "cards_count": len(digest.cards),
        }
    )


def _authorize_cron(request: Request) -> None:
    """Validate the CRON_SECRET if it is configured."""
    expected = os.environ.get("CRON_SECRET")
    if not expected:
        return
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def _run_pipeline() -> DailyDigest:
    """Execute the full scan -> filter -> verify -> generate -> send chain."""
    from src.pipeline import filter_signals, generate_digest, verify_signals
    from src.scanner import scan_reddit
    from src.telegram import send_digest

    raw_signals = scan_reddit()
    filtered = filter_signals(raw_signals)
    verified = verify_signals(filtered)
    digest = generate_digest(verified)
    digest.signals_scanned = len(raw_signals)

    try:
        send_digest(digest)
    except Exception:
        logger.exception("Telegram send failed (digest already saved to KV)")

    return digest


def _next_run_iso(hour: int, minute: int) -> str:
    """Compute the next UTC datetime matching (hour, minute) and return ISO string."""
    now = datetime.now(UTC)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate.isoformat()


def _digest_to_dict(digest: DailyDigest, elapsed: float) -> dict:
    """Convert a DailyDigest to a serializable dict for the frontend."""
    return {
        "date": digest.date.strftime("%d.%m.%Y"),
        "signals_scanned": digest.signals_scanned,
        "signals_verified": digest.signals_verified,
        "elapsed_seconds": round(elapsed, 1),
        "cards": [_card_to_dict(c) for c in digest.cards],
    }


def _card_to_dict(card: MethodCard) -> dict:
    """Convert a MethodCard to a serializable dict."""
    return {
        "title": card.title,
        "category": card.category,
        "hype_rating": card.hype_rating,
        "hype_label": card.hype_label,
        "summary": card.summary,
        "why_important": card.why_important,
        "action": card.action,
        "source_url": card.source_url,
    }
