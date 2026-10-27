"""Run 9 report builder and step bookkeeping on synthetic inputs: degradation to "not run yet", the rows-served check,
R9-H3 equivalence + throughput, the R9-H2 configuration rule and ×10 bar, R9-H4 via run9_pairs, R9-H5 token ratio.
No model or API is called."""
import json
from pathlib import Path

import numpy as np
import pytest

from mergeone.eval import run9_report as R9
from mergeone.eval import run9_step as S
from mergeone.models.base import DECISIONS_V3


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _j(p: Path, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d))


def _v3(pid, probs, tokens=2300, mode="joint", ts="2026-11-02T10:00:00+00:00", model="pplx-decider-flash-v1.2@6d771ad7"):
    return {"pr_number": pid, "split": "test", "labels": {}, "qset": "v3", "mode": mode, "ts": ts,
            "decision": {"probs": probs, "input_tokens": tokens, "latency_ms": 100.0, "model_version": model, "error": None}}


def _meta(fp8=False, buckets=R9.DEFAULT_BUCKETS):
    return {"revision": "6d771ad7b5ecff5e93b8fc7b90d4c5ccc341954d", "gpu_name": "NVIDIA L4", "batch_tokens": 131072, "batch_max": 32, "batch_ms": 20,
            "dtype": "fp8 (…)" if fp8 else "bf16 (vendor default; no quantisation)", "started_at": "2026-11-02T09:00:00+00:00",
            "fast_options_effective": {"compile": "layer", "cuda_graphs": True, "buckets": buckets, "token_budget": 32768, "prune_last": True,
                                       "fp8": fp8, "prefetch": True}}


def _lt(p: Path, mult, p99, errors=0, n=600, fp8=False):
    _j(p, {"summary": {"multiplier": mult, "rate_per_s": 0.3472 * mult, "n": n, "p50_ms": p99 / 2, "p99_ms": p99, "n_errors": errors,
                       "error_rate": errors / n, "p99_schedule_lag_s": 0.01, "concurrency": 512, "retry_429": 0, "errors_by_kind": {},
                       "server_meta": {"fast_options_effective": {"fp8": fp8}}}, "samples": []})


def test_missing_inputs_say_not_run(tmp_path):
    rep = R9.run(tmp_path)
    assert rep["hypotheses"]["R9-H1"]["status"].startswith("deferred")
    assert all(v["pass"] is None for v in rep["hypotheses"].values())
    assert rep["h2"]["configuration"] is None and rep["inputs"] == []
    assert all(e["status"] == R9.NOT_RUN for e in rep["rows"]["arms"].values())
    md = (tmp_path / "results/run9/tables.md").read_text()
    assert "not run yet" in md and "## Inputs" in md and "R9-H1 | deferred" in md
    assert json.loads((tmp_path / "results/run9/metrics.json").read_text())["hypotheses"]["R9-H3"]["pass"] is None


def _equiv_files(tmp_path, n=100, fp8_noise=0.005, seed=0):
    rng = np.random.default_rng(seed)
    base = rng.uniform(0, 1, (n, len(DECISIONS_V3)))
    fp8 = np.clip(base + rng.normal(0, fp8_noise, base.shape), 0, 1)
    _w(tmp_path / R9.preds_path("bf16", "equiv"), [_v3(i, dict(zip(DECISIONS_V3, map(float, base[i])))) for i in range(n)])
    _w(tmp_path / R9.preds_path("fp8", "equiv"), [_v3(i, dict(zip(DECISIONS_V3, map(float, fp8[i])))) for i in range(n)])
    return base, fp8


