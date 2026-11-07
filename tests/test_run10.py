"""Run 10 tooling on synthetic inputs: the d3-flash registry row and adapter pin, run10_rows (commands, guard, set-aside),
and run10_report's clause logic for R10-H1…H4 (pass and fail cases) plus its degradation on empty / partial results.
No model or API is called."""
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from mergeone.eval import run10_report as R10
from mergeone.eval import run10_rows as RW
from mergeone.eval import run8_relation as M8
from mergeone.models import registry
from mergeone.models.base import DECISIONS_V4, RELATION2


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _j(p: Path, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d))


# ----------------------------------------------------------------- registry row + adapter
def test_d3_registry_row_and_pin(monkeypatch):
    monkeypatch.delenv("MERGEONE_D3_ENDPOINT", raising=False)
    s = registry.load_registry()["d3-flash-local"]
    assert s.dialect == "systemone" and s.key_optional and s.model_id == "d3-flash" and s.weights == "vllm-sr/d3-flash"
    assert s.endpoint == "http://127.0.0.1:8000/v1/systemone" and s.license == "Apache-2.0" and s.params_b == 8.4
    assert s.input_per_mtok == 0 and s.output_per_mtok == 0 and s.self_hosted
    monkeypatch.setenv("MERGEONE_D3_ENDPOINT", "http://127.0.0.1:9000/v1/systemone")
    assert registry.load_registry()["d3-flash-local"].endpoint == "http://127.0.0.1:9000/v1/systemone"
    m = registry.build("d3-flash-local", {}, version="v4")
    assert m.pinned == "d3-flash"
    seen = []

    def handler(req):  # the reply shape of d3_server.py (results/run10prep/d3_check.json)
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={"model": "d3-flash", "answers": {q: {"type": "noul", "noul": 0.2} for q in body["questions"]},
                                         "usage": {"input_tokens": 13191, "output_tokens": 0}})
    m.client = httpx.Client(transport=httpx.MockTransport(handler))
    d = m.decide("state", {}, DECISIONS_V4)
    assert d.error is None and d.model_version == "d3-flash" and d.probs["needs_human_review"] == 0.2 and d.cost_usd == 0
    assert seen[0]["model"] == "d3-flash" and list(seen[0]["questions"]) == list(DECISIONS_V4) and d.input_tokens == 13191


# ----------------------------------------------------------------- run10_rows
def test_rows_commands_and_guard(capsys):
    assert set(RW.ROWS) == {"control", "full", "haiku", "d3"} and RW.ROWS["haiku"]["parts"] == ("relation2",)
    assert RW.ROWS["d3"]["parts"] == ("v4_test", "v4_val", "relation2")
    c = RW.command("d3", "v4_val")
    assert "--model d3-flash-local --qset v4 --split val" in c and "--run run10/d3" in c
    assert "--only-missing-from results/run10/d3/predictions_d3-flash-local_val_all.jsonl" in c
    assert "--prompt-file prompts/haiku_relation2.md" in RW.command("haiku", "relation2")
    a = RW.predict_args("control", "relation2")
    assert a.qset == "relation2" and a.dataset.endswith("results/run8/relation_pairs.jsonl") and a.append and a.run == "run10/control"
    with pytest.raises(SystemExit, match="REFUSING"):
        RW.main(["run", "control"])
    with pytest.raises(SystemExit, match="no part"):
        RW.main(["run", "haiku", "--part", "v4_test", RW.FROZEN_FLAG])
    RW.main(["list"])
    assert "run10_rows run d3 --i-have-frozen-the-protocol" in capsys.readouterr().out


def test_set_aside_keeps_other_parts(tmp_path):
    d = tmp_path / "run10" / "d3"
    rows = [{"pr_number": i, "split": "test", "qset": "v4", "decision": {"error": None}} for i in range(3)]
    rows += [{"pr_number": 100 + i, "split": "test", "qset": "relation2", "decision": {"error": None}} for i in range(2)]
    _w(d / "predictions_d3-flash-local_test_all.jsonl", rows)
    _j(d / "meta_d3-flash-local_test_all_v4.json", {"n": 3})
    _j(d / "throughput.json", {"items_per_s": 1})
    r = RW.set_aside("d3", "v4_test", root=tmp_path, stamp="T")
    assert r["moved_rows"] == 3 and r["kept_rows"] == 2
    left = [json.loads(l) for l in (d / "predictions_d3-flash-local_test_all.jsonl").open()]
    assert {x["qset"] for x in left} == {"relation2"}
    assert (d / "passes/T/meta_d3-flash-local_test_all_v4.json").exists() and (d / "passes/T/throughput.json").exists()
    assert not (d / "throughput.json").exists()


