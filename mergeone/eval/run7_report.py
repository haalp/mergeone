"""Run 7 report: C1–C5 tables and the R7-H1…H5 verdicts (PROTOCOL.md §R7.3, decisions in §R7.6). No model call, no key.

  python -m mergeone.eval.run7_report [--root .] [--out-dir results/run7]
        → results/run7/tables.md (markdown tables + verdict lines), results/run7/metrics.json (every number, with the
          path and sha256 of each input read); a short summary on stdout. results/run7/README.md is written by hand.

Every section degrades: an input that is not on disk makes that section (or row) say "not run yet", so the report can be
regenerated after each step of R7.5. Paths are relative to --root (the repository root).

Inputs
  C1  results/run7/c1/predictions_<model>_test_all_compact.jsonl     v3 and pair rows (`--compact`; filtered on `qset`)
      results/run7/c1/meta_<model>_test_all_compact[_v3].json        model_version (optional)
      results/run5/metrics.json        full-state baseline per v3 question: models[m].questions[q].test_raw_vs_iterated
      results/run5/predictions_<model>_test_all.jsonl                full-state v3 rows (paired bootstrap, tokens, version)
      results/run4/metrics.json        full-state pair baseline: models[m].pair_test.roc_auc (the number runs 4–6 compared to)
      results/run4/predictions_<model>_test_all.jsonl                full-state pair rows (paired bootstrap, tokens)
      results/run6/pairs_eval_test.jsonl                             the run-4 R4-H2 test pair ids (187; run6 pair-subset)
      data/run3/dataset.jsonl          `iterated` labels, read as run 5 reads them (rows' own labels if absent)
  C2  data/run7/tuning_log.jsonl, data/run7/frozen.json, results/run7/c2/predictions_pplx-flash_test_all.jsonl;
      untuned = run 5's pplx-flash test numbers (results/run5/metrics.json, predictions file) — never re-run (R7.4)
  C3  results/run7/evidence_metrics_iteration.json, results/run7/evidence_metrics_rejection.json (carry R7-H3)
  C4  deferred to run 8 (§R7.6)
  C5  results/run7/relation_metrics.json (run7_relation report; carries R7-H5)

Scoring (fixed here, before any run-7 test call)
  * AUC per v3 question = run5._score (raw answers vs `iterated`, test split) — the computation behind run 5's
    `test_raw_vs_iterated`; pair AUC = `same_change` vs `labels.positive` on the R4-H2 test pair ids (as runs 4 and 6).
  * ΔAUC = run-7 AUC − the baseline number in the run-4/run-5 metrics JSON. Its 95% CI is a paired bootstrap over the
    PRs (pairs) answered in both renderings (seed 7, 1,000 resamples, metrics.bootstrap_ci), on the difference of the two
    AUCs computed on the same resample. "mean" = mean over the ten v3 questions; its CI resamples PRs with all ten answers.
  * R7-H1: mean v3 ΔAUC ≥ −0.02 for jev and for pplx-flash, and pair ΔAUC ≥ −0.02 (both models).
  * R7-H2 (§R7.6): mean v3 AUC vs run 5's labels (`iterated`) — tuned − untuned ≥ +0.02 — and the test iteration eval
    (needs_human_review vs `iterated`) drops ≤ 0.01. The revert half of the eval has no untuned test number (run 5 did
    not run the k8s slice and R7.4 forbids re-running the untuned prompt), so it is reported as unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import rankdata

from ..models.base import DECISIONS_V3, PRIMARY_V3
from . import run5
from .metrics import bootstrap_ci

RUN_DIR = Path("results/run7")
C1_DIR = RUN_DIR / "c1"
C2_DIR = RUN_DIR / "c2"
RUN4_DIR = Path("results/run4")
RUN5_DIR = Path("results/run5")
PAIR_KEYS = Path("results/run6/pairs_eval_test.jsonl")
DATASET = run5.DATASET
TUNING_LOG = Path("data/run7/tuning_log.jsonl")
FROZEN = Path("data/run7/frozen.json")
EVIDENCE = {t: RUN_DIR / f"evidence_metrics_{t}.json" for t in ("iteration", "rejection")}
RELATION = RUN_DIR / "relation_metrics.json"

MODELS = ("jev", "pplx-flash")   # §R7.6: no haiku in run 7
C2_MODEL = "pplx-flash"
ITER = run5.ITER
H1_TOL, H2_GAIN, H2_EVAL_TOL = 0.02, 0.02, 0.01
BOOT_N, BOOT_SEED = 1000, 7
NOT_RUN = "not run yet"
EPS = 1e-12

BARS = {
    "R7-H1": "compact − full mean v3 AUC ≥ −0.02 for jev and pplx-flash, and pair AUC ≥ −0.02",
    "R7-H2": "tuned − untuned mean v3 AUC (vs iterated, test) ≥ +0.02 and test iteration eval drop ≤ 0.01",
    "R7-H3": "evidence-only test AUC on iteration ≥ 0.76 or stacked gain ≥ +0.02",
    "R7-H4": "deferred to run 8 (§R7.6)",
    "R7-H5": "related-vs-unrelated AUC ≥ 0.80 for ≥ 1 model and same_change-vs-rest ≥ 0.80",
}

_f = run5._f


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(x, nd=3) -> str:
    return "—" if x is None else f"{x:+.{nd}f}"


def _ci(c) -> str:
    return f"[{_f(c[0])}, {_f(c[1])}]" if c else "—"


def _mark(p) -> str:
    return "pending" if p is None else ("pass" if p else "fail")


# ----------------------------------------------------------------- inputs
class Inputs:
    """Reads files under `root` and records each one read: {relative path: sha256}."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.seen: dict[str, str] = {}

    def _path(self, rel: Path) -> Path | None:
        p = self.root / rel
        if not p.exists():
            return None
        self.seen[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
        return p

    def jsonl(self, rel: Path) -> list[dict] | None:
        p = self._path(rel)
        return None if p is None else [json.loads(l) for l in p.open() if l.strip()]

    def json(self, rel: Path) -> dict | None:
        p = self._path(rel)
        return None if p is None else json.loads(p.read_text())


def latest(rows: list[dict] | None, qset: str) -> dict[int, dict]:
    """{pr_number: row} for error-free joint rows of one question set; last row wins (as run5._load)."""
    out = {}
    for r in rows or []:
        if r.get("qset") == qset and r.get("mode", "joint") == "joint" and r["decision"].get("error") is None:
            out[r["pr_number"]] = r
    return out


def _ok(x) -> bool:
    return x is not None and x == x


def _p(row: dict, q: str):
    v = row["decision"]["probs"].get(q)
    return float(v) if _ok(v) else None


def versions(rows: dict[int, dict], meta: list[dict | None] = ()) -> list[str]:
    vs = {r["decision"].get("model_version") for r in rows.values()} | {(m or {}).get("model_version") for m in meta}
    return sorted(v for v in vs if v)


def tokens(rows: dict[int, dict], keys=None) -> dict | None:
    t = [rows[k]["decision"].get("input_tokens") for k in (keys if keys is not None else rows)]
    t = [x for x in t if x]
    if not t:
        return None
    return {"n": len(t), "median": float(np.median(t)), "p90": float(np.percentile(t, 90))}


# ----------------------------------------------------------------- scoring
def auc_of(rows: dict[int, dict], y: dict[int, int], q: str) -> tuple[float | None, int]:
    """run5._score on the rows with a label and an answer to q."""
    keys = [k for k in rows if k in y and _p(rows[k], q) is not None]
    s = run5._score(np.array([y[k] for k in keys], dtype=int), np.array([_p(rows[k], q) for k in keys], dtype=float), ci=False)
    return s.get("roc_auc"), s["n"]


def rank_auc(y: np.ndarray, P: np.ndarray) -> np.ndarray:
    """ROC-AUC of every column of P (Mann–Whitney with average ranks for ties = sklearn's roc_auc_score), vectorised so
    the 1,000-resample bootstrap stays fast. nan per column if y is one-class."""
    y = np.asarray(y, dtype=int)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return np.full(P.shape[1], np.nan)
    r = rankdata(P, axis=0)
    return (r[y == 1].sum(axis=0) - n1 * (n1 + 1) / 2) / (n1 * n0)


def _mean_auc_fn(nq: int):
    def fn(y, P):
        a = rank_auc(y, P)
        return float(a[:nq].mean() - a[nq:].mean())  # nan if a resample is one-class; bootstrap_ci drops it
    return fn


def paired_ci(new: dict[int, dict], old: dict[int, dict], y: dict[int, int], qs: tuple[str, ...]) -> dict:
    """Paired bootstrap of mean_q AUC(new) − mean_q AUC(old) over keys answered (all of qs) in both. One q = that question."""
    keys = sorted(k for k in new if k in old and k in y and all(_p(new[k], q) is not None and _p(old[k], q) is not None for q in qs))
    if not keys:
        return {"n_paired": 0, "ci95": None, "delta_same_keys": None}
    yy = np.array([y[k] for k in keys], dtype=int)
    P = np.array([[_p(new[k], q) for q in qs] + [_p(old[k], q) for q in qs] for k in keys], dtype=float)
    if not 0 < yy.sum() < len(yy):
        return {"n_paired": len(keys), "ci95": None, "delta_same_keys": None}
    fn = _mean_auc_fn(len(qs))
    lo, hi = bootstrap_ci(yy, P, fn, n=BOOT_N, seed=BOOT_SEED)
    return {"n_paired": len(keys), "ci95": None if lo != lo else [lo, hi], "delta_same_keys": fn(yy, P)}


def compare_v3(new: dict[int, dict], old: dict[int, dict], y: dict[int, int], baseline: dict | None) -> dict:
    """Per v3 question and mean: new AUC, baseline AUC (from run 5's metrics JSON), Δ and paired-bootstrap CI."""
    per = {}
    for q in DECISIONS_V3:
        a, n = auc_of(new, y, q)
        b = ((baseline or {}).get("questions") or {}).get(q, {}).get("test_raw_vs_iterated")
        per[q] = {"auc": a, "n": n, "baseline": b, "delta": None if a is None or b is None else a - b,
                  **paired_ci(new, old, y, (q,))}
    both = [q for q in DECISIONS_V3 if per[q]["auc"] is not None and per[q]["baseline"] is not None]
    mean = {"auc": float(np.mean([per[q]["auc"] for q in both])) if both else None,
            "baseline": float(np.mean([per[q]["baseline"] for q in both])) if both else None, "n_questions": len(both)}
    mean["delta"] = None if len(both) != len(DECISIONS_V3) else mean["auc"] - mean["baseline"]
    mean.update(paired_ci(new, old, y, DECISIONS_V3))
    return {"questions": per, "mean": mean}


def iter_labels(inp: Inputs, *row_sets: dict[int, dict]) -> tuple[dict[int, int], str]:
    """`iterated` per PR from data/run3/dataset.jsonl (run 5's source); the rows' own labels if the dataset is absent."""
    ds = inp.jsonl(DATASET)
    if ds is not None:
        return {r["pr_number"]: int(bool(r["labels"][ITER])) for r in ds}, str(DATASET)
    y = {}
    for rows in row_sets:
        y.update({k: int(bool(r["labels"][ITER])) for k, r in rows.items() if ITER in (r.get("labels") or {})})
    return y, "prediction rows' labels (dataset not on disk)"


# ----------------------------------------------------------------- C1
def c1_v3(inp: Inputs, m: str, compact: dict[int, dict], full: dict[int, dict], m5: dict | None) -> dict:
    if not compact:
        return {"status": NOT_RUN}
    y, src = iter_labels(inp, compact, full)
    base = (m5 or {}).get("models", {}).get(m)
    out = {"status": "ok" if base else f"baseline missing ({RUN5_DIR / 'metrics.json'} has no {m})", "label_source": src,
           **compare_v3(compact, full, y, base)}
    common = [k for k in compact if k in full]
    out["tokens"] = {"compact": tokens(compact, common or None), "full": tokens(full, common or None), "same_prs": bool(common)}
    return out


def c1_pair(inp: Inputs, m: str, compact: dict[int, dict], full: dict[int, dict], m4: dict | None) -> dict:
    if not compact:
        return {"status": NOT_RUN}
    ids = inp.jsonl(PAIR_KEYS)
    if ids is None:
        return {"status": f"not evaluable: {PAIR_KEYS} not present (the R4-H2 test pair ids)"}
    keys = {r["pr_number"] for r in ids}
    new = {k: r for k, r in compact.items() if k in keys}
    old = {k: r for k, r in full.items() if k in keys}
    y = {k: int(bool(r["labels"]["positive"])) for k, r in {**old, **new}.items() if "positive" in (r.get("labels") or {})}
    a, n = auc_of(new, y, "same_change")
    b = ((m4 or {}).get("models", {}).get(m, {}).get("pair_test") or {}).get("roc_auc")
    common = [k for k in new if k in old]
    return {"status": "ok" if b is not None else f"baseline missing ({RUN4_DIR / 'metrics.json'} has no {m} pair_test)",
            "n_eval_pairs": len(keys), "auc": a, "n": n, "baseline": b, "delta": None if a is None or b is None else a - b,
            **paired_ci(new, old, y, ("same_change",)),
            "tokens": {"compact": tokens(new, common or None), "full": tokens(old, common or None), "same_prs": bool(common)}}


def c1(inp: Inputs) -> dict:
    m5, m4 = inp.json(RUN5_DIR / "metrics.json"), inp.json(RUN4_DIR / "metrics.json")
    out = {"models": {}}
    for m in MODELS:
        rows = inp.jsonl(C1_DIR / f"predictions_{m}_test_all_compact.jsonl")
        if rows is None:
            out["models"][m] = {"status": NOT_RUN}
            continue
        full_rows = inp.jsonl(RUN5_DIR / f"predictions_{m}_test_all.jsonl") or []
        pair_rows = inp.jsonl(RUN4_DIR / f"predictions_{m}_test_all.jsonl") or []
        cv3, cpair = latest(rows, "v3"), latest(rows, "pair")
        fv3, fpair = latest(full_rows, "v3"), latest(pair_rows, "pair")
        metas = [inp.json(C1_DIR / f"meta_{m}_test_all_compact{s}.json") for s in ("_v3", "")]
        out["models"][m] = {"status": "ok", "version_compact": versions({**cv3, **cpair}, metas), "version_full": versions({**fv3, **fpair}),
                            "render_source": sorted({r.get("render_source") for r in rows if r.get("render_source")}),
                            "v3": c1_v3(inp, m, cv3, fv3, m5), "pair": c1_pair(inp, m, cpair, fpair, m4)}
    out["R7-H1"] = h1(out["models"])
    return out


def _all(vals: list) -> bool | None:
    if any(v is False for v in vals):
        return False
    return None if any(v is None for v in vals) else True


def h1(models: dict) -> dict:
    per = {}
    for m in MODELS:
        e = models.get(m, {})
        dv = (e.get("v3") or {}).get("mean", {}).get("delta")
        dp = (e.get("pair") or {}).get("delta")
        per[m] = {"v3_mean_delta": dv, "pair_delta": dp,
                  "pass": _all([None if dv is None else dv >= -H1_TOL - EPS, None if dp is None else dp >= -H1_TOL - EPS])}
    return {"per_model": per, "pass": _all([v["pass"] for v in per.values()]), "bar": BARS["R7-H1"]}


# ----------------------------------------------------------------- C2
def c2_rounds(log: list[dict]) -> list[dict]:
    keep = ("round", "primary_val_auc", "mean_val_auc", "revert_auc", "iteration_auc", "eval_score", "kept", "decision", "n_tuning")
    return [{**{k: r.get(k) for k in keep}, "prompt_sha256": r.get("prompt_sha256"), "prompt_path": r.get("prompt_path")} for r in log]


def c2(inp: Inputs) -> dict:
    log, frozen = inp.jsonl(TUNING_LOG), inp.json(FROZEN)
    out = {"rounds": c2_rounds(log) if log is not None else None, "rounds_status": "ok" if log is not None else NOT_RUN,
           "frozen": frozen, "frozen_status": "ok" if frozen is not None else NOT_RUN}
    rows = inp.jsonl(C2_DIR / f"predictions_{C2_MODEL}_test_all.jsonl")
    tuned = latest(rows, "v3")
    if not tuned:
        out["test"] = {"status": NOT_RUN}
        out["R7-H2"] = {"pass": None, "status": "test call not run yet", "bar": BARS["R7-H2"]}
        return out
    untuned = latest(inp.jsonl(RUN5_DIR / f"predictions_{C2_MODEL}_test_all.jsonl"), "v3")
    base = ((inp.json(RUN5_DIR / "metrics.json") or {}).get("models") or {}).get(C2_MODEL)
    y, src = iter_labels(inp, tuned, untuned)
    shas = sorted({r.get("prompt_sha256") for r in tuned.values()}, key=str)
    test = {"status": "ok" if base else f"untuned baseline missing ({RUN5_DIR / 'metrics.json'})", "label_source": src,
            "n": len(tuned), "prompt_sha256": shas, "versions": versions(tuned), "versions_untuned": versions(untuned),
            "prompt_matches_frozen": None if frozen is None else shas == [frozen.get("prompt_sha256")],
            **compare_v3(tuned, untuned, y, base)}
    q = test["questions"][PRIMARY_V3]
    test["iteration_eval"] = {"auc": q["auc"], "baseline": q["baseline"], "delta": q["delta"], "ci95": q["ci95"]}
    test["revert_eval"] = {"status": "unavailable: no untuned k8s test number (run 5 did not run it; R7.4 forbids re-running untuned)"}
    out["test"] = test
    out["R7-H2"] = h2(test)
    return out


def h2(test: dict) -> dict:
    d, e = test["mean"]["delta"], test["iteration_eval"]["delta"]
    r = {"mean_delta": d, "mean_delta_ci95": test["mean"]["ci95"], "iteration_eval_delta": e, "bar": BARS["R7-H2"]}
    if test.get("prompt_matches_frozen") is False:
        return {**r, "pass": None, "status": "test rows' prompt_sha256 ≠ data/run7/frozen.json (protocol check failed)"}
    r["pass"] = _all([None if d is None else d >= H2_GAIN - EPS, None if e is None else e >= -H2_EVAL_TOL - EPS])
    return r


# ----------------------------------------------------------------- C3, C5
def c3(inp: Inputs) -> dict:
    out = {}
    for t, rel in EVIDENCE.items():
        m = inp.json(rel)
        if m is None:
            out[t] = {"status": NOT_RUN}
            continue
        eo = m.get("evidence_only") or {}
        out[t] = {"status": "ok", "n_test": m.get("n_test"), "base_rate_test": m.get("base_rate_test"), "patterns_sha256": m.get("patterns_sha256"),
                  "evidence_only_auc": eo.get("test_auc"),
                  "stacked": {s: {k: v.get(k) for k in ("test_auc", "nhr_alone_test_auc", "gain")} for s, v in (m.get("stacked") or {}).items()},
                  "R7-H3": m.get("R7-H3")}
    h = (out["iteration"].get("R7-H3") or {}) if out["iteration"]["status"] == "ok" else {}
    out["R7-H3"] = {"pass": h.get("pass"), "evidence_only_auc": h.get("evidence_only_auc"), "stack_gain": h.get("stack_gain"),
                    "bar": h.get("bar") or BARS["R7-H3"], **({} if h else {"status": "iteration scoring not run yet"})}
    return out


def c5(inp: Inputs) -> dict:
    m = inp.json(RELATION)
    if m is None:
        return {"status": NOT_RUN, "R7-H5": {"pass": None, "status": "relation report not run yet", "bar": BARS["R7-H5"]}}
    keep = ("n", "related_vs_unrelated_auc", "n_unrelated", "same_change_vs_rest_auc", "n_duplicate", "accuracy", "accuracy_ci95", "status")
    models = {k: {**{f: v.get(f) for f in keep}, "usd_per_pr_k20": (v.get("cost") or {}).get("usd_per_pr")} for k, v in (m.get("models") or {}).items()}
    h = (m.get("hypotheses") or {}).get("R7-H5") or {}
    return {"status": "ok", "n_pairs": m.get("n_pairs"), "gold_classes": m.get("gold_classes"), "models": models,
            "pair_question_baseline": m.get("pair_question_baseline"), "R7-H5": {**h, "bar": h.get("bar") or BARS["R7-H5"]}}


# ----------------------------------------------------------------- report
def build(root: Path = Path(".")) -> dict:
    inp = Inputs(root)
    rep = {"computed_at": _now(), "bootstrap": {"n": BOOT_N, "seed": BOOT_SEED},
           "c1": c1(inp), "c2": c2(inp), "c3": c3(inp), "c4": {"status": "deferred to run 8 (§R7.6)"}, "c5": c5(inp)}
    rep["hypotheses"] = {"R7-H1": rep["c1"]["R7-H1"], "R7-H2": rep["c2"]["R7-H2"], "R7-H3": rep["c3"]["R7-H3"],
                         "R7-H4": {"pass": None, "status": "deferred to run 8", "bar": BARS["R7-H4"]}, "R7-H5": rep["c5"]["R7-H5"]}
    rep["inputs"] = [{"path": p, "sha256": s} for p, s in sorted(inp.seen.items())]
    return rep


def _tok(t: dict | None) -> str:
    return "—" if not t else f"{t['median']:.0f} / {t['p90']:.0f}"


def _c1_tables(c: dict) -> list[str]:
    L = ["## C1 — compact state (test; full state = run 5 v3 / run 4 pair)", ""]
    for m, e in c["models"].items():
        if e["status"] != "ok":
            L += [f"### {m}", "", f"**{e['status']}**", ""]
            continue
        vf, vc = ", ".join(e["version_full"]) or "—", ", ".join(e["version_compact"]) or "—"
        L += [f"### {m} — version: full {vf} → compact {vc}" + (" (version drift)" if vf != vc else ""), ""]
        v = e["v3"]
        if v["status"] == NOT_RUN:
            L += [f"v3: **{NOT_RUN}**", ""]
        else:
            L += [f"v3 ({v['status']}; labels: {v['label_source']}; ΔCI: paired bootstrap, seed {BOOT_SEED}, {BOOT_N} resamples)", "",
                  "| question | n | compact AUC | full AUC (run 5) | Δ | 95% CI (paired) | n paired |", "|---|---|---|---|---|---|---|"]
            for q, r in v["questions"].items():
                L.append(f"| {q} | {r['n']} | {_f(r['auc'])} | {_f(r['baseline'])} | {_d(r['delta'])} | {_ci(r['ci95'])} | {r['n_paired']} |")
            mn = v["mean"]
            L.append(f"| **mean** | | {_f(mn['auc'])} | {_f(mn['baseline'])} | **{_d(mn['delta'])}** | {_ci(mn['ci95'])} | {mn['n_paired']} |")
            t = v["tokens"]
            L += ["", f"input tokens per request, median / p90: compact {_tok(t['compact'])} · full {_tok(t['full'])}"
                      + (" (same PRs)" if t["same_prs"] else ""), ""]
        p = e["pair"]
        if p["status"] == NOT_RUN or "auc" not in p:
            L += [f"pair: **{p['status']}**", ""]
        else:
            L += [f"pair ({p['status']}): same_change AUC compact {_f(p['auc'])} (n {p['n']} of {p['n_eval_pairs']}) vs full {_f(p['baseline'])} (run 4) → "
                  f"Δ {_d(p['delta'])} {_ci(p['ci95'])}; tokens median / p90: compact {_tok(p['tokens']['compact'])} · full {_tok(p['tokens']['full'])}", ""]
    h = c["R7-H1"]
    L += ["R7-H1 per model: " + "; ".join(f"{m}: v3 mean Δ {_d(v['v3_mean_delta'])}, pair Δ {_d(v['pair_delta'])} → {_mark(v['pass'])}"
                                         for m, v in h["per_model"].items()),
          "", f"**R7-H1: {_mark(h['pass'])}** — bar: {h['bar']}", ""]
    return L


def _c2_tables(c: dict) -> list[str]:
    L = ["## C2 — prompt tuning (pplx-flash; rounds on val, one test call)", ""]
    if c["rounds"] is None:
        L += [f"tuning log: **{NOT_RUN}**", ""]
    else:
        L += ["| round | prompt sha256 | primary val AUC (nhr→iterated) | mean v3 val AUC | revert AUC | iteration AUC | eval score | decision |",
              "|---|---|---|---|---|---|---|---|"]
        for r in c["rounds"]:
            L.append(f"| {r['round']} | `{(r['prompt_sha256'] or '')[:12]}` | {_f(r['primary_val_auc'])} | {_f(r['mean_val_auc'])} | {_f(r['revert_auc'])} | "
                     f"{_f(r['iteration_auc'])} | {_f(r['eval_score'])} | {'keep' if r['kept'] else 'reject'} |")
        L.append("")
    fz = c["frozen"]
    L += [f"frozen: round {fz['round']}, `{fz['prompt_sha256'][:12]}` ({fz.get('prompt_path')}), {fz.get('stopped')}" if fz else f"frozen: **{NOT_RUN}**", ""]
    t = c["test"]
    if t["status"] == NOT_RUN:
        L += [f"test: **{NOT_RUN}**", ""]
    else:
        L += [f"test ({t['status']}; n {t['n']}; labels: {t['label_source']}; prompt matches frozen: {t['prompt_matches_frozen']}; "
              f"version {', '.join(t['versions']) or '—'} vs run 5 {', '.join(t['versions_untuned']) or '—'})", "",
              "| question | n | tuned AUC | untuned AUC (run 5) | Δ | 95% CI (paired) |", "|---|---|---|---|---|---|"]
        for q, r in t["questions"].items():
            L.append(f"| {q} | {r['n']} | {_f(r['auc'])} | {_f(r['baseline'])} | {_d(r['delta'])} | {_ci(r['ci95'])} |")
        mn = t["mean"]
        L += [f"| **mean** | | {_f(mn['auc'])} | {_f(mn['baseline'])} | **{_d(mn['delta'])}** | {_ci(mn['ci95'])} |", "",
              f"test iteration eval (needs_human_review → iterated): {_f(t['iteration_eval']['auc'])} vs {_f(t['iteration_eval']['baseline'])} "
              f"(Δ {_d(t['iteration_eval']['delta'])}); revert eval: {t['revert_eval']['status']}", ""]
    h = c["R7-H2"]
    L += [f"**R7-H2: {_mark(h['pass'])}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]
    return L


def _c3_tables(c: dict) -> list[str]:
    L = ["## C3 — structural evidence floor (no model)", "", "| target | n test | evidence-only AUC | stacked AUC / nhr alone / gain |", "|---|---|---|---|"]
    for t in EVIDENCE:
        e = c[t]
        if e["status"] != "ok":
            L.append(f"| {t} | — | {e['status']} | |")
            continue
        st = "; ".join(f"{s}: {_f(v['test_auc'])} / {_f(v['nhr_alone_test_auc'])} / {_d(v['gain'])}" for s, v in e["stacked"].items()) or "—"
        L.append(f"| {t} | {e['n_test']} | {_f(e['evidence_only_auc'])} | {st} |")
    h = c["R7-H3"]
    return L + ["", f"**R7-H3: {_mark(h['pass'])}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]


def _c5_tables(c: dict) -> list[str]:
    L = ["## C5 — typed relation on the run-6B pairs", ""]
    if c["status"] != "ok":
        return L + [f"**{c['status']}**", "", f"**R7-H5: pending** — bar: {c['R7-H5']['bar']}", ""]
    L += [f"{c['n_pairs']} pairs · gold: " + ", ".join(f"{k} {v}" for k, v in (c["gold_classes"] or {}).items()), "",
          "| model | n | related vs unrelated AUC (n unrelated) | same_change vs rest AUC (n duplicate) | accuracy [95% CI] | $/PR at k=20 |",
          "|---|---|---|---|---|---|"]
    for m, e in c["models"].items():
        if e.get("n") is None:
            L.append(f"| {m} | — | {e.get('status') or NOT_RUN} | | | |")
            continue
        L.append(f"| {m} | {e['n']} | {_f(e['related_vs_unrelated_auc'])} ({e['n_unrelated']}) | {_f(e['same_change_vs_rest_auc'])} ({e['n_duplicate']}) | "
                 f"{_f(e['accuracy'])} {_ci(e['accuracy_ci95'])} | {_f(e['usd_per_pr_k20'], 5)} |")
    h = c["R7-H5"]
    return L + ["", f"**R7-H5: {_mark(h.get('pass'))}** — bar: {h['bar']}", "", "Per-class accuracy and confusion: results/run7/relation_tables.md.", ""]


def tables(rep: dict) -> str:
    L = ["# Run 7 — report tables", "", f"computed {rep['computed_at']} · `python -m mergeone.eval.run7_report`", "",
         "## Verdicts", "", "| hypothesis | result | bar |", "|---|---|---|"]
    for h, v in rep["hypotheses"].items():
        L.append(f"| {h} | {v.get('status') if h == 'R7-H4' else _mark(v.get('pass'))} | {v['bar']} |")
    L.append("")
    L += _c1_tables(rep["c1"]) + _c2_tables(rep["c2"]) + _c3_tables(rep["c3"])
    L += ["## C4 — registry row", "", "deferred to run 8 (§R7.6); R7-H4 is not a run-7 hypothesis.", ""]
    L += _c5_tables(rep["c5"])
    L += ["## Inputs", "", "| path | sha256 |", "|---|---|"] + [f"| {i['path']} | `{i['sha256'][:16]}` |" for i in rep["inputs"]]
    return "\n".join(L) + "\n"


def summary(rep: dict) -> str:
    L = [f"{h}: {v.get('status') if h == 'R7-H4' else _mark(v.get('pass'))}" for h, v in rep["hypotheses"].items()]
    for m, e in rep["c1"]["models"].items():
        if e["status"] == "ok":
            L.append(f"C1 {m}: version full {', '.join(e['version_full']) or '—'} → compact {', '.join(e['version_compact']) or '—'}")
    return "\n".join(L)


def run(root: Path = Path("."), out_dir: Path | None = None) -> dict:
    root = Path(root)
    out = Path(out_dir) if out_dir is not None else root / RUN_DIR
    rep = build(root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(rep, indent=2, default=float))
    (out / "tables.md").write_text(tables(rep))
    print(summary(rep))
    print(f"wrote {out / 'tables.md'}, {out / 'metrics.json'} ({len(rep['inputs'])} inputs read)")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run7_report", description="run-7 report: C1–C5 tables and R7-H1…H5 verdicts (no model call)")
    ap.add_argument("--root", default=".", help="repository root (input paths are relative to it)")
    ap.add_argument("--out-dir", default=None, help=f"output directory (default: <root>/{RUN_DIR})")
    a = ap.parse_args(argv)
    run(Path(a.root), Path(a.out_dir) if a.out_dir else None)


if __name__ == "__main__":
    main()
