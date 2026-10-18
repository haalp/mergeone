"""System One-dialect decision models (Jev, Perplexity Decisions, clef, llama.cpp/vLLM `/v1/systemone`, …).

POST <endpoint>
body: {"state": str, "model": <id>, "questions": {name: {type: noul|choice|score, ...}}}
Answers come back as {name: {"noul": p}} or {name: {"probabilities": {...}, "confidence": c}}.

Which model is called is a row in prompts/models.yaml (see mergeone.models.registry). Questions are
frozen in prompts/jev_<qset>.json and shared by every model of this dialect. The version actually served
is pinned from the first response's `model` field and stored in results.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from .base import Decision, DECISIONS, Timer, questions_for
from .registry import ModelSpec, load_registry

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {"v1": Path(__file__).resolve().parents[2] / "prompts" / "jev_v1.json",
             "v2": Path(__file__).resolve().parents[2] / "prompts" / "jev_v2.json",
             "v3": Path(__file__).resolve().parents[2] / "prompts" / "jev_v3.json",
             "pair": Path(__file__).resolve().parents[2] / "prompts" / "jev_pair.json",
             "pair_assume": Path(__file__).resolve().parents[2] / "prompts" / "jev_pair.json"}


class SystemOneModel:
    """Any model that speaks the System One schema. Construct from a registry ModelSpec."""

    max_retries_429 = 5

    def __init__(self, spec: ModelSpec, prices: dict | None = None, model: str | None = None, timeout: float = 30.0, version: str = "v1"):
        self.spec = spec
        self.name = spec.name
        self.endpoint = spec.endpoint
        key = os.environ.get(spec.key_env or "", "")
        if not key and not spec.key_optional:
            raise RuntimeError(f"{spec.key_env} not set")
        model = model or spec.model_id
        self.model = model
        self.pinned: str | None = None if model.endswith("-latest") else model
        self.version = version
        self.questions = json.loads(QUESTIONS[version].read_text())
        if os.environ.get("MERGEONE_NO_RETRY") == "1":  # load tests (R5-H6): a 429 is an error, not a retry
            self.max_retries_429 = 0
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        # run 6 load tests at 300× need more than httpx's default 100-connection pool; unset = httpx defaults (runs 1–5)
        mc = os.environ.get("MERGEONE_HTTP_MAX_CONNECTIONS")
        limits = httpx.Limits(max_connections=int(mc), max_keepalive_connections=int(mc)) if mc else httpx.Limits(max_connections=100, max_keepalive_connections=20)
        self.client = httpx.Client(timeout=timeout, headers=headers, limits=limits)
        p = (prices or {}).get(self.name) or {"input_per_mtok": spec.input_per_mtok, "output_per_mtok": spec.output_per_mtok}
        self.in_price = p["input_per_mtok"] / 1e6
        self.out_price = p["output_per_mtok"] / 1e6

    def decide(self, state: str, features: dict[str, float], questions=DECISIONS) -> Decision:
        questions = questions_for(self.version, questions)
        body = {
            "state": state,
            "model": self.pinned or self.model,
            "questions": {q: self.questions[q] for q in questions},
        }
        try:
            with Timer() as t:
                r = self.client.post(self.endpoint, json=body)
                for attempt in range(self.max_retries_429):  # hosted rate limits (pplx: 10 rps/org); retried latency is recorded as-is
                    if r.status_code != 429:
                        break
                    time.sleep(min(8.0, 0.5 * 2 ** attempt))
                    r = self.client.post(self.endpoint, json=body)
            if r.status_code != 200:
                return Decision(probs={q: float("nan") for q in questions}, latency_ms=t.ms,
                                error=f"HTTP {r.status_code}: {r.text[:300]}", model_version=self.pinned or self.model)
            d = r.json()
            if self.pinned is None and d.get("model"):
                self.pinned = d["model"]  # pin for the rest of the run
            answers = d.get("answers") or d.get("decisions") or {}  # `decisions`: /v1/decide dialect
            probs, conf, raw_choice = {}, {}, {}
            for q in questions:
                a = answers.get(q, {})
                if a.get("type") == "noul" or "noul" in a:
                    probs[q] = float(a["noul"])
                elif "probability" in a:  # some /v1/decide servers
                    probs[q] = float(a["probability"])
                elif "probabilities" in a:  # choice
                    pr = a["probabilities"]
                    if q == "same_change":  # run 4: duplicate_of = P(same) + 0.5 P(subset)
                        probs[q] = float(pr.get("same", 0.0)) + 0.5 * float(pr.get("subset", 0.0))
                        conf[q] = float(a.get("confidence", float("nan")))
                        raw_choice = {q: pr}
                    elif q == "assumption_conflict":  # run 5: conflict_of = P(conflict) + 0.5 P(depends)
                        probs[q] = float(pr.get("conflict", 0.0)) + 0.5 * float(pr.get("depends", 0.0))
                        conf[q] = float(a.get("confidence", float("nan")))
                        raw_choice[q] = pr
                    else:
                        probs[q] = float(pr.get("yes", pr.get("true", 0.0)))
                        conf[q] = float(a.get("confidence", float("nan")))
                else:
                    probs[q] = float("nan")
            usage = d.get("usage", {})
            it, ot = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            return Decision(probs=probs, confidence=conf, latency_ms=t.ms, input_tokens=it, output_tokens=ot,
                            cost_usd=it * self.in_price + ot * self.out_price, model_version=d.get("model", self.model),
                            raw={"usage": usage, **({"choice": raw_choice} if raw_choice else {})})
        except Exception as e:
            return Decision(probs={q: float("nan") for q in questions}, error=f"{type(e).__name__}: {e}", model_version=self.pinned or self.model)


def _registry_model(name: str):
    class _M(SystemOneModel):
        def __init__(self, prices: dict | None = None, model: str | None = None, timeout: float = 30.0, version: str = "v1"):
            super().__init__(load_registry()[name], prices, model=model, timeout=timeout, version=version)
    _M.__name__ = _M.__qualname__ = name
    return _M


# Backwards-compatible names (runs 1–4 scripts and tests import these).
JevModel = _registry_model("jev")
PplxModel = _registry_model("pplx")
PplxFlashModel = _registry_model("pplx-flash")
