"""Run 10 report: R10-H1…H4 tables and verdicts (PROTOCOL.md §R10.3). No model call, no key.

  python -m mergeone.eval.run10_report [--root .] [--out-dir results/run10]
        → results/run10/tables.md, results/run10/metrics.json (every number, with the path and sha256 of each input read);
          a short summary and the list of missing inputs on stdout. results/run10/README.md is written by hand.

Every section degrades: a row or part not on disk is "not run yet", so the report runs on an empty results/run10 and after
each step of §R10.5. Paths are relative to --root.

Inputs (mergeone.eval.run10_rows writes them; scripts/run10_gpu.sh for the d3 row)
  results/run10/<row>/predictions_<model>_test_all.jsonl   qset v4 rows (705 test PRs) and qset relation2 rows (100 pairs)
  results/run10/d3/predictions_d3-flash-local_val_all.jsonl  qset v4 on val (the calibration map's input; not a bar)
  results/run10/d3/throughput.json                          the timed v4 test pass (run9_step: error-free PRs ÷ the pass's wall s)
  results/run10/d3/serve_meta.json                          revision, GPU, usd_per_hour (H3's d3 cost)
  data/run3/dataset.jsonl                                   `iterated` labels (run 5 / run 8's v4 target)
  data/run6/hold_labels_adjudicated.jsonl (+ hold_labels.jsonl, hold_labels_fable.jsonl, data/run7/relation_gold.jsonl)
                                                            relation gold (run7_relation.load_gold); metadata as run 8
  results/run8/v4/predictions_pplx-flash_test_all.jsonl, results/run8/relation_metrics.json   run 8, shown beside (reference)
  rows: control = pplx-flash, full = pplx, haiku = haiku (relation2 only), d3 = d3-flash-local

Scoring (fixed here, before any run-10 number exists)
  * AUC per v4 question = ROC-AUC of the raw answer vs `iterated` on the test PRs the row answered (run7_report.rank_auc =
    sklearn's), 95% CI = percentile bootstrap over PRs, 1,000 resamples, seed 7 (metrics.bootstrap_ci).
  * R10-H2, per row (full, d3): Δ = AUC_row(needs_human_review) − AUC_control, each on its own answered PRs (run 8's
    convention), and the paired bootstrap of the same difference over PRs both answered (run7_report.paired_ci: seed 7,
    1,000). Pass = Δ ≥ +0.03 and the paired CI's lower bound > 0. H2 passes if any row passes. haiku: not asked (§R10.2).
  * R10-H1, per row (full, haiku, d3): run8_relation.score (run 8's rule, HIGH 0.5 / LOW 0.25, metadata same_base branch)
    on the adjudicated pairs the row answered; competing + same_area accuracy = correct / n over those two gold classes
    (17 pairs) ≥ 0.40, and class accuracy (all pairs) ≥ the same-day control's class accuracy − 0.02. Pass if any row passes.
  * R10-H3 (reported; one bar): per-PR cost = main call + ten pair calls.
      hosted rows asked both (control, full):  c_main × (1 + 10 × t_pair / t_main), c_main = mean cost_usd of the row's v4
                                               test answers, t_pair / t_main = median input tokens of its relation2 / v4 rows
      haiku (pair calls only):                 c_main(control) + 10 × mean cost_usd of haiku's relation2 answers
      d3 (self-hosted):                        (usd_per_hour ÷ 3600 ÷ measured PR/s) × (1 + 10 × t_pair / t_main)
    p50 latency = median latency_ms of the row's v4 test answers (haiku: of its pair calls). Bar: a row passing H1 or H2
    ships only if its per-PR cost ≤ 5 × the control's; H3 = pass when every such row is within 5×, "n/a" when none passes.
  * R10-H4 (d3 vs control, v4 test): AUC ≥ control − 0.02 on at least 6 of the 8 questions, and throughput ≥ 1.0 PR/s
    (throughput.json items_per_s). Argmax agreement (p ≥ 0.5) and mean |Δp| on PRs both answered are reported, no bar.
    Calibration map (no bar): isotonic per question fit on d3's val answers vs `iterated`, test ECE raw vs mapped.
  * Same UTC day: every row's answer dates are listed; more than one date across rows is flagged, never re-scored.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ..models.base import DECISIONS_V4, PRIMARY_V4, RELATION2
from . import run5
from . import run7_relation as R7
from . import run7_report as RR
from . import run8_relation as M8
from .metrics import bootstrap_ci, ece
from .run10_rows import ROWS
from .run7_report import Inputs, NOT_RUN, _all, _ci, _d, _f, _mark, _now, latest
from .run8_report import combined

RUN_DIR = Path("results/run10")
CONTROL = "control"
V4_ROWS = ("control", "full", "d3")
REL_ROWS = ("control", "full", "haiku", "d3")
RUN8_V4 = Path("results/run8/v4/predictions_pplx-flash_test_all.jsonl")
RUN8_REL = Path("results/run8/relation_metrics.json")
GOLD = {"adj_path": R7.DATA6 / "hold_labels_adjudicated.jsonl", "human_path": R7.DATA6 / "hold_labels.jsonl",
        "second_path": R7.DATA6 / "hold_labels_fable.jsonl", "override_path": R7.DATA7 / "relation_gold.jsonl"}
BOOT_N, BOOT_SEED = RR.BOOT_N, RR.BOOT_SEED   # 1,000 resamples, seed 7
EPS = RR.EPS
H1_SUB, H1_ACC_TOL = 0.40, 0.02
H2_GAIN = 0.03
H3_X, PAIRS_PER_PR = 5.0, 10
H4_TOL, H4_MIN_Q, H4_PRS = 0.02, 6, 1.0
PROTOCOL_C = 32   # §R10.3 names concurrency 32 for the throughput pass; the run's own value is read from throughput.json

BARS = {   # quoted from PROTOCOL.md §R10.3
    "R10-H1": "on the 100 adjudicated pairs, accuracy on competing + same-area (17 pairs) ≥ 0.40, with class accuracy ≥ the "
              "control's same-day class accuracy − 0.02",
    "R10-H2": "needs_human_review AUC on the 705 test PRs ≥ control + 0.03 with a bootstrap CI excluding zero (1,000 resamples)",
    "R10-H3": "per-PR cost (main call + ten pair calls) and p50 latency for every row; a row that passes H1 or H2 ships only if "
              "its per-PR cost ≤ 5× the control's",
    "R10-H4": "d3-flash vs the hosted control: AUC on each question ≥ control − 0.02 on at least six of eight; throughput ≥ 1.0 PR/s "
              "on the 48 GB GPU; agreement and mean |Δp| reported (no bar)",
}


def preds_path(row: str, split: str = "test") -> Path:
    return RUN_DIR / row / f"predictions_{ROWS[row]['model']}_{split}_all.jsonl"


def _num(x) -> float | None:
    return float(x) if x is not None and x == x else None


# ----------------------------------------------------------------- loading
class Loaded:
    """Every row's rows, read once (with sha256 recorded)."""

    def __init__(self, inp: Inputs):
        self.inp = inp
        self.test = {r: inp.jsonl(preds_path(r)) for r in ROWS}
        self.val = {"d3": inp.jsonl(preds_path("d3", "val"))}

    def v4(self, row: str, split: str = "test") -> dict[int, dict]:
        rows = self.test.get(row) if split == "test" else self.val.get(row)
        return latest(rows, "v4")

    def rel(self, row: str) -> dict[int, dict]:
        return relation2_preds(self.test.get(row))


