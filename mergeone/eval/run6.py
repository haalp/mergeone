"""Run 6 part A: the self-hosted pplx-decider-flash row (PROTOCOL.md "Run 6 addendum, part A").

  python -m mergeone.eval.run6 pair-subset      results/run4/pairs.jsonl → results/run6/pairs_eval_test.jsonl (the run-4 R4-H2 test pairs)
  python -m mergeone.cli report results/run6 --run6

Inputs (results/run6/):
  predictions_pplx-flash-local_{val,test}_all.jsonl   v3 rows (mode joint / single) and pair rows, filtered on `qset` and `mode`
  loadtest_pplx-flash-local_x{1,10,30,100,300}.json    open-loop load tests (v3, joint)
  serve_meta.json                                       revision, GPU, VM type, usd_per_hour (written by serve/systemone_server.py)
Hosted references: results/run5/predictions_pplx-flash_test_all.jsonl (v3), results/run5/metrics.json (0.758),
results/run4/predictions_pplx-flash_test_all.jsonl (pair), results/run4/metrics.json (0.842).

  R6A-H1  equivalence: self-hosted joint v3 test answers vs hosted run-5 v3 test answers, joined on pr_number, all 10 questions:
          argmax agreement at 0.5 >= 0.95 and mean |Δp| <= 0.02.
  R6A-H2  scale: R3-H5 bar at 100× (p99 <= 1× p99 + 200 ms, 0 errors) + saturation (first multiplier with p99 > 2 × 1× p99).
  R6A-H3  quality: run-5 R5-H5 computation (run5._h5_model: isotonic on self-hosted val vs `iterated`, applied to test) →
          needs_human_review ROC-AUC within 0.02 of the hosted row; same_change pair AUC on the run-4 test pairs, reported.
  R6A-H4  joint vs single question per request: single AUC >= joint AUC + 0.02 → single becomes this model's default.
  cost    $/1k PRs = usd_per_hour / (highest 0-error rate × 3600 / 1000).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from ..models.base import DECISIONS_V3, PRIMARY_V3
from . import run5
from .metrics import roc_auc, bootstrap_ci, pr_auc

LOCAL, HOSTED = "pplx-flash-local", "pplx-flash"
DATASET = Path("data/run3/dataset.jsonl")  # relative to cwd (repo root), as runs 3–5
RUN_DIR = Path("results/run6")
RUN5_DIR = Path("results/run5")
RUN4_DIR = Path("results/run4")
PAIRS = RUN4_DIR / "pairs.jsonl"
LT_MULTS = (1, 10, 30, 100, 300)
HOSTED_NHR_AUC = 0.758   # PROTOCOL R6A-H3 (results/run5/metrics.json is preferred when present)
HOSTED_PAIR_AUC = 0.842  # PROTOCOL R6A-H3 (results/run4/metrics.json is preferred when present)
H1_AGREE, H1_DP = 0.95, 0.02
H3_TOL, H4_GAIN = 0.02, 0.02
H2_SLACK_MS = 200.0
SAT_FACTOR = 2.0

NOTES = {
    "h1": "R6A-H1: rows joined on pr_number (self-hosted joint v3 test ∩ hosted run-5 v3 test, non-error); an answer agrees when both "
          "probabilities are on the same side of 0.5 (>= 0.5 = yes). Δp = self-hosted − hosted. Not used to tune anything (R6A.4).",
    "h3": "R6A-H3: run5._h5_model unchanged — isotonic (out_of_bounds=clip) fitted on the self-hosted val answers vs `iterated`, applied "
          "to the self-hosted test answers. If no self-hosted val rows exist, the raw answer's AUC is reported and marked 'val not run'. "
          "Pair AUC: run-4 R4-H2 test pairs (kind != retrieved, or retrieved with a_label = superseded_by), duplicate_of = P(same) + 0.5 P(subset); "
          "needs results/run4/pairs.jsonl for the pair kinds. If R6A-H1 fails the row is 'a different model': H3 is reported, not compared.",
    "h4": "R6A-H4: single = one request per question (predict --single-question; 10 sequential calls per PR, latency = wall-clock of the PR). "
          "Compared on the PRs answered in both modes. Calibrated AUCs when both modes have their own val rows (each refit on its own val "
          "answers), otherwise raw AUCs for both (isotonic is monotone, so raw ranks ≈ calibrated ranks); which one is stated as `basis`.",
    "h2": "R6A-H2: open-loop replay (cli loadtest, v3, joint, 600 test PRs, 429 retry off) from the gate client on the same VM (loopback). "
          "Bar = R3-H5: ×100 p99 <= ×1 p99 + 200 ms and 0 errors. Saturation = first multiplier in 1,10,30,100,300 whose p99 exceeds "
          "2 × the ×1 p99. Load-test p99 is per decision (one joint request).",
    "cost": "Cost: $/1k PRs = usd_per_hour (serve_meta.json) / (rate_per_s × 3600 / 1000) at the highest multiplier with 0 errors "
            "(PROTOCOL R6A.3). Hosted: run-5 in-run mean list-price cost per PR × 1000.",
}


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).open()] if Path(path).exists() else []


def _mode(r: dict) -> str:
    return r.get("mode", "joint")


def load_preds(path: Path, qset: str, mode: str | None = "joint") -> dict[int, dict]:
    """{pr_number: decision} for non-error rows of one qset (and mode; None = any), last row wins."""
    out = {}
    for r in _rows(path):
        if r.get("qset") != qset or r["decision"].get("error") is not None:
            continue
        if mode is not None and _mode(r) != mode:
            continue
        out[r["pr_number"]] = r["decision"]
    return out


def _ok(x) -> bool:
    return x is not None and x == x


# ----------------------------------------------------------------- H1
def equivalence(local: dict[int, dict], hosted: dict[int, dict]) -> dict:
    keys = sorted(set(local) & set(hosted))
    per_q, all_agree, all_dp = {}, [], []
    for q in DECISIONS_V3:
        a = np.array([local[k]["probs"].get(q, np.nan) for k in keys], dtype=float)
        b = np.array([hosted[k]["probs"].get(q, np.nan) for k in keys], dtype=float)
        m = ~np.isnan(a) & ~np.isnan(b)
        a, b = a[m], b[m]
        agree = (a >= 0.5) == (b >= 0.5)
        dp = a - b
        all_agree += agree.tolist(); all_dp += np.abs(dp).tolist()
        per_q[q] = {"n": int(m.sum()), "agreement": float(agree.mean()) if len(a) else None, "mean_abs_dp": float(np.abs(dp).mean()) if len(a) else None,
                    "max_abs_dp": float(np.abs(dp).max()) if len(a) else None, "mean_dp": float(dp.mean()) if len(a) else None,
                    "pearson": float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 and a.std() > 0 and b.std() > 0 else None,
                    "yes_rate_local": float((a >= 0.5).mean()) if len(a) else None, "yes_rate_hosted": float((b >= 0.5).mean()) if len(b) else None}
    agree = float(np.mean(all_agree)) if all_agree else None
    mdp = float(np.mean(all_dp)) if all_dp else None
    ok = agree is not None and mdp is not None
    return {"n_prs_joined": len(keys), "n_local": len(local), "n_hosted": len(hosted), "n_answers": len(all_agree),
            "agreement": agree, "mean_abs_dp": mdp, "p99_abs_dp": float(np.percentile(all_dp, 99)) if all_dp else None,
            "per_question": per_q, "pass": (agree >= H1_AGREE and mdp <= H1_DP) if ok else None,
            "local_versions": sorted({d.get("model_version") for d in local.values()} - {None, ""}),
            "hosted_versions": sorted({d.get("model_version") for d in hosted.values()} - {None, ""})}


# ----------------------------------------------------------------- H3 / H4
def quality(val: dict[int, dict], test: dict[int, dict], ds: dict) -> dict:
    """run5._h5_model on the self-hosted answers; picks calibrated nhr AUC if val exists, else raw."""
    if not test:
        return {"status": "pending (no self-hosted v3 test rows)", "roc_auc": None}
    e = run5._h5_model(val, test, ds)
    cal = e.get("nhr_v3cal") or {}
    if val and cal.get("roc_auc") is not None:
        s, basis = cal, "calibrated (isotonic on self-hosted val vs iterated)"
    else:
        s, basis = e.get("nhr_raw") or {}, "raw (val not run)" if not val else "raw (isotonic not fitted)"
    return {"status": "ok", "basis": basis, "val_run": bool(val), "n_val": e["n_val"], "n_test": e["n_test"], "roc_auc": s.get("roc_auc"),
            "roc_auc_ci95": s.get("roc_auc_ci95"), "ece": s.get("ece"), "pr_auc": s.get("pr_auc"),
            "raw_roc_auc": (e.get("nhr_raw") or {}).get("roc_auc"), "nhr_v3cal": cal, "nhr_raw": e.get("nhr_raw"),
            "questions": e["questions"], "stack": e["stack"]}


def _pair_keys(pairs: dict[int, dict], split: str = "test") -> list[int]:
    """run4.run's R4-H2 key rule: non-retrieved pairs (gold + random negatives) and retrieved pairs whose A carries a superseded_by label."""
    return [k for k, p in pairs.items() if p["split"] == split and (p["kind"] != "retrieved" or p["labels"]["a_label"] == "superseded_by")]


