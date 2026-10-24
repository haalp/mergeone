"""Common decision interface. A model is a variable in the experiment.

Every model takes a rendered state (string) plus the structural features
(dict) and returns probabilities for the three decisions, along with
measured latency, token usage, and cost. Models may ignore inputs they do
not use (e.g. `rules` ignores the text; `jev` ignores the numeric dict).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Protocol

DECISIONS = ("will_be_reverted", "touches_sensitive_area", "large_blast_radius")
PRIMARY = "will_be_reverted"
# run2: semantic typed triage (PROTOCOL.md R2.2)
DECISIONS_V2 = ("is_logic_change", "weakens_tests", "message_matches_diff", "touches_concurrency", "needs_human_review")
PRIMARY_V2 = "needs_human_review"
# run5: v3 = v2 + five noul questions (PROTOCOL R5.1 C); outcome = review iteration (`iterated`)
V3_NEW = ("exceeds_scope", "breaking_change", "tests_cover_change", "hard_to_revert", "follows_conventions")
DECISIONS_V3 = DECISIONS_V2 + V3_NEW
PRIMARY_V3 = "needs_human_review"
PAIR = ("same_change",)
# run5 (PROTOCOL R5.1-B): assumption_conflict(A, B) → conflict_of = P(conflict) + 0.5·P(depends)
PAIR_ASSUME = ("assumption_conflict",)
# run7 (C5, docs/run7-addendum-draft.md R7.2): typed relation of a (PR, candidate) pair as four noul questions,
# prompts/jev_relation.json / haiku_relation.md; the class rule is in mergeone.eval.run7_relation
RELATION = ("same_change", "same_goal", "same_branch", "builds_on")
# run8: relation2 = three binaries the diff can answer (no branch question: the base branch is metadata), class rule with
# metadata inputs in mergeone.eval.run8_relation; prompts/jev_relation2.json / haiku_relation2.md
RELATION2 = ("same_change", "builds_on", "same_goal")
# run8: v4 = v3 minus follows_conventions and message_matches_diff (prompts/jev_v4.json / haiku_v4.md)
V4_DROPPED = ("message_matches_diff", "follows_conventions")
DECISIONS_V4 = tuple(q for q in DECISIONS_V3 if q not in V4_DROPPED)
PRIMARY_V4 = "needs_human_review"
QUESTION_SETS = {"v1": DECISIONS, "v2": DECISIONS_V2, "v3": DECISIONS_V3, "v4": DECISIONS_V4, "pair": PAIR, "pair_assume": PAIR_ASSUME,
                 "relation": RELATION, "relation2": RELATION2}


def questions_for(version: str, questions: tuple[str, ...]) -> tuple[str, ...]:
    """`predict --qset pair_assume` reaches models with the CLI's fallback question tuple; the pair_assume
    version always asks exactly its own question. Every other version keeps the caller's questions."""
    return PAIR_ASSUME if version == "pair_assume" else questions


@dataclass
class Decision:
    probs: dict[str, float]  # decision name -> P(true)
    confidence: dict[str, float] = field(default_factory=dict)  # model-reported confidence if any
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model_version: str = ""
    error: str | None = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def decide_single(model, state: str, features: dict[str, float], questions: tuple[str, ...]) -> Decision:
    """Run 6 (R6A-H4): ask one question per request, sequentially, and merge the answers into one Decision.

    latency_ms = wall-clock of the whole PR (the calls are sequential, so ≈ the sum of the per-call latencies);
    tokens and cost are summed; `error` is set if any call failed (that question's prob is NaN); raw.mode = "single"."""
    t0 = time.perf_counter()
    probs, conf, raw_q, choice = {}, {}, {}, {}
    it = ot = 0
    cost = 0.0
    errors, version = [], ""
    for q in questions:
        d = model.decide(state, features, (q,))
        probs.update(d.probs)
        conf.update(d.confidence)
        it += d.input_tokens; ot += d.output_tokens; cost += d.cost_usd
        version = version or d.model_version
        if d.error:
            errors.append(f"{q}: {d.error}")
        raw_q[q] = {"latency_ms": d.latency_ms, "input_tokens": d.input_tokens, "output_tokens": d.output_tokens, "error": d.error}
        choice.update((d.raw or {}).get("choice") or {})
    raw = {"mode": "single", "usage": {"input_tokens": it, "output_tokens": ot, "requests": len(questions)}, "per_question": raw_q}
    if choice:
        raw["choice"] = choice
    return Decision(probs=probs, confidence=conf, latency_ms=(time.perf_counter() - t0) * 1000.0, input_tokens=it, output_tokens=ot,
                    cost_usd=cost, model_version=version, error="; ".join(errors) if errors else None, raw=raw)


class DecisionModel(Protocol):
    name: str

    def decide(self, state: str, features: dict[str, float], questions: tuple[str, ...] = DECISIONS) -> Decision: ...


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t0) * 1000.0
