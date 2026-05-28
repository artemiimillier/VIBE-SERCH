"""Tests for Pydantic data models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.models import (
    AnalysisResult,
    DailyDigest,
    MethodCard,
    RawSignal,
    VerificationResult,
    VerifiedFact,
)


def _make_raw_signal(**overrides: object) -> dict:
    """Create a valid RawSignal data dict with optional overrides."""
    defaults: dict = {
        "title": "New AI tool released",
        "content": "A new tool for vibe-coding has been released.",
        "url": "https://example.com/post",
        "source": "reddit",
        "subreddit": "vibecoding",
        "score": 150,
        "num_comments": 42,
        "source_tier": 3,
        "published_at": datetime(2025, 5, 27, 10, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return defaults


def _make_analysis_result(**overrides: object) -> dict:
    defaults: dict = {
        "significance": 0.85,
        "credibility_args": ["Published by official team", "Has code repo"],
        "key_claims": ["50% faster inference"],
    }
    defaults.update(overrides)
    return defaults


def _make_verification_result(**overrides: object) -> dict:
    defaults: dict = {
        "weaknesses": ["No independent benchmarks"],
        "hype_risk": "Medium - only self-reported metrics",
        "confidence": 0.7,
        "hype_rating": "likely",
        "reasoning": "Claims are plausible but unverified externally.",
    }
    defaults.update(overrides)
    return defaults


class TestRawSignal:
    def test_valid(self) -> None:
        signal = RawSignal(**_make_raw_signal())
        assert signal.title == "New AI tool released"
        assert signal.source_tier == 3

    def test_invalid_score_negative(self) -> None:
        with pytest.raises(ValidationError):
            RawSignal(**_make_raw_signal(score=-1))

    def test_invalid_source_tier_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            RawSignal(**_make_raw_signal(source_tier=0))
        with pytest.raises(ValidationError):
            RawSignal(**_make_raw_signal(source_tier=6))

    def test_missing_required_field(self) -> None:
        data = _make_raw_signal()
        del data["url"]
        with pytest.raises(ValidationError):
            RawSignal(**data)


class TestAnalysisResult:
    def test_valid(self) -> None:
        result = AnalysisResult(**_make_analysis_result())
        assert result.significance == 0.85

    def test_significance_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisResult(**_make_analysis_result(significance=1.5))
        with pytest.raises(ValidationError):
            AnalysisResult(**_make_analysis_result(significance=-0.1))


class TestVerificationResult:
    def test_valid(self) -> None:
        result = VerificationResult(**_make_verification_result())
        assert result.hype_rating == "likely"

    def test_invalid_hype_rating(self) -> None:
        with pytest.raises(ValidationError):
            VerificationResult(**_make_verification_result(hype_rating="unknown"))

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            VerificationResult(**_make_verification_result(confidence=2.0))


class TestVerifiedFact:
    def test_valid(self) -> None:
        fact = VerifiedFact(
            signal=RawSignal(**_make_raw_signal()),
            analysis=AnalysisResult(**_make_analysis_result()),
            verification=VerificationResult(**_make_verification_result()),
            trust_score=0.75,
        )
        assert fact.trust_score == 0.75

    def test_trust_score_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            VerifiedFact(
                signal=RawSignal(**_make_raw_signal()),
                analysis=AnalysisResult(**_make_analysis_result()),
                verification=VerificationResult(**_make_verification_result()),
                trust_score=1.5,
            )


class TestMethodCard:
    def test_valid(self) -> None:
        card = MethodCard(
            title="New tool: FastCode",
            category="Tools",
            hype_rating="confirmed",
            hype_label="Confirmed",
            summary="FastCode speeds up development by 2x.",
            why_important="Reduces iteration time for vibe-coders.",
            action="Try FastCode at fastcode.dev",
            source_url="https://example.com/fastcode",
        )
        assert card.category == "Tools"

    def test_invalid_category(self) -> None:
        with pytest.raises(ValidationError):
            MethodCard(
                title="Test",
                category="InvalidCategory",
                hype_rating="confirmed",
                hype_label="Confirmed",
                summary="Test",
                why_important="Test",
                action="Test",
                source_url="https://example.com",
            )


class TestDailyDigest:
    def test_valid_empty_cards(self) -> None:
        digest = DailyDigest(
            cards=[],
            date=datetime(2025, 5, 27, tzinfo=UTC),
            total_cost=0.05,
            signals_scanned=100,
            signals_verified=10,
        )
        assert digest.signals_scanned == 100

    def test_negative_cost(self) -> None:
        with pytest.raises(ValidationError):
            DailyDigest(
                cards=[],
                date=datetime(2025, 5, 27, tzinfo=UTC),
                total_cost=-1.0,
                signals_scanned=0,
                signals_verified=0,
            )