def relation2_preds(rows: list[dict] | None) -> dict[int, dict]:
    """{pair_id: decision} over error-free relation2 rows with all three probabilities (last row wins; run8_relation.load_preds)."""
    out = {}
    for r in rows or []:
        d = r["decision"]
        pr = d.get("probs") or {}
        if r.get("qset") == "relation2" and d.get("error") is None and all(_num(pr.get(q)) is not None for q in RELATION2):
            out[r["pr_number"]] = d
    return out


def utc_dates(rows: list[dict] | None) -> list[str]:
    return sorted({(r.get("ts") or "")[:10] for r in rows or [] if r.get("ts")})


# ----------------------------------------------------------------- AUC
def auc_ci(rows: dict[int, dict], y: dict[int, int], q: str, n_boot: int = BOOT_N, seed: int = BOOT_SEED) -> dict:
    keys = [k for k in rows if k in y and RR._p(rows[k], q) is not None]
    if not keys:
        return {"auc": None, "n": 0, "positives": 0, "ci95": None}
    yy = np.array([y[k] for k in keys], dtype=int)
    P = np.array([[RR._p(rows[k], q)] for k in keys], dtype=float)
    if not 0 < yy.sum() < len(yy):
        return {"auc": None, "n": len(keys), "positives": int(yy.sum()), "ci95": None}
    fn = lambda yb, Pb: float(RR.rank_auc(yb, Pb)[0])
    lo, hi = bootstrap_ci(yy, P, fn, n=n_boot, seed=seed)
    return {"auc": fn(yy, P), "n": len(keys), "positives": int(yy.sum()), "ci95": None if lo != lo else [lo, hi]}


def v4_block(rows: dict[int, dict], y: dict[int, int]) -> dict:
    per = {q: auc_ci(rows, y, q) for q in DECISIONS_V4}
    a = [per[q]["auc"] for q in DECISIONS_V4 if per[q]["auc"] is not None]
    return {"n": len(rows), "questions": per, "mean_auc": float(np.mean(a)) if len(a) == len(DECISIONS_V4) else None,
            "versions": RR.versions(rows)}


def v4_section(L: Loaded, y: dict[int, int]) -> dict:
    out = {"rows": {}}
    for row in V4_ROWS:
        v = L.v4(row)
        out["rows"][row] = {"status": f"{NOT_RUN} ({preds_path(row)} has no v4 rows)"} if not v else {"status": "ok", **v4_block(v, y)}
    r8 = latest(L.inp.jsonl(RUN8_V4), "v4")
    out["run8_pplx_flash_reference"] = {"status": "ok", **v4_block(r8, y)} if r8 else {"status": f"{NOT_RUN} ({RUN8_V4})"}
    return out


