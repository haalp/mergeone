"""Run 6A: registry row, --single-question, mode-aware compact/resume, loadtest wiring and the run-6 report on synthetic rows."""
import json
import types

import httpx
import numpy as np
import pytest

from mergeone import cli
from mergeone.cli import cmd_compact
from mergeone.eval import leaderboard, run6
from mergeone.models import registry
from mergeone.models.base import DECISIONS_V3, decide_single


# ----------------------------------------------------------------- registry + client
def _fake_server(calls):
    def handler(req: httpx.Request):
        body = json.loads(req.content)
        calls.append(body)
        ans = {q: ({"type": "noul", "noul": 0.25} if v["type"] == "noul" else
                   {"type": "choice", "probabilities": {"same": 0.5, "subset": 0.2, "related": 0.2, "unrelated": 0.1}, "confidence": 0.4})
               for q, v in body["questions"].items()}
        return httpx.Response(200, json={"model": "pplx-decider-flash-v1.2@abc", "answers": ans,
                                         "usage": {"input_tokens": 100, "output_tokens": 0, "sequences": len(ans)}})
    return handler


def test_registry_row_and_client(monkeypatch):
    monkeypatch.delenv("MERGEONE_LOCAL_ENDPOINT", raising=False)
    spec = registry.load_registry()["pplx-flash-local"]
    assert spec.dialect == "systemone" and spec.key_optional and spec.model_id == "pplx-decider-flash-v1.2"
    assert spec.endpoint == "http://127.0.0.1:8080/v1/systemone" and spec.input_per_mtok == 0 and spec.self_hosted
    assert spec.weights == "perplexity-ai/pplx-decider-flash-v1.2" and "local" in registry.load_registry()
    m = registry.build("pplx-flash-local", {}, version="v3")
    calls = []
    m.client = httpx.Client(transport=httpx.MockTransport(_fake_server(calls)))
    d = m.decide("state", {}, DECISIONS_V3)
    assert d.error is None and d.probs["needs_human_review"] == 0.25 and d.model_version == "pplx-decider-flash-v1.2@abc"
    assert len(calls) == 1 and len(calls[0]["questions"]) == 10 and calls[0]["model"] == "pplx-decider-flash-v1.2"
    s = decide_single(m, "state", {}, DECISIONS_V3)
    assert len(calls) == 11 and all(len(c["questions"]) == 1 for c in calls[1:])
    assert s.error is None and set(s.probs) == set(DECISIONS_V3) and s.input_tokens == 1000 and s.raw["mode"] == "single"
    assert s.raw["usage"]["requests"] == 10 and s.latency_ms >= sum(v["latency_ms"] for v in s.raw["per_question"].values())
    mp = registry.build("pplx-flash-local", {}, version="pair")
    mp.client = httpx.Client(transport=httpx.MockTransport(_fake_server(calls)))
    p = decide_single(mp, "x", {}, ("same_change",))
    assert p.error is None and p.probs["same_change"] == pytest.approx(0.6) and p.raw["choice"]["same_change"]["same"] == 0.5
    mp.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": {"type": "LengthError"}})))
    e = decide_single(mp, "x", {}, ("same_change",))
    assert e.error.startswith("same_change: HTTP 400") and e.probs["same_change"] != e.probs["same_change"]


def test_http_pool_follows_env(monkeypatch):
    monkeypatch.setenv("MERGEONE_HTTP_MAX_CONNECTIONS", "512")
    m = registry.build("pplx-flash-local", {}, version="v3")
    assert m.client._transport._pool._max_connections == 512


# ----------------------------------------------------------------- predict --single-question, resume, compact
def _ds(tmp_path, n=6):
    feat = {"churn": 40, "n_topdirs": 2, "touches_sensitive": 0, "title_risk_words": 0, "test_ratio": 0.2, "generated_ratio": 0,
            "vendor_ratio": 0, "title_lowrisk_words": 0, "deletions": 5, "insertions": 35, "body_len": 200, "body_mentions_issue": 1, "n_files": 3}
    p = tmp_path / "ds.jsonl"
    p.write_text("".join(json.dumps({"pr_number": i, "split": "test", "labels": {"iterated": i % 2 == 0}, "features": feat, "state": f"s{i}"}) + "\n" for i in range(n)))
    return p


