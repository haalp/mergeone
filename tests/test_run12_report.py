"""Run 12 report builder on synthetic inputs: "not run" without rows, no verdict while m₁ is unset, the interval-based
primary and secondary clauses, the shipping rule, the token note. No model or API is called."""
import json
from pathlib import Path

import numpy as np

from mergeone.eval import run12_report as R12
from mergeone.models.base import DECISIONS_V4

FULL = "results/run12/full/predictions_pplx-flash_holdout2_all.jsonl"
FOC = "results/run12/focused/predictions_pplx-flash_holdout2_all.jsonl"


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _row(pid, probs, labels, tokens):
    return {"pr_number": pid, "split": "holdout2", "labels": labels, "qset": "v4",
            "decision": {"probs": probs, "input_tokens": tokens, "latency_ms": 1.0, "model_version": "pplx-decider-flash-v1.2", "error": None}}


def _setup(tmp_path, n=240, noise_focused=0.05, sharper_q=None, worse=False, tokens_ctrl=2000):
    y = (np.arange(n) % 4 == 0).astype(int)
    rng = np.random.default_rng(0)
    full, foc, ds = [], [], []
    for i, t in enumerate(y):
        base = {q: float(np.clip((0.7 if t else 0.3) + rng.normal(0, 0.25), 0, 1)) for q in DECISIONS_V4}
        f = {q: float(np.clip(base[q] + rng.normal(0, noise_focused), 0, 1)) for q in DECISIONS_V4}
        if sharper_q:
            f[sharper_q] = 0.95 if t else 0.05
        if worse:
            f = {q: float(rng.random()) for q in DECISIONS_V4}
        lab = {"iterated": bool(t)}
        full.append(_row(i, base, lab, tokens_ctrl))
        foc.append(_row(i, f, lab, tokens_ctrl))
        ds.append({"pr_number": i, "labels": lab, "origin": "new" if i % 3 else "run3_test"})
    _w(tmp_path / FULL, full)
    _w(tmp_path / FOC, foc)
    _w(tmp_path / "data/run12/h1_full.jsonl", ds)
    (tmp_path / "data/run12/h1_renderings.json").write_text(json.dumps(
        {"rows": n, "iterated_positives": int(y.sum()), "states_differ": 150, "states_differ_among_positives": 40, "dropped": {"no_diff": 2}}))


def test_missing_inputs_say_not_run(tmp_path):
    rep = R12.build(tmp_path)
    assert rep["verdicts"] == {"R12-H1": R12.NOT_RUN} and "not run" in R12.tables(rep).lower()


def test_no_verdict_until_the_margin_is_frozen(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(R12.RR, "BOOT_N", 60)
    assert R12.H1_MARGIN is None                       # the repository default before the freeze
    rep = R12.build(tmp_path)
    h = rep["R12-H1"]
    assert rep["verdicts"]["R12-H1"] == R12.NOT_FROZEN and h["pass"] is None and h["ships"] is None
    assert h["mean"]["ci95"] is not None and h["n"] == {"control": 240, "focused": 240, "paired": 240, "paired_positives": 60}
    assert h["by_origin"] == {"new": {"paired": 160, "positives": 40}, "run3_test": {"paired": 80, "positives": 20}}
    t = R12.tables(rep)
    assert "not set" in t and "the two states differ for 150 (40 of 60 positives)" in t and "| **mean (8)** |" in t


def test_primary_passes_on_the_interval_and_secondary_gates_the_default(tmp_path, monkeypatch):
    monkeypatch.setattr(R12, "H1_MARGIN", 0.03)
    monkeypatch.setattr(R12.RR, "BOOT_N", 80)
    _setup(tmp_path, noise_focused=0.02)
    h = R12.build(tmp_path)["R12-H1"]
    assert h["clauses"]["primary"] is True and h["mean"]["ci95"][0] >= -0.03
    assert h["clauses"]["secondary"] is False and h["ships"] == "selectable"
    _setup(tmp_path, noise_focused=0.02, sharper_q="tests_cover_change")
    rep = R12.build(tmp_path)
    h = rep["R12-H1"]
    assert h["clauses"] == {"primary": True, "secondary": True} and h["ships"] == "selectable and default"
    assert rep["verdicts"]["R12-H1"] == "pass" and h["questions"]["tests_cover_change"]["ci95"][0] > 0
    assert "−0.030" in R12.bars()["R12-H1"]


def test_primary_fails_when_the_lower_end_is_below_the_margin(tmp_path, monkeypatch):
    monkeypatch.setattr(R12, "H1_MARGIN", 0.03)
    monkeypatch.setattr(R12.RR, "BOOT_N", 80)
    _setup(tmp_path, worse=True)
    rep = R12.build(tmp_path)
    h = rep["R12-H1"]
    assert h["clauses"]["primary"] is False and h["ships"] == "nothing" and rep["verdicts"]["R12-H1"] == "fail"
    assert "ships: nothing" in R12.tables(rep)


def test_token_drift_against_the_previous_control_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(R12.RR, "BOOT_N", 40)
    _setup(tmp_path, tokens_ctrl=9000)
    rep = R12.build(tmp_path)
    t = rep["R12-H1"]["tokens"]
    assert t["moved_more_than_2x"] is True and t["ratio_median"] == 1.0 and "moved more than 2×" in R12.tables(rep)
    _setup(tmp_path, tokens_ctrl=2400)
    assert R12.build(tmp_path)["R12-H1"]["tokens"]["moved_more_than_2x"] is False