def pair_quality(local: dict[int, dict], hosted: dict[int, dict], pairs: dict[int, dict], hosted_ref: float) -> dict:
    if not local:
        return {"status": "not run", "roc_auc": None, "hosted_ref": hosted_ref}
    if not pairs:
        return {"status": f"not evaluable: {PAIRS} not present (pair kinds needed for the R4-H2 key set)", "roc_auc": None,
                "n_pair_rows": len(local), "hosted_ref": hosted_ref}
    keys = [k for k in _pair_keys(pairs) if k in local and _ok(local[k]["probs"].get("same_change"))]
    y = np.array([int(pairs[k]["labels"]["positive"]) for k in keys])
    x = np.array([local[k]["probs"]["same_change"] for k in keys], dtype=float)
    out = {"status": "ok", "n": len(keys), "n_eval_pairs": len(_pair_keys(pairs)), "positives": int(y.sum()), "hosted_ref": hosted_ref}
    if not (0 < y.sum() < len(y)):
        out.update(status="insufficient (one class)", roc_auc=None)
        return out
    out.update(roc_auc=roc_auc(y, x), ci95=list(bootstrap_ci(y, x, roc_auc)), pr_auc=pr_auc(y, x))
    hk = [k for k in keys if k in hosted and _ok(hosted[k]["probs"].get("same_change"))]
    if hk:
        yh = np.array([int(pairs[k]["labels"]["positive"]) for k in hk])
        xh = np.array([hosted[k]["probs"]["same_change"] for k in hk], dtype=float)
        xl = np.array([local[k]["probs"]["same_change"] for k in hk], dtype=float)
        out["hosted_same_keys"] = {"n": len(hk), "roc_auc": roc_auc(yh, xh) if 0 < yh.sum() < len(yh) else None,
                                   "mean_abs_dp": float(np.abs(xl - xh).mean()), "agreement": float(((xl >= 0.5) == (xh >= 0.5)).mean())}
    out["pass"] = abs(out["roc_auc"] - hosted_ref) <= H3_TOL
    return out