# ----------------------------------------------------------------- H2
def h2_clause(delta: float | None, ci: list | None) -> bool | None:
    """Δ ≥ +0.03 and the paired CI excludes zero (lower bound > 0)."""
    if delta is None or ci is None:
        return None
    return bool(delta >= H2_GAIN - EPS and ci[0] > 0)


def _any_pass(vals: list) -> bool | None:
    if any(v is True for v in vals):
        return True
    return None if any(v is None for v in vals) or not vals else False


def h2(L: Loaded, v4s: dict, y: dict[int, int]) -> dict:
    ctrl = L.v4(CONTROL)
    c_auc = ((v4s["rows"][CONTROL].get("questions") or {}).get(PRIMARY_V4) or {}).get("auc")
    per = {}
    for row in ("full", "d3"):
        rows = L.v4(row)
        if not rows:
            per[row] = {"status": f"{NOT_RUN} ({preds_path(row)})", "pass": None}
            continue
        if not ctrl:
            per[row] = {"status": f"control {NOT_RUN} ({preds_path(CONTROL)})", "pass": None}
            continue
        r_auc = v4s["rows"][row]["questions"][PRIMARY_V4]["auc"]
        delta = None if r_auc is None or c_auc is None else r_auc - c_auc
        pc = RR.paired_ci(rows, ctrl, y, (PRIMARY_V4,))
        per[row] = {"status": "ok", "auc": r_auc, "control_auc": c_auc, "delta": delta, "delta_same_prs": pc["delta_same_keys"],
                    "ci95": pc["ci95"], "n_paired": pc["n_paired"], "pass": h2_clause(delta, pc["ci95"])}
    per["haiku"] = {"status": "not asked (relation only, §R10.2)", "pass": None}
    p = _any_pass([per[r]["pass"] for r in ("full", "d3")])
    return {"per_row": per, "R10-H2": {"pass": p, "bar": BARS["R10-H2"],
                                       **({} if p is not None else {"status": "full / d3 vs control not scored yet"})}}


# ----------------------------------------------------------------- H1
def h1_clauses(e: dict, control_accuracy: float | None) -> dict:
    """e = run8_relation.score output (+ competing_same_area); the two §R10.3 clauses."""
    sub = (e.get("competing_same_area") or combined(e.get("per_class") or {}))["accuracy"]
    acc = e.get("accuracy")
    return {"competing_same_area": None if sub is None else bool(sub >= H1_SUB - EPS),
            "class_accuracy_vs_control": None if acc is None or control_accuracy is None else bool(acc >= control_accuracy - H1_ACC_TOL - EPS)}


def load_gold(inp: Inputs) -> tuple[dict[int, dict] | None, list[dict]]:
    for p in GOLD.values():
        inp._path(p)
    if not (inp.root / GOLD["adj_path"]).exists():
        return None, []
    gold = R7.load_gold(**{k: inp.root / v for k, v in GOLD.items()})
    return gold, M8._rows(inp.root / GOLD["adj_path"])


def h1(L: Loaded) -> dict:
    gold, adj = load_gold(L.inp)
    if gold is None:
        return {"status": f"not evaluable: {GOLD['adj_path']} not present", "rows": {},
                "R10-H1": {"pass": None, "status": "gold not present", "bar": BARS["R10-H1"]}}
    meta, msrc = M8.pair_metadata(L.inp, adj)
    out = {"status": "ok", "n_pairs": len(gold), "gold_classes": {c: sum(1 for g in gold.values() if g["relation"] == c) for c in M8.CLASSES},
           "rule": "run8_relation.relation2_class (HIGH 0.5, LOW 0.25; same_base from metadata, unknown → True)",
           "metadata_source": msrc, "rows": {}}
    for row in REL_ROWS:
        p = L.rel(row)
        if not p:
            out["rows"][row] = {"status": f"{NOT_RUN} ({preds_path(row)} has no relation2 rows)"}
            continue
        e = M8.score(gold, p, meta)
        e["competing_same_area"] = combined(e["per_class"])
        e["versions"] = sorted({d.get("model_version") for d in p.values()} - {None, ""})
        out["rows"][row] = {"status": "ok", **e}
    r8 = L.inp.json(RUN8_REL)
    out["run8_reference"] = {m: {"accuracy": e.get("accuracy"), "competing_same_area": combined(e.get("per_class") or {})["accuracy"]}
                             for m, e in ((r8 or {}).get("models") or {}).items() if "n" in e}
    c = out["rows"][CONTROL]
    c_acc = c.get("accuracy") if c["status"] == "ok" else None
    per = {}
    for row in ("full", "haiku", "d3"):
        e = out["rows"][row]
        if e["status"] != "ok":
            per[row] = {"status": e["status"], "pass": None}
            continue
        cl = h1_clauses(e, c_acc)
        per[row] = {"accuracy": e["accuracy"], "control_accuracy": c_acc, "competing_same_area": e["competing_same_area"],
                    "missing_pairs": e["missing"], "clauses": cl, "pass": _all(list(cl.values())),
                    **({"status": f"control {NOT_RUN}"} if c_acc is None else {})}
    out["per_row"] = per
    p = _any_pass([v["pass"] for v in per.values()])
    out["R10-H1"] = {"pass": p, "bar": BARS["R10-H1"], **({} if p is not None else {"status": "rows or control not scored yet"})}
    return out


