"""TypeSafe Jev (System One model) via the raw HTTP API.

POST https://api.typesafe.ai/v1/systemone
body: {"state": str, "model": "jev-latest" | pinned, "questions": {name: {...}}}
Question types: noul (yes/no probability), choice, score. We use noul for
all three decisions so every answer is a direct P(true).

Questions are frozen in prompts/jev_v1.json. Version is pinned from the
first response's `model` field and stored in results.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from .base import Decision, DECISIONS, Timer

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {"v1": Path(__file__).resolve().parents[2] / "prompts" / "jev_v1.json",
             "v2": Path(__file__).resolve().parents[2] / "prompts" / "jev_v2.json"}


class JevModel:
    name = "jev"

    def __init__(self, prices: dict | None = None, model: str = "jev-latest", timeout: float = 30.0, version: str = "v1"):
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise RuntimeError("TYPESAFE_API_KEY not set")
        self.model = model
        self.pinned: str | None = None if model == "jev-latest" else model
        self.version = version
        self.questions = json.loads(QUESTIONS[version].read_text())
        self.client = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        p = (prices or {}).get("jev", {"input_per_mtok": 0.042, "output_per_mtok": 0.0})
        self.in_price = p["input_per_mtok"] / 1e6
        self.out_price = p["output_per_mtok"] / 1e6

    def decide(self, state: str, features: dict[str, float], questions=DECISIONS) -> Decision:
        body = {
            "state": state,
            "model": self.pinned or self.model,
            "questions": {q: self.questions[q] for q in questions},
        }
        try:
            with Timer() as t:
                r = self.client.post(ENDPOINT, json=body)
            if r.status_code != 200:
                return Decision(probs={q: float("nan") for q in questions}, latency_ms=t.ms,
                                error=f"HTTP {r.status_code}: {r.text[:300]}", model_version=self.pinned or self.model)
            d = r.json()
            if self.pinned is None and d.get("model"):
                self.pinned = d["model"]  # pin for the rest of the run
            answers = d["answers"]
            probs, conf = {}, {}
            for q in questions:
                a = answers[q]
                if a.get("type") == "noul" or "noul" in a:
                    probs[q] = float(a["noul"])
                elif "probabilities" in a:  # choice fallback: P(yes)
                    pr = a["probabilities"]
                    probs[q] = float(pr.get("yes", pr.get("true", 0.0)))
                    conf[q] = float(a.get("confidence", float("nan")))
                else:
                    probs[q] = float("nan")
            usage = d.get("usage", {})
            it, ot = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            return Decision(probs=probs, confidence=conf, latency_ms=t.ms, input_tokens=it, output_tokens=ot,
                            cost_usd=it * self.in_price + ot * self.out_price, model_version=d.get("model", self.model),
                            raw={"usage": usage})
        except Exception as e:
            return Decision(probs={q: float("nan") for q in questions}, error=f"{type(e).__name__}: {e}", model_version=self.pinned or self.model)