def _lat(decisions) -> dict | None:
    lat = sorted(d["latency_ms"] for d in decisions if d.get("latency_ms") is not None)
    if not lat:
        return None
    return {"n": len(lat), "p50_ms": float(np.percentile(lat, 50)), "p99_ms": float(np.percentile(lat, 99)),
            "mean_requests_per_pr": float(np.mean([(d.get("raw") or {}).get("usage", {}).get("requests", 1) for d in decisions]))}


def joint_vs_single(jv: dict, jt: dict, sv: dict, st: dict, ds: dict) -> dict:
    if not st:
        return {"status": "not run (no mode=single test rows)", "pass": None}
    keys = set(jt) & set(st)
    jt2, st2 = {k: jt[k] for k in keys}, {k: st[k] for k in keys}
    both_val = bool(jv) and bool(sv)
    jq, sq = quality(jv if both_val else {}, jt2, ds), quality(sv if both_val else {}, st2, ds)
    ja, sa = jq["roc_auc"], sq["roc_auc"]
    gain = (sa - ja) if (ja is not None and sa is not None) else None
    return {"status": "ok", "n_test_both": len(keys), "basis": "calibrated (each mode on its own val)" if both_val else "raw (no single-question val rows)",
            "joint": {"roc_auc": ja, "ci95": jq.get("roc_auc_ci95"), "latency": _lat(jt2.values())},
            "single": {"roc_auc": sa, "ci95": sq.get("roc_auc_ci95"), "latency": _lat(st2.values()),
                       "questions_raw_auc_vs_iterated": {q: r["test_raw_vs_iterated"] for q, r in sq["questions"].items()}},
            "joint_questions_raw_auc_vs_iterated": {q: r["test_raw_vs_iterated"] for q, r in jq["questions"].items()},
            "gain": gain, "pass": None if gain is None else gain >= H4_GAIN,
            "default": None if gain is None else ("single" if gain >= H4_GAIN else "joint")}


