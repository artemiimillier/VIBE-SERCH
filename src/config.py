"""Application configuration loaded from environment variables."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from .env file via pydantic-settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM - support both Anthropic direct and OpenRouter
    anthropic_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    # Reddit (PRAW) - optional for web-only mode
    reddit_client_id: str = ""
    reddit_client_secret: SecretStr | None = None
    reddit_user_agent: str = "vibe-serch/1.0"

    # Telegram - optional for web-only mode
    telegram_bot_token: SecretStr | None = None
    telegram_channel_id: str = ""

    # Cron schedule (display only; actual schedule lives in vercel.json, always UTC).
    cron_hour: int = 6
    cron_minute: int = 0
    cron_timezone: str = "UTC"

    # Pipeline limits (Vercel Hobby has a 60s function ceiling)
    max_signals_to_verify: int = 8

    # Scanner defaults
    subreddits: list[str] = [
        "vibecoding",
        "LocalLLaMA",
        "ClaudeAI",
        "cursor",
        "MachineLearning",
    ]
    min_score: int = 20

    # Domain trust tiers (1 = most trusted, 5 = least)
    domain_tiers: dict[str, int] = {
        # Tier 1 - primary sources
        "arxiv.org": 1,
        "anthropic.com": 1,
        "openai.com": 1,
        "github.blog": 1,
        "ai.meta.com": 1,
        # Tier 2 - established tech media
        "techcrunch.com": 2,
        "theverge.com": 2,
        "arstechnica.com": 2,
        "wired.com": 2,
        # Tier 3 - community hubs
        "news.ycombinator.com": 3,
        "producthunt.com": 3,
        # Tier 4 - user-generated
        "reddit.com": 4,
        "medium.com": 4,
        "dev.to": 4,
        "substack.com": 4,
    }

    @property
    def llm_api_key(self) -> str:
        """Return whichever LLM API key is available."""
        if self.anthropic_api_key:
            return self.anthropic_api_key.get_secret_value()
        if self.openrouter_api_key:
            return self.openrouter_api_key.get_secret_value()
        raise ValueError("No LLM API key configured (set ANTHROPIC_API_KEY or OPENROUTER_API_KEY)")

    @property
    def use_openrouter(self) -> bool:
        """True if using OpenRouter instead of Anthropic direct."""
        return self.anthropic_api_key is None and self.openrouter_api_key is not None


def get_settings() -> Settings:
    """Create and return a Settings instance."""
    return Settings()
