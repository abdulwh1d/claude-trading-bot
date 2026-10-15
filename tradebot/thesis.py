"""Step 2: define the thesis as a type, not a paragraph.

The model fills in this shape through structured outputs, so "leaning bullish
but cautious" can never reach the risk engine.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TradeThesis(BaseModel):
    symbol: str = Field(description="Ticker of the stock being analyzed.")
    action: Literal["buy", "sell", "hold"]
    confidence: Literal["low", "medium", "high"]
    entry_reason: str = Field(description="Short explanation of why, citing the inputs.")
    stop_loss: float = Field(description="Price at which the thesis is clearly wrong.")
    invalidation: str = Field(description="What would prove this thesis wrong.")
    sources: list[str] = Field(description="Which inputs support it.")

    # Anthropic's structured-outputs docs: enum capitalization is not guaranteed
    # ("Buy" instead of "buy"). The article compares with .lower() in the risk
    # engine, but Pydantic's Literal check runs first and would reject "Buy",
    # so normalize here, before validation.
    @field_validator("action", "confidence", mode="before")
    @classmethod
    def normalize_case(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("symbol", mode="before")
    @classmethod
    def upper_symbol(cls, v):
        return v.strip().upper() if isinstance(v, str) else v


class ResearchBrief(BaseModel):
    """Overnight summary written by Sonnet 5.5 through the Batch API (Step 10)."""

    symbol: str
    what_changed: str = Field(
        description="One or two sentences on what is new. Say 'Nothing material' if nothing is."
    )
    material: bool = Field(description="True only if something could plausibly move the stock.")
    events: list[str] = Field(
        description="Filings, earnings, guidance, deals, legal or management news. One line each, with its date."
    )
    risks: list[str] = Field(description="Specific risks raised by the documents.")
    sources: list[str] = Field(description="Ids of the documents used.")


class DailyHypothesis(BaseModel):
    """The after-close review produces exactly one falsifiable hypothesis, not a story."""

    hypothesis: str = Field(description="One falsifiable statement about the system's decision rules.")
    metric: str = Field(description="The metric that would confirm or refute it.")
    pass_threshold: str = Field(description="The value of that metric that would confirm it.")
    min_sample: int = Field(description="How many observations are needed before judging it.")