# ----------------------------------------------------------------- H3
def _median(xs) -> float | None:
    xs = [float(x) for x in xs if x]
    return float(np.median(xs)) if xs else None


def _mean(xs) -> float | None:
    xs = [float(x) for x in xs if x is not None and x == x]
    return float(np.mean(xs)) if xs else None


def per_pr_cost(main_cost: float | None, main_tokens: float | None, pair_tokens: float | None, k: int = PAIRS_PER_PR) -> float | None:
    """main × (1 + k × pair_tokens / main_tokens): ten pair calls priced like the main call per token."""
    if main_cost is None or not main_tokens or pair_tokens is None:
        return None
    return main_cost * (1 + k * pair_tokens / main_tokens)


def cost_clause(cost: float | None, control_cost: float | None) -> bool | None:
    if cost is None or not control_cost:
        return None
    return bool(cost <= H3_X * control_cost + EPS)


def h3(L: Loaded, h1v: dict, h2v: dict) -> dict:
    rows = {}
    for row in ROWS:
        v4, rel = L.v4(row), L.rel(row)
        dec_v4, dec_rel = [r["decision"] for r in v4.values()], list(rel.values())
        e = {"n_v4": len(v4), "n_relation2": len(rel),
             "t_main": _median(d.get("input_tokens") for d in dec_v4), "t_pair": _median(d.get("input_tokens") for d in dec_rel),
             "p50_latency_ms": _median(d.get("latency_ms") for d in (dec_v4 if dec_v4 else dec_rel)),
             "latency_of": "v4 main call" if dec_v4 else ("relation2 pair call" if dec_rel else None),
             "spent_usd": sum(float(d.get("cost_usd") or 0.0) for d in dec_v4 + dec_rel)}
        if ROWS[row]["hosted"]:
            e["c_main"] = _mean(d.get("cost_usd") for d in dec_v4)
            e["c_pair"] = _mean(d.get("cost_usd") for d in dec_rel)
        rows[row] = e
    ctl = rows[CONTROL]
    for row in ("control", "full"):
        e = rows[row]
        e["formula"] = "c_main × (1 + 10 × t_pair / t_main)"
        e["per_pr_usd"] = per_pr_cost(e["c_main"], e["t_main"], e["t_pair"])
    h = rows["haiku"]
    h["formula"] = "c_main(control) + 10 × c_pair(haiku)"
    h["per_pr_usd"] = None if ctl["c_main"] is None or h["c_pair"] is None else ctl["c_main"] + PAIRS_PER_PR * h["c_pair"]
    d = rows["d3"]
    tp, sm = L.inp.json(RUN_DIR / "d3" / "throughput.json"), L.inp.json(RUN_DIR / "d3" / "serve_meta.json")
    d["prs_per_s"] = _num((tp or {}).get("items_per_s"))
    d["concurrency"] = (tp or {}).get("concurrency")
    d["usd_per_hour"] = _num((sm or {}).get("usd_per_hour"))
    d["c_main"] = None if not d["prs_per_s"] or d["usd_per_hour"] is None else d["usd_per_hour"] / 3600 / d["prs_per_s"]
    d["formula"] = "(usd_per_hour ÷ 3600 ÷ PR/s) × (1 + 10 × t_pair / t_main)"
    d["per_pr_usd"] = per_pr_cost(d["c_main"], d["t_main"], d["t_pair"])
    passing = []
    for row, e in rows.items():
        e["ratio_to_control"] = None if e["per_pr_usd"] is None or not ctl["per_pr_usd"] else e["per_pr_usd"] / ctl["per_pr_usd"]
        e["within_5x"] = cost_clause(e["per_pr_usd"], ctl["per_pr_usd"]) if row != CONTROL else True if ctl["per_pr_usd"] else None
        if row == CONTROL:
            e["passes_h1_or_h2"], e["ships"] = None, None   # the comparison, not a candidate
            continue
        vals = [((h1v.get("per_row") or {}).get(row) or {}).get("pass")]
        if row in ("full", "d3"):
            vals.append(((h2v.get("per_row") or {}).get(row) or {}).get("pass"))
        e["passes_h1_or_h2"] = _any_pass(vals)
        e["ships"] = e["within_5x"] if e["passes_h1_or_h2"] else (False if e["passes_h1_or_h2"] is False else None)
        if e["passes_h1_or_h2"]:
            passing.append(row)
    if passing:
        p = _all([rows[r]["within_5x"] for r in passing])
        v = {"pass": p, "rows_under_bar": passing}
    else:
        pend = [r for r in ("full", "haiku", "d3") if rows[r]["passes_h1_or_h2"] is None]
        v = {"pass": None, "na": not pend, "rows_under_bar": [],
             "status": "n/a: no row passed H1 or H2 (costs reported)" if not pend else f"pending: H1/H2 not scored for {', '.join(pend)}"}
    return {"rows": rows, "R10-H3": {**v, "bar": BARS["R10-H3"]}}


