"""Telegram publishing module - sends daily digest to a channel."""

import asyncio
import logging

from src.config import get_settings
from src.models import DailyDigest
from src.pipeline import _format_digest_text
from telegram import Bot

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds


# ---------------------------------------------------------------------------
# Low-level async send
# ---------------------------------------------------------------------------


async def _send_message(text: str) -> None:
    """Send a single message to the configured Telegram channel."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise ValueError("Telegram bot token not configured")
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    await bot.send_message(
        chat_id=settings.telegram_channel_id,
        text=text,
        parse_mode=None,  # plain text with emoji
    )


# ---------------------------------------------------------------------------
# Message splitting
# ---------------------------------------------------------------------------


def _split_message(text: str) -> list[str]:
    """Split text into parts that fit within Telegram's 4096 char limit.

    Splits at double-newline (card) boundaries when possible.
    """
    if len(text) <= TELEGRAM_MAX_LENGTH:
        return [text]

    parts: list[str] = []
    chunks = text.split("\n\n")
    current = ""

    for chunk in chunks:
        candidate = f"{current}\n\n{chunk}" if current else chunk
        if len(candidate) <= TELEGRAM_MAX_LENGTH:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = _handle_oversized_chunk(chunk, parts)

    if current:
        parts.append(current)

    return parts


def _handle_oversized_chunk(chunk: str, parts: list[str]) -> str:
    """Handle a chunk that exceeds the max length on its own.

    If a single chunk is too long, force-split it at the limit.
    Returns the remaining text to continue accumulating.
    """
    if len(chunk) <= TELEGRAM_MAX_LENGTH:
        return chunk

    # Force-split oversized chunk at character boundary
    for i in range(0, len(chunk), TELEGRAM_MAX_LENGTH):
        segment = chunk[i : i + TELEGRAM_MAX_LENGTH]
        if i + TELEGRAM_MAX_LENGTH < len(chunk):
            parts.append(segment)
        else:
            return segment
    return ""


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


async def _send_with_retry(text: str) -> None:
    """Send a message with exponential backoff retry."""
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            await _send_message(text)
            return
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                wait = INITIAL_BACKOFF * (2**attempt)
                logger.warning(
                    "Telegram send failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    wait,
                    e,
                )
                await asyncio.sleep(wait)

    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_digest(digest: DailyDigest) -> None:
    """Format and send digest to Telegram channel.

    Splits into multiple messages if text exceeds 4096 chars.
    Retries each message up to 3 times with exponential backoff.
    """
    text = _format_digest_text(digest)
    parts = _split_message(text)

    logger.info(
        "Sending digest to Telegram: %d chars, %d part(s)",
        len(text),
        len(parts),
    )

    asyncio.run(_send_all_parts(parts))

    logger.info("Digest sent to Telegram successfully (%d parts)", len(parts))


async def _send_all_parts(parts: list[str]) -> None:
    """Send all message parts sequentially with a small delay between them."""
    for i, part in enumerate(parts):
        if i > 0:
            await asyncio.sleep(0.5)  # small delay between messages
        await _send_with_retry(part)
