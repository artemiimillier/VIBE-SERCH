"""Filtering, verification, and digest generation pipeline."""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from pydantic import BaseModel

from src.llm import call_llm
from src.models import (
    AnalysisResult,
    DailyDigest,
    MethodCard,
    RawSignal,
    VerificationResult,
    VerifiedFact,
)

VERIFY_WORKERS = 4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------


def _parse_json_response(text: str, model_class: type[BaseModel]) -> BaseModel | None:
    """Extract JSON from LLM response and parse with a Pydantic model.

    Handles raw JSON, markdown code blocks, and extra text.
    Returns None on failure (with a warning log).
    """
    json_str = _extract_json_string(text)
    if json_str is None:
        logger.warning("No JSON found in LLM response: %.200s", text)
        return None
    return _validate_json(json_str, model_class)


def _extract_json_string(text: str) -> str | None:
    """Pull the first JSON object from text or code blocks."""
    # Try markdown code block first (```json ... ``` or ``` ... ```)
    block_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if block_match:
        return block_match.group(1).strip()

    # Try to find raw JSON object
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        return brace_match.group(0)

    return None


def _validate_json(json_str: str, model_class: type[BaseModel]) -> BaseModel | None:
    """Parse JSON string and validate against a Pydantic model."""
    try:
        data = json.loads(json_str)
        return model_class.model_validate(data)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("Failed to parse JSON into %s: %s", model_class.__name__, exc)
        return None


# ---------------------------------------------------------------------------
# Trust score computation
# ---------------------------------------------------------------------------


def _compute_trust_score(
    significance: float,
    confidence: float,
    source_tier: int,
) -> float:
    """Compute composite trust score from analysis, verification, and tier.

    Formula: significance * 0.4 + confidence * 0.35 + tier_weight * 0.25
    tier_weight = (5 - source_tier) / 4  (tier 1 -> 1.0, tier 5 -> 0.0)
    """
    tier_weight = (5 - source_tier) / 4.0
    raw = significance * 0.4 + confidence * 0.35 + tier_weight * 0.25
    return round(min(max(raw, 0.0), 1.0), 4)


# ---------------------------------------------------------------------------
# Phase 2.1 - filter_signals (Haiku)
# ---------------------------------------------------------------------------

_FILTER_PROMPT_TEMPLATE = """\
You are an AI news editor for a daily developer digest about vibe-coding and AI tools.

Below is a list of {count} raw signals from Reddit. Each has an index, title, and URL.

SIGNALS:
{signal_list}

Your task:
1. Remove DUPLICATES (same news from different sources).
2. Remove IRRELEVANT signals (keep ONLY: vibe-coding, AI tools for development, \
significant AI model releases/updates, AI agent frameworks).
3. Group similar signals and pick the best representative.
4. Select the top 20 most significant, unique signals.

Reply ONLY with JSON:
{{"keep": ["url1", "url2", ...]}}
"""


def filter_signals(signals: list[RawSignal]) -> list[RawSignal]:
    """Filter raw signals via Haiku for relevance and dedup.

    Returns filtered list. Falls back to original on parse error.
    """
    if not signals:
        return []

    logger.info("=" * 60)
    logger.info("PHASE: FILTER - sending %d signals to Haiku", len(signals))
    logger.info("=" * 60)
    prompt = _build_filter_prompt(signals)
    response = call_llm(prompt, model_tier="fast", label="FILTER")
    kept = _parse_filter_response(response, signals)

    logger.info(
        "FILTER RESULT: %d -> %d signals kept",
        len(signals),
        len(kept),
    )
    for s in kept:
        logger.debug("  KEPT: [tier %d] r/%s | %s", s.source_tier, s.subreddit, s.title[:80])
    return kept


def _build_filter_prompt(signals: list[RawSignal]) -> str:
    """Build the filtering prompt with all signal summaries."""
    lines: list[str] = []
    for i, sig in enumerate(signals, 1):
        title = sig.title[:120]
        lines.append(f"{i}. [{sig.subreddit}] {title} | {sig.url}")
    return _FILTER_PROMPT_TEMPLATE.format(count=len(signals), signal_list="\n".join(lines))