# ----------------------------------------------------------------- H4
def agreement(a: dict[int, dict], b: dict[int, dict], qs=DECISIONS_V4) -> dict:
    """argmax (p ≥ 0.5) agreement and mean |Δp| (a − b) on the PRs both answered, per question and over all answers."""
    keys = sorted(set(a) & set(b))
    per, ag_all, dp_all = {}, [], []
    for q in qs:
        pa = np.array([_num(a[k]["decision"]["probs"].get(q)) if _num(a[k]["decision"]["probs"].get(q)) is not None else np.nan for k in keys], dtype=float)
        pb = np.array([_num(b[k]["decision"]["probs"].get(q)) if _num(b[k]["decision"]["probs"].get(q)) is not None else np.nan for k in keys], dtype=float)
        m = ~np.isnan(pa) & ~np.isnan(pb)
        pa, pb = pa[m], pb[m]
        ag, dp = (pa >= 0.5) == (pb >= 0.5), np.abs(pa - pb)
        ag_all += ag.tolist(); dp_all += dp.tolist()
        per[q] = {"n": int(m.sum()), "agreement": float(ag.mean()) if len(pa) else None, "mean_abs_dp": float(dp.mean()) if len(pa) else None,
                  "mean_dp": float((pa - pb).mean()) if len(pa) else None}
    return {"n_prs": len(keys), "n_answers": len(ag_all), "agreement": float(np.mean(ag_all)) if ag_all else None,
            "mean_abs_dp": float(np.mean(dp_all)) if dp_all else None, "per_question": per}


def h4_clauses(d3_auc: dict[str, float | None], ctrl_auc: dict[str, float | None], prs_per_s: float | None) -> dict:
    ok = {q: (None if d3_auc.get(q) is None or ctrl_auc.get(q) is None else bool(d3_auc[q] >= ctrl_auc[q] - H4_TOL - EPS)) for q in DECISIONS_V4}
    n_ok = sum(1 for v in ok.values() if v)
    auc_clause = None if any(v is None for v in ok.values()) else bool(n_ok >= H4_MIN_Q)
    return {"per_question": ok, "n_within": n_ok, "auc_clause": auc_clause,
            "throughput_clause": None if prs_per_s is None else bool(prs_per_s >= H4_PRS - EPS)}


def calibration_map(val: dict[int, dict], test: dict[int, dict], y: dict[int, int]) -> dict:
    """Isotonic per question on val vs `iterated` (run5._iso), applied to test; ECE raw vs mapped. No bar."""
    out = {"n_val": len(val), "questions": {}}
    if not val or not test:
        out["status"] = f"{NOT_RUN} (val {len(val)}, test {len(test)})"
        return out
    out["status"] = "ok"
    for q in DECISIONS_V4:
        kv = [k for k in val if k in y and RR._p(val[k], q) is not None]
        kt = [k for k in test if k in y and RR._p(test[k], q) is not None]
        iso = run5._iso(np.array([RR._p(val[k], q) for k in kv]), np.array([y[k] for k in kv])) if kv else None
        yt, pt = np.array([y[k] for k in kt]), np.array([RR._p(test[k], q) for k in kt], dtype=float)
        out["questions"][q] = {"n_val": len(kv), "n_test": len(kt), "fitted": iso is not None,
                               "ece_raw": ece(yt, pt) if len(kt) else None,
                               "ece_mapped": ece(yt, iso.predict(pt)) if iso is not None and len(kt) else None}
    return out


def h4(L: Loaded, v4s: dict, y: dict[int, int]) -> dict:
    d3, ctrl = L.v4("d3"), L.v4(CONTROL)
    tp = L.inp.json(RUN_DIR / "d3" / "throughput.json")
    out = {"throughput": tp, "calibration_map": calibration_map(L.v4("d3", "val"), d3, y)}
    if not d3 or not ctrl:
        st = "; ".join(s for s, ok in ((f"d3 {NOT_RUN} ({preds_path('d3')})", d3), (f"control {NOT_RUN} ({preds_path(CONTROL)})", ctrl)) if not ok)
        prs = _num((tp or {}).get("items_per_s"))
        out["R10-H4"] = {"pass": None, "status": st, "bar": BARS["R10-H4"],
                         "throughput_clause": None if prs is None else bool(prs >= H4_PRS - EPS)}
        return out
    da = {q: v4s["rows"]["d3"]["questions"][q]["auc"] for q in DECISIONS_V4}
    ca = {q: v4s["rows"][CONTROL]["questions"][q]["auc"] for q in DECISIONS_V4}
    prs = _num((tp or {}).get("items_per_s"))
    cl = h4_clauses(da, ca, prs)
    out.update(auc={q: {"d3": da[q], "control": ca[q], "delta": None if da[q] is None or ca[q] is None else da[q] - ca[q],
                        "within": cl["per_question"][q]} for q in DECISIONS_V4},
               agreement=agreement(d3, ctrl))
    st = []
    if prs is None:
        st.append(f"throughput {NOT_RUN} ({RUN_DIR / 'd3' / 'throughput.json'})")
    elif (tp or {}).get("concurrency") not in (None, PROTOCOL_C):
        st.append(f"timed at concurrency {tp.get('concurrency')} (§R10.3 names {PROTOCOL_C}; CHANGELOG 2026-11-08)")
    p = _all([cl["auc_clause"], cl["throughput_clause"]])
    out["R10-H4"] = {"pass": p, "n_within": cl["n_within"], "auc_clause": cl["auc_clause"], "throughput_clause": cl["throughput_clause"],
                     "prs_per_s": prs, "bar": BARS["R10-H4"], **({"status": "; ".join(st)} if st else {})}
    return out


