"""Entry point for the VIBE-SERCH pipeline."""

import logging
import os
import sys
import time
from datetime import UTC, datetime

from src.pipeline import filter_signals, generate_digest, verify_signals
from src.scanner import scan_reddit
from src.telegram import send_digest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging to stdout and dated file.

    With --verbose: console shows DEBUG (full AI thinking).
    Without: console shows INFO, file always gets DEBUG.
    """
    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    os.makedirs(log_dir, exist_ok=True)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    log_file = os.path.join(log_dir, f"{date_str}.log")

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console_level = logging.DEBUG if verbose else logging.INFO

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(console_level)
    stdout_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(stdout_handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(file_handler)

    logger.info("Logging to file: %s", log_file)
    if verbose:
        logger.info("VERBOSE MODE: showing full AI prompts and responses in console")


# ---------------------------------------------------------------------------
# Pipeline steps with timing
# ---------------------------------------------------------------------------


def _step_scan() -> list:
    """Step 1: Scan Reddit for raw signals."""
    from src.models import RawSignal

    t0 = time.time()
    raw_signals: list[RawSignal] = scan_reddit()
    elapsed = time.time() - t0

    logger.info(
        "Step 1 - Scan: %d raw signals in %.1fs",
        len(raw_signals),
        elapsed,
    )
    return raw_signals


def _step_filter(raw_signals: list) -> list:
    """Step 2: Filter signals via Haiku."""
    t0 = time.time()
    filtered = filter_signals(raw_signals)
    elapsed = time.time() - t0

    logger.info(
        "Step 2 - Filter: %d -> %d signals in %.1fs",
        len(raw_signals),
        len(filtered),
        elapsed,
    )
    return filtered


def _step_verify(filtered: list) -> list:
    """Step 3: Verify signals via Sonnet adversarial pair."""
    t0 = time.time()
    verified = verify_signals(filtered)
    elapsed = time.time() - t0

    logger.info(
        "Step 3 - Verify: %d -> %d facts in %.1fs",
        len(filtered),
        len(verified),
        elapsed,
    )
    return verified


def _step_generate(verified: list, signals_scanned: int) -> object:
    """Step 4: Generate digest via Opus."""
    t0 = time.time()
    digest = generate_digest(verified)
    elapsed = time.time() - t0

    # Update signals_scanned from actual scan count
    digest.signals_scanned = signals_scanned

    logger.info(
        "Step 4 - Generate: %d cards in %.1fs, cost=$%.4f",
        len(digest.cards),
        elapsed,
        digest.total_cost,
    )
    return digest


def _step_send(digest: object) -> None:
    """Step 5: Send digest to Telegram."""
    t0 = time.time()
    send_digest(digest)  # type: ignore[arg-type]
    elapsed = time.time() - t0

    logger.info("Step 5 - Send: delivered in %.1fs", elapsed)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the full scan-verify-publish pipeline."""
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    _setup_logging(verbose=verbose)

    logger.info("VIBE-SERCH pipeline starting")
    if not verbose:
        logger.info("Tip: run with --verbose to see full AI thinking process")
    t_total = time.time()

    try:
        raw_signals = _step_scan()
        filtered = _step_filter(raw_signals)
        verified = _step_verify(filtered)
        digest = _step_generate(verified, signals_scanned=len(raw_signals))
        _step_send(digest)
    except NotImplementedError as e:
        logger.warning("Pipeline step not yet implemented: %s", e)
        return
    except Exception:
        logger.exception("Pipeline failed")
        sys.exit(1)

    total_elapsed = time.time() - t_total
    logger.info("Pipeline complete in %.1fs", total_elapsed)


if __name__ == "__main__":
    main()
