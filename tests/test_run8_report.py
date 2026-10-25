"""Run 8 report builder on synthetic inputs: every section degrades to "not run yet", one compact-vs-control delta,
the R8-H2 clauses and the R8-H5 fast-signal clauses. No model or API is called."""
import json
from pathlib import Path

import numpy as np

from mergeone.eval import run8_report as R8
from mergeone.models.base import DECISIONS_V4


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _row(pid, qset, probs, labels, tokens, version="jev-1.14.0"):
    return {"pr_number": pid, "split": "test", "labels": labels, "qset": qset,
            "decision": {"probs": probs, "input_tokens": tokens, "latency_ms": 1.0, "model_version": version, "error": None}}


def test_missing_inputs_say_not_run(tmp_path):
    rep = R8.run(tmp_path)
    assert all(v["pass"] is None for v in rep["hypotheses"].values())
    for m in R8.MODELS:
        assert rep["h1"]["models"][m]["v4"]["status"].startswith(R8.NOT_RUN)
        assert rep["h1"]["models"][m]["pair"]["status"].startswith(R8.NOT_RUN)
        assert rep["h3"]["models"][m]["status"].startswith(R8.NOT_RUN)
    assert rep["h2"]["status"] == R8.NOT_RUN and rep["h5"]["source"] == "none"
    assert rep["h4"]["status"].startswith("deferred")
    assert rep["inputs"] == []
    md = (tmp_path / "results/run8/tables.md").read_text()
    assert "not run yet" in md and "deferred" in md and "## Inputs" in md
    assert json.loads((tmp_path / "results/run8/metrics.json").read_text())["hypotheses"]["R8-H1"]["pass"] is None


