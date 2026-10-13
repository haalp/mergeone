"""Hand-written heuristics. The floor. Frozen before results."""
from __future__ import annotations

import math

from .base import Decision, DECISIONS, Timer


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class RulesModel:
    name = "rules"

    def decide(self, state: str, features: dict[str, float], questions=DECISIONS) -> Decision:
        with Timer() as t:
            f = features
            # Larger, more scattered, non-test, non-generated, sensitive changes
            # with "fix"-type titles are riskier. Weights are guesses, not fitted.
            score = (
                0.35 * math.log1p(f["churn"]) / 6.0
                + 0.25 * min(f["n_topdirs"], 8) / 8.0
                + 0.6 * f["touches_sensitive"]
                + 0.4 * min(f["title_risk_words"], 2)
                - 0.5 * f["test_ratio"]
                - 0.8 * f["generated_ratio"]
                - 0.8 * f["vendor_ratio"]
                - 0.3 * min(f["title_lowrisk_words"], 2)
                - 2.2  # base-rate offset: reverts are rare
            )
            probs = {
                "will_be_reverted": _sigmoid(score),
                "touches_sensitive_area": float(f["touches_sensitive"]),
                "large_blast_radius": float(f["n_files"] > 10 and f["n_topdirs"] > 3),
            }
        return Decision(probs={q: probs[q] for q in questions}, latency_ms=t.ms, model_version="rules_v1")
