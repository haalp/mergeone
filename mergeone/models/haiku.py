"""Claude Haiku 5.5 with strict JSON structured output, thinking disabled.

Prompt is frozen in prompts/haiku_v1.md. The model is asked for a
probability per decision; calibration of those numbers is what we measure.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .base import Decision, DECISIONS, DECISIONS_V2, Timer

MODEL_ID = "claude-haiku-5-5"
PROMPTS = {"v1": Path(__file__).resolve().parents[2] / "prompts" / "haiku_v1.md",
           "v2": Path(__file__).resolve().parents[2] / "prompts" / "haiku_v2.md",
           "pair": Path(__file__).resolve().parents[2] / "prompts" / "haiku_pair.md"}
SCHEMA_PAIR = {
    "type": "object",
    "properties": {"same_change": {"type": "object", "properties": {k: {"type": "number"} for k in ("same", "subset", "related", "unrelated")},
                                   "required": ["same", "subset", "related", "unrelated"], "additionalProperties": False}},
    "required": ["same_change"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "will_be_reverted": {"type": "number", "description": "Probability 0-1 that this merged PR is reverted on master within 30 days."},
        "touches_sensitive_area": {"type": "number", "description": "Probability 0-1 that the PR modifies security- or API-critical directories."},
        "large_blast_radius": {"type": "number", "description": "Probability 0-1 that the PR touches more than 10 files across more than 3 top-level areas."},
    },
    "required": list(DECISIONS),
    "additionalProperties": False,
}
SCHEMA_V2 = {
    "type": "object",
    "properties": {
        "is_logic_change": {"type": "number", "description": "Probability 0-1 that the PR changes program behaviour."},
        "weakens_tests": {"type": "number", "description": "Probability 0-1 that the PR weakens tests (removes, skips, loosens, raises timeouts)."},
        "message_matches_diff": {"type": "number", "description": "Probability 0-1 that title+message accurately describe the diff."},
        "touches_concurrency": {"type": "number", "description": "Probability 0-1 that the diff touches concurrency-hazard code."},
        "needs_human_review": {"type": "number", "description": "Probability 0-1 that a careful maintainer would want human review before merge."},
    },
    "required": list(DECISIONS_V2),
    "additionalProperties": False,
}


class HaikuModel:
    name = "haiku"

    def __init__(self, prices: dict | None = None, effort: str = "low", version: str = "v1"):
        import anthropic

        self.client = anthropic.Anthropic()
        self.version = version
        self.prompt = PROMPTS[version].read_text()
        self.schema_all = {"v1": SCHEMA, "v2": SCHEMA_V2, "pair": SCHEMA_PAIR}[version]
        self.effort = effort
        p = (prices or {}).get("haiku", {"input_per_mtok": 0.10, "output_per_mtok": 0.50})
        self.in_price = p["input_per_mtok"] / 1e6
        self.out_price = p["output_per_mtok"] / 1e6

    def decide(self, state: str, features: dict[str, float], questions=DECISIONS) -> Decision:
        schema = dict(self.schema_all)
        schema["properties"] = {k: v for k, v in self.schema_all["properties"].items() if k in questions}
        schema["required"] = list(questions)
        try:
            with Timer() as t:
                resp = self.client.messages.create(
                    model=MODEL_ID,
                    max_tokens=256,
                    system=self.prompt,
                    thinking={"type": "disabled"},
                    output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                    messages=[{"role": "user", "content": state}],
                )
            text = next(b.text for b in resp.content if b.type == "text")
            out = json.loads(text)
            raw_choice = {}
            if "same_change" in questions and isinstance(out.get("same_change"), dict):
                pr = out["same_change"]; z = sum(max(0.0, float(v)) for v in pr.values()) or 1.0
                pr = {k: max(0.0, float(v)) / z for k, v in pr.items()}
                out["same_change"] = pr.get("same", 0.0) + 0.5 * pr.get("subset", 0.0)  # run 4: duplicate_of
                raw_choice = {"same_change": pr}
            probs = {q: float(min(max(out[q], 0.0), 1.0)) for q in questions}
            it, ot = resp.usage.input_tokens, resp.usage.output_tokens
            return Decision(
                probs=probs,
                latency_ms=t.ms,
                input_tokens=it,
                output_tokens=ot,
                cost_usd=it * self.in_price + ot * self.out_price,
                model_version=resp.model,
                raw={"stop_reason": resp.stop_reason, **({"choice": raw_choice} if raw_choice else {})},
            )
        except Exception as e:  # recorded, never hidden
            return Decision(probs={q: float("nan") for q in questions}, error=f"{type(e).__name__}: {e}", model_version=MODEL_ID)