def test_h3_pass_and_h2_picks_fp8(tmp_path):
    base, fp8 = _equiv_files(tmp_path)
    _j(tmp_path / "results/run9/bf16/serve_meta.json", _meta())
    _j(tmp_path / "results/run9/fp8/serve_meta.json", _meta(fp8=True))
    _j(tmp_path / "results/run9/fp8/equiv/server_before.json", {"meta": {"fast_options_effective": _meta(fp8=True)["fast_options_effective"]}})
    _j(tmp_path / "results/run9/bf16/equiv/throughput.json", {"items_per_s": 2.0, "n_items": 100, "wall_s": 50.0})
    _j(tmp_path / "results/run9/fp8/equiv/throughput.json", {"items_per_s": 2.7, "n_items": 100, "wall_s": 37.0})
    for x, p99 in ((1, 900), (10, 1050), (30, 2500)):
        _lt(tmp_path / f"results/run9/fp8/load/loadtest_pplx-flash-local_x{x}.json", x, p99, fp8=True)
        _lt(tmp_path / f"results/run9/bf16/load/loadtest_pplx-flash-local_x{x}.json", x, p99 * 1.5)
    rep = R9.build(tmp_path)
    e = rep["h3"]["equivalence"]
    a = (base >= 0.5) == (fp8 >= 0.5)
    assert e["n_answers"] == base.size and np.isclose(e["agreement"], a.mean()) and np.isclose(e["mean_abs_dp"], np.abs(fp8 - base).mean())
    h3 = rep["hypotheses"]["R9-H3"]
    assert h3["pass"] is True and np.isclose(h3["speed_ratio"], 1.35)
    assert rep["rows"]["arms"]["fp8"]["matches_expected"] is True and rep["rows"]["arms"]["bf16"]["matches_expected"] is True
    assert rep["rows"]["arms"]["fp8"]["steps_same_options"] == {"equiv": True} and rep["rows"]["one_revision"]
    h2 = rep["hypotheses"]["R9-H2"]
    assert rep["h2"]["configuration"] == "fp8" and h2["pass"] is True and h2["saturation"] == "×30"
    assert rep["h2"]["curves"]["bf16"]["saturation"] == "×30"
    md = R9.tables(rep)
    assert "Configuration: **fp8**" in md and "BF16 control (same day)" in md


def test_h3_speed_fail_then_bucket_row_then_control(tmp_path):
    _equiv_files(tmp_path)
    _j(tmp_path / "results/run9/bf16/equiv/throughput.json", {"items_per_s": 2.0})
    _j(tmp_path / "results/run9/fp8/equiv/throughput.json", {"items_per_s": 2.4})
    rep = R9.build(tmp_path)
    assert rep["hypotheses"]["R9-H3"]["pass"] is False and rep["hypotheses"]["R9-H3"]["equivalence_pass"] is True
    assert rep["h2"]["configuration"] is None and "bucket-row equivalence" in rep["h2"]["configuration_rule"]
    # bucket row far from the control → the control carries H2
    rng = np.random.default_rng(3)
    _w(tmp_path / R9.preds_path("buckets", "equiv"), [_v3(i, dict(zip(DECISIONS_V3, map(float, rng.uniform(0, 1, 10))))) for i in range(100)])
    _lt(tmp_path / "results/run9/bf16/load/loadtest_pplx-flash-local_x1.json", 1, 900)
    _lt(tmp_path / "results/run9/bf16/load/loadtest_pplx-flash-local_x10.json", 10, 1050, errors=3)
    rep = R9.build(tmp_path)
    assert rep["h2"]["bucket_equivalence"]["pass"] is False and rep["h2"]["configuration"] == "bf16"
    h2 = rep["hypotheses"]["R9-H2"]
    assert h2["pass"] is False and h2["x10_errors"] == 3 and h2["saturation"] is None  # ×30 not run


def test_fp8_unavailable(tmp_path):
    _equiv_files(tmp_path)
    (tmp_path / R9.preds_path("fp8", "equiv")).unlink()
    _j(tmp_path / "results/run9/fp8/unavailable.json", {"reason": "torch._scaled_mm rowwise scaling not supported on sm_89"})
    _j(tmp_path / "results/run9/buckets/unavailable.json", {"reason": "server did not start"})
    rep = R9.build(tmp_path)
    assert rep["hypotheses"]["R9-H3"]["unavailable"] and R9._verdict("R9-H3", rep["hypotheses"]["R9-H3"]).startswith("not run")
    assert rep["h2"]["configuration"] == "bf16" and "unavailable" in rep["h2"]["configuration_rule"]


def test_h5_token_ratio_and_equivalence(tmp_path):
    rng = np.random.default_rng(5)
    n = 50
    base = rng.uniform(0, 1, (n, 10))
    _w(tmp_path / R9.preds_path("bf16", "equiv"), [_v3(i, dict(zip(DECISIONS_V3, map(float, base[i]))), tokens=2000) for i in range(n)])
    _w(tmp_path / "results/run9/h5/predictions_pplx-flash_test_all.jsonl",
       [_v3(i, dict(zip(DECISIONS_V3, map(float, base[i]))), tokens=2000 * (7 if i % 2 else 9), model="pplx-flash-1") for i in range(n)])
    _w(tmp_path / "results/run9/h5/predictions_pplx-flash-local_test_all.jsonl",
       [_v3(i, dict(zip(DECISIONS_V3, map(float, np.clip(base[i] + 0.001, 0, 1)))), tokens=21000, mode="single") for i in range(n)])
    rep = R9.build(tmp_path)
    h5 = rep["h5"]
    assert h5["same_utc_day"] is True and h5["tokens"]["n"] == n and h5["tokens"]["median"] == pytest.approx(8.0)
    assert h5["equivalence"]["pass"] is True and rep["hypotheses"]["R9-H5"]["pass"] is True
    # a different UTC day is reported beside the verdict
    _w(tmp_path / "results/run9/h5/predictions_pplx-flash_test_all.jsonl",
       [_v3(i, dict(zip(DECISIONS_V3, map(float, base[i]))), tokens=2000 * 3, ts="2026-11-03T01:00:00+00:00") for i in range(n)])
    rep = R9.build(tmp_path)
    assert rep["h5"]["same_utc_day"] is False and rep["hypotheses"]["R9-H5"]["pass"] is False
    assert "not the same UTC day" in rep["hypotheses"]["R9-H5"]["status"]


