"""Tests for LLM client cost calculation (no API calls)."""

from src.llm import PRICING, calculate_cost


class TestCalculateCost:
    def test_haiku_cost(self) -> None:
        """Haiku cost should match known pricing."""
        cost = calculate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        expected = 0.80 + 4.00  # $0.80 input + $4.00 output per 1M
        assert abs(cost - expected) < 0.001

    def test_sonnet_cost(self) -> None:
        """Sonnet cost should match known pricing."""
        cost = calculate_cost("claude-sonnet-4-6-20250514", 1_000_000, 1_000_000)
        expected = 3.00 + 15.00
        assert abs(cost - expected) < 0.001

    def test_opus_cost(self) -> None:
        """Opus cost should match known pricing."""
        cost = calculate_cost("claude-opus-4-20250514", 1_000_000, 1_000_000)
        expected = 15.00 + 75.00
        assert abs(cost - expected) < 0.001

    def test_zero_tokens(self) -> None:
        """Zero tokens should cost nothing."""
        cost = calculate_cost("claude-haiku-4-5-20251001", 0, 0)
        assert cost == 0.0

    def test_small_token_count(self) -> None:
        """Small token counts should produce proportional costs."""
        cost = calculate_cost("claude-haiku-4-5-20251001", 1000, 500)
        expected_input = (1000 / 1_000_000) * 0.80
        expected_output = (500 / 1_000_000) * 4.00
        assert abs(cost - (expected_input + expected_output)) < 0.0001

    def test_unknown_model_falls_back_to_haiku(self) -> None:
        """Unknown model should fall back to Haiku pricing."""
        cost = calculate_cost("unknown-model", 1_000_000, 1_000_000)
        haiku_cost = calculate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        assert cost == haiku_cost

    def test_all_models_in_pricing(self) -> None:
        """All models in PRICING should have both input and output keys."""
        for model, prices in PRICING.items():
            assert "input" in prices, f"Missing input price for {model}"
            assert "output" in prices, f"Missing output price for {model}"
            assert prices["input"] > 0
            assert prices["output"] > 0
