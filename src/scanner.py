"""Reddit scanner - fetches signals from subreddits via public JSON API (no auth needed)."""

import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from src.config import get_settings
from src.models import RawSignal

logger = logging.getLogger(__name__)

REDDIT_BASE = "https://www.reddit.com"
REQUEST_TIMEOUT = 15.0


def _get_source_tier(url: str, domain_tiers: dict[str, int]) -> int:
    """Determine the trust tier for a URL based on its domain."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    if _is_reddit_self_post(hostname, url):
        return 4

    if hostname in domain_tiers:
        return domain_tiers[hostname]

    bare = hostname.removeprefix("www.")
    if bare in domain_tiers:
        return domain_tiers[bare]

    return _match_subdomain(bare, domain_tiers)


def _is_reddit_self_post(hostname: str, url: str) -> bool:
    """Check if the URL points to a Reddit self-post."""
    if not url or not hostname:
        return True
    return hostname.endswith("reddit.com") or hostname.endswith("redd.it")


def _match_subdomain(bare_host: str, domain_tiers: dict[str, int]) -> int:
    """Try matching parent domains against the tiers dict."""
    parts = bare_host.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[i:])
        if parent in domain_tiers:
            return domain_tiers[parent]
    return 5


def _fetch_subreddit_json(
    client: httpx.Client,
    subreddit: str,
    sort: str,
    params: dict | None = None,
) -> list[dict]:
    """Fetch posts from a subreddit's public JSON endpoint."""
    url = f"{REDDIT_BASE}/r/{subreddit}/{sort}.json"
    all_params = {"limit": "50", "raw_json": "1"}
    if params:
        all_params.update(params)

    resp = client.get(url, params=all_params)
    resp.raise_for_status()
    data = resp.json()

    children = data.get("data", {}).get("children", [])
    return [child["data"] for child in children if child.get("kind") == "t3"]


def _post_to_signal(post: dict, domain_tiers: dict[str, int]) -> RawSignal:
    """Convert a Reddit JSON post dict to a RawSignal."""
    return RawSignal(
        title=post.get("title", ""),
        content=post.get("selftext", "") or "",
        url=post.get("url", ""),
        source="reddit",
        subreddit=post.get("subreddit", ""),
        score=max(post.get("score", 0), 0),
        num_comments=max(post.get("num_comments", 0), 0),
        source_tier=_get_source_tier(post.get("url", ""), domain_tiers),
        published_at=datetime.fromtimestamp(post.get("created_utc", 0), tz=UTC),
    )


def _fetch_subreddit_posts(
    client: httpx.Client,
    subreddit: str,
    min_score: int,
    domain_tiers: dict[str, int],
) -> dict[str, RawSignal]:
    """Fetch hot + top(day) posts from a single subreddit."""
    signals: dict[str, RawSignal] = {}

    hot_posts = _fetch_subreddit_json(client, subreddit, "hot")
    top_posts = _fetch_subreddit_json(client, subreddit, "top", {"t": "day"})
    all_posts = hot_posts + top_posts

    total = len(all_posts)
    passed = 0

    for post in all_posts:
        if post.get("score", 0) < min_score:
            continue
        url = post.get("url", "")
        if url in signals:
            continue
        signals[url] = _post_to_signal(post, domain_tiers)
        passed += 1

    logger.info(
        "r/%s: scanned %d posts, %d passed filter (min_score=%d)",
        subreddit,
        total,
        passed,
        min_score,
    )
    return signals


def scan_reddit() -> list[RawSignal]:
    """Scan all configured subreddits via public JSON API (no auth needed)."""
    settings = get_settings()
    all_signals: dict[str, RawSignal] = {}

    headers = {"User-Agent": settings.reddit_user_agent}
    with httpx.Client(headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        for name in settings.subreddits:
            try:
                sub_signals = _fetch_subreddit_posts(
                    client, name, settings.min_score, settings.domain_tiers
                )
                for url, signal in sub_signals.items():
                    if url not in all_signals:
                        all_signals[url] = signal
            except Exception:
                logger.exception("Failed to scan r/%s, skipping", name)

    logger.info("Total unique signals collected: %d", len(all_signals))
    return list(all_signals.values())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    results = scan_reddit()
    print(f"\nCollected {len(results)} signals\n")
    for sig in results[:5]:
        print(f"  [{sig.source_tier}] r/{sig.subreddit} | {sig.score}pts | {sig.title[:80]}")
