"""Tests for the pipeline: filtering, verification, digest generation."""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from src.models import (
    AnalysisResult,
    DailyDigest,
    MethodCard,
    RawSignal,
    VerificationResult,
    VerifiedFact,
)
from src.pipeline import (
    TELEGRAM_MAX_LENGTH,
    _apply_threshold,
    _compute_trust_score,
    _format_digest_text,
    _parse_json_response,
    filter_signals,
    generate_digest,
    verify_signals,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_signal(**overrides: object) -> RawSignal:
    """Create a valid RawSignal with optional overrides."""
    defaults: dict = {
        "title": "New AI tool released",
        "content": "A new tool for vibe-coding.",
        "url": "https://example.com/post",
        "source": "reddit",
        "subreddit": "vibecoding",
        "score": 150,
        "num_comments": 42,
        "source_tier": 3,
        "published_at": datetime(2025, 5, 27, 10, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return RawSignal(**defaults)


def _make_fact(
    trust_score: float = 0.7,
    confidence: float = 0.8,
    **signal_overrides: object,
) -> VerifiedFact:
    """Create a valid VerifiedFact for threshold tests."""
    return VerifiedFact(
        signal=_make_signal(**signal_overrides),
        analysis=AnalysisResult(
            significance=0.85,
            credibility_args=["Official source"],
            key_claims=["50% faster inference"],
        ),
        verification=VerificationResult(
            weaknesses=["No benchmarks"],
            hype_risk="Medium",
            confidence=confidence,
            hype_rating="likely",
            reasoning="Plausible but unverified.",
        ),
        trust_score=trust_score,
    )


# ---------------------------------------------------------------------------
# _parse_json_response
# ---------------------------------------------------------------------------


class TestParseJsonResponse:
    def test_valid_raw_json(self) -> None:
        text = '{"significance": 0.8, "credibility_args": ["a"], "key_claims": ["b"]}'
        result = _parse_json_response(text, AnalysisResult)
        assert isinstance(result, AnalysisResult)
        assert result.significance == 0.8

    def test_json_in_markdown_block(self) -> None:
        text = (
            "Here is my analysis:\n```json\n"
            '{"significance": 0.9, "credibility_args": ["x"], "key_claims": ["y"]}'
            "\n```\n"
        )
        result = _parse_json_response(text, AnalysisResult)
        assert isinstance(result, AnalysisResult)
        assert result.significance == 0.9

    def test_json_in_plain_code_block(self) -> None:
        text = '```\n{"significance": 0.5, "credibility_args": [], "key_claims": ["c"]}\n```'
        result = _parse_json_response(text, AnalysisResult)
        assert isinstance(result, AnalysisResult)
        assert result.significance == 0.5

    def test_invalid_json_returns_none(self) -> None:
        result = _parse_json_response("not json at all", AnalysisResult)
        assert result is None

    def test_valid_json_wrong_schema_returns_none(self) -> None:
        text = '{"wrong_field": 42}'
        result = _parse_json_response(text, AnalysisResult)
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = _parse_json_response("", AnalysisResult)
        assert result is None

    def test_json_with_surrounding_text(self) -> None:
        text = (
            'Sure, here is the result: {"significance": 0.6, '
            '"credibility_args": ["z"], "key_claims": ["w"]} Hope this helps!'
        )
        result = _parse_json_response(text, AnalysisResult)
        assert isinstance(result, AnalysisResult)
        assert result.significance == 0.6

    def test_verification_result_parsing(self) -> None:
        text = json.dumps(
            {
                "weaknesses": ["w1"],
                "hype_risk": "High",
                "confidence": 0.4,
                "hype_rating": "unverified",
                "reasoning": "No evidence.",
            }
        )
        result = _parse_json_response(text, VerificationResult)
        assert isinstance(result, VerificationResult)
        assert result.confidence == 0.4


# ---------------------------------------------------------------------------
# _compute_trust_score
# ---------------------------------------------------------------------------


class TestComputeTrustScore:
    def test_tier1_high_scores(self) -> None:
        score = _compute_trust_score(1.0, 1.0, 1)
        # 1.0*0.4 + 1.0*0.35 + 1.0*0.25 = 1.0
        assert score == pytest.approx(1.0, abs=0.01)

    def test_tier5_zero_scores(self) -> None:
        score = _compute_trust_score(0.0, 0.0, 5)
        # 0*0.4 + 0*0.35 + 0*0.25 = 0.0
        assert score == pytest.approx(0.0, abs=0.01)

    def test_mid_values(self) -> None:
        # significance=0.7, confidence=0.6, tier=3 -> tier_weight=0.5
        # 0.7*0.4 + 0.6*0.35 + 0.5*0.25 = 0.28 + 0.21 + 0.125 = 0.615
        score = _compute_trust_score(0.7, 0.6, 3)
        assert score == pytest.approx(0.615, abs=0.01)

    def test_tier_weight_calculation(self) -> None:
        # tier 1 -> weight 1.0, tier 2 -> 0.75, tier 3 -> 0.5, tier 4 -> 0.25, tier 5 -> 0.0
        for tier, expected_weight in [(1, 1.0), (2, 0.75), (3, 0.5), (4, 0.25), (5, 0.0)]:
            score = _compute_trust_score(0.0, 0.0, tier)
            assert score == pytest.approx(expected_weight * 0.25, abs=0.001)

    def test_result_clamped_to_unit_range(self) -> None:
        score = _compute_trust_score(1.0, 1.0, 1)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# filter_signals (mocked LLM)
# ---------------------------------------------------------------------------


class TestFilterSignals:
    def test_empty_input(self) -> None:
        assert filter_signals([]) == []

    @patch("src.pipeline.call_llm")
    def test_keeps_selected_urls(self, mock_llm: object) -> None:
        s1 = _make_signal(url="https://a.com/1", title="Signal 1")
        s2 = _make_signal(url="https://b.com/2", title="Signal 2")
        s3 = _make_signal(url="https://c.com/3", title="Signal 3")

        mock_llm.return_value = json.dumps(  # type: ignore[attr-defined]
            {"keep": ["https://a.com/1", "https://c.com/3"]}
        )

        result = filter_signals([s1, s2, s3])
        urls = {s.url for s in result}
        assert urls == {"https://a.com/1", "https://c.com/3"}

    @patch("src.pipeline.call_llm")
    def test_fallback_on_garbage_response(self, mock_llm: object) -> None:
        s1 = _make_signal(url="https://a.com/1")
        mock_llm.return_value = "I cannot parse this lol"  # type: ignore[attr-defined]

        result = filter_signals([s1])
        assert len(result) == 1
        assert result[0].url == "https://a.com/1"

    @patch("src.pipeline.call_llm")
    def test_fallback_on_empty_keep_list(self, mock_llm: object) -> None:
        s1 = _make_signal(url="https://a.com/1")
        mock_llm.return_value = json.dumps({"keep": []})  # type: ignore[attr-defined]

        result = filter_signals([s1])
        assert len(result) == 1

    @patch("src.pipeline.call_llm")
    def test_markdown_wrapped_response(self, mock_llm: object) -> None:
        s1 = _make_signal(url="https://a.com/1")
        s2 = _make_signal(url="https://b.com/2")
        mock_llm.return_value = (  # type: ignore[attr-defined]
            '```json\n{"keep": ["https://a.com/1"]}\n```'
        )

        result = filter_signals([s1, s2])
        assert len(result) == 1
        assert result[0].url == "https://a.com/1"


# ---------------------------------------------------------------------------
# verify_signals (mocked LLM)
# ---------------------------------------------------------------------------


class TestVerifySignals:
    def test_empty_input(self) -> None:
        assert verify_signals([]) == []

    @patch("src.pipeline.call_llm")
    def test_successful_verification(self, mock_llm: object) -> None:
        """Both analyst and verifier return valid JSON."""
        analyst_resp = json.dumps(
            {
                "significance": 0.8,
                "credibility_args": ["Official blog"],
                "key_claims": ["2x faster"],
            }
        )
        verifier_resp = json.dumps(
            {
                "weaknesses": ["Self-reported"],
                "hype_risk": "Medium",
                "confidence": 0.7,
                "hype_rating": "likely",
                "reasoning": "Plausible.",
            }
        )
        mock_llm.side_effect = [analyst_resp, verifier_resp]  # type: ignore[attr-defined]

        signal = _make_signal(source_tier=2)
        result = verify_signals([signal])

        assert len(result) == 1
        fact = result[0]
        assert fact.analysis.significance == 0.8
        assert fact.verification.confidence == 0.7
        assert fact.trust_score > 0

    @patch("src.pipeline.call_llm")
    def test_analyst_failure_skips_signal(self, mock_llm: object) -> None:
        mock_llm.return_value = "garbage"  # type: ignore[attr-defined]

        signal = _make_signal()
        result = verify_signals([signal])
        assert result == []

    @patch("src.pipeline.call_llm")
    def test_verifier_failure_skips_signal(self, mock_llm: object) -> None:
        analyst_resp = json.dumps(
            {
                "significance": 0.8,
                "credibility_args": ["x"],
                "key_claims": ["y"],
            }
        )
        mock_llm.side_effect = [analyst_resp, "garbage"]  # type: ignore[attr-defined]

        signal = _make_signal()
        result = verify_signals([signal])
        assert result == []

    @patch("src.pipeline.call_llm")
    def test_low_confidence_filtered_out(self, mock_llm: object) -> None:
        """Signal with confidence <= 0.5 should be rejected."""
        analyst_resp = json.dumps(
            {
                "significance": 0.9,
                "credibility_args": ["Hype"],
                "key_claims": ["Revolutionary"],
            }
        )
        verifier_resp = json.dumps(
            {
                "weaknesses": ["All hype"],
                "hype_risk": "Very high",
                "confidence": 0.3,
                "hype_rating": "disputed",
                "reasoning": "No evidence.",
            }
        )
        mock_llm.side_effect = [analyst_resp, verifier_resp]  # type: ignore[attr-defined]

        signal = _make_signal()
        result = verify_signals([signal])
        assert result == []

    @patch("src.pipeline.call_llm")
    def test_multiple_signals_sorted_by_trust(self, mock_llm: object) -> None:
        """Multiple signals should come back sorted by trust_score desc."""
        # Signal A - high trust
        analyst_a = json.dumps(
            {
                "significance": 0.9,
                "credibility_args": ["Official"],
                "key_claims": ["Claim A"],
            }
        )
        verifier_a = json.dumps(
            {
                "weaknesses": [],
                "hype_risk": "Low",
                "confidence": 0.9,
                "hype_rating": "confirmed",
                "reasoning": "Solid.",
            }
        )
        # Signal B - medium trust
        analyst_b = json.dumps(
            {
                "significance": 0.5,
                "credibility_args": ["Blog"],
                "key_claims": ["Claim B"],
            }
        )
        verifier_b = json.dumps(
            {
                "weaknesses": ["Vague"],
                "hype_risk": "Medium",
                "confidence": 0.6,
                "hype_rating": "likely",
                "reasoning": "OK.",
            }
        )
        mock_llm.side_effect = [  # type: ignore[attr-defined]
            analyst_a,
            verifier_a,
            analyst_b,
            verifier_b,
        ]

        sig_a = _make_signal(url="https://a.com", source_tier=1)
        sig_b = _make_signal(url="https://b.com", source_tier=4)

        result = verify_signals([sig_a, sig_b])
        assert len(result) == 2
        assert result[0].trust_score >= result[1].trust_score


# ---------------------------------------------------------------------------
# _apply_threshold
# ---------------------------------------------------------------------------


class TestApplyThreshold:
    def test_filters_low_confidence(self) -> None:
        low = _make_fact(trust_score=0.8, confidence=0.3)
        high = _make_fact(trust_score=0.7, confidence=0.8, url="https://b.com")
        result = _apply_threshold([low, high])
        assert len(result) == 1
        assert result[0].verification.confidence == 0.8

    def test_sorted_by_trust_score_desc(self) -> None:
        f1 = _make_fact(trust_score=0.5, confidence=0.6, url="https://1.com")
        f2 = _make_fact(trust_score=0.9, confidence=0.9, url="https://2.com")
        f3 = _make_fact(trust_score=0.7, confidence=0.7, url="https://3.com")
        result = _apply_threshold([f1, f2, f3])
        scores = [f.trust_score for f in result]
        assert scores == sorted(scores, reverse=True)

    def test_max_seven_results(self) -> None:
        facts = [
            _make_fact(
                trust_score=round(0.5 + i * 0.01, 2),
                confidence=0.8,
                url=f"https://{i}.com",
            )
            for i in range(10)
        ]
        result = _apply_threshold(facts)
        assert len(result) == 7

    def test_empty_input(self) -> None:
        assert _apply_threshold([]) == []

    def test_all_below_threshold(self) -> None:
        facts = [
            _make_fact(trust_score=0.8, confidence=0.4, url=f"https://{i}.com") for i in range(5)
        ]
        result = _apply_threshold(facts)
        assert result == []

    def test_boundary_confidence_excluded(self) -> None:
        """Confidence exactly 0.5 should be excluded (> 0.5 required)."""
        fact = _make_fact(trust_score=0.8, confidence=0.5)
        result = _apply_threshold([fact])
        assert result == []

    def test_boundary_confidence_included(self) -> None:
        """Confidence 0.51 should pass."""
        fact = _make_fact(trust_score=0.8, confidence=0.51)
        result = _apply_threshold([fact])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Phase 3 - generate_digest (mocked LLM)
# ---------------------------------------------------------------------------

_VALID_DIGEST_RESPONSE = json.dumps(
    {
        "cards": [
            {
                "title": "Claude 4 Opus released",
                "category": "Tools",
                "hype_rating": "confirmed",
                "hype_label": "✅ Подтверждено",
                "summary": "What: Anthropic released Claude 4 Opus with improved coding.",
                "why_important": "Why: Best-in-class for agentic tasks.",
                "action": "Action: Try it on your hardest coding task today.",
                "source_url": "https://anthropic.com/claude-4",
            },
            {
                "title": "Cursor adds MCP support",
                "category": "Methods",
                "hype_rating": "likely",
                "hype_label": "🟡 Вероятно",
                "summary": "What: Cursor IDE now supports MCP protocol natively.",
                "why_important": "Why: Simplifies AI tool integration.",
                "action": "Action: Update Cursor and configure MCP servers.",
                "source_url": "https://cursor.com/mcp",
            },
        ]
    }
)


class TestGenerateDigest:
    @patch("src.pipeline.call_llm")
    def test_valid_response(self, mock_llm: object) -> None:
        """LLM returns valid JSON - should produce a digest with cards."""
        mock_llm.return_value = _VALID_DIGEST_RESPONSE  # type: ignore[attr-defined]

        facts = [_make_fact(url=f"https://{i}.com") for i in range(2)]
        digest = generate_digest(facts)

        assert isinstance(digest, DailyDigest)
        assert len(digest.cards) == 2
        assert digest.cards[0].title == "Claude 4 Opus released"
        assert digest.cards[0].category == "Tools"
        assert digest.cards[0].hype_rating == "confirmed"
        assert digest.cards[1].hype_rating == "likely"
        assert digest.signals_verified == 2

    @patch("src.pipeline.call_llm")
    def test_calls_opus(self, mock_llm: object) -> None:
        """Should call LLM with model_tier='best'."""
        mock_llm.return_value = _VALID_DIGEST_RESPONSE  # type: ignore[attr-defined]

        facts = [_make_fact()]
        generate_digest(facts)

        mock_llm.assert_called_once()  # type: ignore[attr-defined]
        call_args = mock_llm.call_args  # type: ignore[attr-defined]
        assert call_args[1].get("model_tier") == "best" or call_args[0][1] == "best"

    def test_empty_facts(self) -> None:
        """Empty input should return empty digest without calling LLM."""
        digest = generate_digest([])
        assert isinstance(digest, DailyDigest)
        assert digest.cards == []
        assert digest.signals_verified == 0

    @patch("src.pipeline.call_llm")
    def test_garbage_response_returns_empty(self, mock_llm: object) -> None:
        """LLM returns garbage - should return digest with no cards."""
        mock_llm.return_value = "I don't know what to say"  # type: ignore[attr-defined]

        facts = [_make_fact()]
        digest = generate_digest(facts)

        assert isinstance(digest, DailyDigest)
        assert digest.cards == []
        assert digest.signals_verified == 1

    @patch("src.pipeline.call_llm")
    def test_invalid_json_structure(self, mock_llm: object) -> None:
        """LLM returns valid JSON but wrong schema."""
        mock_llm.return_value = json.dumps(  # type: ignore[attr-defined]
            {"wrong_key": "value"}
        )

        facts = [_make_fact()]
        digest = generate_digest(facts)

        assert isinstance(digest, DailyDigest)
        assert digest.cards == []

    @patch("src.pipeline.call_llm")
    def test_category_normalization(self, mock_llm: object) -> None:
        """LLM returns lowercase category - should be normalized."""
        response = json.dumps(
            {
                "cards": [
                    {
                        "title": "Test",
                        "category": "tools",
                        "hype_rating": "likely",
                        "hype_label": "🟡 Вероятно",
                        "summary": "What: test",
                        "why_important": "Why: test",
                        "action": "Action: test",
                        "source_url": "https://test.com",
                    }
                ]
            }
        )
        mock_llm.return_value = response  # type: ignore[attr-defined]

        digest = generate_digest([_make_fact()])
        assert digest.cards[0].category == "Tools"

    @patch("src.pipeline.call_llm")
    def test_hype_label_override(self, mock_llm: object) -> None:
        """Hype label should use the canonical mapping, not raw LLM output."""
        response = json.dumps(
            {
                "cards": [
                    {
                        "title": "Test",
                        "category": "Research",
                        "hype_rating": "disputed",
                        "hype_label": "wrong label",
                        "summary": "What: test",
                        "why_important": "Why: test",
                        "action": "Action: test",
                        "source_url": "https://test.com",
                    }
                ]
            }
        )
        mock_llm.return_value = response  # type: ignore[attr-defined]

        digest = generate_digest([_make_fact()])
        assert digest.cards[0].hype_label == "🟠 Спорно"


# ---------------------------------------------------------------------------
# _format_digest_text
# ---------------------------------------------------------------------------


def _make_digest(**overrides: object) -> DailyDigest:
    """Create a DailyDigest for formatting tests."""
    defaults: dict = {
        "cards": [
            MethodCard(
                title="Test Tool",
                category="Tools",
                hype_rating="confirmed",
                hype_label="✅ Подтверждено",
                summary="What: A new tool for testing.",
                why_important="Why: Makes tests faster.",
                action="Action: Install and run.",
                source_url="https://example.com",
            )
        ],
        "date": datetime(2025, 5, 27, 8, 0, 0, tzinfo=UTC),
        "total_cost": 0.0,
        "signals_scanned": 50,
        "signals_verified": 5,
    }
    defaults.update(overrides)
    return DailyDigest(**defaults)


class TestFormatDigestText:
    def test_contains_header(self) -> None:
        digest = _make_digest()
        text = _format_digest_text(digest)
        assert "VIBE-SERCH" in text
        assert "27.05.2025" in text
        assert "50 фактов проверено" in text
        assert "5 прошли верификацию" in text

    def test_contains_footer(self) -> None:
        digest = _make_digest()
        text = _format_digest_text(digest)
        assert "мультиагентной системой VIBE-SERCH" in text

    def test_contains_card_content(self) -> None:
        digest = _make_digest()
        text = _format_digest_text(digest)
        assert "[Tools] Test Tool" in text
        assert "✅ Подтверждено" in text
        assert "What: A new tool for testing." in text
        assert "Источник: https://example.com" in text

    def test_under_telegram_limit(self) -> None:
        digest = _make_digest()
        text = _format_digest_text(digest)
        assert len(text) <= TELEGRAM_MAX_LENGTH

    def test_empty_cards(self) -> None:
        digest = _make_digest(cards=[])
        text = _format_digest_text(digest)
        assert "VIBE-SERCH" in text
        assert "мультиагентной системой" in text

    def test_trims_cards_when_exceeding_limit(self) -> None:
        """Many long cards should be trimmed to fit under 4096."""
        long_cards = [
            MethodCard(
                title=f"Card {i} with a very long title " + "x" * 100,
                category="Tools",
                hype_rating="confirmed",
                hype_label="✅ Подтверждено",
                summary="What: " + "Detail. " * 50,
                why_important="Why: " + "Important. " * 30,
                action="Action: " + "Step. " * 30,
                source_url=f"https://example.com/{i}",
            )
            for i in range(20)
        ]
        digest = _make_digest(cards=long_cards)
        text = _format_digest_text(digest)
        assert len(text) <= TELEGRAM_MAX_LENGTH