# ----------------------------------------------------------------- H2 + cost
def scale(run_dir: Path, model: str, usd_per_hour: float | None) -> dict:
    lts = {}
    for x in LT_MULTS:
        p = run_dir / f"loadtest_{model}_x{x}.json"
        if p.exists():
            lts[f"x{x}"] = json.loads(p.read_text())["summary"]
    out = {"loadtests": lts}
    x1 = (lts.get("x1") or {}).get("p99_ms")
    s = lts.get("x100")
    if not s or x1 is None:
        out.update({"status": "pending" if not lts else "pending (need ×1 and ×100)", "pass": None})
    else:
        bad_retry = s.get("retry_429") not in (0, None)
        out["status"] = "ok" if not bad_retry else "invalid: adapter 429 retry was not disabled"
        out["pass"] = bool(not bad_retry and s["error_rate"] == 0 and s["p99_ms"] is not None and s["p99_ms"] <= x1 + H2_SLACK_MS)
        out["x1_p99_ms"], out["x100_p99_ms"], out["x100_error_rate"] = x1, s["p99_ms"], s["error_rate"]
    sat = None
    if x1 is not None:
        for x in LT_MULTS:
            t = lts.get(f"x{x}")
            if t and t.get("p99_ms") is not None and t["p99_ms"] > SAT_FACTOR * x1:
                sat = x
                break
    out["saturation_multiplier"] = sat
    out["saturation_note"] = (f"first p99 > {SAT_FACTOR:g} × ×1 p99 at ×{sat}" if sat else
                              (f"not reached up to ×{max(int(k[1:]) for k in lts)}" if lts and x1 is not None else "pending"))
    out["first_error_multiplier"] = next((x for x in LT_MULTS if (lts.get(f"x{x}") or {}).get("error_rate", 0) > 0), None)
    ok = [t for t in lts.values() if t.get("error_rate") == 0]
    best = max(ok, key=lambda t: t["rate_per_s"]) if ok else None
    out["cost"] = {"usd_per_hour": usd_per_hour, "max_zero_error_multiplier": best["multiplier"] if best else None,
                   "max_zero_error_rate_per_s": best["rate_per_s"] if best else None,
                   "prs_per_hour": best["rate_per_s"] * 3600 if best else None,
                   "usd_per_1k_prs": (usd_per_hour / (best["rate_per_s"] * 3600 / 1000)) if (best and usd_per_hour) else None,
                   "note": "lower bound on throughput: the open-loop rate offered, not the GPU's ceiling, unless the curve saturated"}
    return out