def _pargs(ds, **kw):
    a = dict(model="rules", split="test", run="r6t", limit=None, concurrency=2, questions="all", words=False, append=True,
             only_errors_from=None, only_missing_from=None, qset="v3", subset=None, dataset=str(ds), single_question=False)
    a.update(kw)
    return types.SimpleNamespace(**a)


def test_predict_single_question_mode_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RESULTS", tmp_path)
    ds = _ds(tmp_path)
    out = tmp_path / "r6t" / "predictions_rules_test_all.jsonl"
    cli.cmd_predict(_pargs(ds, limit=3))                                   # joint, PRs 0-2
    cli.cmd_predict(_pargs(ds, only_missing_from=str(out)))                # joint, only 3-5
    cli.cmd_predict(_pargs(ds, single_question=True, only_missing_from=str(out)))  # single: all 6 (joint rows don't count)
    rows = [json.loads(l) for l in out.open()]
    assert sorted((r["mode"], r["pr_number"]) for r in rows) == sorted([("joint", i) for i in range(6)] + [("single", i) for i in range(6)])
    single = [r for r in rows if r["mode"] == "single"]
    assert all(r["decision"]["raw"]["mode"] == "single" and set(r["decision"]["probs"]) == set(DECISIONS_V3) for r in single)
    assert (tmp_path / "r6t" / "meta_rules_test_all_v3_single.json").exists() and (tmp_path / "r6t" / "meta_rules_test_all_v3.json").exists()
    cli.cmd_predict(_pargs(ds, single_question=True, only_missing_from=str(out)))  # nothing left
    assert len(out.read_text().splitlines()) == 12


def test_compact_keys_on_mode(tmp_path):
    f = tmp_path / "p.jsonl"
    rows = [{"pr_number": 1, "qset": "v3", "decision": {"error": None}},
            {"pr_number": 1, "qset": "v3", "mode": "single", "decision": {"error": "x"}},
            {"pr_number": 1, "qset": "v3", "mode": "single", "decision": {"error": None}},
            {"pr_number": 1, "qset": "v3", "mode": "joint", "decision": {"error": "late"}}]
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    cmd_compact(types.SimpleNamespace(files=[str(f)]))
    out = [json.loads(l) for l in f.open()]
    assert sorted((r.get("mode", "joint"), r["decision"]["error"]) for r in out) == [("joint", None), ("single", None)]


