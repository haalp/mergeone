"""Run 7 report builder on synthetic inputs: every section degrades to "not run yet", and one C1 delta against a fake
run-5 / run-4 baseline. No model or API is called."""
import json
from pathlib import Path

import numpy as np

from mergeone.eval import run7_report as RR
from mergeone.models.base import DECISIONS_V3


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _row(pid, qset, probs, labels, tokens, version):
    return {"pr_number": pid, "split": "test", "labels": labels, "qset": qset,
            "decision": {"probs": probs, "input_tokens": tokens, "latency_ms": 1.0, "model_version": version, "error": None}}


def test_missing_inputs_say_not_run(tmp_path):
    rep = RR.run(tmp_path)
    assert all(v["pass"] is None for h, v in rep["hypotheses"].items())
    assert rep["c1"]["models"]["jev"]["status"] == RR.NOT_RUN
    assert rep["c2"]["test"]["status"] == RR.NOT_RUN and rep["c2"]["rounds"] is None
    assert rep["c3"]["iteration"]["status"] == RR.NOT_RUN and rep["c5"]["status"] == RR.NOT_RUN
    assert rep["inputs"] == []
    md = (tmp_path / "results/run7/tables.md").read_text()
    assert "not run yet" in md and "deferred to run 8" in md
    assert json.loads((tmp_path / "results/run7/metrics.json").read_text())["hypotheses"]["R7-H1"]["pass"] is None
    assert not (tmp_path / "results/run7/README.md").exists()


