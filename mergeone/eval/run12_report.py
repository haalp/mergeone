"""Run 12 report: R12-H1 tables and verdict (PROTOCOL.md R12.0b, R12.1). No model call, no key.

  python -m mergeone.eval.run12_report [--root .]   → results/run12/metrics.json, results/run12/tables.md

Inputs:
  results/run12/full/predictions_pplx-flash_holdout2_all.jsonl      the same-day full-state control (qset v4 rows)
  results/run12/focused/predictions_pplx-flash_holdout2_all.jsonl   the focused rendering, the same PRs
  data/run12/h1_full.jsonl                                           labels (iterated) and each PR's origin
  data/run12/h1_renderings.json                                      how many PRs, positives and differing states

R12-H1, written on intervals (R12.0b): Δ = focused − control, paired bootstrap over the PRs both answered.
  primary   (non-inferiority)  lower end of the 95 % CI of the eight-question mean Δ ≥ −m₁
  secondary (superiority)      lower end of the 95 % CI of `tests_cover_change` Δ > 0; reported either way, gates only the default
  shipping  `focused` becomes selectable when the primary passes, the default only when the secondary passes too
m₁ is H1_MARGIN below. It is None until the freeze commit writes the number and its arithmetic into PROTOCOL.md; with
None the builder reports every measured number and no verdict. A point estimate alone never decides.
R12.0c: the control's median input tokens are compared with the previous run's control; more than 2× either way is flagged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..models.base import DECISIONS_V4
from . import run7_report as RR
from .run7_report import Inputs, NOT_RUN, _ci, _d, _f, _mark, _now, compare_v4, latest, tokens
from .run8_report import baseline_of

RUN_DIR = Path("results/run12")
MODEL = "pplx-flash"
SPLIT = "holdout2"
CTRL = RUN_DIR / "full" / f"predictions_{MODEL}_{SPLIT}_all.jsonl"
TREAT = RUN_DIR / "focused" / f"predictions_{MODEL}_{SPLIT}_all.jsonl"
DATASET = Path("data/run12/h1_full.jsonl")
RENDERINGS = Path("data/run12/h1_renderings.json")
SECONDARY_Q = "tests_cover_change"

H1_MARGIN: float | None = None          # m₁; set once, in the freeze commit, never after results exist
PREV_CONTROL_TOKENS = 2209.0            # run 11's control, median input tokens per joint request (results/run11/README.md)
NOT_FROZEN = "not frozen (m₁ is not set; PROTOCOL.md R12.1)"


def bars() -> dict:
    m = "m₁ (not set)" if H1_MARGIN is None else f"{H1_MARGIN:.3f}"
    return {"R12-H1": f"focused − same-day full control on holdout-2: lower end of the paired 95 % CI of the mean v4 AUC Δ ≥ −{m}",
            "R12-H1 secondary": f"`{SECONDARY_Q}`: lower end of the paired 95 % CI of Δ > 0 (does not gate shipping as selectable)"}


def _labels(inp: Inputs, *row_sets: dict[int, dict]) -> tuple[dict[int, int], dict[int, str], str]:
    ds = inp.jsonl(DATASET)
    if ds is not None:
        return ({r["pr_number"]: int(bool(r["labels"][RR.ITER])) for r in ds}, {r["pr_number"]: r.get("origin", "?") for r in ds}, str(DATASET))
    y = {}
    for rows in row_sets:
        y.update({k: int(bool(r["labels"][RR.ITER])) for k, r in rows.items() if RR.ITER in (r.get("labels") or {})})
    return y, {}, "prediction rows' labels (dataset not on disk)"


def h1(inp: Inputs) -> dict:
    treat = latest(inp.jsonl(TREAT), "v4")
    if not treat:
        return {"status": f"{NOT_RUN} ({TREAT} has no v4 rows)"}
    ctrl = latest(inp.jsonl(CTRL), "v4")
    if not ctrl:
        return {"status": f"control {NOT_RUN} ({CTRL} has no v4 rows)"}
    y, origin, src = _labels(inp, treat, ctrl)
    out = {"status": "ok", "label_source": src, "versions_focused": RR.versions(treat), "versions_control": RR.versions(ctrl),
           **compare_v4(treat, ctrl, y, baseline_of(ctrl, y))}
    common = sorted(k for k in treat if k in ctrl and k in y)
    out["n"] = {"control": len(ctrl), "focused": len(treat), "paired": len(common), "paired_positives": int(sum(y[k] for k in common))}
    out["by_origin"] = {o: {"paired": sum(1 for k in common if origin.get(k) == o), "positives": int(sum(y[k] for k in common if origin.get(k) == o))}
                        for o in sorted(set(origin.values()))}
    out["renderings"] = inp.json(RENDERINGS)
    tf, tc = tokens(treat, common or None), tokens(ctrl, common or None)
    ratio_prev = None if not (tc and tc["median"]) else tc["median"] / PREV_CONTROL_TOKENS
    out["tokens"] = {"focused": tf, "control": tc, "ratio_median": None if not (tf and tc and tc["median"]) else tf["median"] / tc["median"],
                     "control_vs_previous_run": ratio_prev, "previous_run_control_median": PREV_CONTROL_TOKENS,
                     "moved_more_than_2x": None if ratio_prev is None else not (0.5 <= ratio_prev <= 2.0)}
    mean_ci, sec_ci = out["mean"].get("ci95"), out["questions"][SECONDARY_Q].get("ci95")
    out["margin"] = H1_MARGIN
    out["clauses"] = {"primary": None if (H1_MARGIN is None or not mean_ci) else mean_ci[0] >= -H1_MARGIN - RR.EPS,
                      "secondary": None if not sec_ci else sec_ci[0] > 0}
    out["pass"] = out["clauses"]["primary"]
    p, s = out["clauses"]["primary"], out["clauses"]["secondary"]
    out["ships"] = None if p is None else ("nothing" if not p else ("selectable and default" if s else "selectable"))
    out["bar"] = bars()["R12-H1"]
    return out


def build(root: Path = Path(".")) -> dict:
    inp = Inputs(root)
    h = h1(inp)
    out = {"built_at": _now(), "protocol": "PROTOCOL.md R12.0, R12.1", "R12-H1": h, "inputs": inp.seen}
    if h.get("status") != "ok":
        out["verdicts"] = {"R12-H1": NOT_RUN}
    else:
        out["verdicts"] = {"R12-H1": NOT_FROZEN if H1_MARGIN is None else _mark(h["pass"])}
    return out


def tables(c: dict) -> str:
    b = bars()
    L = ["# Run 12 — tables", "", f"Built {c['built_at']} by `python -m mergeone.eval.run12_report`; every number from `results/run12/` and `data/run12/`.", "",
         "| | bar | verdict |", "|---|---|---|", f"| R12-H1 | {b['R12-H1']} | {c['verdicts']['R12-H1']} |",
         "", f"## R12-H1 — focused rendering vs same-day full-state control ({MODEL}, holdout-2, v4)", ""]
    h = c["R12-H1"]
    if h.get("status") != "ok":
        return "\n".join(L + [h.get("status", NOT_RUN)]) + "\n"
    n = h["n"]
    L += [f"{n['paired']} PRs answered by both ({n['paired_positives']} `iterated` positives); control {n['control']}, focused {n['focused']}.",
          "by origin: " + "; ".join(f"{o} {v['paired']} PRs, {v['positives']} positives" for o, v in h["by_origin"].items()) if h["by_origin"] else "by origin: —"]
    r = h.get("renderings")
    if r:
        L.append(f"renderings: {r['rows']} PRs, the two states differ for {r['states_differ']} ({r['states_differ_among_positives']} of {r['iterated_positives']} positives); left out {r.get('dropped') or {}}.")
    L += ["", "| question | control AUC | focused AUC | Δ | paired 95 % CI |", "|---|---|---|---|---|"]
    for q in DECISIONS_V4:
        v = h["questions"][q]
        L.append(f"| `{q}`{' (secondary)' if q == SECONDARY_Q else ''} | {_f(v['baseline'])} | {_f(v['auc'])} | {_d(v['delta'])} | {_ci(v.get('ci95'))} |")
    m = h["mean"]
    L.append(f"| **mean (8)** | {_f(m['baseline'])} | {_f(m['auc'])} | {_d(m['delta'])} | {_ci(m.get('ci95'))} |")
    t = h["tokens"]
    L += ["", f"input tokens per PR (median, same PRs): control {_f((t['control'] or {}).get('median'), 0)}, focused {_f((t['focused'] or {}).get('median'), 0)}, "
              f"ratio {_f(t.get('ratio_median'), 3)} (reported; same budget, no clause)",
          f"R12.0c: control median vs run 11's control ({_f(t['previous_run_control_median'], 0)}): ×{_f(t.get('control_vs_previous_run'), 2)}"
          f"{' — moved more than 2×, cost ratios are same-day only' if t.get('moved_more_than_2x') else ''}",
          "", f"primary: lower end of the mean's CI {_f((m.get('ci95') or [None])[0])} against −m₁ = "
              f"{'not set' if h['margin'] is None else _d(-h['margin'])} → {NOT_FROZEN if h['margin'] is None else _mark(h['clauses']['primary'])}",
          f"secondary: lower end of `{SECONDARY_Q}`'s CI {_f((h['questions'][SECONDARY_Q].get('ci95') or [None])[0])} against 0 → {_mark(h['clauses']['secondary'])}",
          f"ships: {h['ships'] if h['ships'] is not None else '—'}"]
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
