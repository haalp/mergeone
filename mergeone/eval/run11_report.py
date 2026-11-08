"""Run 11 report: R11-H1…H3 tables and verdicts (PROTOCOL.md §R11.3). No model call, no key.

  python -m mergeone.eval.run11_report [--root .]   → results/run11/metrics.json, results/run11/tables.md

Inputs:
  results/run11/main/predictions_pplx-flash_test_all.jsonl           the same-day full-state control (qset v4 rows)
  results/run11/main/predictions_pplx-flash_test_all_compact.jsonl   the compact main call (qset v4 rows)
  results/run11/prefilter.json                                         R11-H2 (mergeone.eval.run11_prefilter)
  data/run3/dataset.jsonl                                              labels (iterated), as run 5–10

R11-H1: Δ = compact − control on the eight v4 questions (each side's AUC on its answered rows, paired bootstrap on
the PRs both answered, as run 8's h1_v4); pass = mean Δ ≥ −0.02 and every question Δ ≥ −0.03 and median input
tokens per PR (compact) ≤ 0.75 × control (same PRs).
R11-H2: read from prefilter.json (the verdict is computed there, from run 9's recorded answers).
R11-H3: conditional; reported as "not run" until results/run11/recalibration.json exists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..models.base import DECISIONS_V4
from . import run7_report as RR
from .run7_report import Inputs, NOT_RUN, _ci, _d, _f, _mark, _now, compare_v4, latest, tokens
from .run8_report import baseline_of

RUN_DIR = Path("results/run11")
MAIN_DIR = RUN_DIR / "main"
PREFILTER = RUN_DIR / "prefilter.json"
RECAL = RUN_DIR / "recalibration.json"
MODEL = "pplx-flash"
H1_MEAN, H1_EACH, H1_TOKENS = -0.02, -0.03, 0.75

BARS = {
    "R11-H1": "compact main call − same-day full-state control: mean v4 AUC Δ ≥ −0.02, every question Δ ≥ −0.03, median input tokens ≤ 0.75× control",
    "R11-H2": "primary score `retrieval`, k' ∈ {3, 5}: recall of the adjudicated duplicate ≥ 0.95 and dup-vs-rest AUC ≥ k'=10 − 0.02 (run 9's recorded answers)",
    "R11-H3": "conditional on ≥ 200 outcomes: refit ECE ≤ shipped − 0.02 on ≥ 5 of 8 questions, AUC unchanged",
}


def h1(inp: Inputs) -> dict:
    rel_c, rel_f = MAIN_DIR / f"predictions_{MODEL}_test_all_compact.jsonl", MAIN_DIR / f"predictions_{MODEL}_test_all.jsonl"
    comp = latest(inp.jsonl(rel_c), "v4")
    if not comp:
        return {"status": f"{NOT_RUN} ({rel_c} has no v4 rows)"}
    ctrl = latest(inp.jsonl(rel_f), "v4")
    if not ctrl:
        return {"status": f"control {NOT_RUN} ({rel_f} has no v4 rows)"}
    y, src = RR.iter_labels(inp, comp, ctrl)
    out = {"status": "ok", "label_source": src, "versions_compact": RR.versions(comp), "versions_control": RR.versions(ctrl),
           **compare_v4(comp, ctrl, y, baseline_of(ctrl, y))}
    common = [k for k in comp if k in ctrl]
    out["tokens"] = {"compact": tokens(comp, common or None), "control": tokens(ctrl, common or None), "same_prs": bool(common)}
    tc, tf = out["tokens"]["compact"], out["tokens"]["control"]
    ratio = None if not (tc and tf and tf["median"]) else tc["median"] / tf["median"]
    out["tokens"]["ratio_median"] = ratio
    per_ok = [(q, v["delta"]) for q, v in out["questions"].items() if v["delta"] is not None]
    clauses = {"mean": None if out["mean"]["delta"] is None else out["mean"]["delta"] >= H1_MEAN - RR.EPS,
               "each": None if len(per_ok) != len(DECISIONS_V4) else all(d >= H1_EACH - RR.EPS for _, d in per_ok),
               "tokens": None if ratio is None else ratio <= H1_TOKENS + RR.EPS}
    out["clauses"] = clauses
    out["pass"] = None if any(v is None for v in clauses.values()) else all(clauses.values())
    out["bar"] = BARS["R11-H1"]
    out["worst_question"] = min(per_ok, key=lambda t: t[1]) if per_ok else None
    return out


def h2(inp: Inputs) -> dict:
    p = inp.root / PREFILTER
    if not p.exists():
        return {"status": f"{NOT_RUN} ({PREFILTER} missing; python -m mergeone.eval.run11_prefilter)"}
    inp._path(PREFILTER)
    rep = json.loads(p.read_text())
    v = rep.get("verdict") or {}
    return {"status": "ok", "pass": v.get("pass"), "ships_k": v.get("ships_k"), "primary": v.get("primary"), "control": rep.get("control"),
            "scores": rep.get("scores"), "groups": rep.get("groups"), "labels": rep.get("labels"), "bar": BARS["R11-H2"]}


def h3(inp: Inputs) -> dict:
    p = inp.root / RECAL
    if not p.exists():
        return {"status": f"{NOT_RUN} (conditional: waits for ≥ 200 outcomes; {RECAL} missing)", "pass": None, "bar": BARS["R11-H3"]}
    inp._path(RECAL)
    rep = json.loads(p.read_text())
    return {"status": "ok", **rep, "bar": BARS["R11-H3"]}


def build(root: Path = Path(".")) -> dict:
    inp = Inputs(root)
    out = {"built_at": _now(), "protocol": "PROTOCOL.md §R11", "R11-H1": h1(inp), "R11-H2": h2(inp), "R11-H3": h3(inp)}
    out["inputs"] = inp.seen
    out["verdicts"] = {k: _mark(out[k].get("pass")) if out[k].get("status") == "ok" else NOT_RUN for k in ("R11-H1", "R11-H2", "R11-H3")}
    return out


def tables(c: dict) -> str:
    L = ["# Run 11 — tables", "", f"Built {c['built_at']} by `python -m mergeone.eval.run11_report`; every number from `results/run11/`.", "",
         "| | bar | verdict |", "|---|---|---|"]
    for k in ("R11-H1", "R11-H2", "R11-H3"):
        L.append(f"| {k} | {BARS[k]} | {c['verdicts'][k]} |")
    L += ["", "## R11-H1 — compact main call vs same-day full-state control (pplx-flash, test, v4)", ""]
    h = c["R11-H1"]
    if h.get("status") != "ok":
        L.append(h.get("status", NOT_RUN))
    else:
        L += ["| question | control AUC | compact AUC | Δ | paired 95 % CI |", "|---|---|---|---|---|"]
        for q in DECISIONS_V4:
            v = h["questions"][q]
            L.append(f"| `{q}` | {_f(v['baseline'])} | {_f(v['auc'])} | {_d(v['delta'])} | {_ci(v.get('ci95'))} |")
        m = h["mean"]
        L.append(f"| **mean (8)** | {_f(m['baseline'])} | {_f(m['auc'])} | {_d(m['delta'])} | {_ci(m.get('ci95'))} |")
        t = h["tokens"]
        L += ["", f"input tokens per PR (median, same PRs): control {_f((t['control'] or {}).get('median'), 0)}, compact "
                  f"{_f((t['compact'] or {}).get('median'), 0)}, ratio {_f(t.get('ratio_median'), 3)} (bar ≤ 0.75)",
              f"clauses: mean {h['clauses']['mean']} · every question ≥ −0.03 {h['clauses']['each']} (worst {h['worst_question']}) · tokens {h['clauses']['tokens']}"]
    L += ["", "## R11-H2 — pair-call prefilter on run 9's recorded set-(b) answers (no model call)", ""]
    h = c["R11-H2"]
    if h.get("status") != "ok":
        L.append(h.get("status", NOT_RUN))
    else:
        L += [f"{h['groups']} adjudicated A's ({h['labels']}); control k' = 10: dup-vs-rest AUC {_f((h['control'] or {}).get('auc_dup_rest'))}, "
              f"{(h['control'] or {}).get('calls_per_a')} pair calls per A", "",
              "| score | k' | recall of the duplicate | dup-vs-rest AUC | Δ vs k'=10 | pass |", "|---|---|---|---|---|---|"]
        for name, per_k in (h["scores"] or {}).items():
            for k, r in per_k.items():
                L.append(f"| {name}{' (primary)' if name == h['primary'] else ''} | {k} | {_f(r['recall_dup'])} | {_f(r['auc_dup_rest'])} | {_d(r['delta_auc'])} | {_mark(r['pass'])} |")
        L += ["", f"ships: k' = {h['ships_k']} ({h['primary']})" if h["pass"] else "ships: nothing (ten pair calls stay)"]
    L += ["", "## R11-H3 — team recalibration (conditional)", "", c["R11-H3"].get("status", NOT_RUN)]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    root = Path(a.root)
    c = build(root)
    (root / RUN_DIR).mkdir(parents=True, exist_ok=True)
    (root / RUN_DIR / "metrics.json").write_text(json.dumps(c, indent=2, default=str))
    (root / RUN_DIR / "tables.md").write_text(tables(c))
    print(" ".join(f"{k} {v}" for k, v in c["verdicts"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
