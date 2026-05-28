"""Pydantic data models for the VIBE-SERCH pipeline."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RawSignal(BaseModel):
    """Raw signal captured from a scanner source."""

    title: str
    content: str
    url: str
    source: str
    subreddit: str
    score: int = Field(ge=0)
    num_comments: int = Field(ge=0)
    source_tier: int = Field(ge=1, le=5)
    published_at: datetime


class AnalysisResult(BaseModel):
    """Result of LLM analyst evaluation."""

    significance: float = Field(ge=0.0, le=1.0)
    credibility_args: list[str]
    key_claims: list[str]


class VerificationResult(BaseModel):
    """Result of LLM verifier evaluation."""

    weaknesses: list[str]
    hype_risk: str
    confidence: float = Field(ge=0.0, le=1.0)
    hype_rating: Literal["confirmed", "likely", "unverified", "disputed", "debunked"]
    reasoning: str


class VerifiedFact(BaseModel):
    """Combined signal with analysis and verification."""

    signal: RawSignal
    analysis: AnalysisResult
    verification: VerificationResult
    trust_score: float = Field(ge=0.0, le=1.0)


class MethodCard(BaseModel):
    """Final digest card for a single verified fact."""

    title: str
    category: Literal["Tools", "Methods", "Research", "Breaking", "Community"]
    hype_rating: Literal["confirmed", "likely", "unverified", "disputed", "debunked"]
    hype_label: str
    summary: str
    why_important: str
    action: str
    source_url: str


class DailyDigest(BaseModel):
    """Complete daily digest output."""

    cards: list[MethodCard]
    date: datetime
    total_cost: float = Field(ge=0.0)
    signals_scanned: int = Field(ge=0)
    signals_verified: int = Field(ge=0)