def _parse_filter_response(response: str, signals: list[RawSignal]) -> list[RawSignal]:
    """Parse Haiku filter response; fall back to originals on error."""
    json_str = _extract_json_string(response)
    if json_str is None:
        logger.warning("Filter response unparseable, returning all signals")
        return signals

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("Filter response invalid JSON, returning all signals")
        return signals

    urls_to_keep = set(data.get("keep", []))
    if not urls_to_keep:
        logger.warning("Filter returned empty keep list, returning all signals")
        return signals

    kept = [s for s in signals if s.url in urls_to_keep]
    if not kept:
        logger.warning("No signals matched kept URLs, returning all signals")
        return signals

    return kept


# ---------------------------------------------------------------------------
# Phase 2.2 - verify_signals (Sonnet adversarial with info asymmetry)
# ---------------------------------------------------------------------------

_ANALYST_PROMPT_TEMPLATE = """\
You are an AI industry analyst. Evaluate this signal:

TITLE: {title}
CONTENT: {content}
SOURCE: {url} (trust tier {source_tier}/5)
SUBREDDIT: r/{subreddit} | Score: {score}

Assess:
1. How significant is this for developers/entrepreneurs building with AI? (0.0-1.0)
2. What are the credibility arguments FOR this being true and important?
3. What are the key factual claims made?

Reply ONLY with JSON:
{{
  "significance": 0.0,
  "credibility_args": ["arg1", "arg2"],
  "key_claims": ["claim1", "claim2", "claim3"]
}}"""

_VERIFIER_PROMPT_TEMPLATE = """\
You are a fact-checker and skeptic. You receive a set of claims \
WITHOUT context of who made them or why.

CLAIMS:
{claims_list}

SOURCE TIER: {source_tier}/5 (1=primary source like arxiv, 5=unknown)

For each claim, find weaknesses:
- What could be exaggerated or oversold?
- What could be inaccurate or taken out of context?
- What's the hype risk?

Reply ONLY with JSON:
{{
  "weaknesses": ["weakness1", "weakness2"],
  "hype_risk": "description of hype risk",
  "confidence": 0.0,
  "hype_rating": "confirmed|likely|unverified|disputed|debunked",
  "reasoning": "1-2 sentences explaining the verdict"
}}"""


def verify_signals(signals: list[RawSignal]) -> list[VerifiedFact]:
    """Run adversarial analyst+verifier on each signal.

    Returns only facts with confidence > 0.5, sorted by trust_score,
    limited to top 7.
    """
    if not signals:
        return []

    from src.config import get_settings

    cap = get_settings().max_signals_to_verify
    if len(signals) > cap:
        signals = sorted(signals, key=lambda s: s.score, reverse=True)[:cap]

    logger.info("=" * 60)
    logger.info(
        "PHASE: VERIFY - %d signals (capped), %d parallel workers, 2 AI calls each",
        len(signals), VERIFY_WORKERS,
    )
    logger.info("=" * 60)

    all_facts: list[VerifiedFact] = []

    with ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as pool:
        future_to_idx = {
            pool.submit(_safe_verify, signal, idx, len(signals)): idx
            for idx, signal in enumerate(signals, 1)
        }
        for future in as_completed(future_to_idx):
            fact = future.result()
            if fact is not None:
                all_facts.append(fact)

    logger.info("=" * 60)
    logger.info("VERIFY COMPLETE: %d/%d signals produced facts", len(all_facts), len(signals))
    logger.info("=" * 60)
    return _apply_threshold(all_facts)


def _safe_verify(signal: RawSignal, idx: int, total: int) -> VerifiedFact | None:
    """Wrapper that catches all exceptions so one bad signal never kills the batch."""
    logger.info(
        "-" * 50 + "\n  VERIFY [%d/%d]: %s\n  URL: %s | r/%s | score=%d | tier=%d",
        idx, total,
        signal.title[:100],
        signal.url,
        signal.subreddit,
        signal.score,
        signal.source_tier,
    )
    try:
        return _verify_single_signal(signal)
    except Exception:
        logger.exception(
            "  ERROR [%d/%d]: crashed for: %s", idx, total, signal.title[:80],
        )
        return None


