"""Run 5 component C: v3 wiring, qset-aware compact, stack/pruning on synthetic data."""
import json
import types

import numpy as np

from mergeone.cli import cmd_compact
from mergeone.models.base import DECISIONS_V3, QUESTION_SETS, V3_NEW
from mergeone.models.rules import RulesModel
from mergeone.eval import run5


def _feat():
    return {"churn": 40, "n_topdirs": 2, "touches_sensitive": 0, "title_risk_words": 0, "test_ratio": 0.2, "generated_ratio": 0,
            "vendor_ratio": 0, "title_lowrisk_words": 0, "deletions": 5, "insertions": 35, "body_len": 200, "body_mentions_issue": 1, "n_files": 3}


def test_v3_question_set_and_rules_floor():
    assert QUESTION_SETS["v3"] == DECISIONS_V3 and len(DECISIONS_V3) == 10
    p = RulesModel().decide("t\ndiff (x)\n+a", _feat(), DECISIONS_V3).probs
    assert set(p) == set(DECISIONS_V3) and all(p[q] == 0.5 for q in V3_NEW)


def test_compact_keeps_one_row_per_pr_and_qset(tmp_path):
    f = tmp_path / "p.jsonl"
    rows = [{"pr_number": 1, "qset": "v3", "decision": {"error": "x"}}, {"pr_number": 1, "qset": "v3", "decision": {"error": None}},
            {"pr_number": 1, "qset": "pair", "decision": {"error": None}}, {"pr_number": 1, "qset": "v3", "decision": {"error": "y"}}]
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    cmd_compact(types.SimpleNamespace(files=[str(f)]))
    out = [json.loads(l) for l in f.open()]
    assert sorted((r["qset"], r["decision"]["error"]) for r in out) == [("pair", None), ("v3", None)]


def test_h5_stack_and_pruning_synthetic():
    rng = np.random.default_rng(0)
    ds, val, test = {}, {}, {}
    for i in range(1200):
        y = rng.random() < 0.1
        sig = 1.0 * y + rng.normal(0, 0.25)
        probs = {q: float(np.clip(rng.random() if q in V3_NEW else 0.3 + 0.4 * sig + rng.normal(0, 0.1), 0, 1)) for q in DECISIONS_V3}
        ds[i] = {"split": "val" if i < 800 else "test", "labels": {"iterated": bool(y), "rejected": bool(rng.random() < 0.4)}}
        (val if i < 800 else test)[i] = {"probs": probs}
    e = run5._h5_model(val, test, ds)
    assert e["nhr_v3cal"]["roc_auc"] > 0.7
    assert e["stack"]["test"]["roc_auc"] > 0.7
    assert set(e["stack"]["pruned"]) <= set(V3_NEW)
    assert set(e["questions"]) == set(DECISIONS_V3)