# ----------------------------------------------------------------- H1
def _score_row(acc, comp_correct, area_correct):
    return {"accuracy": acc, "per_class": {"competing": {"n": 7, "correct": comp_correct}, "same_area": {"n": 10, "correct": area_correct}}}


def test_h1_clauses():
    ok = R10.h1_clauses(_score_row(0.60, 4, 3), 0.61)          # 7/17 = 0.41 ≥ 0.40; 0.60 ≥ 0.61 − 0.02
    assert ok == {"competing_same_area": True, "class_accuracy_vs_control": True}
    assert R10.h1_clauses(_score_row(0.60, 3, 3), 0.61)["competing_same_area"] is False      # 6/17 = 0.35
    assert R10.h1_clauses(_score_row(0.58, 5, 5), 0.61)["class_accuracy_vs_control"] is False  # bought by breaking dup/backport
    assert R10.h1_clauses(_score_row(0.59, 5, 5), 0.61)["class_accuracy_vs_control"] is True   # exactly control − 0.02
    assert R10.h1_clauses(_score_row(0.9, 7, 10), None)["class_accuracy_vs_control"] is None


def _gold():
    cls = ["duplicate"] * 6 + ["competing"] * 2 + ["same_area"] * 2
    return {i: {"relation": c, "source": "notes rule"} for i, c in enumerate(cls)}


def _rel_rows(pids_probs, ts="2026-11-09T10:00:00+00:00", cost=1e-5, tokens=2500):
    return [{"pr_number": k, "split": "test", "labels": {}, "qset": "relation2", "mode": "joint", "ts": ts,
             "decision": {"probs": p, "error": None, "input_tokens": tokens, "latency_ms": 200.0, "cost_usd": cost, "model_version": "x"}}
            for k, p in pids_probs.items()]


DUP = {"same_change": 0.9, "builds_on": 0.1, "same_goal": 0.1}
COMP = {"same_change": 0.1, "builds_on": 0.1, "same_goal": 0.8}
AREA = {"same_change": 0.3, "builds_on": 0.1, "same_goal": 0.1}
NONE = {"same_change": 0.0, "builds_on": 0.0, "same_goal": 0.0}


def test_h1_through_run8_rule(tmp_path, monkeypatch):
    gold = _gold()
    monkeypatch.setattr(R10, "load_gold", lambda inp: (gold, []))
    monkeypatch.setattr(M8, "pair_metadata", lambda inp, adj: ({}, {}))
    # control: duplicates right, competing / same_area wrong → class accuracy 0.6
    ctrl = {k: (DUP if g["relation"] == "duplicate" else NONE) for k, g in gold.items()}
    # d3: reads competing and same_area, keeps duplicates → pass; full: reads them but loses duplicates → fails the class clause
    good = {k: {"duplicate": DUP, "competing": COMP, "same_area": AREA}[g["relation"]] for k, g in gold.items()}
    bad = {k: ({"competing": COMP, "same_area": AREA}.get(g["relation"], NONE)) for k, g in gold.items()}
    _w(tmp_path / R10.preds_path("control"), _rel_rows(ctrl))
    _w(tmp_path / R10.preds_path("d3"), _rel_rows(good))
    _w(tmp_path / R10.preds_path("full"), _rel_rows(bad))
    L = R10.Loaded(R10.Inputs(tmp_path))
    c = R10.h1(L)
    assert c["rows"]["control"]["accuracy"] == pytest.approx(0.6)
    assert c["per_row"]["d3"]["pass"] is True and c["per_row"]["d3"]["competing_same_area"]["accuracy"] == 1.0
    assert c["per_row"]["full"]["clauses"] == {"competing_same_area": True, "class_accuracy_vs_control": False}
    assert c["per_row"]["full"]["pass"] is False and c["per_row"]["haiku"]["pass"] is None
    assert c["R10-H1"]["pass"] is True
    _w(tmp_path / R10.preds_path("d3"), _rel_rows(ctrl))   # d3 = control's answers → fails the 0.40 clause
    c = R10.h1(R10.Loaded(R10.Inputs(tmp_path)))
    assert c["per_row"]["d3"]["pass"] is False and c["R10-H1"]["pass"] is None   # haiku not run yet: no verdict
    _w(tmp_path / R10.preds_path("haiku"), _rel_rows(ctrl))
    assert R10.h1(R10.Loaded(R10.Inputs(tmp_path)))["R10-H1"]["pass"] is False