def test_pair_compact_vs_same_day_control(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    n = 120
    y = (np.arange(n) % 3 == 0).astype(int)
    sig = lambda s: (1 / (1 + np.exp(-s))).round(6)
    ctrl_s, comp_s = sig(y + rng.normal(0, 0.6, n)), sig(y + rng.normal(0, 1.2, n))
    ctrl = [_row(10_000 + i, "pair", {"same_change": float(v)}, {"positive": bool(t)}, 3000) for i, (t, v) in enumerate(zip(y, ctrl_s))]
    comp = [_row(10_000 + i, "pair", {"same_change": float(v)}, {"positive": bool(t)}, 1200) for i, (t, v) in enumerate(zip(y, comp_s))]
    # an id outside the eval set must be ignored
    comp.append(_row(99_999, "pair", {"same_change": 0.0}, {"positive": True}, 1))
    _w(tmp_path / "results/run8/pair/predictions_jev_test_all.jsonl", ctrl)
    _w(tmp_path / "results/run8/pair/predictions_jev_test_all_compact.jsonl", comp)
    _w(tmp_path / "results/run6/pairs_eval_test.jsonl", [{"pr_number": 10_000 + i} for i in range(n)])
    monkeypatch.setattr(R8.RR, "BOOT_N", 200)
    from sklearn.metrics import roc_auc_score
    a_ctrl, a_comp = roc_auc_score(y, ctrl_s), roc_auc_score(y, comp_s)
    rep = R8.build(tmp_path)
    p = rep["h1"]["models"]["jev"]["pair"]
    assert p["status"] == "ok" and p["n"] == n and p["n_eval_pairs"] == n
    assert np.isclose(p["auc"], a_comp) and np.isclose(p["baseline"], a_ctrl) and np.isclose(p["delta"], a_comp - a_ctrl)
    lo, hi = p["ci95"]
    assert lo <= p["delta_same_keys"] <= hi and p["n_paired"] == n
    assert p["tokens"]["compact"]["median"] == 1200 and p["tokens"]["control"]["median"] == 3000
    h = rep["hypotheses"]["R8-H1"]["per_model"]["jev"]
    assert np.isclose(h["pair_delta"], a_comp - a_ctrl) and h["v4_mean_delta"] is None
    assert h["pass"] is (False if a_comp - a_ctrl < -0.02 else None)   # v4 half not run → pending unless pair already failed
    assert rep["h1"]["models"]["jev"]["v4"]["status"].startswith(R8.NOT_RUN)
    assert "compact" in R8.tables(rep)


def test_v4_compact_and_h3(tmp_path, monkeypatch):
    n = 60
    y = (np.arange(n) % 2 == 0).astype(int)
    probs = lambda s: {q: float(s) for q in DECISIONS_V4}
    full = [_row(i, "v4", probs(0.8 if t else 0.2), {"iterated": bool(t)}, 2000) for i, t in enumerate(y)]
    v3c = [_row(i, "v3", probs(0.7 if t else 0.3), {"iterated": bool(t)}, 2100) for i, t in enumerate(y)]
    comp = [_row(i, "v4", probs(0.6 if t else 0.4), {"iterated": bool(t)}, 900) for i, t in enumerate(y)]
    _w(tmp_path / "results/run8/v4/predictions_jev_test_all.jsonl", full + v3c)
    _w(tmp_path / "results/run8/v4/predictions_jev_test_all_compact.jsonl", comp)
    monkeypatch.setattr(R8.RR, "BOOT_N", 50)
    rep = R8.build(tmp_path)
    v = rep["h1"]["models"]["jev"]["v4"]
    assert v["status"] == "ok" and np.isclose(v["mean"]["delta"], 0.0) and v["tokens"]["compact"]["median"] == 900
    e = rep["h3"]["models"]["jev"]
    assert e["v4_vs_control"]["status"] == "ok" and np.isclose(e["v4_vs_control"]["mean"]["auc"], 1.0)
    assert e["control_vs_run5"]["status"].startswith("run-5 baseline missing")
    assert rep["h3"]["models"]["pplx-flash"]["status"].startswith(R8.NOT_RUN)


def test_h2_clauses(tmp_path):
    pc = lambda comp, sa: {"competing": {"n": 7, "correct": comp}, "same_area": {"n": 10, "correct": sa}}
    rel = {"n_pairs": 100, "gold_classes": {}, "models": {
        "jev": {"n": 100, "accuracy": 0.82, "accuracy_ci95": [0.7, 0.9], "same_change_vs_rest_auc": 0.67, "n_duplicate": 46, "per_class": pc(4, 3)},
        "pplx-flash": {"n": 100, "accuracy": 0.85, "accuracy_ci95": [0.7, 0.9], "same_change_vs_rest_auc": 0.70, "n_duplicate": 46, "per_class": pc(1, 1)}},
        "baseline_run7": {}, "control_same_day": {}}
    _w(tmp_path / "results/run8/relation_metrics.json", [])
    (tmp_path / "results/run8/relation_metrics.json").write_text(json.dumps(rel))
    h = R8.build(tmp_path)["hypotheses"]["R8-H2"]
    assert h["per_model"]["jev"]["clauses"] == {"accuracy": True, "competing_same_area": True, "same_change_auc": True}  # 0.67 ≥ 0.685 − 0.02
    assert h["per_model"]["pplx-flash"]["clauses"]["competing_same_area"] is False
    assert h["pass"] is True


def test_h5_fast_signal_from_replay(tmp_path):
    d = lambda flagged: {"reference": "replay", "questions": {"x": {}}, "degraded": flagged}
    replay = {"pplx-flash test: run 5 → run 7": d(["message_matches_diff", "follows_conventions"]),
              "jev test: run 5 → run 7": d([]), "pplx-flash run 6A self-hosted": d([])}
    (tmp_path / "results/run8").mkdir(parents=True)
    (tmp_path / "results/run8/drift_replay.json").write_text(json.dumps(replay))
    h = R8.build(tmp_path)["h5"]
    assert h["source"] == "go" and h["R8-H5"]["clauses"]["pplx_exact"] is True and h["R8-H5"]["clauses"]["run6a_nothing"] is True
    assert h["slow"]["status"].startswith("not implemented") and h["R8-H5"]["pass"] is None
    replay["jev test: run 5 → run 7"] = d(["tests_cover_change"])
    (tmp_path / "results/run8/drift_replay.json").write_text(json.dumps(replay))
    assert R8.build(tmp_path)["hypotheses"]["R8-H5"]["pass"] is False