def test_loadtest_accepts_300_and_records_concurrency(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RESULTS", tmp_path)
    ds = _ds(tmp_path, n=30)
    cli.main(["loadtest", "--model", "rules", "--multiplier", "300", "--limit", "30", "--run", "lt", "--dataset", str(ds), "--qset", "v3", "--concurrency", "512", "--no-retry"])
    s = json.loads((tmp_path / "lt" / "loadtest_rules_x300.json").read_text())["summary"]
    assert s["multiplier"] == 300 and s["concurrency"] == 512 and s["http_max_connections"] == 512 and s["error_rate"] == 0
    assert abs(s["rate_per_s"] - 300 * 30000 / 86400) < 1e-9


# ----------------------------------------------------------------- report on synthetic rows
def _row(pid, split, probs, qset="v3", mode=None, lat=300.0, err=None):
    r = {"pr_number": pid, "split": split, "labels": {}, "qset": qset,
         "decision": {"probs": probs, "latency_ms": lat, "cost_usd": 2e-5, "model_version": "m", "error": err, "raw": {}}}
    if mode:
        r["mode"] = mode
    return r


def _write(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def synth(tmp_path, monkeypatch):
    rng = np.random.default_rng(6)
    ds, hosted, lval, ltest, pairs, lpair, hpair = [], [], [], [], [], [], []
    for i in range(1000):
        split = "val" if i < 500 else "test"
        y = rng.random() < 0.15
        ds.append({"pr_number": i, "split": split, "labels": {"iterated": bool(y), "rejected": bool(rng.random() < 0.3), "bad_outcome": False}})
        base = {q: float(np.clip(0.3 + 0.35 * y + rng.normal(0, 0.15), 0.01, 0.99)) for q in DECISIONS_V3}
        local = {q: float(np.clip(v + rng.normal(0, 0.005), 0, 1)) for q, v in base.items()}
        single = {q: float(np.clip(0.25 + 0.5 * y + rng.normal(0, 0.12), 0.01, 0.99)) for q in DECISIONS_V3}
        if split == "test":
            hosted.append(_row(i, split, base))
            ltest += [_row(i, split, local, mode="joint"), _row(i, split, single, mode="single", lat=2500.0)]
        else:
            lval.append(_row(i, split, local, mode="joint"))
    hosted.append(_row(9999, "test", {"same_change": 0.9}, qset="pair"))  # other qsets ignored
    for j in range(120):
        pid, pos = 10_000 + j, j % 4 == 0
        kind = "gold_not_retrieved" if pos else ("random_negative" if j % 2 else "retrieved")
        pairs.append({"pr_number": pid, "split": "test", "kind": kind, "labels": {"positive": pos, "a_label": "superseded_by" if j % 3 == 0 else "not_marked"}})
        p = float(np.clip(0.2 + 0.5 * pos + rng.normal(0, 0.15), 0, 1))
        lpair.append(_row(pid, "test", {"same_change": p}, qset="pair", mode="joint"))
        hpair.append(_row(pid, "test", {"same_change": float(np.clip(p + rng.normal(0, 0.01), 0, 1))}, qset="pair"))
    rd, r5, r4 = tmp_path / "run6", tmp_path / "run5", tmp_path / "run4"
    _write(tmp_path / "ds.jsonl", ds)
    _write(r5 / "predictions_pplx-flash_test_all.jsonl", hosted)
    _write(r4 / "predictions_pplx-flash_test_all.jsonl", hpair)
    _write(r4 / "pairs.jsonl", pairs)
    _write(rd / "predictions_pplx-flash-local_val_all.jsonl", lval)
    _write(rd / "predictions_pplx-flash-local_test_all.jsonl", ltest + lpair)
    for x, p99, err in ((1, 300, 0), (10, 310, 0), (30, 350, 0), (100, 480, 0), (300, 900, 0.02)):
        (rd / f"loadtest_pplx-flash-local_x{x}.json").write_text(json.dumps({"summary": {
            "model": "pplx-flash-local", "multiplier": float(x), "rate_per_s": x * 30000 / 86400, "n": 600, "p50_ms": p99 / 2, "p99_ms": p99,
            "p99_schedule_lag_s": 0.01, "error_rate": err, "concurrency": 512, "retry_429": 0, "qset": "v3"}, "samples": []}))
    (rd / "serve_meta.json").write_text(json.dumps({"served_as": "pplx-decider-flash-v1.2@abc", "gpu_name": "NVIDIA L4", "vm_type": "g6.xlarge",
                                                    "usd_per_hour": 0.8048, "versions": {"torch": "2.14"}}))
    monkeypatch.setattr(run6, "DATASET", tmp_path / "ds.jsonl")
    monkeypatch.setattr(run6, "RUN5_DIR", r5)
    monkeypatch.setattr(run6, "RUN4_DIR", r4)
    monkeypatch.setattr(run6, "PAIRS", r4 / "pairs.jsonl")
    return rd


def test_report_synthetic(synth):
    rep = run6.run(synth)
    H = rep["hypotheses"]
    h1 = rep["r6a_h1"]
    assert h1["n_prs_joined"] == 500 and h1["n_answers"] == 5000 and h1["mean_abs_dp"] < 0.01 and h1["agreement"] > 0.95 and H["R6A-H1"]["pass"]
    assert set(h1["per_question"]) == set(DECISIONS_V3)
    h3 = rep["r6a_h3"]
    assert h3["val_run"] and h3["basis"].startswith("calibrated") and h3["roc_auc"] > 0.7 and h3["hosted_ref"] == run6.HOSTED_NHR_AUC
    assert H["R6A-H3"]["pass"] in (True, False)
    pq = h3["pair"]
    assert pq["status"] == "ok" and pq["n"] == len(run6._pair_keys({r["pr_number"]: r for r in run6._rows(run6.PAIRS)})) and pq["roc_auc"] > 0.8
    assert pq["hosted_same_keys"]["mean_abs_dp"] < 0.02
    h4 = rep["r6a_h4"]
    assert h4["status"] == "ok" and h4["n_test_both"] == 500 and h4["basis"].startswith("raw") and h4["default"] in ("single", "joint")
    assert h4["single"]["latency"]["p99_ms"] == 2500.0 and h4["joint"]["latency"]["p99_ms"] == 300.0
    h2 = rep["r6a_h2"]
    assert h2["pass"] is True and H["R6A-H2"]["pass"] is True  # 480 <= 300 + 200, 0 errors
    assert h2["saturation_multiplier"] == 300 and h2["first_error_multiplier"] == 300
    co = h2["cost"]
    assert co["max_zero_error_multiplier"] == 100
    assert co["usd_per_1k_prs"] == pytest.approx(0.8048 / (100 * 30000 / 86400 * 3600 / 1000))
    assert (synth / "metrics.json").exists() and "R6A-H4" in (synth / "tables.md").read_text()
    assert rep["models"]["pplx-flash-local"]["compute_usd_per_1k"] == co["usd_per_1k_prs"]


def test_report_without_val_or_pairs_or_single(synth):
    (synth / "predictions_pplx-flash-local_val_all.jsonl").unlink()
    rows = [json.loads(l) for l in (synth / "predictions_pplx-flash-local_test_all.jsonl").open()]
    _write(synth / "predictions_pplx-flash-local_test_all.jsonl", [r for r in rows if r["qset"] == "v3" and r.get("mode") == "joint"])
    for x in (100, 300):
        (synth / f"loadtest_pplx-flash-local_x{x}.json").unlink()
    rep = run6.run(synth)
    assert rep["r6a_h3"]["basis"] == "raw (val not run)" and rep["r6a_h3"]["roc_auc"] is not None
    assert rep["r6a_h3"]["pair"]["status"] == "not run" and rep["hypotheses"]["R6A-H3-pair"]["pass"] is None
    assert rep["r6a_h4"]["status"].startswith("not run") and rep["hypotheses"]["R6A-H4"]["pass"] is None
    assert rep["hypotheses"]["R6A-H2"]["pass"] is None and rep["r6a_h2"]["saturation_multiplier"] is None


def test_h1_failure_marks_different_model(synth):
    p = synth / "predictions_pplx-flash-local_test_all.jsonl"
    rows = [json.loads(l) for l in p.open()]
    for r in rows:
        if r["qset"] == "v3" and r.get("mode") == "joint":
            r["decision"]["probs"] = {q: 1 - v for q, v in r["decision"]["probs"].items()}
    _write(p, rows)
    rep = run6.run(synth)
    assert rep["hypotheses"]["R6A-H1"]["pass"] is False
    assert rep["hypotheses"]["R6A-H3"]["pass"] is None and "different model" in rep["hypotheses"]["R6A-H3"]["status"]


def test_leaderboard_self_hosted_row(synth, monkeypatch):
    run6.run(synth)
    monkeypatch.setattr(leaderboard, "RESULTS", synth.parent)
    monkeypatch.setattr(leaderboard, "RUNS", ("run6",))
    board = leaderboard.collect()
    b = board["pplx-flash-local"]
    assert b["scale_pass"] is True and b["speed_p99_ms"] == 480 and b["speed_source"] == "loadtest x100"
    assert b["cost_usd_per_1k"] == pytest.approx(0.8048 / (100 * 30000 / 86400 * 3.6))
    assert b["runs"]["run6"]["p99_ms"] == 300.0  # in-run p99 from joint rows only (single rows are 2500 ms)
    md = leaderboard.render(leaderboard.rank(board), registry.load_registry())
    assert "decision model (self-hosted)" in md


def test_pair_subset(tmp_path):
    pairs = [{"pr_number": 1, "split": "test", "kind": "retrieved", "labels": {"positive": False, "a_label": "not_marked"}},
             {"pr_number": 2, "split": "test", "kind": "retrieved", "labels": {"positive": True, "a_label": "superseded_by"}},
             {"pr_number": 3, "split": "test", "kind": "random_negative", "labels": {"positive": False, "a_label": "superseded_by"}},
             {"pr_number": 4, "split": "val", "kind": "gold_not_retrieved", "labels": {"positive": True, "a_label": "superseded_by"}}]
    _write(tmp_path / "pairs.jsonl", pairs)
    run6.cmd_pair_subset(types.SimpleNamespace(pairs=str(tmp_path / "pairs.jsonl"), split="test", out=str(tmp_path / "o.jsonl")))
    assert [json.loads(l)["pr_number"] for l in (tmp_path / "o.jsonl").open()] == [2, 3]
    run6.cmd_pair_subset(types.SimpleNamespace(pairs=str(tmp_path / "missing.jsonl"), split="test", out=str(tmp_path / "x.jsonl")))
    assert not (tmp_path / "x.jsonl").exists()