def _verify_single_signal(signal: RawSignal) -> VerifiedFact | None:
    """Run analyst + verifier on one signal; return None on failure."""
    logger.info("  STEP 1/2: Running ANALYST (Sonnet)...")
    analysis = _run_analyst(signal)
    if analysis is None:
        logger.warning("  ANALYST FAILED for: %s", signal.title[:80])
        return None

    logger.info("  ANALYST RESULT:")
    logger.info("    significance = %.2f", analysis.significance)
    logger.info("    key_claims (%d):", len(analysis.key_claims))
    for i, claim in enumerate(analysis.key_claims, 1):
        logger.info("      %d. %s", i, claim)
    logger.info("    credibility_args (%d):", len(analysis.credibility_args))
    for arg in analysis.credibility_args:
        logger.info("      - %s", arg)

    logger.info("  STEP 2/2: Running VERIFIER (Sonnet) - claims only, no context...")
    verification = _run_verifier(analysis.key_claims, signal.source_tier)
    if verification is None:
        logger.warning("  VERIFIER FAILED for: %s", signal.title[:80])
        return None

    logger.info("  VERIFIER RESULT:")
    logger.info("    confidence = %.2f", verification.confidence)
    logger.info("    hype_rating = %s", verification.hype_rating)
    logger.info("    reasoning: %s", verification.reasoning)
    logger.info("    hype_risk: %s", verification.hype_risk)
    logger.info("    weaknesses (%d):", len(verification.weaknesses))
    for w in verification.weaknesses:
        logger.info("      - %s", w)

    trust = _compute_trust_score(analysis.significance, verification.confidence, signal.source_tier)
    tier_weight = (5 - signal.source_tier) / 4.0
    logger.info(
        "  TRUST SCORE: %.3f  (sig=%.2f*0.4 + conf=%.2f*0.35 + tier_w=%.2f*0.25)",
        trust, analysis.significance, verification.confidence, tier_weight,
    )

    verdict = "PASS" if verification.confidence > 0.5 else "REJECT"
    logger.info("  VERDICT: %s (threshold: confidence > 0.5)", verdict)

    return VerifiedFact(
        signal=signal,
        analysis=analysis,
        verification=verification,
        trust_score=trust,
    )


def _run_analyst(signal: RawSignal) -> AnalysisResult | None:
    """Call 1 - Analyst (Sonnet): evaluate significance and claims."""
    content_preview = signal.content[:500] if signal.content else "(no content)"
    prompt = _ANALYST_PROMPT_TEMPLATE.format(
        title=signal.title,
        content=content_preview,
        url=signal.url,
        source_tier=signal.source_tier,
        subreddit=signal.subreddit,
        score=signal.score,
    )
    response = call_llm(prompt, model_tier="quality", label="ANALYST")
    result = _parse_json_response(response, AnalysisResult)
    if result is None:
        logger.warning("  ANALYST: failed to parse response into AnalysisResult")
        logger.debug("  ANALYST raw response: %s", response[:500])
    return result if isinstance(result, AnalysisResult) else None


def _run_verifier(key_claims: list[str], source_tier: int) -> VerificationResult | None:
    """Call 2 - Verifier (Sonnet): challenge claims with no analyst context."""
    claims_lines = "\n".join(f"{i}. {claim}" for i, claim in enumerate(key_claims, 1))
    prompt = _VERIFIER_PROMPT_TEMPLATE.format(claims_list=claims_lines, source_tier=source_tier)
    response = call_llm(prompt, model_tier="quality", label="VERIFIER")
    result = _parse_json_response(response, VerificationResult)
    if result is None:
        logger.warning("  VERIFIER: failed to parse response into VerificationResult")
        logger.debug("  VERIFIER raw response: %s", response[:500])
    return result if isinstance(result, VerificationResult) else None


# ---------------------------------------------------------------------------
# Phase 2.3 - Threshold filtering
# ---------------------------------------------------------------------------


