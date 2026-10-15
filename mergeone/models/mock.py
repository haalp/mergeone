"""Deterministic mock decision model for load tests and CI (no API key).

Latency is simulated (default 300 ms ± 60) so throughput tests exercise the
worker pool realistically. Probabilities are a hash of the state, so the
same input always yields the same decision (idempotency tests).
"""
from __future__ import annotations

import hashlib
import random
import time

from .base import Decision, DECISIONS_V2, Timer


class MockModel:
    name = "mock"

    def __init__(self, latency_ms: float = 300.0, jitter_ms: float = 60.0, error_rate: float = 0.0, version: str = "v2"):
        self.latency_ms, self.jitter_ms, self.error_rate = latency_ms, jitter_ms, error_rate
        self.version = version

    def decide(self, state: str, features: dict, questions=DECISIONS_V2) -> Decision:
        h = hashlib.sha256(state.encode()).digest()
        rng = random.Random(h)
        with Timer() as t:
            if self.latency_ms:
                time.sleep(max(0.0, rng.gauss(self.latency_ms, self.jitter_ms)) / 1000.0)
            failed = rng.random() < self.error_rate
            probs = {q: round(rng.random(), 3) for q in questions}
        if failed:
            return Decision(probs={q: float("nan") for q in questions}, error="MockError: simulated", model_version="mock-1", latency_ms=t.ms)
        return Decision(probs=probs, latency_ms=t.ms, input_tokens=len(state) // 4, output_tokens=0, cost_usd=0.0, model_version="mock-1")