def test_h4_through_report(tmp_path):
    rng = np.random.default_rng(2)
    n = 40
    y = (np.arange(n) % 4 == 0)
    pc = np.clip(y * 0.6 + rng.uniform(0, 0.4, n), 0, 1)
    row = lambda pid, p, lab, q="pair", m="joint": {"pr_number": pid, "split": "test", "labels": lab, "qset": q, "mode": m, "group_size": 10,
                                                   "decision": {"probs": {"same_change": float(p)}, "input_tokens": 3000 if q == "pair" else 1100, "error": None}}
    _w(tmp_path / "results/run6/pairs_eval_test.jsonl", [{"pr_number": i} for i in range(n)])
    for arm, q, m in (("bf16", "pair", "joint"), ("shared", "pair_shared", "shared")):
        _w(tmp_path / f"results/run9/{arm}/pairs_a/predictions_pplx-flash-local_test_all.jsonl", [row(i, pc[i], {"positive": bool(y[i])}, q, m) for i in range(n)])
        _w(tmp_path / f"results/run9/{arm}/pairs_b/predictions_pplx-flash-local_test_all.jsonl",
           [row(100 + i, pc[i], {"adjudicated": "duplicate" if y[i] else "pointer"}, q, m) for i in range(n)])
    _j(tmp_path / "results/run9/bf16/pairs_b/throughput.json", {"items_per_s": 4.0})
    _j(tmp_path / "results/run9/shared/pairs_b/throughput.json", {"items_per_s": 11.0})
    rep = R9.build(tmp_path)
    h = rep["hypotheses"]["R9-H4"]
    assert h["pass"] is True and h["speed_ratio"] == pytest.approx(2.75) and h["auc_a_delta"] == pytest.approx(0.0)
    assert rep["h4"]["tokens"]["shared_b"]["median_per_candidate"] == 1100 and rep["h4"]["agreement"]["b"]["agreement"] == 1.0
    assert "ratio **2.75×**" in R9.tables(rep)


# ----------------------------------------------------------------- run9_step
def test_throughput_from_predict_meta(tmp_path):
    d = tmp_path / "results/run9/bf16/equiv"
    _j(d / "meta_pplx-flash-local_test_all_v3.json", {"n": 705, "errors": 5, "wall_s": 350.0, "concurrency": 32, "mode": "joint"})
    before = {"meta": {"fast_options_effective": {"fp8": False}}, "healthz": {"stats": {"decide_s": 10.0}}}
    after = {"meta": {"fast_options_effective": {"fp8": False}, "dtype": "bf16"}, "healthz": {"stats": {"decide_s": 300.0}}}
    t = S.throughput(d, "v3", before=before, after=after, outer_wall_s=360.0)
    assert t["n_items"] == 700 and t["items_per_s"] == pytest.approx(2.0) and t["decide_s"] == pytest.approx(290.0)
    assert t["wall_source"] == "predict meta" and t["arm"] == "bf16" and t["step"] == "equiv" and t["server"]["fast_options_effective"] == {"fp8": False}
    d2 = tmp_path / "results/run9/shared/pairs_b"
    _j(d2 / "meta_pplx-flash-local_test_all_pair_shared.json", {"n": 1000, "errors": 0, "n_requests": 100, "wall_s": 50.0, "concurrency": 32})
    t2 = S.throughput(d2, "pair_shared")
    assert t2["items_per_s"] == 20.0 and t2["requests_per_s"] == 2.0
    d3 = tmp_path / "results/run9/bf16/pairs_b"
    _j(d3 / "meta_pplx-flash-local_test_all.json", {"n": 1000, "errors": 0, "concurrency": 32})
    assert S.throughput(d3, "pair", outer_wall_s=100.0)["wall_source"] == "outer"
    with pytest.raises(SystemExit):
        S.throughput(tmp_path / "nowhere", "v3")


def test_snapshot_records_errors_when_server_down():
    s = S.snapshot(port=9)
    assert "error" in s["meta"] and "error" in s["healthz"]