def _apply_threshold(facts: list[VerifiedFact]) -> list[VerifiedFact]:
    """Keep confidence > 0.5, sort by trust_score desc, limit to 7."""
    logger.info("-" * 50)
    logger.info("THRESHOLD FILTER: %d facts, keeping confidence > 0.5, top 7", len(facts))

    passed = [f for f in facts if f.verification.confidence > 0.5]
    rejected = [f for f in facts if f.verification.confidence <= 0.5]

    if rejected:
        logger.info("REJECTED (%d):", len(rejected))
        for fact in rejected:
            logger.info(
                "  X conf=%.2f trust=%.3f hype=%s | %s",
                fact.verification.confidence,
                fact.trust_score,
                fact.verification.hype_rating,
                fact.signal.title[:80],
            )

    passed.sort(key=lambda f: f.trust_score, reverse=True)
    top = passed[:7]

    logger.info("PASSED (%d, showing top %d):", len(passed), len(top))
    for i, fact in enumerate(top, 1):
        logger.info(
            "  %d. trust=%.3f conf=%.2f hype=%s | %s",
            i,
            fact.trust_score,
            fact.verification.confidence,
            fact.verification.hype_rating,
            fact.signal.title[:80],
        )

    return top


# ---------------------------------------------------------------------------
# Phase 3 - Digest generation (Opus)
# ---------------------------------------------------------------------------

_DIGEST_PROMPT_TEMPLATE = """\
You are the editor of a verified daily digest for developers and entrepreneurs building with AI.

Here are {n} verified facts for today, each with hype_rating and reasoning:
{verified_facts_json}

Generate a morning digest. For each fact, create a Method Card:

**[Category] Title**
[Hype Meter emoji + text]

What: 1-2 sentences, maximum density
Why important: 1 sentence
Action: 1-3 concrete steps the reader can apply today
Source: link

Categories: Tools / Methods / Research / Breaking / Community

Hype meter:
- confirmed -> ✅ Подтверждено
- likely -> 🟡 Вероятно
- unverified -> ⚪ Не подтверждено
- disputed -> 🟠 Спорно
- debunked -> 🔴 Опровергнуто

Voice: like Simon Willison - practitioner who builds with tools. Honest, concrete, no hype.
Total digest: < 3500 characters (Telegram limit with margin).
Language: Russian.

Reply ONLY with JSON:
{{
  "cards": [
    {{
      "title": "...",
      "category": "Tools|Methods|Research|Breaking|Community",
      "hype_rating": "confirmed|likely|unverified|disputed|debunked",
      "hype_label": "✅ Подтверждено",
      "summary": "What: ...",
      "why_important": "Why: ...",
      "action": "Action: ...",
      "source_url": "https://..."
    }}
  ]
}}"""

_HYPE_LABELS: dict[str, str] = {
    "confirmed": "✅ Подтверждено",
    "likely": "🟡 Вероятно",
    "unverified": "⚪ Не подтверждено",
    "disputed": "🟠 Спорно",
    "debunked": "🔴 Опровергнуто",
}

TELEGRAM_MAX_LENGTH = 4096


class _DigestCardResponse(BaseModel):
    """Schema for a single card in the LLM digest response."""

    title: str
    category: str
    hype_rating: str
    hype_label: str
    summary: str
    why_important: str
    action: str
    source_url: str


class _DigestResponse(BaseModel):
    """Schema for the full LLM digest response."""

    cards: list[_DigestCardResponse]


def generate_digest(facts: list[VerifiedFact]) -> DailyDigest:
    """Generate a daily digest from verified facts via Opus.

    Returns an empty digest on parse failure or empty input.
    """
    if not facts:
        logger.info("generate_digest: no facts, returning empty digest")
        return _empty_digest()

    logger.info("=" * 60)
    logger.info("PHASE: DIGEST - generating from %d verified facts (Opus)", len(facts))
    logger.info("=" * 60)
    for i, f in enumerate(facts, 1):
        logger.info(
            "  INPUT FACT %d: trust=%.3f hype=%s | %s",
            i, f.trust_score, f.verification.hype_rating, f.signal.title[:80],
        )

    prompt = _build_digest_prompt(facts)
    response = call_llm(prompt, model_tier="best", label="DIGEST")
    cards = _parse_digest_response(response)

    logger.info("DIGEST RESULT: %d method cards generated", len(cards))
    for card in cards:
        logger.info("  CARD: [%s] %s | %s", card.category, card.title, card.hype_label)

    return DailyDigest(
        cards=cards,
        date=datetime.now(UTC),
        total_cost=0.0,
        signals_scanned=0,
        signals_verified=len(facts),
    )


def _empty_digest() -> DailyDigest:
    """Return an empty DailyDigest with zeroed counters."""
    return DailyDigest(
        cards=[],
        date=datetime.now(UTC),
        total_cost=0.0,
        signals_scanned=0,
        signals_verified=0,
    )


