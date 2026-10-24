"""Claude Haiku 5.5 with strict JSON structured output, thinking disabled.

Prompt is frozen in prompts/haiku_v1.md. The model is asked for a
probability per decision; calibration of those numbers is what we measure.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .base import Decision, DECISIONS, DECISIONS_V2, DECISIONS_V3, DECISIONS_V4, Timer, questions_for

MODEL_ID = "claude-haiku-5-5"
PROMPTS = {"v1": Path(__file__).resolve().parents[2] / "prompts" / "haiku_v1.md",
           "v2": Path(__file__).resolve().parents[2] / "prompts" / "haiku_v2.md",
           "v3": Path(__file__).resolve().parents[2] / "prompts" / "haiku_v3.md",
           "pair": Path(__file__).resolve().parents[2] / "prompts" / "haiku_pair.md",
           "pair_assume": Path(__file__).resolve().parents[2] / "prompts" / "haiku_pair.md",
           "relation": Path(__file__).resolve().parents[2] / "prompts" / "haiku_relation.md",  # run 7 C5
           "relation2": Path(__file__).resolve().parents[2] / "prompts" / "haiku_relation2.md",  # run 8
           "v4": Path(__file__).resolve().parents[2] / "prompts" / "haiku_v4.md"}  # run 8: v3 minus two questions
SCHEMA_PAIR = {
    "type": "object",
    "properties": {"same_change": {"type": "object", "properties": {k: {"type": "number"} for k in ("same", "subset", "related", "unrelated")},
                                   "required": ["same", "subset", "related", "unrelated"], "additionalProperties": False}},
    "required": ["same_change"],
    "additionalProperties": False,
}
SCHEMA_PAIR_ASSUME = {  # run 5 (R5.1-B); haiku_pair.md carries the second-key instructions
    "type": "object",
    "properties": {"assumption_conflict": {"type": "object", "properties": {k: {"type": "number"} for k in ("conflict", "depends", "independent")},
                                           "required": ["conflict", "depends", "independent"], "additionalProperties": False}},
    "required": ["assumption_conflict"],
    "additionalProperties": False,
}
SCHEMA_RELATION = {  # run 7 C5: four independent probabilities (not a distribution); mirrors prompts/jev_relation.json
    "type": "object",
    "properties": {
        "same_change": {"type": "number", "description": "Probability 0-1 that A and B make the same change (merging both is redundant)."},
        "same_goal": {"type": "number", "description": "Probability 0-1 that A and B aim at the same problem or outcome."},
        "same_branch": {"type": "number", "description": "Probability 0-1 that A and B target the same base branch."},
        "builds_on": {"type": "number", "description": "Probability 0-1 that one PR continues, extends, fixes or reverts the other."},
    },
    "required": ["same_change", "same_goal", "same_branch", "builds_on"],
    "additionalProperties": False,
}
SCHEMA_RELATION2 = {  # run 8: three independent probabilities; mirrors prompts/jev_relation2.json
    "type": "object",
    "properties": {
        "same_change": {"type": "number", "description": "Probability 0-1 that B makes A unnecessary (B already makes A's change)."},
        "builds_on": {"type": "number", "description": "Probability 0-1 that A depends on or follows B (continues, extends, fixes or reverts it)."},
        "same_goal": {"type": "number", "description": "Probability 0-1 that A and B address the same bug or feature by a different method."},
    },
    "required": ["same_change", "builds_on", "same_goal"],
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

SCHEMA_V3 = {
    "type": "object",
    "properties": {
        **SCHEMA_V2["properties"],
        "exceeds_scope": {"type": "number", "description": "Probability 0-1 that the PR does more than its title and stated purpose."},
        "breaking_change": {"type": "number", "description": "Probability 0-1 that the PR changes a public or shared contract in a way that can break callers."},
        "tests_cover_change": {"type": "number", "description": "Probability 0-1 that the changed behaviour is exercised by tests in the diff."},
        "hard_to_revert": {"type": "number", "description": "Probability 0-1 that reverting after merge would be difficult or unsafe."},
        "follows_conventions": {"type": "number", "description": "Probability 0-1 that the change follows the conventions of the surrounding code."},
    },
    "required": list(DECISIONS_V3),
    "additionalProperties": False,
}

SCHEMA_V4 = {  # run 8: v3 minus message_matches_diff and follows_conventions
    "type": "object",
    "properties": {k: v for k, v in SCHEMA_V3["properties"].items() if k in DECISIONS_V4},
    "required": list(DECISIONS_V4),
    "additionalProperties": False,
}


class HaikuModel:
    name = "haiku"

    def __init__(self, prices: dict | None = None, effort: str = "low", version: str = "v1", model: str | None = None, spec=None):
        import anthropic

        self.client = anthropic.Anthropic()
        self.name = spec.name if spec is not None else "haiku"
        self.model_id = model or MODEL_ID  # any Claude model id via prompts/models.yaml (dialect: anthropic)
        self.version = version
        self.prompt = PROMPTS[version].read_text()
        self.schema_all = {"v1": SCHEMA, "v2": SCHEMA_V2, "v3": SCHEMA_V3, "pair": SCHEMA_PAIR, "pair_assume": SCHEMA_PAIR_ASSUME,
                           "relation": SCHEMA_RELATION, "relation2": SCHEMA_RELATION2, "v4": SCHEMA_V4}[version]
        self.effort = effort
        p = (prices or {}).get(self.name) or ({"input_per_mtok": spec.input_per_mtok, "output_per_mtok": spec.output_per_mtok} if spec is not None else {"input_per_mtok": 0.10, "output_per_mtok": 0.50})
        self.in_price = p["input_per_mtok"] / 1e6
        self.out_price = p["output_per_mtok"] / 1e6

    def decide(self, state: str, features: dict[str, float], questions=DECISIONS) -> Decision:
        questions = questions_for(self.version, questions)
        schema = dict(self.schema_all)
        schema["properties"] = {k: v for k, v in self.schema_all["properties"].items() if k in questions}
        schema["required"] = list(questions)
        try:
            with Timer() as t:
                resp = self.client.messages.create(
                    model=self.model_id,
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
            if "assumption_conflict" in questions and isinstance(out.get("assumption_conflict"), dict):
                pr = out["assumption_conflict"]; z = sum(max(0.0, float(v)) for v in pr.values()) or 1.0
                pr = {k: max(0.0, float(v)) / z for k, v in pr.items()}
                out["assumption_conflict"] = pr.get("conflict", 0.0) + 0.5 * pr.get("depends", 0.0)  # run 5: conflict_of
                raw_choice["assumption_conflict"] = pr
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
            return Decision(probs={q: float("nan") for q in questions}, error=f"{type(e).__name__}: {e}", model_version=self.model_id)