# ----------------------------------------------------------------- report
def missing_inputs(L: Loaded) -> list[str]:
    miss = []
    for row, r in ROWS.items():
        for part in r["parts"]:
            if part == "v4_val":
                ok = bool(L.v4("d3", "val"))
            elif part == "v4_test":
                ok = bool(L.v4(row))
            else:
                ok = bool(L.rel(row))
            if not ok:
                miss.append(f"{row}/{part}")
    for f in ("throughput.json", "serve_meta.json"):
        if L.inp.json(RUN_DIR / "d3" / f) is None:
            miss.append(f"d3/{f}")
    return miss


def build(root: Path = Path(".")) -> dict:
    inp = Inputs(root)
    L = Loaded(inp)
    y, src = RR.iter_labels(inp, *[L.v4(r) for r in V4_ROWS], L.v4("d3", "val"))
    rep = {"computed_at": _now(), "bootstrap": {"n": BOOT_N, "seed": BOOT_SEED}, "label": RR.ITER, "label_source": src}
    rep["served"] = {"d3": inp.json(RUN_DIR / "d3" / "serve_meta.json"), "warm": inp.json(RUN_DIR / "d3" / "warm.json")}
    dates = {row: utc_dates((L.test.get(row) or []) + (L.val.get(row) or [])) for row in ROWS}
    alld = sorted({x for v in dates.values() for x in v})
    rep["dates"] = {"rows": dates, "all": alld, "one_utc_day": (len(alld) == 1) if alld else None}
    rep["v4"] = v4_section(L, y)
    rep["h1"] = h1(L)
    rep["h2"] = h2(L, rep["v4"], y)
    rep["h3"] = h3(L, rep["h1"], rep["h2"])
    rep["h4"] = h4(L, rep["v4"], y)
    rep["hypotheses"] = {f"R10-H{i}": rep[f"h{i}"][f"R10-H{i}"] for i in (1, 2, 3, 4)}
    rep["missing"] = missing_inputs(L)
    rep["inputs"] = [{"path": p, "sha256": s} for p, s in sorted(inp.seen.items())]
    return rep


def _cell(x) -> str:
    return str(x).replace("|", "\\|")


def _verdict(v: dict) -> str:
    return "n/a" if v.get("na") else _mark(v.get("pass"))


def _usd(x, nd=5) -> str:
    return "—" if x is None else f"${x:.{nd}f}"


def _yn(p) -> str:
    return "pending" if p is None else ("yes" if p else "no")


def _auc_cell(e: dict) -> str:
    return "—" if not e or e.get("auc") is None else f"{_f(e['auc'])} {_ci(e['ci95'])}"


def _v4_tables(v: dict) -> list[str]:
    cols = [r for r in V4_ROWS] + ["run8_pplx_flash_reference"]
    names = {"control": "control (pplx-flash)", "full": "full (pplx)", "d3": "d3-flash", "run8_pplx_flash_reference": "run 8 pplx-flash (reference)"}
    get = lambda c: v["rows"][c] if c in v["rows"] else v[c]
    L = ["## v4 questions, test split (AUC vs `iterated`, 95% bootstrap CI, 1,000 resamples)", "",
         "| question | " + " | ".join(names[c] for c in cols) + " |", "|---|" + "---|" * len(cols)]
    for q in DECISIONS_V4:
        L.append(f"| {q} | " + " | ".join(_auc_cell((get(c).get("questions") or {}).get(q)) if get(c)["status"] == "ok" else NOT_RUN for c in cols) + " |")
    L.append("| **mean (8)** | " + " | ".join(_f(get(c).get("mean_auc")) if get(c)["status"] == "ok" else "—" for c in cols) + " |")
    L.append("| n PRs | " + " | ".join(str(get(c).get("n", "—")) for c in cols) + " |")
    L.append("| served as | " + " | ".join(_cell(", ".join(get(c).get("versions") or []) or "—") for c in cols) + " |")
    return L + [""]