def _build_digest_prompt(facts: list[VerifiedFact]) -> str:
    """Serialize verified facts to JSON and fill the prompt template."""
    facts_data = [_serialize_fact(f) for f in facts]
    return _DIGEST_PROMPT_TEMPLATE.format(
        n=len(facts),
        verified_facts_json=json.dumps(facts_data, ensure_ascii=False, indent=2),
    )


def _serialize_fact(fact: VerifiedFact) -> dict:
    """Convert a VerifiedFact to a compact dict for the LLM prompt."""
    return {
        "title": fact.signal.title,
        "url": fact.signal.url,
        "source": fact.signal.source,
        "subreddit": fact.signal.subreddit,
        "content_preview": (fact.signal.content[:300] if fact.signal.content else ""),
        "hype_rating": fact.verification.hype_rating,
        "reasoning": fact.verification.reasoning,
        "trust_score": fact.trust_score,
    }


def _parse_digest_response(response: str) -> list[MethodCard]:
    """Parse LLM response into a list of MethodCard objects."""
    parsed = _parse_json_response(response, _DigestResponse)
    if not isinstance(parsed, _DigestResponse):
        logger.warning("Digest LLM response unparseable, returning empty cards")
        return []
    return [_card_response_to_method_card(c) for c in parsed.cards]


def _card_response_to_method_card(card: _DigestCardResponse) -> MethodCard:
    """Map a parsed card response to a MethodCard with validated fields."""
    category = _normalize_category(card.category)
    hype_rating = _normalize_hype_rating(card.hype_rating)
    hype_label = _HYPE_LABELS.get(hype_rating, card.hype_label)

    return MethodCard(
        title=card.title,
        category=category,
        hype_rating=hype_rating,
        hype_label=hype_label,
        summary=card.summary,
        why_important=card.why_important,
        action=card.action,
        source_url=card.source_url,
    )


def _normalize_category(raw: str) -> str:
    """Map a raw category string to a valid MethodCard category."""
    valid = {"Tools", "Methods", "Research", "Breaking", "Community"}
    stripped = raw.strip()
    if stripped in valid:
        return stripped
    # Case-insensitive fallback
    for v in valid:
        if stripped.lower() == v.lower():
            return v
    return "Tools"


def _normalize_hype_rating(raw: str) -> str:
    """Map a raw hype_rating to a valid literal value."""
    valid = {"confirmed", "likely", "unverified", "disputed", "debunked"}
    stripped = raw.strip().lower()
    return stripped if stripped in valid else "unverified"


# ---------------------------------------------------------------------------
# Digest formatting (for Telegram)
# ---------------------------------------------------------------------------


def _format_digest_text(digest: DailyDigest) -> str:
    """Format a DailyDigest as Telegram-friendly plain text with emoji.

    Returns text under TELEGRAM_MAX_LENGTH (4096 chars).
    If it exceeds the limit, cards are trimmed until it fits.
    """
    header = _build_header(digest)
    footer = "\n---\nФакты проверены мультиагентной системой VIBE-SERCH"
    card_texts = [_format_card(card) for card in digest.cards]

    return _assemble_digest_text(header, card_texts, footer)


def _build_header(digest: DailyDigest) -> str:
    """Build the digest header with date and stats."""
    date_str = digest.date.strftime("%d.%m.%Y")
    total = digest.signals_scanned
    verified = digest.signals_verified
    return f"VIBE-SERCH | {date_str}\n{total} фактов проверено, {verified} прошли верификацию\n"


def _format_card(card: MethodCard) -> str:
    """Format a single MethodCard as plain text."""
    return (
        f"\n[{card.category}] {card.title}\n"
        f"{card.hype_label}\n\n"
        f"{card.summary}\n"
        f"{card.why_important}\n"
        f"{card.action}\n"
        f"Источник: {card.source_url}"
    )


def _assemble_digest_text(
    header: str,
    card_texts: list[str],
    footer: str,
) -> str:
    """Assemble header + cards + footer, trimming cards if over limit."""
    included: list[str] = []
    for ct in card_texts:
        candidate = header + "\n".join([*included, ct]) + footer
        if len(candidate) <= TELEGRAM_MAX_LENGTH:
            included.append(ct)
        else:
            break
    return header + "\n".join(included) + footer
