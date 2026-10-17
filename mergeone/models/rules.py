"""Hand-written heuristics. The floor. Frozen before results."""
from __future__ import annotations

import math

from .base import Decision, DECISIONS, DECISIONS_V2, V3_NEW, Timer

CONCURRENCY_WORDS = ("mutex", "lock", "rwlock", "atomic", "goroutine", "channel", "chan ", "sync.", "async ", "await ",
                     "thread", "semaphore", "waitgroup", "race", "concurren", "parallel", "worker pool", "deadlock")


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
            if any(q in DECISIONS_V2 for q in questions):
                probs.update(self._v2(state, f, score))
            if any(q in V3_NEW for q in questions):
                probs.update({q: 0.5 for q in V3_NEW})  # run 5 floor: v2 heuristics + nothing for the new questions (R5.1 C)
        return Decision(probs={q: probs[q] for q in questions}, latency_ms=t.ms, model_version="rules_v1")

    @staticmethod
    def _v2(state: str, f: dict[str, float], score: float) -> dict[str, float]:
        """v2 floor. Written 2026-10-16 before any run3 prediction; weights are guesses, not fitted."""
        s = state.lower()
        diff = s.split("\ndiff (", 1)[-1]
        non_code = f["test_ratio"] + f["generated_ratio"] + f["vendor_ratio"]
        docs_like = min(f["title_lowrisk_words"], 2) / 2.0
        logic = _sigmoid(0.9 * math.log1p(f["churn"]) / 5.0 - 1.5 * non_code - 1.2 * docs_like - 0.2)
        test_removed = f["test_ratio"] > 0 and f["deletions"] > f["insertions"]
        weak = 0.7 if test_removed else (0.35 if ("skip(" in diff or "t.skip" in diff or "xit(" in diff or "@pytest.mark.skip" in diff) else 0.08)
        match = _sigmoid(0.004 * min(f["body_len"], 800) + 0.8 * f["body_mentions_issue"] - 0.3 * math.log1p(f["churn"]) / 3.0 + 0.4)
        conc = 0.75 if sum(w in diff for w in CONCURRENCY_WORDS) >= 2 else (0.35 if any(w in diff for w in CONCURRENCY_WORDS) else 0.05)
        return {
            "is_logic_change": logic,
            "weakens_tests": weak,
            "message_matches_diff": match,
            "touches_concurrency": conc,
            "needs_human_review": _sigmoid(score + 1.4),
        }
