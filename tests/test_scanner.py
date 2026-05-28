"""Tests for the Reddit scanner module (httpx-based, no auth)."""

from datetime import UTC
from unittest.mock import MagicMock, patch

import httpx

from src.models import RawSignal
from src.scanner import _get_source_tier, scan_reddit

DOMAIN_TIERS: dict[str, int] = {
    "arxiv.org": 1,
    "openai.com": 1,
    "anthropic.com": 1,
    "techcrunch.com": 2,
    "news.ycombinator.com": 3,
    "reddit.com": 4,
    "medium.com": 4,
}


class TestGetSourceTier:
    def test_tier1_known_domain(self) -> None:
        assert _get_source_tier("https://arxiv.org/abs/2401.00001", DOMAIN_TIERS) == 1

    def test_tier2_known_domain(self) -> None:
        assert _get_source_tier("https://techcrunch.com/article", DOMAIN_TIERS) == 2

    def test_tier3_known_domain(self) -> None:
        assert _get_source_tier("https://news.ycombinator.com/item?id=123", DOMAIN_TIERS) == 3

    def test_tier4_reddit_self_post(self) -> None:
        url = "https://www.reddit.com/r/ClaudeAI/comments/abc123/my_post/"
        assert _get_source_tier(url, DOMAIN_TIERS) == 4

    def test_tier5_unknown_domain(self) -> None:
        assert _get_source_tier("https://randomsite.xyz/page", DOMAIN_TIERS) == 5

    def test_subdomain_matching(self) -> None:
        assert _get_source_tier("https://blog.openai.com/post", DOMAIN_TIERS) == 1

    def test_www_prefix_stripped(self) -> None:
        assert _get_source_tier("https://www.arxiv.org/abs/123", DOMAIN_TIERS) == 1

    def test_empty_url_returns_tier4(self) -> None:
        assert _get_source_tier("", DOMAIN_TIERS) == 4

    def test_redd_it_shortlink(self) -> None:
        assert _get_source_tier("https://redd.it/abc123", DOMAIN_TIERS) == 4


def _reddit_json_response(posts: list[dict]) -> dict:
    """Build a fake Reddit JSON API response."""
    children = [{"kind": "t3", "data": p} for p in posts]
    return {"data": {"children": children}}


def _make_post(
    title: str = "Test post",
    selftext: str = "Body",
    url: str = "https://reddit.com/r/test/abc",
    score: int = 50,
    num_comments: int = 10,
    subreddit: str = "vibecoding",
    created_utc: float = 1700000000.0,
) -> dict:
    """Create a fake Reddit post dict."""
    return {
        "title": title,
        "selftext": selftext,
        "url": url,
        "score": score,
        "num_comments": num_comments,
        "subreddit": subreddit,
        "created_utc": created_utc,
    }


def _make_mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.reddit_user_agent = "test-agent"
    settings.subreddits = ["vibecoding", "ClaudeAI"]
    settings.min_score = 20
    settings.domain_tiers = DOMAIN_TIERS
    return settings


class TestScanReddit:
    @patch("src.scanner.get_settings")
    @patch("src.scanner.httpx.Client")
    def test_returns_raw_signals(
        self, mock_client_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_settings.return_value = _make_mock_settings()
        post = _make_post(score=50)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _reddit_json_response([post])
        mock_resp.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_client_cls.return_value.__enter__.return_value.get.return_value = mock_resp
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = scan_reddit()
        assert isinstance(result, list)
        assert all(isinstance(s, RawSignal) for s in result)

    @patch("src.scanner.get_settings")
    @patch("src.scanner.httpx.Client")
    def test_deduplication_by_url(
        self, mock_client_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_settings.return_value = _make_mock_settings()
        same_url = "https://arxiv.org/abs/2401.99999"
        posts = [
            _make_post(title="A", url=same_url, score=100),
            _make_post(title="B", url=same_url, score=200),
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = _reddit_json_response(posts)
        mock_resp.raise_for_status = MagicMock()
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = scan_reddit()
        urls = [s.url for s in result]
        assert urls.count(same_url) == 1

    @patch("src.scanner.get_settings")
    @patch("src.scanner.httpx.Client")
    def test_score_filtering(self, mock_client_cls: MagicMock, mock_settings: MagicMock) -> None:
        mock_settings.return_value = _make_mock_settings()
        posts = [
            _make_post(title="High", url="https://a.com/1", score=100),
            _make_post(title="Low", url="https://b.com/2", score=5),
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = _reddit_json_response(posts)
        mock_resp.raise_for_status = MagicMock()
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = scan_reddit()
        titles = [s.title for s in result]
        assert "High" in titles
        assert "Low" not in titles

    @patch("src.scanner.get_settings")
    @patch("src.scanner.httpx.Client")
    def test_subreddit_error_does_not_abort(
        self, mock_client_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        settings = _make_mock_settings()
        settings.subreddits = ["good_sub", "bad_sub"]
        mock_settings.return_value = settings

        good_post = _make_post(title="Good", url="https://good.com/1", score=50)
        call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            url = args[0] if args else kwargs.get("url", "")
            if "bad_sub" in str(url):
                raise httpx.HTTPError("fail")
            resp = MagicMock()
            resp.json.return_value = _reddit_json_response([good_post])
            resp.raise_for_status = MagicMock()
            return resp

        ctx = MagicMock()
        ctx.get.side_effect = get_side_effect
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = scan_reddit()
        assert len(result) >= 1
        assert result[0].title == "Good"

    @patch("src.scanner.get_settings")
    @patch("src.scanner.httpx.Client")
    def test_published_at_is_utc(
        self, mock_client_cls: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_settings.return_value = _make_mock_settings()
        post = _make_post(created_utc=1700000000.0)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _reddit_json_response([post])
        mock_resp.raise_for_status = MagicMock()
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = scan_reddit()
        assert result[0].published_at.tzinfo == UTC

    @patch("src.scanner.get_settings")
    @patch("src.scanner.httpx.Client")
    def test_source_is_reddit(self, mock_client_cls: MagicMock, mock_settings: MagicMock) -> None:
        mock_settings.return_value = _make_mock_settings()
        post = _make_post()
        mock_resp = MagicMock()
        mock_resp.json.return_value = _reddit_json_response([post])
        mock_resp.raise_for_status = MagicMock()
        ctx = MagicMock()
        ctx.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=ctx)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = scan_reddit()
        assert all(s.source == "reddit" for s in result)