def test_c1_delta_vs_fake_baseline(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    n = 120
    y = (np.arange(n) % 3 == 0).astype(int)
    full_s = y + rng.normal(0, 0.6, n)       # full state: informative
    comp_s = y + rng.normal(0, 1.2, n)       # compact: noisier
    sig = lambda s: (1 / (1 + np.exp(-s))).round(6)
    full = [_row(i, "v3", {q: float(v) for q in DECISIONS_V3}, {"iterated": bool(t)}, 3000 + i, "jev-1.13.0")
            for i, (t, v) in enumerate(zip(y, sig(full_s)))]
    comp = [_row(i, "v3", {q: float(v) for q in DECISIONS_V3}, {"iterated": bool(t)}, 1000 + i, "jev-1.14.0")
            for i, (t, v) in enumerate(zip(y, sig(comp_s)))]
    # pair rows: ids 10_000+, compact = full answers (Δ = 0 vs the recomputed baseline)
    pairs = [_row(10_000 + i, "pair", {"same_change": float(v)}, {"positive": bool(t)}, 900, "jev-1.13.0")
             for i, (t, v) in enumerate(zip(y, sig(full_s)))]
    _w(tmp_path / "results/run5/predictions_jev_test_all.jsonl", full)
    _w(tmp_path / "results/run4/predictions_jev_test_all.jsonl", pairs)
    _w(tmp_path / "results/run6/pairs_eval_test.jsonl", [{"pr_number": r["pr_number"]} for r in pairs])
    _w(tmp_path / "results/run7/c1/predictions_jev_test_all_compact.jsonl",
       comp + [{**r, "decision": {**r["decision"], "input_tokens": 400, "model_version": "jev-1.14.0"}} for r in pairs])
    # fake baselines: run 5 questions from the full rows' AUC, run 4 pair_test the same
    from sklearn.metrics import roc_auc_score
    a_full, a_comp = roc_auc_score(y, sig(full_s)), roc_auc_score(y, sig(comp_s))
    m5 = {"models": {"jev": {"questions": {q: {"test_raw_vs_iterated": a_full} for q in DECISIONS_V3}}}}
    (tmp_path / "results/run5/metrics.json").write_text(json.dumps(m5))
    (tmp_path / "results/run4/metrics.json").write_text(json.dumps({"models": {"jev": {"pair_test": {"roc_auc": a_full}}}}))
    monkeypatch.setattr(RR, "BOOT_N", 200)  # keep the test fast; the module default stays 1000
    rep = RR.build(tmp_path)
    e = rep["c1"]["models"]["jev"]
    assert e["version_full"] == ["jev-1.13.0"] and e["version_compact"] == ["jev-1.14.0"]
    v = e["v3"]
    assert v["status"] == "ok" and v["label_source"].startswith("prediction rows")
    q = v["questions"]["needs_human_review"]
    assert np.isclose(q["auc"], a_comp) and np.isclose(q["baseline"], a_full) and np.isclose(q["delta"], a_comp - a_full)
    assert np.isclose(q["delta_same_keys"], a_comp - a_full) and q["n_paired"] == n
    lo, hi = q["ci95"]
    assert lo <= q["delta"] <= hi
    assert np.isclose(v["mean"]["delta"], a_comp - a_full)
    assert v["tokens"]["compact"]["median"] < v["tokens"]["full"]["median"]
    p = e["pair"]
    assert p["n"] == n and np.isclose(p["delta"], 0.0) and p["tokens"]["compact"]["median"] == 400
    h = rep["hypotheses"]["R7-H1"]
    assert h["per_model"]["jev"]["pass"] is (a_comp - a_full >= -0.02)
    assert h["per_model"]["pplx-flash"]["pass"] is None  # not run → pending overall unless jev already failed
    assert h["pass"] is (False if a_comp - a_full < -0.02 else None)
    md = RR.tables(rep)
    assert "full jev-1.13.0 → compact jev-1.14.0 (version drift)" in md


def test_c3_c5_present_and_h2_bar(tmp_path):
    ev = {"n_test": 10, "evidence_only": {"test_auc": 0.77}, "stacked": {"jev": {"test_auc": 0.8, "nhr_alone_test_auc": 0.79, "gain": 0.01}},
          "R7-H3": {"pass": True, "evidence_only_auc": 0.77, "stack_gain": {"jev": 0.01}, "bar": "b"}}
    (tmp_path / "results/run7").mkdir(parents=True)
    (tmp_path / "results/run7/evidence_metrics_iteration.json").write_text(json.dumps(ev))
    rel = {"n_pairs": 100, "gold_classes": {"duplicate": 46}, "models": {"jev": {"n": 100, "related_vs_unrelated_auc": 0.9, "n_unrelated": 2,
           "same_change_vs_rest_auc": 0.7, "n_duplicate": 46, "accuracy": 0.5, "accuracy_ci95": [0.4, 0.6], "cost": {"usd_per_pr": 0.001}}},
           "hypotheses": {"R7-H5": {"pass": False, "bar": "b5"}}}
    (tmp_path / "results/run7/relation_metrics.json").write_text(json.dumps(rel))
    rep = RR.build(tmp_path)
    assert rep["hypotheses"]["R7-H3"]["pass"] is True and rep["c3"]["rejection"]["status"] == RR.NOT_RUN
    assert rep["hypotheses"]["R7-H5"]["pass"] is False and rep["c5"]["models"]["jev"]["usd_per_pr_k20"] == 0.001
    assert {i["path"] for i in rep["inputs"]} == {"results/run7/evidence_metrics_iteration.json", "results/run7/relation_metrics.json"}
    t = {"mean": {"delta": 0.025, "ci95": None}, "iteration_eval": {"delta": -0.005}, "prompt_matches_frozen": True}
    assert RR.h2(t)["pass"] is True
    assert RR.h2({**t, "iteration_eval": {"delta": -0.02}})["pass"] is False
    assert RR.h2({**t, "prompt_matches_frozen": False})["pass"] is None


def test_post_hoc_controls_missing_say_not_run(tmp_path):
    rep = RR.build(tmp_path)
    ph = rep["post_hoc"]
    assert ph["pre_registered"] is False and ph["inputs"] == []
    for m in RR.MODELS:
        assert ph["controls"]["models"][m]["status"].startswith("not run")
    assert ph["c5_metadata_branch"]["status"].startswith("not run")
    assert all(v["pass"] is None for v in rep["hypotheses"].values())  # verdicts untouched by the post-hoc block
    md = RR.tables(rep)
    sec = md[md.index("## Post-hoc (not pre-registered): same-day controls (drift)"):]
    assert "not run (results/run7/control/predictions_jev_test_all.jsonl not present)" in sec
    assert "## Post-hoc (not pre-registered): C5 class rule with metadata branch" in sec


def test_post_hoc_c5_metadata_branch_override(tmp_path):
    from mergeone.eval.run6b import _hpid
    repo = "o/r"
    # (a, b, adjudicated label, rationale, base A, base B, model P(same_branch))
    spec = [(1, 2, "duplicate", "", "main", "main", 0.1),          # frozen: backport (wrong) → metadata: duplicate
            (3, 4, "pointer", "backport to stable", "main", "stable", 0.9),  # frozen: duplicate (wrong) → backport
            (5, 6, "duplicate", "", "main", "main", 0.9),          # right either way
            (7, 8, "pointer", "cherry-pick", "main", "release-1", 0.9)]  # B's base only in the PR memory
    _w(tmp_path / "data/run6/hold_labels_adjudicated.jsonl",
       [{"item": i, "pr_number": 100 + a, "repo": repo, "a_number": a, "b_number": b, "label": lab, "rationale": rat}
        for i, (a, b, lab, rat, *_) in enumerate(spec, 1)])
    ds = [{"pr_number": 100 + a, "repo": repo, "number": a, "base": ba, "labels": {"iterated": False}} for a, b, _, _, ba, bb, _ in spec]
    ds += [{"pr_number": 100 + b, "repo": repo, "number": b, "base": bb, "labels": {"iterated": False}} for a, b, _, _, ba, bb, _ in spec[:3]]
    _w(tmp_path / "data/run3/dataset.jsonl", ds)
    _w(tmp_path / "data/run4/memory/o__r.jsonl", [{"repo": repo, "number": "8", "base": "release-1"}])
    preds = [{"pr_number": _hpid(repo, a, b), "qset": "relation", "split": "test",
              "decision": {"probs": {"same_change": 0.9, "same_goal": 0.9, "same_branch": sb, "builds_on": 0.1}, "error": None}}
             for a, b, _, _, _, _, sb in spec]
    _w(tmp_path / "results/run7/relation/predictions_jev_test_all.jsonl", preds)
    rep = RR.build(tmp_path)
    b = rep["post_hoc"]["c5_metadata_branch"]
    assert b["status"] == "ok" and b["base_source"] == {"dataset": 7, "memory": 1, "missing": 0}
    assert b["equal_base"]["n_equal"] == 2 and b["equal_base"]["fraction"] == 0.5
    e = b["models"]["jev"]
    assert e["before"]["accuracy"] == 0.25 and e["after"]["accuracy"] == 1.0
    assert e["before"]["confusion"]["duplicate"]["backport"] == 1 and e["after"]["confusion"]["backport"]["backport"] == 2
    assert e["before"]["same_change_vs_rest_auc"] == e["after"]["same_change_vs_rest_auc"]
    assert e["model_same_branch_agrees_with_metadata"] == 1
    assert b["models"]["pplx-flash"]["status"].startswith("not run")
    assert rep["hypotheses"]["R7-H5"]["pass"] is None  # no relation_metrics.json → verdict pending, not set by post-hoc
    assert {i["path"] for i in rep["post_hoc"]["inputs"]} >= {"data/run6/hold_labels_adjudicated.jsonl", "data/run3/dataset.jsonl",
                                                             "data/run4/memory/o__r.jsonl", "results/run7/relation/predictions_jev_test_all.jsonl"}
    assert "post-hoc" in RR.tables(rep).lower()