# ----------------------------------------------------------------- H2
def test_h2_clause():
    assert R10.h2_clause(0.05, [0.01, 0.09]) is True
    assert R10.h2_clause(0.03, [0.001, 0.06]) is True
    assert R10.h2_clause(0.029, [0.001, 0.06]) is False     # gain below +0.03
    assert R10.h2_clause(0.05, [-0.01, 0.11]) is False      # CI includes zero
    assert R10.h2_clause(None, [0.01, 0.1]) is None and R10.h2_clause(0.05, None) is None


def _dataset(tmp_path, n_test=300, n_val=200, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_test + n_val):
        rows.append({"pr_number": i, "split": "test" if i < n_test else "val", "labels": {"iterated": bool(rng.random() < 0.3)}})
    _w(tmp_path / "data/run3/dataset.jsonl", rows)
    return {r["pr_number"]: int(r["labels"]["iterated"]) for r in rows}


def _v4_rows(y, keys, signal, rng, split="test", ts="2026-11-09T10:00:00+00:00", cost=1.3e-4, tokens=13600, latency=1000.0, model="m"):
    out = []
    for k in keys:
        p = {q: float(np.clip(signal[q] * y[k] + rng.normal(0.4, 0.25), 0, 1)) for q in DECISIONS_V4}
        out.append({"pr_number": k, "split": split, "labels": {}, "qset": "v4", "mode": "joint", "ts": ts,
                    "decision": {"probs": p, "error": None, "input_tokens": tokens, "latency_ms": latency, "cost_usd": cost, "model_version": model}})
    return out


def test_h2_pass_and_fail_on_synthetic_answers(tmp_path):
    y = _dataset(tmp_path)
    rng = np.random.default_rng(1)
    test = [k for k in y if k < 300]
    weak = {q: 0.15 for q in DECISIONS_V4}
    strong = {**weak, "needs_human_review": 0.6}
    _w(tmp_path / R10.preds_path("control"), _v4_rows(y, test, weak, rng))
    _w(tmp_path / R10.preds_path("full"), _v4_rows(y, test, strong, rng))
    _w(tmp_path / R10.preds_path("d3"), _v4_rows(y, test, weak, rng))
    L = R10.Loaded(R10.Inputs(tmp_path))
    yy, _ = R10.RR.iter_labels(L.inp)
    v4s = R10.v4_section(L, yy)
    c = R10.h2(L, v4s, yy)
    f, d = c["per_row"]["full"], c["per_row"]["d3"]
    assert f["delta"] > 0.1 and f["ci95"][0] > 0 and f["pass"] is True and f["n_paired"] == 300
    assert d["pass"] is False and c["R10-H2"]["pass"] is True
    q = v4s["rows"]["control"]["questions"]["needs_human_review"]
    assert q["n"] == 300 and q["ci95"][0] < q["auc"] < q["ci95"][1]


# ----------------------------------------------------------------- H3
def test_h3_cost_formula_and_bar():
    assert R10.per_pr_cost(1e-4, 10000, 2500) == pytest.approx(1e-4 * 3.5)
    assert R10.per_pr_cost(None, 10000, 2500) is None and R10.per_pr_cost(1e-4, 0, 2500) is None
    assert R10.cost_clause(0.0025, 0.0005) is True and R10.cost_clause(0.00251, 0.0005) is False
    assert R10.cost_clause(None, 0.0005) is None


