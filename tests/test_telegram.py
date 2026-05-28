"""Tests for the Telegram publishing module."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import DailyDigest, MethodCard
from src.telegram import (
    TELEGRAM_MAX_LENGTH,
    _send_all_parts,
    _send_with_retry,
    _split_message,
    send_digest,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_card(**overrides: object) -> MethodCard:
    """Create a valid MethodCard with optional overrides."""
    defaults: dict = {
        "title": "Test Tool",
        "category": "Tools",
        "hype_rating": "confirmed",
        "hype_label": "Confirmed",
        "summary": "What: A test tool.",
        "why_important": "Why: Testing matters.",
        "action": "Action: Use it.",
        "source_url": "https://example.com",
    }
    defaults.update(overrides)
    return MethodCard(**defaults)


def _make_digest(**overrides: object) -> DailyDigest:
    """Create a valid DailyDigest for testing."""
    defaults: dict = {
        "cards": [_make_card()],
        "date": datetime(2025, 5, 27, 8, 0, 0, tzinfo=UTC),
        "total_cost": 0.05,
        "signals_scanned": 50,
        "signals_verified": 5,
    }
    defaults.update(overrides)
    return DailyDigest(**defaults)


# ---------------------------------------------------------------------------
# _split_message
# ---------------------------------------------------------------------------


class TestSplitMessage:
    def test_short_message_single_part(self) -> None:
        text = "Hello, world!"
        parts = _split_message(text)
        assert parts == [text]

    def test_exact_limit_single_part(self) -> None:
        text = "x" * TELEGRAM_MAX_LENGTH
        parts = _split_message(text)
        assert len(parts) == 1

    def test_over_limit_splits_at_double_newline(self) -> None:
        chunk_a = "A" * 2000
        chunk_b = "B" * 2000
        chunk_c = "C" * 2000
        text = f"{chunk_a}\n\n{chunk_b}\n\n{chunk_c}"
        parts = _split_message(text)
        assert len(parts) >= 2
        for part in parts:
            assert len(part) <= TELEGRAM_MAX_LENGTH

    def test_all_content_preserved(self) -> None:
        chunk_a = "A" * 2000
        chunk_b = "B" * 2000
        text = f"{chunk_a}\n\n{chunk_b}"
        parts = _split_message(text)
        rejoined = "\n\n".join(parts)
        assert chunk_a in rejoined
        assert chunk_b in rejoined

    def test_empty_string(self) -> None:
        parts = _split_message("")
        assert parts == [""]

    def test_single_oversized_chunk(self) -> None:
        """A single block > 4096 with no newlines gets force-split."""
        text = "X" * 5000
        parts = _split_message(text)
        assert len(parts) >= 2
        for part in parts:
            assert len(part) <= TELEGRAM_MAX_LENGTH


# ---------------------------------------------------------------------------
# _send_with_retry
# ---------------------------------------------------------------------------


class TestSendWithRetry:
    def test_success_on_first_attempt(self) -> None:
        with patch("src.telegram._send_message", new_callable=AsyncMock) as mock_send:
            asyncio.run(_send_with_retry("hello"))
            mock_send.assert_awaited_once_with("hello")

    def test_retries_on_failure_then_succeeds(self) -> None:
        with patch("src.telegram._send_message", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = [Exception("fail"), None]
            asyncio.run(_send_with_retry("hello"))
            assert mock_send.await_count == 2

    def test_raises_after_max_retries(self) -> None:
        with patch("src.telegram._send_message", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = Exception("persistent failure")
            with pytest.raises(Exception, match="persistent failure"):
                asyncio.run(_send_with_retry("hello"))
            assert mock_send.await_count == 3

    def test_retries_exactly_three_times(self) -> None:
        with patch("src.telegram._send_message", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = RuntimeError("down")
            with pytest.raises(RuntimeError):
                asyncio.run(_send_with_retry("hello"))
            assert mock_send.await_count == 3


# ---------------------------------------------------------------------------
# send_digest (integration with mocked bot)
# ---------------------------------------------------------------------------


class TestSendDigest:
    @patch("src.telegram._format_digest_text")
    @patch("src.telegram._send_all_parts", new_callable=AsyncMock)
    def test_sends_formatted_text(
        self,
        mock_send_parts: AsyncMock,
        mock_format: MagicMock,
    ) -> None:
        mock_format.return_value = "digest text"
        digest = _make_digest()

        send_digest(digest)

        mock_format.assert_called_once_with(digest)
        mock_send_parts.assert_awaited_once()

    @patch("src.telegram._format_digest_text")
    @patch("src.telegram._send_all_parts", new_callable=AsyncMock)
    def test_splits_long_message(
        self,
        mock_send_parts: AsyncMock,
        mock_format: MagicMock,
    ) -> None:
        """A message over 4096 chars should be split into multiple parts."""
        mock_format.return_value = "A" * 3000 + "\n\n" + "B" * 3000
        digest = _make_digest()

        send_digest(digest)

        mock_send_parts.assert_awaited_once()
        parts = mock_send_parts.call_args[0][0]
        assert len(parts) >= 2

    @patch("src.telegram._format_digest_text")
    @patch("src.telegram._send_all_parts", new_callable=AsyncMock)
    def test_error_propagates(
        self,
        mock_send_parts: AsyncMock,
        mock_format: MagicMock,
    ) -> None:
        mock_format.return_value = "text"
        mock_send_parts.side_effect = RuntimeError("connection lost")

        with pytest.raises(RuntimeError, match="connection lost"):
            send_digest(_make_digest())


# ---------------------------------------------------------------------------
# _send_all_parts
# ---------------------------------------------------------------------------


class TestSendAllParts:
    def test_sends_each_part(self) -> None:
        with patch("src.telegram._send_with_retry", new_callable=AsyncMock) as mock_retry:
            asyncio.run(_send_all_parts(["part1", "part2", "part3"]))
            assert mock_retry.await_count == 3

    def test_empty_parts_list(self) -> None:
        with patch("src.telegram._send_with_retry", new_callable=AsyncMock) as mock_retry:
            asyncio.run(_send_all_parts([]))
            mock_retry.assert_not_awaited()
