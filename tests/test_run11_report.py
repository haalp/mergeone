"""Run 11 report builder on synthetic inputs: every section degrades to "not run", R11-H1's three clauses (pass, a
per-question fail, a token fail), R11-H2 read from prefilter.json, the tables. No model or API is called."""
import json
from pathlib import Path

import numpy as np

from mergeone.eval import run11_report as R11
from mergeone.models.base import DECISIONS_V4


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _row(pid, qset, probs, labels, tokens, version="pplx-decider-flash-v1.2"):
    return {"pr_number": pid, "split": "test", "labels": labels, "qset": qset,
            "decision": {"probs": probs, "input_tokens": tokens, "latency_ms": 1.0, "model_version": version, "error": None}}


def test_missing_inputs_say_not_run(tmp_path):
    rep = R11.build(tmp_path)
    assert all(v == R11.NOT_RUN for v in rep["verdicts"].values())
    assert "not run" in R11.tables(rep).lower()


def _rows(n, comp_scale, tokens_c, weak_q=None):
    y = (np.arange(n) % 2 == 0).astype(int)
    rng = np.random.default_rng(0)
    full, comp = [], []
    for i, t in enumerate(y):
        base = {q: float(np.clip((0.8 if t else 0.2) + rng.normal(0, 0.05), 0, 1)) for q in DECISIONS_V4}
        c = {q: float(np.clip((0.5 + (base[q] - 0.5) * comp_scale), 0, 1)) for q in DECISIONS_V4}
        if weak_q:
            c[weak_q] = float(rng.random())  # one question becomes noise under compact
        full.append(_row(i, "v4", base, {"iterated": bool(t)}, 2000))
        comp.append(_row(i, "v4", c, {"iterated": bool(t)}, tokens_c))
    return full, comp


def test_h1_pass(tmp_path, monkeypatch):
    full, comp = _rows(120, 1.0, 1200)
    _w(tmp_path / "results/run11/main/predictions_pplx-flash_test_all.jsonl", full)
    _w(tmp_path / "results/run11/main/predictions_pplx-flash_test_all_compact.jsonl", comp)
    monkeypatch.setattr(R11.RR, "BOOT_N", 50)
    rep = R11.build(tmp_path)
    h = rep["R11-H1"]
    assert h["status"] == "ok" and np.isclose(h["mean"]["delta"], 0.0) and h["tokens"]["ratio_median"] == 0.6
    assert h["clauses"] == {"mean": True, "each": True, "tokens": True} and h["pass"] is True
    assert rep["verdicts"]["R11-H1"] == "pass"
    t = R11.tables(rep)
    assert "ratio 0.600" in t and "| **mean (8)** |" in t


def test_h1_one_weak_question_fails_each_clause(tmp_path, monkeypatch):
    full, comp = _rows(200, 1.0, 1200, weak_q="hard_to_revert")
    _w(tmp_path / "results/run11/main/predictions_pplx-flash_test_all.jsonl", full)
    _w(tmp_path / "results/run11/main/predictions_pplx-flash_test_all_compact.jsonl", comp)
    monkeypatch.setattr(R11.RR, "BOOT_N", 50)
    h = R11.build(tmp_path)["R11-H1"]
    assert h["clauses"]["each"] is False and h["worst_question"][0] == "hard_to_revert" and h["pass"] is False


def test_h1_tokens_clause(tmp_path, monkeypatch):
    full, comp = _rows(80, 1.0, 1700)  # 0.85 of the control: not enough saving
    _w(tmp_path / "results/run11/main/predictions_pplx-flash_test_all.jsonl", full)
    _w(tmp_path / "results/run11/main/predictions_pplx-flash_test_all_compact.jsonl", comp)
    monkeypatch.setattr(R11.RR, "BOOT_N", 50)
    h = R11.build(tmp_path)["R11-H1"]
    assert h["clauses"]["tokens"] is False and h["pass"] is False


def test_h2_from_prefilter_json(tmp_path):
    pf = {"groups": 100, "labels": {"duplicate": 46, "pointer": 52, "unrelated": 2},
          "control": {"k": 10, "auc_dup_rest": 0.70, "calls_per_a": 10.0},
          "scores": {"retrieval": {"3": {"recall_dup": 0.96, "auc_dup_rest": 0.69, "delta_auc": -0.01, "pass": True},
                                   "5": {"recall_dup": 0.98, "auc_dup_rest": 0.70, "delta_auc": 0.0, "pass": True}}},
          "verdict": {"primary": "retrieval", "ships_k": 3, "pass": True}}
    (tmp_path / "results/run11").mkdir(parents=True)
    (tmp_path / "results/run11/prefilter.json").write_text(json.dumps(pf))
    rep = R11.build(tmp_path)
    assert rep["verdicts"]["R11-H2"] == "pass" and rep["R11-H2"]["ships_k"] == 3
    assert "retrieval (primary) | 3 |" in R11.tables(rep) and "ships: k' = 3" in R11.tables(rep)
    assert rep["verdicts"]["R11-H3"] == R11.NOT_RUN
