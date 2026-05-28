"""Tests for application configuration."""

from src.config import Settings


class TestSettings:
    def test_settings_from_env_vars(self, monkeypatch: object) -> None:
        """Settings should load from environment variables."""
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        mp.setenv("REDDIT_CLIENT_ID", "test-client-id")
        mp.setenv("REDDIT_CLIENT_SECRET", "test-client-secret")
        mp.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
        mp.setenv("TELEGRAM_CHANNEL_ID", "@test_channel")

        try:
            settings = Settings(
                _env_file=None,  # type: ignore[call-arg]
            )
            assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test-key"
            assert settings.reddit_client_id == "test-client-id"
            assert settings.reddit_client_secret.get_secret_value() == "test-client-secret"
            assert settings.reddit_user_agent == "vibe-serch/1.0"
            assert settings.telegram_bot_token.get_secret_value() == "123456:ABC-DEF"
            assert settings.telegram_channel_id == "@test_channel"
        finally:
            mp.undo()

    def test_default_subreddits(self, monkeypatch: object) -> None:
        """Default subreddits list should be populated."""
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        mp.setenv("REDDIT_CLIENT_ID", "id")
        mp.setenv("REDDIT_CLIENT_SECRET", "secret")
        mp.setenv("TELEGRAM_BOT_TOKEN", "token")
        mp.setenv("TELEGRAM_CHANNEL_ID", "channel")

        try:
            settings = Settings(_env_file=None)  # type: ignore[call-arg]
            assert "vibecoding" in settings.subreddits
            assert "LocalLLaMA" in settings.subreddits
            assert len(settings.subreddits) == 5
        finally:
            mp.undo()

    def test_default_domain_tiers(self, monkeypatch: object) -> None:
        """Domain tiers should have correct defaults."""
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        mp.setenv("REDDIT_CLIENT_ID", "id")
        mp.setenv("REDDIT_CLIENT_SECRET", "secret")
        mp.setenv("TELEGRAM_BOT_TOKEN", "token")
        mp.setenv("TELEGRAM_CHANNEL_ID", "channel")

        try:
            settings = Settings(_env_file=None)  # type: ignore[call-arg]
            assert settings.domain_tiers["arxiv.org"] == 1
            assert settings.domain_tiers["techcrunch.com"] == 2
            assert settings.domain_tiers["news.ycombinator.com"] == 3
            assert settings.domain_tiers["reddit.com"] == 4
        finally:
            mp.undo()

    def test_default_min_score(self, monkeypatch: object) -> None:
        """min_score should default to 20."""
        import pytest

        mp = pytest.MonkeyPatch()
        mp.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        mp.setenv("REDDIT_CLIENT_ID", "id")
        mp.setenv("REDDIT_CLIENT_SECRET", "secret")
        mp.setenv("TELEGRAM_BOT_TOKEN", "token")
        mp.setenv("TELEGRAM_CHANNEL_ID", "channel")

        try:
            settings = Settings(_env_file=None)  # type: ignore[call-arg]
            assert settings.min_score == 20
        finally:
            mp.undo()
