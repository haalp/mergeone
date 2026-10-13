"""Cascade: cheap model first; escalate to the expensive one when uncertain.

Uncertainty on the primary decision = closeness of P to 0.5 (or 1 - reported
confidence when the model gives one). Threshold t is chosen on validation
(see eval.select_cascade_threshold) and frozen before test scoring.

The cascade is simulated offline from the two models' saved predictions so
that the same API calls are not paid twice; cost and latency are composed
accordingly (escalated examples pay both calls).
"""
from __future__ import annotations

import math

from .base import Decision, PRIMARY


def uncertainty(d: Decision) -> float:
    p = d.probs.get(PRIMARY, float("nan"))
    if math.isnan(p):
        return 1.0
    c = d.confidence.get(PRIMARY)
    if c is not None and not math.isnan(c):
        return 1.0 - c
    return 1.0 - abs(p - 0.5) * 2.0  # 0 at p in {0,1}, 1 at p=0.5


def compose(cheap: Decision, expensive: Decision, threshold: float) -> tuple[Decision, bool]:
    """Return (decision, escalated)."""
    if uncertainty(cheap) <= threshold and cheap.error is None:
        return cheap, False
    merged = Decision(
        probs=dict(expensive.probs),
        confidence=dict(expensive.confidence),
        latency_ms=cheap.latency_ms + expensive.latency_ms,
        input_tokens=cheap.input_tokens + expensive.input_tokens,
        output_tokens=cheap.output_tokens + expensive.output_tokens,
        cost_usd=cheap.cost_usd + expensive.cost_usd,
        model_version=f"{cheap.model_version}->{expensive.model_version}",
        error=expensive.error,
    )
    return merged, True
