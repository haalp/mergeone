"""mergeone gate: typed decisions -> verdict + route, with an audit log.

    from mergeone.gate import Gate
    gate = Gate.from_policy(".mergeone.yaml")
    result = gate.decide(state_text, changed_paths=[...])
    result.verdict, result.route, result.reason, result.probs

Every decision is appended to the gatelog (JSONL) *with its input hash and
rendered state*, because System One models return no rationale: the log is
the only way to audit a verdict after the fact.
"""
from __future__ import annotations

import hashlib
import json
import operator
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..models.base import DECISIONS_V2, Decision

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}
COND_RE = re.compile(r"^\s*(>=|<=|>|<|==)\s*([0-9.]+)\s*$")


@dataclass
class GateResult:
    verdict: str              # pass | hold | block
    route: str                # auto-merge | auto-merge-after-ci | human | domain-review | ...
    reason: str
    rule: str                 # rule name, "default", "always_human_paths", or "on_error"
    probs: dict[str, float]   # calibrated
    raw_probs: dict[str, float]
    model_version: str
    latency_ms: float
    cost_usd: float
    input_sha256: str
    error: str | None = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self):
        return asdict(self)


class Calibrator:
    """Piecewise-linear map fitted offline (isotonic on val). calibration.json: {question: {"x": [...], "y": [...]}}"""

    def __init__(self, maps: dict[str, dict]):
        self.maps = maps

    @classmethod
    def load(cls, path: Path | None):
        if path and Path(path).exists():
            return cls(json.loads(Path(path).read_text()))
        return cls({})

    def __call__(self, probs: dict[str, float]) -> dict[str, float]:
        import numpy as np
        out = {}
        for q, p in probs.items():
            m = self.maps.get(q)
            out[q] = float(np.interp(p, m["x"], m["y"])) if m and p == p else p
        return out


class Gate:
    def __init__(self, policy: dict, model, calibrator: Calibrator, log_path: Path | None = None):
        self.policy = policy
        self.model = model
        self.cal = calibrator
        self.log_path = Path(log_path) if log_path else None
        self.rules = policy.get("rules", [])
        for r in self.rules:
            r["_conds"] = {q: COND_RE.match(str(c)).groups() for q, c in r.get("when", {}).items()}

    @classmethod
    def from_policy(cls, path: str | Path, log_path: str | Path | None = ".mergeone/gatelog.jsonl", model=None):
        policy = yaml.safe_load(Path(path).read_text())
        version = policy.get("question_set", "v2")
        if model is None:
            name = policy.get("model", "jev")
            if name == "jev":
                from ..models.jev import JevModel
                model = JevModel(version=version)
            elif name == "haiku":
                from ..models.haiku import HaikuModel
                model = HaikuModel(version=version)
            else:
                raise ValueError(f"unsupported model for gate: {name}")
        cal_path = None
        if policy.get("calibration", "auto") == "auto":
            cal_path = Path(__file__).with_name(f"calibration_{policy.get('model','jev')}_{version}.json")
        elif policy.get("calibration"):
            cal_path = Path(policy["calibration"])
        return cls(policy, model, Calibrator.load(cal_path), log_path)

    def decide(self, state: str, changed_paths: list[str] | None = None, features: dict | None = None, context: dict | None = None) -> GateResult:
        sha = hashlib.sha256(state.encode()).hexdigest()
        changed_paths = changed_paths or []
        for prefix in self.policy.get("always_human_paths", []):
            if any(p.startswith(prefix) for p in changed_paths):
                res = GateResult("hold", "human", f"touches protected path {prefix}", "always_human_paths", {}, {}, "", 0.0, 0.0, sha)
                self._log(res, state, context)
                return res
        d: Decision = self.model.decide(state, features or {}, DECISIONS_V2)
        if d.error:
            oe = self.policy.get("on_error", {"verdict": "hold", "route": "human", "reason": "model error"})
            res = GateResult(oe["verdict"], oe["route"], oe["reason"], "on_error", {}, {}, d.model_version, d.latency_ms, d.cost_usd, sha, error=d.error)
            self._log(res, state, context)
            return res
        cal = self.cal(d.probs)
        rule, verdict, route, reason = self._match(cal)
        res = GateResult(verdict, route, reason, rule, cal, d.probs, d.model_version, d.latency_ms, d.cost_usd, sha)
        self._log(res, state, context)
        return res

    def _match(self, probs):
        for r in self.rules:
            ok = True
            for q, (op, val) in r["_conds"].items():
                p = probs.get(q)
                if p is None or not OPS[op](p, float(val)):
                    ok = False
                    break
            if ok:
                return r["name"], r["verdict"], r["route"], r.get("reason", "")
        dflt = self.policy.get("default", {"verdict": "pass", "route": "auto-merge", "reason": ""})
        return "default", dflt["verdict"], dflt["route"], dflt.get("reason", "")

    def _log(self, res: GateResult, state: str, context: dict | None):
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as f:
            f.write(json.dumps({**res.to_dict(), "context": context or {}, "state": state}) + "\n")