def test_h3_rows_and_ship_rule(tmp_path):
    y = _dataset(tmp_path)
    rng = np.random.default_rng(2)
    test = [k for k in y if k < 300]
    sig = {q: 0.3 for q in DECISIONS_V4}
    _w(tmp_path / R10.preds_path("control"), _v4_rows(y, test, sig, rng, cost=1e-4, tokens=10000)
       + _rel_rows({10_000 + i: NONE for i in range(10)}, cost=2.5e-5, tokens=2500))
    _w(tmp_path / R10.preds_path("full"), _v4_rows(y, test, sig, rng, cost=2e-4, tokens=10000)
       + _rel_rows({10_000 + i: NONE for i in range(10)}, cost=5e-5, tokens=2500))
    _w(tmp_path / R10.preds_path("haiku"), _rel_rows({10_000 + i: NONE for i in range(10)}, cost=1e-3, tokens=3000))
    _w(tmp_path / R10.preds_path("d3"), _v4_rows(y, test, sig, rng, cost=0.0, tokens=13000) + _rel_rows({10_000 + i: NONE for i in range(10)}, cost=0, tokens=2600))
    _j(tmp_path / "results/run10/d3/throughput.json", {"items_per_s": 1.2, "concurrency": 8})
    _j(tmp_path / "results/run10/d3/serve_meta.json", {"usd_per_hour": 0.59})
    L = R10.Loaded(R10.Inputs(tmp_path))
    # haiku passes H1 (but costs > 5×), full passes H2 (2× control), d3 passes nothing
    h1v = {"per_row": {"full": {"pass": False}, "haiku": {"pass": True}, "d3": {"pass": False}}}
    h2v = {"per_row": {"full": {"pass": True}, "d3": {"pass": False}}}
    c = R10.h3(L, h1v, h2v)
    r = c["rows"]
    assert r["control"]["per_pr_usd"] == pytest.approx(1e-4 * 3.5)
    assert r["full"]["ratio_to_control"] == pytest.approx(2.0) and r["full"]["ships"] is True
    assert r["haiku"]["per_pr_usd"] == pytest.approx(1e-4 + 10 * 1e-3) and r["haiku"]["within_5x"] is False and r["haiku"]["ships"] is False
    assert r["d3"]["c_main"] == pytest.approx(0.59 / 3600 / 1.2) and r["d3"]["per_pr_usd"] == pytest.approx(0.59 / 3600 / 1.2 * 3.0)
    assert r["d3"]["passes_h1_or_h2"] is False and r["d3"]["ships"] is False
    assert r["control"]["p50_latency_ms"] == 1000.0 and r["haiku"]["latency_of"] == "relation2 pair call"
    assert c["R10-H3"]["pass"] is False and c["R10-H3"]["rows_under_bar"] == ["full", "haiku"]
    h1v["per_row"]["haiku"]["pass"] = False
    c = R10.h3(L, h1v, h2v)
    assert c["R10-H3"]["pass"] is True and c["R10-H3"]["rows_under_bar"] == ["full"]
    h2v["per_row"]["full"]["pass"] = False
    c = R10.h3(L, h1v, h2v)
    assert c["R10-H3"]["pass"] is None and c["R10-H3"]["na"] is True and R10._verdict(c["R10-H3"]) == "n/a"


# ----------------------------------------------------------------- H4
def test_h4_clauses():
    ctrl = {q: 0.70 for q in DECISIONS_V4}
    six = {q: (0.69 if i < 6 else 0.60) for i, q in enumerate(DECISIONS_V4)}       # 6 within 0.02, 2 below
    five = {q: (0.69 if i < 5 else 0.60) for i, q in enumerate(DECISIONS_V4)}
    assert R10.h4_clauses(six, ctrl, 1.2)["auc_clause"] is True
    assert R10.h4_clauses(six, ctrl, 1.2)["n_within"] == 6 and R10.h4_clauses(six, ctrl, 1.2)["throughput_clause"] is True
    assert R10.h4_clauses(five, ctrl, 1.2)["auc_clause"] is False
    assert R10.h4_clauses(six, ctrl, 0.99)["throughput_clause"] is False
    assert R10.h4_clauses({**six, "needs_human_review": None}, ctrl, 1.2)["auc_clause"] is None
    assert R10.h4_clauses(six, ctrl, None)["throughput_clause"] is None
    edge = {q: 0.68 for q in DECISIONS_V4}                                             # exactly control − 0.02
    assert R10.h4_clauses(edge, ctrl, 1.0) == {**R10.h4_clauses(edge, ctrl, 1.0), "auc_clause": True, "throughput_clause": True}