def _h1_tables(c: dict) -> list[str]:
    L = ["## R10-H1 — competing and same-area (relation2, run 8's rule + metadata branch)", ""]
    if c["status"] != "ok":
        return L + [f"**{c['status']}**", ""]
    L += [f"{c['n_pairs']} pairs · gold: " + ", ".join(f"{k} {v}" for k, v in c["gold_classes"].items()) + f" · {c['rule']}", "",
          "| row | n | missing | class accuracy [95% CI] | competing | same_area | competing + same_area | same_change vs rest AUC | served as |",
          "|---|---|---|---|---|---|---|---|---|"]
    for row in REL_ROWS:
        e = c["rows"][row]
        if e["status"] != "ok":
            L.append(f"| {row} | {e['status']} | | | | | | | |")
            continue
        pc = e["per_class"]
        cs = e["competing_same_area"]
        L.append(f"| {row} | {e['n']} | {e['missing']} | {_f(e['accuracy'])} {_ci(e['accuracy_ci95'])} | {pc['competing']['correct']}/{pc['competing']['n']} | "
                 f"{pc['same_area']['correct']}/{pc['same_area']['n']} | **{cs['correct']}/{cs['n']} = {_f(cs['accuracy'])}** | "
                 f"{_f(e['same_change_vs_rest_auc'])} | {_cell(', '.join(e['versions']) or '—')} |")
    for m, e in c["run8_reference"].items():
        L.append(f"| run 8 {m} (reference, 2026-10-25) | | | {_f(e['accuracy'])} | | | {_f(e['competing_same_area'])} | | |")
    L += ["", "| row | competing + same_area ≥ 0.40 | class accuracy ≥ control − 0.02 | result |", "|---|---|---|---|"]
    for row, v in c["per_row"].items():
        if "clauses" not in v:
            L.append(f"| {row} | {v.get('status')} | | pending |")
            continue
        L.append(f"| {row} | {_f(v['competing_same_area']['accuracy'])} → {_mark(v['clauses']['competing_same_area'])} | "
                 f"{_f(v['accuracy'])} vs {_f(v['control_accuracy'])} − 0.02 → {_mark(v['clauses']['class_accuracy_vs_control'])} | **{_mark(v['pass'])}** |")
    for row in REL_ROWS:
        e = c["rows"][row]
        if e["status"] == "ok":
            L += ["", f"### {row}: confusion (rows gold, columns predicted)", "", "| gold \\ pred | " + " | ".join(M8.CLASSES) + " |",
                  "|---|" + "---|" * len(M8.CLASSES)]
            L += [f"| {g} | " + " | ".join(str(e["confusion"][g][p]) for p in M8.CLASSES) + " |" for g in M8.CLASSES]
    h = c["R10-H1"]
    return L + ["", f"**R10-H1: {_verdict(h)}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]


def _h2_tables(c: dict) -> list[str]:
    L = ["## R10-H2 — needs_human_review", "", "| row | AUC | control AUC | Δ | Δ same PRs | 95% CI (paired) | n paired | result |",
         "|---|---|---|---|---|---|---|---|"]
    for row, e in c["per_row"].items():
        if e["status"] != "ok":
            L.append(f"| {row} | {e['status']} | | | | | | {_mark(e['pass'])} |")
            continue
        L.append(f"| {row} | {_f(e['auc'])} | {_f(e['control_auc'])} | **{_d(e['delta'])}** | {_d(e['delta_same_prs'])} | {_ci(e['ci95'])} | "
                 f"{e['n_paired']} | **{_mark(e['pass'])}** |")
    h = c["R10-H2"]
    return L + ["", f"**R10-H2: {_verdict(h)}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]


def _h3_tables(c: dict) -> list[str]:
    L = ["## R10-H3 — cost and speed per PR (main call + ten pair calls)", "",
         "| row | formula | c_main | t_main | t_pair | per-PR cost | × control | ≤ 5× | passes H1 or H2 | ships | p50 latency ms | spent |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row, e in c["rows"].items():
        extra = f" ({_f(e.get('prs_per_s'), 2)} PR/s at c {e.get('concurrency')}, ${_f(e.get('usd_per_hour'), 2)}/h)" if row == "d3" else ""
        L.append(f"| {row} | {e['formula']}{extra} | {_usd(e.get('c_main'), 6)} | {_f(e['t_main'], 0)} | {_f(e['t_pair'], 0)} | **{_usd(e['per_pr_usd'])}** | "
                 f"{_f(e['ratio_to_control'], 2)} | {'—' if row == CONTROL else _mark(e['within_5x'])} | {'—' if row == CONTROL else _yn(e['passes_h1_or_h2'])} | "
                 f"{'—' if row == CONTROL else _yn(e['ships'])} | "
                 f"{_f(e['p50_latency_ms'], 0)}{' (' + e['latency_of'] + ')' if e['latency_of'] and e['latency_of'] != 'v4 main call' else ''} | "
                 f"{_usd(e['spent_usd'], 4) if row != 'd3' else '(GPU-hours)'} |")
    h = c["R10-H3"]
    return L + ["", "c_main: mean cost_usd per v4 answer (hosted; list prices as recorded per answer) or GPU $/h ÷ 3600 ÷ PR/s (d3); "
                    "t_main / t_pair: median input tokens of the row's v4 / relation2 answers; the ten pair calls are an estimate from "
                    "the main call's price per token (haiku: measured pair cost). p50 latency of the d3 row includes queueing at its "
                    "concurrency.", "",
                f"**R10-H3: {_verdict(h)}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]


def _h4_tables(c: dict) -> list[str]:
    L = ["## R10-H4 — d3-flash (self-hosted BF16) vs the hosted control", ""]
    if "auc" in c:
        L += ["| question | d3 AUC | control AUC | Δ | ≥ control − 0.02 | agreement | mean \\|Δp\\| | mean Δp (d3 − control) |",
              "|---|---|---|---|---|---|---|---|"]
        ag = c["agreement"]["per_question"]
        for q in DECISIONS_V4:
            a = c["auc"][q]
            L.append(f"| {q} | {_f(a['d3'])} | {_f(a['control'])} | {_d(a['delta'])} | {_mark(a['within'])} | {_f(ag[q]['agreement'])} | "
                     f"{_f(ag[q]['mean_abs_dp'], 4)} | {_d(ag[q]['mean_dp'], 4)} |")
        L += [f"| **all** | | | | **{c['R10-H4']['n_within']}/8** | {_f(c['agreement']['agreement'])} | {_f(c['agreement']['mean_abs_dp'], 4)} | |", ""]
    tp = c.get("throughput")
    L.append("Throughput (timed v4 test pass): " + (NOT_RUN if not tp else
             f"{tp.get('n_items')} PRs in {_f(tp.get('wall_s'), 1)} s = **{_f(tp.get('items_per_s'), 3)} PR/s** (errors {tp.get('errors')}, "
             f"concurrency {tp.get('concurrency')})") + f"; bar ≥ {H4_PRS} PR/s.")
    cm = c["calibration_map"]
    L += ["", f"Calibration map (isotonic on d3 val vs `iterated`, no bar): {cm['status']}, {cm['n_val']} val PRs."]
    if cm["status"] == "ok":
        L += ["", "| question | n val | test ECE raw | test ECE mapped |", "|---|---|---|---|"]
        L += [f"| {q} | {e['n_val']} | {_f(e['ece_raw'])} | {_f(e['ece_mapped'])} |" for q, e in cm["questions"].items()]
    h = c["R10-H4"]
    return L + ["", f"**R10-H4: {_verdict(h)}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]


def tables(rep: dict) -> str:
    L = ["# Run 10 — report tables", "", f"computed {rep['computed_at']} · `python -m mergeone.eval.run10_report` · label `{rep['label']}` "
         f"from {rep['label_source']} · bootstrap seed {rep['bootstrap']['seed']}, {rep['bootstrap']['n']} resamples", "",
         "## Verdicts", "", "| hypothesis | result | bar (PROTOCOL.md §R10.3) |", "|---|---|---|"]
    for h, v in rep["hypotheses"].items():
        L.append(f"| {h} | {_verdict(v)}" + (f" ({_cell(v['status'])})" if v.get("status") else "") + f" | {_cell(v['bar'])} |")
    d = rep["dates"]
    L += ["", "Every comparison is against the same-day hosted pplx-flash control (results/run10/control); run-8 numbers are "
              "shown beside it, never instead of it.", "",
          "UTC dates per row: " + "; ".join(f"{r} {', '.join(v) or '—'}" for r, v in d["rows"].items())
          + f" · one UTC day: {d['one_utc_day']}" + ("" if d["one_utc_day"] is not False else " — **more than one day (§R10.5 asks for one); reported, not re-scored**"), ""]
    s = rep["served"]["d3"]
    if s:
        L += [f"d3 served: {s.get('weights')} @ {(s.get('revision') or '—')[:12]} as `{s.get('served_as')}` on {s.get('gpu_name')}, "
              f"{s.get('dtype')}, batch size {s.get('batch_size')}, ${s.get('usd_per_hour')}/h ({s.get('vm_type')}), started {s.get('started_at')}", ""]
    if rep["missing"]:
        L += ["Not run yet: " + ", ".join(rep["missing"]), ""]
    L += _v4_tables(rep["v4"]) + _h1_tables(rep["h1"]) + _h2_tables(rep["h2"]) + _h3_tables(rep["h3"]) + _h4_tables(rep["h4"])
    L += ["## Inputs", "", "| path | sha256 |", "|---|---|"] + [f"| {i['path']} | `{i['sha256'][:16]}` |" for i in rep["inputs"]]
    return "\n".join(L) + "\n"


def summary(rep: dict) -> str:
    s = "\n".join(f"{h}: {_verdict(v)}" + (f" — {v['status']}" if v.get("status") else "") for h, v in rep["hypotheses"].items())
    return s + ("\nmissing: " + ", ".join(rep["missing"]) if rep["missing"] else "\nall inputs present")


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
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run10_report", description="run-10 report: R10-H1…H4 (no model call)")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args(argv)
    run(Path(a.root), Path(a.out_dir) if a.out_dir else None)


if __name__ == "__main__":
    main()
