"""Tests for the main pipeline orchestrator."""

from unittest.mock import MagicMock, patch

import pytest

from src.main import main

# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


class TestMain:
    @patch("src.main.send_digest")
    @patch("src.main.generate_digest")
    @patch("src.main.verify_signals")
    @patch("src.main.filter_signals")
    @patch("src.main.scan_reddit")
    @patch("src.main._setup_logging")
    def test_calls_all_steps_in_order(
        self,
        mock_logging: MagicMock,
        mock_scan: MagicMock,
        mock_filter: MagicMock,
        mock_verify: MagicMock,
        mock_generate: MagicMock,
        mock_send: MagicMock,
    ) -> None:
        """All pipeline steps should be called in sequence."""
        mock_scan.return_value = ["signal1", "signal2"]
        mock_filter.return_value = ["signal1"]
        mock_verify.return_value = ["fact1"]

        mock_digest = MagicMock()
        mock_digest.cards = ["card1"]
        mock_digest.total_cost = 0.05
        mock_digest.signals_scanned = 0
        mock_generate.return_value = mock_digest

        main()

        mock_scan.assert_called_once()
        mock_filter.assert_called_once_with(["signal1", "signal2"])
        mock_verify.assert_called_once_with(["signal1"])
        mock_generate.assert_called_once_with(["fact1"])
        mock_send.assert_called_once_with(mock_digest)

    @patch("src.main.send_digest")
    @patch("src.main.generate_digest")
    @patch("src.main.verify_signals")
    @patch("src.main.filter_signals")
    @patch("src.main.scan_reddit")
    @patch("src.main._setup_logging")
    def test_updates_signals_scanned(
        self,
        mock_logging: MagicMock,
        mock_scan: MagicMock,
        mock_filter: MagicMock,
        mock_verify: MagicMock,
        mock_generate: MagicMock,
        mock_send: MagicMock,
    ) -> None:
        """signals_scanned should be set to the number of raw signals."""
        mock_scan.return_value = ["s1", "s2", "s3"]
        mock_filter.return_value = ["s1"]
        mock_verify.return_value = ["f1"]

        mock_digest = MagicMock()
        mock_digest.cards = ["card1"]
        mock_digest.total_cost = 0.01
        mock_digest.signals_scanned = 0
        mock_generate.return_value = mock_digest

        main()

        assert mock_digest.signals_scanned == 3

    @patch("src.main.scan_reddit")
    @patch("src.main._setup_logging")
    def test_not_implemented_handled_gracefully(
        self,
        mock_logging: MagicMock,
        mock_scan: MagicMock,
    ) -> None:
        """NotImplementedError should be caught and not crash."""
        mock_scan.side_effect = NotImplementedError("future step")
        # Should not raise
        main()

    @patch("src.main.scan_reddit")
    @patch("src.main._setup_logging")
    def test_fatal_error_exits(
        self,
        mock_logging: MagicMock,
        mock_scan: MagicMock,
    ) -> None:
        """Unexpected exceptions should cause sys.exit(1)."""
        mock_scan.side_effect = RuntimeError("connection failed")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    @patch("src.main.send_digest")
    @patch("src.main.generate_digest")
    @patch("src.main.verify_signals")
    @patch("src.main.filter_signals")
    @patch("src.main.scan_reddit")
    @patch("src.main._setup_logging")
    def test_send_failure_exits(
        self,
        mock_logging: MagicMock,
        mock_scan: MagicMock,
        mock_filter: MagicMock,
        mock_verify: MagicMock,
        mock_generate: MagicMock,
        mock_send: MagicMock,
    ) -> None:
        """Telegram send failure should trigger sys.exit(1)."""
        mock_scan.return_value = []
        mock_filter.return_value = []
        mock_verify.return_value = []

        mock_digest = MagicMock()
        mock_digest.cards = []
        mock_digest.total_cost = 0.0
        mock_digest.signals_scanned = 0
        mock_generate.return_value = mock_digest

        mock_send.side_effect = RuntimeError("Telegram API down")

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
