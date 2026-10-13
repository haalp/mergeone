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


class DecisionModel(Protocol):
    name: str

    def decide(self, state: str, features: dict[str, float], questions: tuple[str, ...] = DECISIONS) -> Decision: ...


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t0) * 1000.0