def test_h4_end_to_end_with_agreement_and_calibration(tmp_path):
    y = _dataset(tmp_path)
    rng = np.random.default_rng(3)
    test, val = [k for k in y if k < 300], [k for k in y if k >= 300]
    sig = {q: 0.4 for q in DECISIONS_V4}
    ctrl = _v4_rows(y, test, sig, rng)
    d3 = [{**r, "decision": {**r["decision"], "model_version": "d3-flash",
                             "probs": {q: float(np.clip(v + rng.normal(0, 0.01), 0, 1)) for q, v in r["decision"]["probs"].items()}}} for r in ctrl]
    _w(tmp_path / R10.preds_path("control"), ctrl)
    _w(tmp_path / R10.preds_path("d3"), d3)
    _w(tmp_path / R10.preds_path("d3", "val"), _v4_rows(y, val, sig, rng, split="val", model="d3-flash"))
    _j(tmp_path / "results/run10/d3/throughput.json", {"items_per_s": 1.5, "concurrency": 32, "n_items": 300, "wall_s": 200, "errors": 0})
    rep = R10.run(tmp_path)
    h = rep["h4"]
    assert h["R10-H4"]["throughput_clause"] is True and h["R10-H4"]["n_within"] >= 6 and h["R10-H4"]["pass"] is True
    assert "status" not in h["R10-H4"]   # concurrency 32 = §R10.3: no note
    assert h["agreement"]["n_prs"] == 300 and h["agreement"]["n_answers"] == 2400 and 0 < h["agreement"]["mean_abs_dp"] < 0.02
    assert h["calibration_map"]["status"] == "ok" and h["calibration_map"]["questions"]["needs_human_review"]["fitted"]
    _j(tmp_path / "results/run10/d3/throughput.json", {"items_per_s": 0.8, "concurrency": 8})
    rep = R10.run(tmp_path)
    assert rep["h4"]["R10-H4"]["pass"] is False and "concurrency 8" in rep["h4"]["R10-H4"]["status"]
    assert rep["dates"]["one_utc_day"] is True


# ----------------------------------------------------------------- degradation
def test_empty_results_say_not_run(tmp_path, capsys):
    rep = R10.run(tmp_path)
    assert all(v["pass"] is None for v in rep["hypotheses"].values())
    assert "control/v4_test" in rep["missing"] and "d3/throughput.json" in rep["missing"] and rep["inputs"] == []
    md = (tmp_path / "results/run10/tables.md").read_text()
    assert "not run yet" in md and "## Verdicts" in md and "## Inputs" in md
    assert json.loads((tmp_path / "results/run10/metrics.json").read_text())["hypotheses"]["R10-H2"]["pass"] is None
    assert "missing: control/v4_test" in capsys.readouterr().out


def test_partial_results(tmp_path):
    y = _dataset(tmp_path)
    rng = np.random.default_rng(4)
    _w(tmp_path / R10.preds_path("control"), _v4_rows(y, [k for k in y if k < 300], {q: 0.3 for q in DECISIONS_V4}, rng,
                                                       ts="2026-11-09T23:59:00+00:00"))
    _w(tmp_path / R10.preds_path("haiku"), _rel_rows({1: NONE}, ts="2026-11-10T00:01:00+00:00"))
    rep = R10.run(tmp_path)
    assert rep["v4"]["rows"]["control"]["status"] == "ok" and rep["v4"]["rows"]["full"]["status"].startswith(R10.NOT_RUN)
    assert rep["h2"]["per_row"]["full"]["pass"] is None and rep["h4"]["R10-H4"]["pass"] is None
    assert rep["dates"]["one_utc_day"] is False
    assert "full/v4_test" in rep["missing"] and "control/v4_test" not in rep["missing"]
    assert set(RELATION2) == {"same_change", "builds_on", "same_goal"}
