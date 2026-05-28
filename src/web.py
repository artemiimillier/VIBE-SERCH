"""FastAPI web interface - run pipeline and display results in browser."""

import asyncio
import atexit
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from src.config import get_settings
from src.models import DailyDigest, MethodCard
from src.scheduler import cron_status, get_next_run, start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)

app = FastAPI(title="VIBE-SERCH")

start_scheduler()
atexit.register(stop_scheduler)


def _read_html() -> str:
    """Read the HTML template from disk."""
    from pathlib import Path

    html_path = Path(__file__).parent / "templates" / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Serve the main page."""
    return _read_html()


@app.get("/api/run")
async def run_pipeline() -> EventSourceResponse:
    """Run the full pipeline, streaming progress via SSE."""
    return EventSourceResponse(
        _pipeline_stream(),
        media_type="text/event-stream",
    )


@app.get("/api/cron/status")
async def get_cron_status() -> JSONResponse:
    """Return current cron job status for the UI."""
    settings = get_settings()
    data = cron_status.to_dict()
    data["next_run"] = get_next_run()
    data["schedule"] = f"{settings.cron_hour:02d}:{settings.cron_minute:02d}"
    data["timezone"] = settings.cron_timezone
    return JSONResponse(data)


@app.get("/api/cron/digest")
async def get_cron_digest() -> JSONResponse:
    """Return the last digest produced by the cron job."""
    with cron_status._lock:
        digest = cron_status.last_digest
    if digest is None:
        return JSONResponse({"digest": None})
    return JSONResponse({"digest": digest})


async def _pipeline_stream() -> AsyncGenerator[dict, None]:
    """Generator that runs pipeline steps and yields SSE events."""
    t_start = time.time()

    # Step 1: Scan Reddit
    yield _event("step", "scan", "Сканирую Reddit...")
    try:
        raw_signals = await asyncio.to_thread(_run_scan)
        yield _event("progress", "scan", f"Найдено {len(raw_signals)} сигналов")
    except Exception as e:
        yield _event("error", "scan", f"Ошибка сканирования: {e}")
        return

    # Step 2: Filter (Haiku)
    yield _event("step", "filter", f"Фильтрую {len(raw_signals)} сигналов через AI...")
    try:
        filtered = await asyncio.to_thread(_run_filter, raw_signals)
        yield _event("progress", "filter", f"Отобрано {len(filtered)} сигналов")
    except Exception as e:
        yield _event("error", "filter", f"Ошибка фильтрации: {e}")
        return

    # Step 3: Verify (Sonnet x2 per signal)
    msg = f"Верифицирую {len(filtered)} сигналов (2 AI-вызова на каждый)..."
    yield _event("step", "verify", msg)
    try:
        verified = await asyncio.to_thread(_run_verify, filtered)
        yield _event("progress", "verify", f"Верифицировано {len(verified)} фактов")
    except Exception as e:
        yield _event("error", "verify", f"Ошибка верификации: {e}")
        return

    # Step 4: Generate digest (Opus)
    yield _event("step", "generate", "Генерирую дайджест через Opus...")
    try:
        digest = await asyncio.to_thread(_run_generate, verified, len(raw_signals))
        yield _event("progress", "generate", f"Создано {len(digest.cards)} карточек")
    except Exception as e:
        yield _event("error", "generate", f"Ошибка генерации: {e}")
        return

    elapsed = time.time() - t_start
    digest_dict = _digest_to_dict(digest, elapsed)

    with cron_status._lock:
        cron_status.last_digest = digest_dict

    # Send final result
    yield _event(
        "result",
        "done",
        json.dumps(digest_dict, ensure_ascii=False),
    )


def _run_scan() -> list:
    """Run Reddit scanner."""
    from src.scanner import scan_reddit

    return scan_reddit()


def _run_filter(signals: list) -> list:
    """Run Haiku filtering."""
    from src.pipeline import filter_signals

    return filter_signals(signals)


def _run_verify(signals: list) -> list:
    """Run Sonnet verification."""
    from src.pipeline import verify_signals

    return verify_signals(signals)


def _run_generate(facts: list, scanned: int) -> DailyDigest:
    """Run Opus digest generation."""
    from src.pipeline import generate_digest

    digest = generate_digest(facts)
    digest.signals_scanned = scanned
    return digest


def _event(event_type: str, step: str, data: str) -> dict:
    """Build an SSE event dict."""
    payload = json.dumps({"step": step, "message": data}, ensure_ascii=False)
    return {"event": event_type, "data": payload}


def _digest_to_dict(digest: DailyDigest, elapsed: float) -> dict:
    """Convert digest to a serializable dict for the frontend."""
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