def _hosted_refs() -> dict:
    m5 = json.loads((RUN5_DIR / "metrics.json").read_text()) if (RUN5_DIR / "metrics.json").exists() else {}
    m4 = json.loads((RUN4_DIR / "metrics.json").read_text()) if (RUN4_DIR / "metrics.json").exists() else {}
    h5 = m5.get("models", {}).get(HOSTED, {})
    lt = {}
    for x in (1, 10, 100):
        p = RUN5_DIR / f"loadtest_{HOSTED}_x{x}.json"
        if p.exists():
            s = json.loads(p.read_text())["summary"]
            lt[f"x{x}"] = {k: s.get(k) for k in ("p99_ms", "error_rate", "n_429", "rate_per_s")}
    return {"nhr_auc": (h5.get("nhr_v3cal") or {}).get("roc_auc") or HOSTED_NHR_AUC,
            "pair_auc": (m4.get("models", {}).get(HOSTED, {}).get("pair_test") or {}).get("roc_auc") or HOSTED_PAIR_AUC,
            "usd_per_1k": (h5.get("latency_cost_test") or h5.get("latency_cost") or {}).get("cost_usd_per_1k"),
            "inrun_p99_ms": (h5.get("latency_cost_test") or {}).get("p99_ms"), "loadtests": lt}


