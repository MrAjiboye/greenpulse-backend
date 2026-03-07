"""
LLM helpers
-----------
Uses Claude (via Anthropic API) to generate natural-language insight
descriptions from raw energy stats.

All functions return None gracefully when:
  - ANTHROPIC_API_KEY is not configured
  - The API call fails for any reason

Callers should fall back to their own template strings in those cases.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("greenpulse.ml.llm")

_SYSTEM = (
    "You are an energy efficiency analyst writing concise, actionable insight "
    "descriptions for a sustainability dashboard used by UK businesses. "
    "Write in plain English, 2-3 sentences maximum. Be specific with the numbers "
    "provided. Do not use bullet points or headers. Do not start with 'I' or 'We'."
)


def _client():
    """Return an Anthropic client, or None if the key is not set."""
    try:
        from app.config import settings
        if not settings.ANTHROPIC_API_KEY:
            return None
        import anthropic
        return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    except Exception:
        return None


def _call(prompt: str) -> Optional[str]:
    """Send a single prompt to Claude Haiku and return the text response."""
    client = _client()
    if client is None:
        return None
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.warning("Claude API call failed: %s", e)
        return None


def describe_peak_usage(peak_kwh: float, avg_kwh: float, peak_ratio: float) -> Optional[str]:
    return _call(
        f"Peak consumption this week was {peak_kwh:.1f} kWh, which is {peak_ratio:.1f}x "
        f"the 7-day average of {avg_kwh:.1f} kWh. "
        "Write an insight description explaining why this is a concern and what the "
        "business should investigate or do about it."
    )


def describe_night_usage(night_avg_kwh: float, day_avg_kwh: float, pct: float) -> Optional[str]:
    return _call(
        f"Average energy consumption during off-hours (10 pm – 6 am) is {night_avg_kwh:.1f} kWh, "
        f"which is {pct:.0f}% of the daytime average ({day_avg_kwh:.1f} kWh). "
        "Write an insight description explaining the likely causes and what the business "
        "should do to reduce this waste."
    )


def describe_weekend_usage(we_avg_kwh: float, wd_avg_kwh: float, pct: float) -> Optional[str]:
    return _call(
        f"Weekend energy usage averages {we_avg_kwh:.1f} kWh, which is {pct:.0f}% of the "
        f"weekday average ({wd_avg_kwh:.1f} kWh). "
        "Write an insight description explaining why this pattern is unusual for most businesses "
        "and what actions could reduce weekend consumption."
    )
