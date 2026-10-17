"""Run 5, component C: question set v3 and the review-iteration outcome (PROTOCOL run-5 addendum, R5.1 C).

  python -m mergeone.eval.run5 pairs            candidates (component A) → results/run5/pairs.jsonl (run 4's builder, top-5)
                                                + pairs_todo_<model>.jsonl (pairs that model has not answered in run 4)
  python -m mergeone.cli report results/run5 --run5

Report (test repositories unless stated):
  R5-H5  per-question isotonic calibration fitted on val vs `iterated`, applied to test; ROC-AUC of calibrated
         needs_human_review → iterated (bar 0.70 for ≥ 1 System One model; < 0.60 = not replicating); the
         10-question logistic stack fitted on val (pruning rule on val, 5-fold) evaluated on test (bar 0.75).
         All ten questions' AUCs vs `iterated` and vs `rejected` (continuity).
  R5-H3  run-4 R4-H3/R4-H4 recomputed with the component-A top-5 (results/run5/candidates.jsonl). Pair predictions:
         run 4's where the (A, B) pair is identical, run 5's (qset pair) for new pairs only. Verdicts: run-3 replay
         (run4._verdict_level) for jev/haiku/rules; pplx/pplx-flash use their run-5 v3 answers with the run-3 rule.
  R5-H6  pplx / pplx-flash load tests at 100× vs the R3-H5 bar; the adapter's 429 retry must be off.

Inputs in results/run5/ share one file per model and split: predictions_<model>_<split>_all.jsonl holds v3 rows,
pair rows and (component B) pair_assume rows; every reader here filters on `qset`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import types
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..models.base import DECISIONS_V3, PRIMARY_V3, V3_NEW
from . import run4
from .metrics import roc_auc, pr_auc, bootstrap_ci, recall_at_fpr, ece, brier

DATASET = Path("data/run3/dataset.jsonl")  # relative to cwd (repo root), as runs 3–4
RUN3_DIR = Path("results/run3")
RUN4_DIR = Path("results/run4")
RUN_DIR = Path("results/run5")
ITER, REJ, BAD = "iterated", "rejected", "bad_outcome"
SYSTEM_ONE = ("jev", "pplx", "pplx-flash")
H3_MODELS = ("jev", "haiku", "pplx", "pplx-flash", "rules")
RUN3_VERDICTS = ("jev", "haiku", "rules")  # have run-3 v2 predictions; pplx/pplx-flash take run-5 v3 answers
PAIR_MODELS = ("jev", "haiku", "pplx", "pplx-flash")
TOP_K = 5
PRUNE_GAIN = 0.005  # R5.1 C: drop a v3 question from the stack if its val marginal gain < 0.005 AUC
CV_FOLDS, CV_SEED = 5, 5
LT_MULTS = (1, 10, 100)

CALIBRATION_NOTE = (
    "R5-H5: per question, isotonic (sklearn, out_of_bounds=clip) fitted on val-repo raw answers vs `iterated`, applied to test. "
    "R5-H3 verdicts: run-3 rule unchanged — default gate policy (always_human_paths cleared), shipped calibration_<model>_v2.json maps "
    "for the four non-primary questions where they exist (jev, haiku), and needs_human_review refit by isotonic on val repos vs "
    "`rejected` (report_run3._calibrate_primary). jev/haiku/rules use their run-3 v2 predictions (as run 4). pplx/pplx-flash have no "
    "shipped maps, so their four non-primary v2 answers are used uncalibrated; their needs_human_review is refit vs `rejected` on "
    "their run-5 v3 val answers; the five new v3 questions are not in the policy and do not affect verdicts.")
STACK_NOTE = (
    "Stack: features = the raw answers to the ten v3 questions (rows with all ten present), StandardScaler + L2 logistic regression "
    "(C=1, lbfgs) fitted on val repos vs `iterated`. Pruning (val only): marginal gain of q = pooled out-of-fold AUC of the 10-question "
    "stack minus that of the stack without q, 5-fold stratified CV within val (seed 5). Only the five questions new in v3 are "
    "prunable; gains are reported for all ten. Pruned questions are dropped for the stack only.")

SCALE_NOTE = (
    "R5-H6 applies the R3-H5 computation: ×100 p99 vs the model's in-run p99 (v3 rows, val+test) + 200 ms, and 0 errors. The in-run "
    "pplx calls ran at concurrency 2 with the adapter's 429 retry on (as run 4), so `pass_vs_x1` — the same bar against the retry-off "
    "×1 load-test p99 — is reported alongside. A load test whose summary does not show retry_429 = 0 is invalid for R5-H6.")


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).open()]


def _hpid(repo: str, a: int, b: int | None = None) -> int:
    key = f"{repo}#{a}" if b is None else f"{repo}#{a}~{b}"
    return int(hashlib.sha1(key.encode()).hexdigest()[:12], 16)


def _top(cr: dict, k: int = TOP_K) -> list[dict]:
    return (cr.get("top5") or cr.get("candidates") or [])[:k]


# ----------------------------------------------------------------- loading
def _load(run_dir: Path, qset: str, key: str | None = None) -> dict[tuple[str, str], dict[int, dict]]:
    """{(model, split): {pr_number: decision}} for non-error rows of one question set (last row wins)."""
    out = defaultdict(dict)
    for p in sorted(Path(run_dir).glob("predictions_*_all.jsonl")):
        _, model, split, _ = p.stem.split("_", 3)
        for line in p.open():
            r = json.loads(line)
            if r.get("qset") != qset or r["decision"].get("error") is not None:
                continue
            if key is not None and r["decision"]["probs"].get(key) != r["decision"]["probs"].get(key):
                continue
            out[(model, split)][r["pr_number"]] = r["decision"]
    return out


def _lat_cost(decisions: list[dict]) -> dict | None:
    if not decisions:
        return None
    lat = sorted(d["latency_ms"] for d in decisions)
    return {"n": len(lat), "p50_ms": lat[len(lat) // 2], "p99_ms": lat[int(len(lat) * 0.99)],
            "cost_usd_per_1k": 1000 * sum(d.get("cost_usd") or 0 for d in decisions) / len(decisions)}


# ----------------------------------------------------------------- R5-H5
def _iso(x: np.ndarray, y: np.ndarray) -> IsotonicRegression | None:
    if len(x) < 30 or y.sum() == 0 or y.sum() == len(y):
        return None
    return IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(x, y)


def _score(y: np.ndarray, p: np.ndarray, ci: bool = True) -> dict:
    m = ~np.isnan(p)
    y, p = y[m], p[m]
    if len(y) == 0 or not (0 < y.sum() < len(y)):
        return {"n": int(len(y)), "positives": int(y.sum()) if len(y) else 0, "roc_auc": None}
    out = {"n": int(len(y)), "positives": int(y.sum()), "roc_auc": roc_auc(y, p), "pr_auc": pr_auc(y, p),
           "ece": ece(y, p), "brier": brier(y, p), "recall_at_fpr5": recall_at_fpr(y, p, 0.05)[0]}
    if ci:
        out["roc_auc_ci95"] = list(bootstrap_ci(y, p, roc_auc))
    return out


def _stack_model():
    return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))


def _cv_auc(X: np.ndarray, y: np.ndarray, cols: list[int]) -> float:
    if not cols:
        return 0.5
    oof = np.zeros(len(y))
    for tr, te in StratifiedKFold(CV_FOLDS, shuffle=True, random_state=CV_SEED).split(X, y):
        oof[te] = _stack_model().fit(X[tr][:, cols], y[tr]).predict_proba(X[te][:, cols])[:, 1]
    return roc_auc(y, oof)


def _matrix(preds: dict[int, dict], ds: dict, label: str):
    keys = [k for k in preds if k in ds and all(preds[k]["probs"].get(q) == preds[k]["probs"].get(q) and preds[k]["probs"].get(q) is not None for q in DECISIONS_V3)]
    X = np.array([[preds[k]["probs"][q] for q in DECISIONS_V3] for k in keys], dtype=float).reshape(len(keys), len(DECISIONS_V3))
    y = np.array([int(ds[k]["labels"][label]) for k in keys])
    return keys, X, y


def _stack(val: dict[int, dict], test: dict[int, dict], ds: dict) -> dict:
    _, Xv, yv = _matrix(val, ds, ITER)
    _, Xt, yt = _matrix(test, ds, ITER)
    if len(yv) < 50 or yv.sum() < CV_FOLDS or len(yt) == 0:
        return {"status": "insufficient data", "n_val": int(len(yv)), "n_test": int(len(yt))}
    allc = list(range(len(DECISIONS_V3)))
    base = _cv_auc(Xv, yv, allc)
    gains = {q: base - _cv_auc(Xv, yv, [j for j in allc if j != i]) for i, q in enumerate(DECISIONS_V3)}
    pruned = [q for q in V3_NEW if gains[q] < PRUNE_GAIN]
    kept = [i for i, q in enumerate(DECISIONS_V3) if q not in pruned]
    out = {"n_val": int(len(yv)), "val_positives": int(yv.sum()), "val_cv_auc_all10": base, "val_marginal_gain": gains,
           "pruned": pruned, "kept": [DECISIONS_V3[i] for i in kept], "val_cv_auc_kept": _cv_auc(Xv, yv, kept)}
    for name, cols in (("test", kept), ("test_all10_unpruned", allc)):
        mdl = _stack_model().fit(Xv[:, cols], yv)
        pt = mdl.predict_proba(Xt[:, cols])[:, 1]
        out[name] = _score(yt, pt, ci=(name == "test"))
        if name == "test":
            lr = mdl[-1]
            out["coef_standardised"] = {DECISIONS_V3[c]: float(w) for c, w in zip(cols, lr.coef_[0])}
    return out


def _h5_model(val: dict[int, dict], test: dict[int, dict], ds: dict) -> dict:
    vk = [k for k in val if k in ds]
    tk = [k for k in test if k in ds]
    y_it_t = np.array([int(ds[k]["labels"][ITER]) for k in tk])
    y_rj_t = np.array([int(ds[k]["labels"][REJ]) for k in tk])
    y_it_v = np.array([int(ds[k]["labels"][ITER]) for k in vk])
    y_rj_v = np.array([int(ds[k]["labels"][REJ]) for k in vk])
    entry = {"n_val": len(vk), "n_test": len(tk), "questions": {}}
    for q in DECISIONS_V3:
        xv = np.array([val[k]["probs"].get(q, np.nan) for k in vk], dtype=float)
        xt = np.array([test[k]["probs"].get(q, np.nan) for k in tk], dtype=float)
        mv, mt = ~np.isnan(xv), ~np.isnan(xt)
        iso = _iso(xv[mv], y_it_v[mv])
        ct = np.full_like(xt, np.nan)
        if iso is not None:
            ct[mt] = iso.predict(xt[mt])
        row = {"test_raw_vs_iterated": _score(y_it_t, xt, ci=False).get("roc_auc"),
               "test_cal_vs_iterated": _score(y_it_t, ct, ci=False).get("roc_auc") if iso is not None else None,
               "test_cal_ece_iterated": _score(y_it_t, ct, ci=False).get("ece") if iso is not None else None,
               "test_raw_vs_rejected": _score(y_rj_t, xt, ci=False).get("roc_auc"),
               "val_raw_vs_iterated": _score(y_it_v, xv, ci=False).get("roc_auc"),
               "val_raw_vs_rejected": _score(y_rj_v, xv, ci=False).get("roc_auc"),
               "calibrated_on_val": iso is not None}
        entry["questions"][q] = row
        if q == PRIMARY_V3:
            entry["nhr_raw"] = _score(y_it_t, xt)
            entry["nhr_v3cal"] = _score(y_it_t, ct) if iso is not None else {"roc_auc": None, "note": "isotonic not fitted (val too small or one class)"}
            iso_r = _iso(xv[mv], y_rj_v[mv])  # run-3 calibration copy (vs rejected), H3/H4 comparability
            if iso_r is not None:
                cr = np.full_like(xt, np.nan); cr[mt] = iso_r.predict(xt[mt])
                entry["nhr_run3cal_vs_rejected"] = _score(y_rj_t, cr, ci=False)
                entry["nhr_run3cal_vs_iterated"] = _score(y_it_t, cr, ci=False)
    entry["stack"] = _stack(val, test, ds)
    return entry


# ----------------------------------------------------------------- R5-H3
def _h3(run_dir: Path, ds: dict, base_bad: float) -> dict:
    cpath = run_dir / "candidates.jsonl"
    if not cpath.exists():
        return {"status": "A pending", "note": f"{cpath} not found (component A); R5-H3 skipped"}
    cands = _rows(cpath)
    r4 = {}
    for m in PAIR_MODELS:
        r4[m] = {}
        for (mm, _), d in _load(RUN4_DIR, "pair", "same_change").items():
            if mm == m:
                r4[m].update({k: v["probs"]["same_change"] for k, v in d.items()})
    r5 = defaultdict(dict)
    for (m, _), d in _load(run_dir, "pair", "same_change").items():
        r5[m].update({k: v["probs"]["same_change"] for k, v in d.items()})
    have_v3_test = {m for (m, sp), d in _load(run_dir, "v3").items() if sp == "test" and d}
    out = {"status": "ok", "n_query_prs": len(cands), "top_k": TOP_K, "models": {}}
    r4m = json.loads((RUN4_DIR / "metrics.json").read_text()) if (RUN4_DIR / "metrics.json").exists() else {}
    for m in H3_MODELS:
        dup, src = defaultdict(float), Counter()
        for cr in cands:
            a_pid = _hpid(cr["repo"], cr["number"])
            test = ds.get(a_pid, {}).get("split") == "test"
            for c in _top(cr):
                if m == "rules":
                    v = c.get("score")
                    v = None if v is None else float(v)
                    s = "retrieval_score"
                else:
                    pid = _hpid(cr["repo"], cr["number"], c["number"])
                    if pid in r4[m]:
                        v, s = r4[m][pid], "run4"
                    elif pid in r5[m]:
                        v, s = r5[m][pid], "run5"
                    else:
                        v, s = None, "missing"
                if test:
                    src[s] += 1
                if v is not None:
                    dup[a_pid] = max(dup[a_pid], v)
        vpath = (RUN3_DIR if m in RUN3_VERDICTS else run_dir) / f"predictions_{m}_test_all.jsonl"
        e = {"pair_sources_test": dict(src), "verdict_source": str(vpath)}
        if m not in RUN3_VERDICTS:
            e["verdict_source"] += " (qset v3; run-3 rule)"
        if not vpath.exists() or (m not in RUN3_VERDICTS and m not in have_v3_test):
            e["status"] = "verdicts pending"
        elif src.get("missing"):
            e["status"] = f"pair predictions pending ({src['missing']} test pairs missing)"
        else:
            e["status"] = "ok"
        if e["status"] == "ok" or (e["status"].startswith("pair predictions pending") and vpath.exists()):
            if m in RUN3_VERDICTS:
                e.update(run4._verdict_level(m, vpath, dup, ds, base_bad))
            else:
                e.update(run4._verdict_level(m, _v3_only(run_dir, m), dup, ds, base_bad))
            prev = r4m.get("models", {}).get(m, {})
            if "with_dedup" in prev:
                e["run4_with_dedup"] = prev["with_dedup"]
        out["models"][m] = e
    return out


def _v3_only(run_dir: Path, model: str) -> Path:
    """run4._verdict_level reads a predictions file and its sibling val file; give it v3 rows only."""
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="run5_v3_"))
    for sp in ("val", "test"):
        src = run_dir / f"predictions_{model}_{sp}_all.jsonl"
        rows = [l for l in src.open() if json.loads(l).get("qset") == "v3"] if src.exists() else []
        (d / f"predictions_{model}_{sp}_all.jsonl").write_text("".join(rows))
    return d / f"predictions_{model}_test_all.jsonl"


# ----------------------------------------------------------------- R5-H6
def _h6(run_dir: Path, inrun: dict[str, dict | None]) -> dict:
    out = {}
    for m in ("pplx", "pplx-flash"):
        lts = {}
        for x in LT_MULTS:
            p = run_dir / f"loadtest_{m}_x{x}.json"
            if p.exists():
                lts[f"x{x}"] = json.loads(p.read_text())["summary"]
        e = {"loadtests": lts, "inrun_p99_ms": (inrun.get(m) or {}).get("p99_ms")}
        s = lts.get("x100")
        if not s:
            e["status"] = "pending"
        else:
            retry_off = s.get("retry_429") == 0
            ref = e["inrun_p99_ms"]
            e["status"] = "ok" if retry_off else "invalid: adapter 429 retry was not disabled"
            e["pass"] = bool(retry_off and s["error_rate"] == 0 and ref is not None and s["p99_ms"] is not None and s["p99_ms"] <= ref + 200)
            x1 = (lts.get("x1") or {}).get("p99_ms")
            e["pass_vs_x1_p99"] = bool(retry_off and s["error_rate"] == 0 and x1 is not None and s["p99_ms"] is not None and s["p99_ms"] <= x1 + 200)
        out[m] = e
    return out


# ----------------------------------------------------------------- report
def run(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    ds = {r["pr_number"]: r for r in _rows(DATASET)}
    v3 = _load(run_dir, "v3")
    models = sorted({m for m, _ in v3})
    tk_all = [k for k, r in ds.items() if r["split"] == "test"]
    base_bad = float(np.mean([ds[k]["labels"][BAD] for k in tk_all]))
    rep = {"dataset": {"n_val": sum(1 for r in ds.values() if r["split"] == "val"), "n_test": len(tk_all),
                       "base_rate_iterated_test": float(np.mean([ds[k]["labels"][ITER] for k in tk_all])),
                       "base_rate_iterated_val": float(np.mean([r["labels"][ITER] for r in ds.values() if r["split"] == "val"])),
                       "base_rate_rejected_test": float(np.mean([ds[k]["labels"][REJ] for k in tk_all])), "base_rate_bad_outcome_test": base_bad},
           "notes": {"calibration": CALIBRATION_NOTE, "stack": STACK_NOTE, "scale": SCALE_NOTE}, "models": {}, "hypotheses": {}}
    inrun = {}
    for m in models:
        e = _h5_model(v3.get((m, "val"), {}), v3.get((m, "test"), {}), ds)
        e["latency_cost"] = inrun[m] = _lat_cost(list(v3.get((m, "val"), {}).values()) + list(v3.get((m, "test"), {}).values()))
        e["latency_cost_test"] = _lat_cost(list(v3.get((m, "test"), {}).values()))
        e["system_one"] = m in SYSTEM_ONE
        rep["models"][m] = e
    rep["r5_h3"] = _h3(run_dir, ds, base_bad)
    rep["r5_h6"] = _h6(run_dir, inrun)

    H = rep["hypotheses"]
    for m, e in rep["models"].items():
        a = e.get("nhr_v3cal", {}).get("roc_auc")
        H[f"R5-H5[{m}]"] = {"roc_auc": a, "ci95": e.get("nhr_v3cal", {}).get("roc_auc_ci95"), "system_one": e["system_one"],
                            "pass": a is not None and a >= 0.70, "not_replicating": a is not None and a < 0.60,
                            "bar": ">= 0.70 calibrated needs_human_review → iterated (System One counts; < 0.60 not replicating)"}
        st = e["stack"].get("test", {})
        H[f"R5-H5-stack[{m}]"] = {"roc_auc": st.get("roc_auc"), "ci95": st.get("roc_auc_ci95"), "pruned_on_val": e["stack"].get("pruned"),
                                  "pass": st.get("roc_auc") is not None and st["roc_auc"] >= 0.75, "bar": ">= 0.75 (stack fit on val)"}
    so = [m for m in SYSTEM_ONE if m in rep["models"]]
    if so:
        H["R5-H5"] = {"system_one_models": so, "best_nhr_auc": max((H[f"R5-H5[{m}]"]["roc_auc"] or 0) for m in so),
                      "best_stack_auc": max((H[f"R5-H5-stack[{m}]"]["roc_auc"] or 0) for m in so),
                      "pass": any(H[f"R5-H5[{m}]"]["pass"] for m in so) and any(H[f"R5-H5-stack[{m}]"]["pass"] for m in so),
                      "not_replicating": all(H[f"R5-H5[{m}]"]["not_replicating"] for m in so),
                      "bar": "nhr >= 0.70 for >= 1 System One model and stack >= 0.75"}
    else:
        H["R5-H5"] = {"status": "System One v3 predictions pending (jev/pplx/pplx-flash run on the Mac)", "pass": None, "bar": "nhr >= 0.70 for >= 1 System One model and stack >= 0.75"}
    h3 = rep["r5_h3"]
    if h3["status"] != "ok":
        H["R5-H3"] = {"status": h3["status"], "pass": None, "bar": "pass bad-rate <= 0.5 x base; closed coverage >= +15 pp"}
    else:
        for m, e in h3["models"].items():
            if "with_dedup" not in e:
                H[f"R5-H3[{m}]"] = {"status": e["status"], "pass": None, "bar": "R4-H3 and R4-H4 bars"}
                continue
            d = e["with_dedup"]["closed_nonpass_share"] - e["without_dedup"]["closed_nonpass_share"]
            H[f"R5-H3a[{m}]"] = {"pass_bad_rate": e["with_dedup"]["pass_bad_rate"], "base": base_bad, "status": e["status"],
                                 "pass": e["status"] == "ok" and e["with_dedup"]["pass_bad_rate"] <= 0.5 * base_bad, "bar": "R4-H3: pass bad-rate <= 0.5 x base"}
            H[f"R5-H3b[{m}]"] = {"closed_nonpass_without": e["without_dedup"]["closed_nonpass_share"], "with_dedup": e["with_dedup"]["closed_nonpass_share"],
                                 "delta_pp": 100 * d, "status": e["status"], "pass": e["status"] == "ok" and d >= 0.15, "bar": "R4-H4: >= +15 pp"}
    for m, e in rep["r5_h6"].items():
        H[f"R5-H6[{m}]"] = {"status": e["status"], **({"p99_ms_x100": e["loadtests"]["x100"]["p99_ms"], "error_rate_x100": e["loadtests"]["x100"]["error_rate"],
                                                       "inrun_p99_ms": e["inrun_p99_ms"], "x1_p99_ms": (e["loadtests"].get("x1") or {}).get("p99_ms"),
                                                       "pass_vs_x1": e.get("pass_vs_x1_p99")} if "x100" in e["loadtests"] else {}),
                            "pass": e.get("pass"), "bar": "100×: p99 <= in-run p99 + 200 ms, 0 errors (429 = error, retry off)"}
    (run_dir / "metrics.json").write_text(json.dumps(rep, indent=2, default=float))
    (run_dir / "tables.md").write_text(_tables(rep))
    print(_tables(rep))


def _f(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and x != x) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _tables(rep) -> str:
    d = rep["dataset"]
    L = ["# Run 5 — component C: question set v3 → review iteration", "",
         f"val {d['n_val']} PRs · test {d['n_test']} PRs · `iterated` base rate: test {_f(d['base_rate_iterated_test'])}, val {_f(d['base_rate_iterated_val'])} · "
         f"rejected (continuity) {_f(d['base_rate_rejected_test'])}", "",
         "## R5-H5: calibrated `needs_human_review` (v3 calibration, isotonic on val vs iterated) → iterated, test repos", "",
         "| model | n | ROC-AUC [95% CI] | raw AUC | PR-AUC | R@FPR5 | ECE (cal) | stack AUC [95% CI] | stack unpruned | pruned on val | p50 ms | p99 ms | $/1k |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        c, s, lc = e.get("nhr_v3cal", {}), e["stack"], e.get("latency_cost") or {}
        ci, sci = c.get("roc_auc_ci95"), s.get("test", {}).get("roc_auc_ci95")
        L.append(f"| {m}{' (S1)' if e['system_one'] else ''} | {e['n_test']} | {_f(c.get('roc_auc'))} [{_f(ci[0]) if ci else '—'}, {_f(ci[1]) if ci else '—'}] | "
                 f"{_f(e.get('nhr_raw', {}).get('roc_auc'))} | {_f(c.get('pr_auc'))} | {_f(c.get('recall_at_fpr5'))} | {_f(c.get('ece'))} | "
                 f"{_f(s.get('test', {}).get('roc_auc'))} [{_f(sci[0]) if sci else '—'}, {_f(sci[1]) if sci else '—'}] | {_f(s.get('test_all10_unpruned', {}).get('roc_auc'))} | "
                 f"{', '.join(s.get('pruned', [])) or ('none' if 'pruned' in s else s.get('status', '—'))} | {_f(lc.get('p50_ms'), 0)} | {_f(lc.get('p99_ms'), 0)} | {_f(lc.get('cost_usd_per_1k'))} |")
    L += ["", "## Every v3 question (test ROC-AUC; val AUC is in-sample for calibration and pruning)", ""]
    for m, e in rep["models"].items():
        s = e["stack"]
        L += [f"### {m}", "", "| question | vs iterated (raw) | vs iterated (cal) | ECE cal | vs rejected | val vs iterated | val marginal gain (stack) |", "|---|---|---|---|---|---|---|"]
        for q, r in e["questions"].items():
            g = (s.get("val_marginal_gain") or {}).get(q)
            L.append(f"| {q} | {_f(r['test_raw_vs_iterated'])} | {_f(r['test_cal_vs_iterated'])} | {_f(r['test_cal_ece_iterated'])} | {_f(r['test_raw_vs_rejected'])} | "
                     f"{_f(r['val_raw_vs_iterated'])} | {_f(g, 4)}{' (pruned)' if q in s.get('pruned', []) else ''} |")
        if "val_cv_auc_all10" in s:
            L.append(f"\nval 5-fold stack AUC: all ten {_f(s['val_cv_auc_all10'])}, kept {_f(s['val_cv_auc_kept'])} ({s['val_positives']} val positives).")
        L.append("")
    h3 = rep["r5_h3"]
    L += ["## R5-H3: verdict level with component-A top-5 (run-4 pair predictions reused where identical)", ""]
    if h3["status"] != "ok":
        L.append(f"**{h3['status']}** — {h3.get('note', '')}")
    else:
        L += ["| model | status | pair sources (test) | variant | pass % | pass bad-rate | closed not passed | dedup holds | run 4 (with dedup) |", "|---|---|---|---|---|---|---|---|---|"]
        for m, e in h3["models"].items():
            src = ", ".join(f"{k} {v}" for k, v in sorted(e["pair_sources_test"].items()))
            if "with_dedup" not in e:
                L.append(f"| {m} | {e['status']} | {src} | — | — | — | — | — | — |")
                continue
            for v in ("without_dedup", "with_dedup"):
                x = e[v]; p4 = e.get("run4_with_dedup") if v == "with_dedup" else None
                L.append(f"| {m} | {e['status']} | {src} | {v} | {_f(100 * x['pass_frac'], 1)} | {_f(x['pass_bad_rate'])} | {_f(100 * x['closed_nonpass_share'], 1)}% | {x['dedup_holds']} | "
                         f"{(_f(p4['pass_bad_rate']) + ' / ' + _f(100 * p4['closed_nonpass_share'], 1) + '%') if p4 else '—'} |")
    L += ["", "## R5-H6: load tests (pplx, pplx-flash; adapter 429 retry disabled)", "",
          "| model | ×1 p99 | ×10 p99 | ×100 p99 | ×100 errors | ×100 429s | in-run p99 | status |", "|---|---|---|---|---|---|---|---|"]
    for m, e in rep["r5_h6"].items():
        lt = e["loadtests"]
        L.append(f"| {m} | {_f((lt.get('x1') or {}).get('p99_ms'), 0)} | {_f((lt.get('x10') or {}).get('p99_ms'), 0)} | {_f((lt.get('x100') or {}).get('p99_ms'), 0)} | "
                 f"{_f((lt.get('x100') or {}).get('error_rate'))} | {(lt.get('x100') or {}).get('n_429', '—')} | {_f(e['inrun_p99_ms'], 0)} | {e['status']} |")
    L += ["", "## Pre-registered criteria", "", "| hypothesis | result | bar | pass |", "|---|---|---|---|"]
    for h, r in rep["hypotheses"].items():
        shown = {k: (round(v, 3) if isinstance(v, float) else ([round(x, 3) for x in v] if isinstance(v, list) and v and isinstance(v[0], float) else v))
                 for k, v in r.items() if k not in ("pass", "bar")}
        mark = "pending" if r["pass"] is None else ("✅" if r["pass"] else "❌")
        L.append(f"| {h} | {json.dumps(shown)} | {r['bar']} | {mark} |")
    L += ["", "## Notes", ""] + [f"- {v}" for v in rep["notes"].values()]
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------- pairs
def cmd_pairs(a) -> None:
    cpath = Path(a.candidates)
    if not cpath.exists():
        print(f"A pending: {cpath} not found; no pairs built")
        return
    rows = run4.cmd_pairs(types.SimpleNamespace(candidates=cpath, out=Path(a.out), top_k=a.top_k))
    done = {m: {k for (mm, _), d in _load(RUN4_DIR, "pair", "same_change").items() if mm == m for k in d} for m in PAIR_MODELS}
    any4 = set().union(*done.values())
    pids = [r["pr_number"] for r in rows]
    report = {"pairs": len(pids), "already_in_run4_any_model": sum(p in any4 for p in pids), "new_vs_run4": sum(p not in any4 for p in pids),
              "retrieved_new_by_split": dict(Counter(r["split"] for r in rows if r["pr_number"] not in any4 and r["kind"] == "retrieved")), "todo": {}}
    out_dir = Path(a.out).parent
    for m in PAIR_MODELS:
        todo = [p for p in pids if p not in done[m]]
        (out_dir / f"pairs_todo_{m}.jsonl").write_text("".join(json.dumps({"pr_number": p}) + "\n" for p in todo))
        report["todo"][m] = len(todo)
    (out_dir / "pairs_summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mergeone.eval.run5")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pairs", help="component-A top-5 → pairs.jsonl via run 4's builder; pairs_todo_<model>.jsonl = not answered in run 4")
    p.add_argument("--candidates", default=str(RUN_DIR / "candidates.jsonl"))
    p.add_argument("--out", default=str(RUN_DIR / "pairs.jsonl"))
    p.add_argument("--top-k", type=int, default=TOP_K)
    p.set_defaults(fn=cmd_pairs)
    r = sub.add_parser("report"); r.add_argument("run_dir", nargs="?", default=str(RUN_DIR)); r.set_defaults(fn=lambda a: run(Path(a.run_dir)))
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