# ----------------------------------------------------------------- report
def run(run_dir: Path = RUN_DIR) -> dict:
    run_dir = Path(run_dir)
    ds = {r["pr_number"]: r for r in _rows(DATASET)}
    lv, lt = run_dir / f"predictions_{LOCAL}_val_all.jsonl", run_dir / f"predictions_{LOCAL}_test_all.jsonl"
    jv, jt = load_preds(lv, "v3", "joint"), load_preds(lt, "v3", "joint")
    sv, st = load_preds(lv, "v3", "single"), load_preds(lt, "v3", "single")
    hosted_v3 = load_preds(RUN5_DIR / f"predictions_{HOSTED}_test_all.jsonl", "v3", "joint")
    local_pair = load_preds(lt, "pair", "joint")
    hosted_pair = load_preds(RUN4_DIR / f"predictions_{HOSTED}_test_all.jsonl", "pair", "joint")
    pairs = {r["pr_number"]: r for r in _rows(PAIRS)}
    meta = json.loads((run_dir / "serve_meta.json").read_text()) if (run_dir / "serve_meta.json").exists() else {}
    refs = _hosted_refs()

    h1 = equivalence(jt, hosted_v3) if jt else {"status": "pending (no self-hosted joint v3 test rows)", "pass": None}
    same_model = h1.get("pass") is not False
    h3 = quality(jv, jt, ds)
    h3["hosted_ref"] = refs["nhr_auc"]
    h3["pair"] = pair_quality(local_pair, hosted_pair, pairs, refs["pair_auc"])
    h4 = joint_vs_single(jv, jt, sv, st, ds)
    h2 = scale(run_dir, LOCAL, meta.get("usd_per_hour"))

    rep = {"serve_meta": meta, "hosted_refs": refs, "notes": NOTES,
           "counts": {"joint_val": len(jv), "joint_test": len(jt), "single_val": len(sv), "single_test": len(st), "pair_test": len(local_pair),
                      "hosted_v3_test": len(hosted_v3)},
           "r6a_h1": h1, "r6a_h2": h2, "r6a_h3": h3, "r6a_h4": h4, "hypotheses": {},
           # leaderboard (mergeone/eval/leaderboard.py) reads models[...] like run 5's
           "models": {LOCAL: {"nhr_v3cal": {"roc_auc": h3.get("roc_auc"), "ece": h3.get("ece"), "roc_auc_ci95": h3.get("roc_auc_ci95"), "basis": h3.get("basis")},
                              "compute_usd_per_1k": h2["cost"]["usd_per_1k_prs"], "x100_p99_ms": h2.get("x100_p99_ms"),
                              "kind": "decision model (self-hosted)", "same_model_as_hosted": h1.get("pass")}}}
    H = rep["hypotheses"]
    H["R6A-H1"] = {k: h1.get(k) for k in ("n_prs_joined", "n_answers", "agreement", "mean_abs_dp")} | {
        "pass": h1.get("pass"), "bar": f"argmax agreement >= {H1_AGREE} and mean abs Δp <= {H1_DP} (705 × 10 answers)"}
    a, ref = h3.get("roc_auc"), refs["nhr_auc"]
    H3 = {"roc_auc": a, "ci95": h3.get("roc_auc_ci95"), "basis": h3.get("basis"), "hosted": ref,
          "delta": (a - ref) if a is not None else None, "bar": f"abs(AUC − hosted {ref:.3f}) <= {H3_TOL}"}
    if a is not None and not same_model:
        H3["status"] = "R6A-H1 failed: reported as a different model, not compared to the hosted row"
    H3["pass"] = abs(a - ref) <= H3_TOL if (a is not None and same_model) else None
    H["R6A-H3"] = H3
    pq = h3["pair"]
    H["R6A-H3-pair"] = {"roc_auc": pq.get("roc_auc"), "n": pq.get("n"), "hosted": pq.get("hosted_ref"), "status": pq.get("status"),
                        "pass": (pq.get("pass") if same_model else None) if pq.get("roc_auc") is not None else None,
                        "bar": f"reported; abs(AUC − hosted {pq.get('hosted_ref'):.3f}) <= {H3_TOL} shown for reference"}
    H["R6A-H4"] = {"joint": (h4.get("joint") or {}).get("roc_auc"), "single": (h4.get("single") or {}).get("roc_auc"), "gain": h4.get("gain"),
                   "basis": h4.get("basis"), "default": h4.get("default"), "status": h4.get("status"), "pass": h4.get("pass"),
                   "bar": f"single >= joint + {H4_GAIN} → single-question default for this model; else joint"}
    H["R6A-H2"] = {"x1_p99_ms": h2.get("x1_p99_ms"), "x100_p99_ms": h2.get("x100_p99_ms"), "x100_error_rate": h2.get("x100_error_rate"),
                   "saturation": h2.get("saturation_note"), "status": h2.get("status"), "pass": h2.get("pass"),
                   "bar": f"×100: p99 <= ×1 p99 + {H2_SLACK_MS:.0f} ms and 0 errors"}
    H[f"R6A-H2[{LOCAL}]"] = H["R6A-H2"]  # leaderboard key
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metrics.json").write_text(json.dumps(rep, indent=2, default=float))
    md = _tables(rep)
    (run_dir / "tables.md").write_text(md)
    print(md)
    return rep


_f = run5._f


def _ci(c):
    return f"[{_f(c[0])}, {_f(c[1])}]" if c else ""


def _tables(rep: dict) -> str:
    m, c, refs = rep["serve_meta"], rep["counts"], rep["hosted_refs"]
    L = ["# Run 6 part A — self-hosted pplx-decider-flash (`pplx-flash-local`)", "",
         f"served as `{m.get('served_as', '—')}` · GPU {m.get('gpu_name', '—')} · VM {m.get('vm_type', '—')} ({m.get('region') or '—'}) · "
         f"${_f(m.get('usd_per_hour'))}/h · torch {(m.get('versions') or {}).get('torch', '—')} · transformers {(m.get('versions') or {}).get('transformers', '—')} · "
         f"pplx_decider {(m.get('versions') or {}).get('pplx_decider', '—')} · fast={m.get('fast', '—')} · batch ≤ {m.get('batch_max', '—')} / {m.get('batch_ms', '—')} ms", "",
         f"rows: joint val {c['joint_val']}, joint test {c['joint_test']}, single val {c['single_val']}, single test {c['single_test']}, pair test {c['pair_test']}; "
         f"hosted v3 test {c['hosted_v3_test']}", ""]
    h1 = rep["r6a_h1"]
    L += ["## R6A-H1: equivalence with the hosted pplx-flash (run-5 test, v3, joint)", ""]
    if "per_question" not in h1:
        L.append(f"**{h1.get('status')}**")
    else:
        L += [f"{h1['n_prs_joined']} PRs joined, {h1['n_answers']} answers · agreement **{_f(h1['agreement'])}** (bar ≥ {H1_AGREE}) · mean |Δp| **{_f(h1['mean_abs_dp'], 4)}** "
              f"(bar ≤ {H1_DP}) · p99 |Δp| {_f(h1['p99_abs_dp'], 4)} · versions: local {', '.join(h1['local_versions']) or '—'} / hosted {', '.join(h1['hosted_versions']) or '—'}", "",
              "| question | n | agreement | mean \\|Δp\\| | max \\|Δp\\| | mean Δp | Pearson | yes-rate local | yes-rate hosted |", "|---|---|---|---|---|---|---|---|---|"]
        for q, r in h1["per_question"].items():
            L.append(f"| {q} | {r['n']} | {_f(r['agreement'])} | {_f(r['mean_abs_dp'], 4)} | {_f(r['max_abs_dp'], 3)} | {_f(r['mean_dp'], 4)} | {_f(r['pearson'])} | "
                     f"{_f(r['yes_rate_local'])} | {_f(r['yes_rate_hosted'])} |")
    h3 = rep["r6a_h3"]
    L += ["", "## R6A-H3: quality (needs_human_review → iterated, test)", ""]
    if h3.get("status") != "ok":
        L.append(f"**{h3.get('status')}**")
    else:
        L += [f"ROC-AUC **{_f(h3['roc_auc'])}** {_ci(h3.get('roc_auc_ci95'))} ({h3['basis']}; raw {_f(h3['raw_roc_auc'])}) vs hosted {_f(h3['hosted_ref'])} · "
              f"ECE {_f(h3.get('ece'))} · n_val {h3['n_val']}, n_test {h3['n_test']}"]
    pq = h3["pair"]
    L += ["", f"same_change pair AUC (run-4 test pairs): {pq.get('status')}" + (
        f" — **{_f(pq['roc_auc'])}** {_ci(pq.get('ci95'))}, n {pq['n']} ({pq['positives']} positive) vs hosted {_f(pq['hosted_ref'])}"
        + (f"; hosted on the same pairs {_f(pq['hosted_same_keys']['roc_auc'])}, agreement {_f(pq['hosted_same_keys']['agreement'])}, mean |Δp| {_f(pq['hosted_same_keys']['mean_abs_dp'], 4)}"
           if pq.get("hosted_same_keys") else "") if pq.get("roc_auc") is not None else "")]
    h4 = rep["r6a_h4"]
    L += ["", "## R6A-H4: joint (10 questions in one request) vs single (one question per request)", ""]
    if h4.get("status") != "ok":
        L.append(f"**{h4.get('status')}**")
    else:
        L += [f"{h4['n_test_both']} test PRs in both modes · basis: {h4['basis']}", "",
              "| mode | nhr → iterated AUC | 95% CI | p50 ms / PR | p99 ms / PR | requests / PR |", "|---|---|---|---|---|---|"]
        for k in ("joint", "single"):
            e = h4[k]; lat = e.get("latency") or {}
            L.append(f"| {k} | {_f(e['roc_auc'])} | {_ci(e.get('ci95'))} | {_f(lat.get('p50_ms'), 0)} | {_f(lat.get('p99_ms'), 0)} | {_f(lat.get('mean_requests_per_pr'), 1)} |")
        L += ["", f"gain {_f(h4['gain'])} (bar ≥ {H4_GAIN}) → default for this model: **{h4['default']}**", "",
              "| question | joint raw AUC vs iterated | single raw AUC vs iterated |", "|---|---|---|"]
        for q in DECISIONS_V3:
            L.append(f"| {q} | {_f(h4['joint_questions_raw_auc_vs_iterated'].get(q))} | {_f(h4['single']['questions_raw_auc_vs_iterated'].get(q))} |")
    h2 = rep["r6a_h2"]
    L += ["", "## R6A-H2: load (open loop, v3 joint, loopback) and saturation curve", "",
          "| multiplier | rate /s | n | p50 ms | p99 ms | p99 schedule lag s | errors | concurrency |", "|---|---|---|---|---|---|---|---|"]
    for k, s in h2["loadtests"].items():
        L.append(f"| {k} | {_f(s.get('rate_per_s'), 2)} | {s.get('n')} | {_f(s.get('p50_ms'), 0)} | {_f(s.get('p99_ms'), 0)} | {_f(s.get('p99_schedule_lag_s'), 3)} | "
                 f"{_f(s.get('error_rate'))} | {s.get('concurrency')} |")
    hl = refs.get("loadtests") or {}
    L += ["", f"Saturation: {h2['saturation_note']}; first errors at {('×' + str(h2['first_error_multiplier'])) if h2['first_error_multiplier'] else 'none'}. "
          f"Hosted pplx-flash (run 5): " + ", ".join(f"{k} p99 {_f(v.get('p99_ms'), 0)} ms / errors {_f(v.get('error_rate'))}" for k, v in hl.items()), ""]
    co = h2["cost"]
    L += ["## Cost", "", "| row | $/1k PRs | basis |", "|---|---|---|",
          f"| self-hosted (compute) | {_f(co['usd_per_1k_prs'], 4)} | ${_f(co['usd_per_hour'])}/h ÷ {_f(co['prs_per_hour'], 0)} PRs/h (×{co['max_zero_error_multiplier'] or '—'}, 0 errors) |",
          f"| hosted pplx-flash (list price) | {_f(refs.get('usd_per_1k'), 4)} | run-5 in-run tokens × $0.01/M |", ""]
    L += ["## Pre-registered criteria", "", "| hypothesis | result | bar | pass |", "|---|---|---|---|"]
    for h, r in rep["hypotheses"].items():
        if h.startswith("R6A-H2["):
            continue
        shown = {k: (round(v, 4) if isinstance(v, float) else ([round(x, 3) for x in v] if isinstance(v, list) and v and isinstance(v[0], float) else v))
                 for k, v in r.items() if k not in ("pass", "bar")}
        mark = "pending" if r["pass"] is None else ("✅" if r["pass"] else "❌")
        L.append(f"| {h} | {json.dumps(shown, ensure_ascii=False)} | {r['bar']} | {mark} |")
    L += ["", "## Notes", ""] + [f"- {v}" for v in rep["notes"].values()]
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------- helpers for the runbook
def cmd_pair_subset(a) -> None:
    pairs = {r["pr_number"]: r for r in _rows(Path(a.pairs))}
    if not pairs:
        print(f"{a.pairs} not present; pair task skipped")
        return
    keys = _pair_keys(pairs, a.split)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps({"pr_number": k}) + "\n" for k in keys))
    print(f"{len(keys)} {a.split} pairs ({sum(pairs[k]['labels']['positive'] for k in keys)} positive) → {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mergeone.eval.run6")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair-subset", help="run-4 R4-H2 eval pairs → {pr_number} rows for predict --subset")
    p.add_argument("--pairs", default=str(PAIRS))
    p.add_argument("--split", default="test")
    p.add_argument("--out", default=str(RUN_DIR / "pairs_eval_test.jsonl"))
    p.set_defaults(fn=cmd_pair_subset)
    r = sub.add_parser("report"); r.add_argument("run_dir", nargs="?", default=str(RUN_DIR)); r.set_defaults(fn=lambda a: run(Path(a.run_dir)))
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
