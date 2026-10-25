"""Run 8 report: R8-H1…H5 tables and verdicts (PROTOCOL.md §R8.3; decisions in §R8.6). No model call, no key.

  python -m mergeone.eval.run8_report [--root .] [--out-dir results/run8]
        → results/run8/tables.md (markdown tables + verdict lines), results/run8/metrics.json (every number, with the
          path and sha256 of each input read); a short summary on stdout. results/run8/README.md is written by hand.

Every section degrades: an input that is not on disk makes that section (or half of it) say "not run yet", so the report
can be regenerated after each step of R8.5. Paths are relative to --root (the repository root). Loaders, the AUC, the
paired bootstrap (seed 7, 1,000 resamples), compare_v3 / compare_v4 and the token statistics are run7_report's.

Inputs (docs/run8-tooling.md §2–4)
  H1  results/run8/pair/predictions_<m>_test_all_compact.jsonl     pair treatment (qset pair, `--compact`)
      results/run8/pair/predictions_<m>_test_all.jsonl             pair same-day full-state control
      results/run6/pairs_eval_test.jsonl                           the 187 R4-H2 test pair ids; label `labels.positive`
      results/run4/metrics.json                                    run 4's pair_test AUC, shown beside (reference only)
      results/run8/v4/predictions_<m>_test_all_compact.jsonl       v4 treatment (qset v4, `--compact`) — not in the
                                                                   run-8 tooling order; reported "not run" when absent
      results/run8/v4/predictions_<m>_test_all.jsonl               its control: qset v4 rows, full state, same day
  H2  results/run8/relation_metrics.json                           run8_relation report (presented, not recomputed)
  H3  results/run8/v4/predictions_<m>_test_all.jsonl               qset v4 (treatment) and qset v3 (same-day control)
      results/run5/metrics.json                                    run 5 test_raw_vs_iterated per question
      results/run5/predictions_<m>_test_all.jsonl                  run-5 v3 rows (paired bootstrap control vs run 5)
      data/run3/dataset.jsonl                                      `iterated` labels (rows' own labels if absent)
  H4  results/run8/c4/                                             deferred unless the directory exists
  H5  results/run8/drift_replay.json   `mergeone drift-replay --json` outputs, saved as {label: report} (optionally under
                                       "comparisons"); a label containing "6A"/"self-hosted" is the run-6A row, else
                                       "jev" or "pplx-flash" names the model (pplx-flash = run 5 → run 7)
      results/run8prep/drift.json      the Python reference (scripts/run8_prep.py); used when the Go replay is absent,
                                       and for the AUC deltas of the false-positive clause (the Go output has none)
      results/run8/drift_slow.json     slow-signal output {label: {"flagged": [...]}}; "not implemented" when absent

Scoring (fixed here, before any run-8 number is scored)
  * R8-H1, per model: Δ = compact − same-day control, each side's AUC on all its answered rows (as run 7's verdict
    computation); Δ on the rows answered in both and its paired-bootstrap CI beside it. v4: mean over the eight v4
    questions vs `iterated` (compare_v4, the control's own AUC as the baseline). pair: same_change vs labels.positive on
    the eval ids. Bar: both Δ ≥ −0.02, both models. Tokens: median / p90 input tokens on the rows answered in both.
  * R8-H2: per model, (a) class accuracy ≥ 0.80, (b) competing + same_area combined accuracy (correct / n over the two gold
    classes) ≥ 0.40, (c) same_change-vs-rest AUC ≥ run 7's number for that model − 0.02 (run 7: 0.728 pplx-flash, 0.685
    jev, §R8.3). Pass = at least one model meets (a), (b) and (c) ("for that model" binds (c) to the model meeting (a)).
  * R8-H3: no bar. Same-day v3 control − run 5 per question on the eight (compare_v4 against run 5's metrics, paired with
    run 5's rows), and v4 treatment − v3 control on the same eight (control's own AUC as the baseline); mean v4.
  * R8-H5: fast signal = the degraded list (shift > 0.20 or spread halved, n ≥ 200). Pass needs: every pplx-flash
    run 5 → run 7 comparison flags exactly {follows_conventions, message_matches_diff}; every jev and run-6A comparison
    flags nothing; no flagged question has |AUC delta| < 0.03 on identical rows; the slow signal flags jev
    tests_cover_change. From drift.json the fast list is `trips["0.20"]` (shift only; spread is not in that file).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..models.base import DECISIONS_V4, PRIMARY_V4
from . import run7_report as RR
from .run7_report import Inputs, NOT_RUN, _all, _ci, _d, _f, _mark, _now, _tok, compare_v4, latest, tokens

RUN_DIR = Path("results/run8")
PAIR_DIR = RUN_DIR / "pair"
V4_DIR = RUN_DIR / "v4"
C4_DIR = RUN_DIR / "c4"
RELATION = RUN_DIR / "relation_metrics.json"
DRIFT_REPLAY = RUN_DIR / "drift_replay.json"
DRIFT_SLOW = RUN_DIR / "drift_slow.json"
DRIFT_PREP = Path("results/run8prep/drift.json")
RUN4_DIR, RUN5_DIR = RR.RUN4_DIR, RR.RUN5_DIR
PAIR_KEYS = RR.PAIR_KEYS

MODELS = ("jev", "pplx-flash")
H1_TOL = 0.02
H2_ACC, H2_COMBINED, H2_AUC_TOL = 0.80, 0.40, 0.02
RUN7_SAME_CHANGE_AUC = {"pplx-flash": 0.728, "jev": 0.685}   # §R8.3
H5_PPLX = frozenset({"follows_conventions", "message_matches_diff"})
H5_FP_AUC = 0.03
H5_SLOW = ("jev", "tests_cover_change")
SLOW_DROP = 0.05   # docs/drift-monitor.md slow signal (shown from drift.json as a reference only)
EPS = RR.EPS

BARS = {
    "R8-H1": "compact − same-day control ≥ −0.02 mean v4 AUC and pair AUC, for jev and pplx-flash",
    "R8-H2": "accuracy ≥ 0.80 for ≥ 1 model and (that model) competing + same_area ≥ 0.40 and same_change AUC ≥ run 7 − 0.02 (0.728 / 0.685)",
    "R8-H3": "no bar; reported",
    "R8-H4": "R6A-H3 and R6A-H4 vs the hosted pplx-flash same-day control (only if C4's candidate is recorded)",
    "R8-H5": "fast signal flags exactly follows_conventions + message_matches_diff for pplx-flash run 5 → 7, nothing on jev / run 6A; "
             "no flag with |AUC Δ| < 0.03; slow signal flags jev tests_cover_change",
}


def _ge(x, bar):
    return None if x is None else x >= bar - EPS


# ----------------------------------------------------------------- H1
def baseline_of(rows: dict[int, dict], y: dict[int, int], qs=DECISIONS_V4) -> dict:
    """A run-5-metrics-shaped baseline from rows (their own AUC per question)."""
    return {"questions": {q: {"test_raw_vs_iterated": RR.auc_of(rows, y, q)[0]} for q in qs}}


def h1_v4(inp: Inputs, m: str) -> dict:
    rel_c, rel_f = V4_DIR / f"predictions_{m}_test_all_compact.jsonl", V4_DIR / f"predictions_{m}_test_all.jsonl"
    comp = latest(inp.jsonl(rel_c), "v4")
    if not comp:
        return {"status": f"{NOT_RUN} ({rel_c} has no v4 rows; not in the run-8 tooling order)"}
    ctrl = latest(inp.jsonl(rel_f), "v4")
    if not ctrl:
        return {"status": f"control {NOT_RUN} ({rel_f} has no v4 rows)"}
    y, src = RR.iter_labels(inp, comp, ctrl)
    out = {"status": "ok", "label_source": src, "versions_compact": RR.versions(comp), "versions_control": RR.versions(ctrl),
           **compare_v4(comp, ctrl, y, baseline_of(ctrl, y))}
    common = [k for k in comp if k in ctrl]
    out["tokens"] = {"compact": tokens(comp, common or None), "control": tokens(ctrl, common or None), "same_prs": bool(common)}
    return out


def h1_pair(inp: Inputs, m: str, keys: set | None, m4: dict | None) -> dict:
    rel_c, rel_f = PAIR_DIR / f"predictions_{m}_test_all_compact.jsonl", PAIR_DIR / f"predictions_{m}_test_all.jsonl"
    comp = latest(inp.jsonl(rel_c), "pair")
    if not comp:
        return {"status": f"{NOT_RUN} ({rel_c})"}
    ctrl = latest(inp.jsonl(rel_f), "pair")
    if not ctrl:
        return {"status": f"control {NOT_RUN} ({rel_f})"}
    if keys is None:
        return {"status": f"not evaluable: {PAIR_KEYS} not present (the R4-H2 test pair ids)"}
    ctrl_auc = RR.pair_cmp(ctrl, {}, keys, None)["auc"]
    r = RR.pair_cmp(comp, ctrl, keys, ctrl_auc)
    r["tokens"] = {"compact": r["tokens"]["new"], "control": r["tokens"]["old"], "same_prs": r["tokens"]["same_prs"]}
    r["run4_reference"] = ((m4 or {}).get("models", {}).get(m, {}).get("pair_test") or {}).get("roc_auc")
    return {"status": "ok", "versions_compact": RR.versions(comp), "versions_control": RR.versions(ctrl), **r}


def h1(inp: Inputs) -> dict:
    ids = inp.jsonl(PAIR_KEYS)
    keys = {r["pr_number"] for r in ids} if ids is not None else None
    m4 = inp.json(RUN4_DIR / "metrics.json")
    out = {"models": {}}
    per = {}
    for m in MODELS:
        v, p = h1_v4(inp, m), h1_pair(inp, m, keys, m4)
        out["models"][m] = {"v4": v, "pair": p}
        dv = (v.get("mean") or {}).get("delta") if v["status"] == "ok" else None
        dp = p.get("delta") if p["status"] == "ok" else None
        per[m] = {"v4_mean_delta": dv, "pair_delta": dp, "pass": _all([_ge(dv, -H1_TOL), _ge(dp, -H1_TOL)])}
    st = [f"{m} v4 compact: {e['v4']['status']}" for m, e in out["models"].items() if e["v4"]["status"] != "ok"]
    st += [f"{m} pair: {e['pair']['status']}" for m, e in out["models"].items() if e["pair"]["status"] != "ok"]
    out["R8-H1"] = {"per_model": per, "pass": _all([v["pass"] for v in per.values()]), "bar": BARS["R8-H1"],
                    **({"status": "; ".join(st)} if st else {})}
    return out


# ----------------------------------------------------------------- H2
def combined(per_class: dict, classes=("competing", "same_area")) -> dict:
    n = sum((per_class.get(c) or {}).get("n", 0) for c in classes)
    k = sum((per_class.get(c) or {}).get("correct", 0) for c in classes)
    return {"n": n, "correct": k, "accuracy": k / n if n else None}


def _rel_row(e: dict) -> dict:
    keep = ("n", "missing", "accuracy", "accuracy_ci95", "same_change_vs_rest_auc", "n_duplicate", "same_change_or_backport_vs_rest_auc",
            "related_vs_unrelated_auc", "n_unrelated", "n_base_unknown", "per_class", "predicted")
    return {**{k: e.get(k) for k in keep}, "competing_same_area": combined(e.get("per_class") or {})}


def h2(inp: Inputs) -> dict:
    m = inp.json(RELATION)
    if m is None:
        return {"status": NOT_RUN, "R8-H2": {"pass": None, "status": f"{RELATION} not present (run8_relation report)", "bar": BARS["R8-H2"]}}
    out = {"status": "ok", "computed_at": m.get("computed_at"), "n_pairs": m.get("n_pairs"), "gold_classes": m.get("gold_classes"),
           "rule": m.get("rule"), "models": {}, "baseline_run7": {}, "control_same_day": {},
           "run7_post_hoc_reference": m.get("run7_post_hoc_reference") or {}}
    for sec in ("models", "baseline_run7", "control_same_day"):
        for k, e in (m.get(sec) or {}).items():
            out[sec][k] = _rel_row(e) if "n" in e else {"status": e.get("status") or NOT_RUN}
    per = {}
    for k in MODELS:
        e = out["models"].get(k)
        if not e or "n" not in e:
            per[k] = {"status": (e or {}).get("status") or NOT_RUN, "pass": None}
            continue
        ref = RUN7_SAME_CHANGE_AUC[k]
        a, c, s = e["accuracy"], e["competing_same_area"]["accuracy"], e["same_change_vs_rest_auc"]
        cl = {"accuracy": _ge(a, H2_ACC), "competing_same_area": _ge(c, H2_COMBINED), "same_change_auc": _ge(s, ref - H2_AUC_TOL)}
        per[k] = {"accuracy": a, "competing_same_area": c, "same_change_auc": s, "run7_same_change_auc": ref,
                  "run7_same_change_auc_file": (out["baseline_run7"].get(k) or {}).get("same_change_vs_rest_auc"),
                  "clauses": cl, "pass": _all(list(cl.values()))}
    vals = [v["pass"] for v in per.values()]
    p = True if any(v is True for v in vals) else (None if any(v is None for v in vals) else False)
    out["R8-H2"] = {"per_model": per, "pass": p, "bar": BARS["R8-H2"],
                    **({} if p is not None else {"status": "relation2 predictions not scored for every model yet"})}
    return out


# ----------------------------------------------------------------- H3
def h3(inp: Inputs) -> dict:
    m5 = inp.json(RUN5_DIR / "metrics.json")
    out = {"models": {}}
    for m in MODELS:
        rel = V4_DIR / f"predictions_{m}_test_all.jsonl"
        rows = inp.jsonl(rel)
        v4, ctrl = latest(rows, "v4"), latest(rows, "v3")
        if not v4 and not ctrl:
            out["models"][m] = {"status": f"{NOT_RUN} ({rel})"}
            continue
        r5 = latest(inp.jsonl(RUN5_DIR / f"predictions_{m}_test_all.jsonl"), "v3")
        y, src = RR.iter_labels(inp, v4, ctrl)
        base5 = (m5 or {}).get("models", {}).get(m)
        e = {"status": "ok", "label_source": src, "n_v4": len(v4), "n_control": len(ctrl), "versions_v4": RR.versions(v4),
             "versions_control": RR.versions(ctrl), "versions_run5": RR.versions(r5)}
        if not ctrl:
            e["control_vs_run5"] = {"status": f"v3 control {NOT_RUN}"}
        elif not base5:
            e["control_vs_run5"] = {"status": f"run-5 baseline missing ({RUN5_DIR / 'metrics.json'} has no {m})"}
        else:
            e["control_vs_run5"] = {"status": "ok", **compare_v4(ctrl, r5, y, base5)}
        if not v4:
            e["v4_vs_control"] = {"status": f"v4 treatment {NOT_RUN}"}
        elif not ctrl:
            e["v4_vs_control"] = {"status": f"v3 control {NOT_RUN}"}
        else:
            c = compare_v4(v4, ctrl, y, baseline_of(ctrl, y))
            common = [k for k in v4 if k in ctrl]
            c["tokens"] = {"v4": tokens(v4, common or None), "control": tokens(ctrl, common or None), "same_prs": bool(common)}
            e["v4_vs_control"] = {"status": "ok", **c}
        out["models"][m] = e
    out["R8-H3"] = {"pass": None, "status": "no bar; reported", "bar": BARS["R8-H3"],
                    "mean_v4": {m: ((e.get("v4_vs_control") or {}).get("mean") or {}).get("auc") for m, e in out["models"].items()}}
    return out


# ----------------------------------------------------------------- H4
def h4(root: Path) -> dict:
    if not (Path(root) / C4_DIR).is_dir():
        return {"status": f"deferred ({C4_DIR}/ not present)", "R8-H4": {"pass": None, "status": "deferred", "bar": BARS["R8-H4"]}}
    files = sorted(str(p.relative_to(root)) for p in (Path(root) / C4_DIR).rglob("*") if p.is_file())
    return {"status": "present; scoring not implemented in this builder", "files": files,
            "R8-H4": {"pass": None, "status": f"{C4_DIR}/ present ({len(files)} files); scoring not implemented here", "bar": BARS["R8-H4"]}}


# ----------------------------------------------------------------- H5
def kind_of(label: str) -> str | None:
    l = label.lower()
    if "6a" in l or "self-hosted" in l:
        return "run6a"
    if "jev" in l:
        return "jev"
    if "pplx-flash" in l:
        return "pplx-flash"
    return None


def _comparisons(d: dict) -> dict:
    d = d.get("comparisons", d) if isinstance(d, dict) else {}
    return {k: v for k, v in d.items() if isinstance(v, dict) and "questions" in v}


def fast_flags(prep: dict | None, replay: dict | None) -> tuple[str, dict]:
    """{label: {"kind", "flagged"}} from the Go replay when present, else from drift.json trips["0.20"]."""
    if replay is not None:
        if "questions" in replay and "degraded" in replay:
            return "go-unlabelled", {}
        return "go", {k: {"kind": kind_of(k), "flagged": sorted(v.get("degraded") or [])} for k, v in _comparisons(replay).items()}
    if prep is not None:
        return "python-reference", {k: {"kind": kind_of(k), "flagged": sorted((v.get("trips") or {}).get("0.20") or [])}
                                    for k, v in _comparisons(prep).items()}
    return "none", {}


def h5(inp: Inputs) -> dict:
    prep, replay, slow = inp.json(DRIFT_PREP), inp.json(DRIFT_REPLAY), inp.json(DRIFT_SLOW)
    source, flags = fast_flags(prep, replay)
    out = {"source": source, "comparisons": flags}
    if source == "none":
        out["R8-H5"] = {"pass": None, "status": f"{NOT_RUN} ({DRIFT_REPLAY} and {DRIFT_PREP} not present)", "bar": BARS["R8-H5"]}
        return out
    if source == "go-unlabelled":
        out["R8-H5"] = {"pass": None, "status": f"{DRIFT_REPLAY} is one unlabelled report; save as {{label: report}}", "bar": BARS["R8-H5"]}
        return out
    prep_c = _comparisons(prep or {})
    auc = {}   # kind → {question: [auc_delta on identical rows]} from drift.json
    for k, v in prep_c.items():
        for q, x in v["questions"].items():
            if x.get("auc_delta") is not None:
                auc.setdefault(kind_of(k), {}).setdefault(q, []).append(x["auc_delta"])
    kinds = {}
    for k, v in flags.items():
        kinds.setdefault(v["kind"], []).append(k)
    clauses = {}
    pp = kinds.get("pplx-flash") or []
    clauses["pplx_exact"] = None if not pp else all(set(flags[k]["flagged"]) == H5_PPLX for k in pp)
    for kd in ("jev", "run6a"):
        ks = kinds.get(kd) or []
        clauses[f"{kd}_nothing"] = None if not ks else all(not flags[k]["flagged"] for k in ks)
    fp = []
    for k, v in flags.items():
        for q in v["flagged"]:
            ds = (auc.get(v["kind"]) or {}).get(q) or []
            if any(abs(d) < H5_FP_AUC for d in ds):
                fp.append(f"{k}: {q}")
    clauses["no_false_positive"] = (not fp) if auc else None
    out["false_positives"] = fp
    # slow signal
    slow_ref = {k: sorted(q for q, x in v["questions"].items() if x.get("auc_delta") is not None and x["auc_delta"] < -SLOW_DROP)
                for k, v in prep_c.items()}
    out["slow_reference_python"] = {"note": f"drift.json AUC delta < −{SLOW_DROP} (reference only; not the slow signal of the verdict)",
                                    "flagged": slow_ref}
    if slow is None:
        out["slow"] = {"status": f"not implemented ({DRIFT_SLOW} not present)"}
        clauses["slow_jev_tests_cover_change"] = None
    else:
        sl = {k: sorted(v.get("flagged") or []) for k, v in slow.get("comparisons", slow).items() if isinstance(v, dict)}
        out["slow"] = {"status": "ok", "flagged": sl}
        jk = [k for k in sl if kind_of(k) == H5_SLOW[0]]
        clauses["slow_jev_tests_cover_change"] = None if not jk else any(H5_SLOW[1] in sl[k] for k in jk)
    p = _all(list(clauses.values()))
    st = []
    if source == "python-reference":
        st.append(f"fast signal from the Python reference ({DRIFT_PREP}, shift > 0.20 only; spread not in that file); "
                  f"Go replay ({DRIFT_REPLAY}) {NOT_RUN}")
    if clauses["slow_jev_tests_cover_change"] is None:
        st.append("slow signal not implemented" if slow is None else "slow signal has no jev comparison")
    out["R8-H5"] = {"pass": p, "clauses": clauses, "bar": BARS["R8-H5"], **({"status": "; ".join(st)} if st else {})}
    return out


# ----------------------------------------------------------------- report
def build(root: Path = Path(".")) -> dict:
    inp = Inputs(root)
    rep = {"computed_at": _now(), "bootstrap": {"n": RR.BOOT_N, "seed": RR.BOOT_SEED},
           "h1": h1(inp), "h2": h2(inp), "h3": h3(inp), "h4": h4(Path(root)), "h5": h5(inp)}
    rep["hypotheses"] = {f"R8-H{i}": rep[f"h{i}"][f"R8-H{i}"] for i in range(1, 6)}
    rep["inputs"] = [{"path": p, "sha256": s} for p, s in sorted(inp.seen.items())]
    return rep


def _verdict(h: str, v: dict) -> str:
    if h == "R8-H3":
        return "reported (no bar)"
    if h == "R8-H4" and v.get("pass") is None:
        return v.get("status") or "deferred"
    return _mark(v.get("pass"))


def _vrows(v: dict, new: str, old: str) -> list[str]:
    L = [f"| question | n | {new} AUC | {old} AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |", "|---|---|---|---|---|---|---|---|"]
    for q, r in v["questions"].items():
        L.append(f"| {q}{' (primary)' if q == PRIMARY_V4 else ''} | {r['n']} | {_f(r['auc'])} | {_f(r['baseline'])} | {_d(r['delta'])} | "
                 f"{_d(r['delta_same_keys'])} | {_ci(r['ci95'])} | {r['n_paired']} |")
    mn = v["mean"]
    L.append(f"| **mean (8)** | | {_f(mn['auc'])} | {_f(mn['baseline'])} | **{_d(mn['delta'])}** | {_d(mn['delta_same_keys'])} | {_ci(mn['ci95'])} | {mn['n_paired']} |")
    return L + ["", "Δ = each side's AUC on all its answered PRs (the verdict computation); Δ same PRs and the CI use the PRs answered in both.", ""]


def _h1_tables(c: dict) -> list[str]:
    L = ["## R8-H1 — compact vs same-day full-state control (test)", ""]
    for m, e in c["models"].items():
        L += [f"### {m}", ""]
        v = e["v4"]
        if v["status"] != "ok":
            L += [f"v4 compact: **{v['status']}**", ""]
        else:
            t = v["tokens"]
            L += [f"v4 (labels: {v['label_source']}; version compact {', '.join(v['versions_compact']) or '—'} · control "
                  f"{', '.join(v['versions_control']) or '—'})", ""] + _vrows(v, "compact", "control")
            L += [f"v4 input tokens per request, median / p90: compact {_tok(t['compact'])} · control {_tok(t['control'])}"
                  + (" (same PRs)" if t["same_prs"] else ""), ""]
        p = e["pair"]
        if p["status"] != "ok":
            L += [f"pair: **{p['status']}**", ""]
        else:
            L += [f"pair (same_change vs labels.positive, {p['n']} of {p['n_eval_pairs']} eval pairs; version compact "
                  f"{', '.join(p['versions_compact']) or '—'} · control {', '.join(p['versions_control']) or '—'}): compact {_f(p['auc'])} vs control "
                  f"{_f(p['baseline'])} → **Δ {_d(p['delta'])}** (same pairs {_d(p['delta_same_keys'])} {_ci(p['ci95'])}, n paired {p['n_paired']}); "
                  f"run 4 full state {_f(p['run4_reference'])} (reference, not the comparison)", "",
                  f"pair input tokens per request, median / p90: compact {_tok(p['tokens']['compact'])} · control {_tok(p['tokens']['control'])}"
                  + (" (same pairs)" if p["tokens"]["same_prs"] else ""), ""]
    h = c["R8-H1"]
    L += ["R8-H1 per model: " + "; ".join(f"{m}: v4 mean Δ {_d(v['v4_mean_delta'])}, pair Δ {_d(v['pair_delta'])} → {_mark(v['pass'])}"
                                         for m, v in h["per_model"].items()), "",
          f"**R8-H1: {_mark(h['pass'])}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]
    return L


def _rel_line(name: str, e: dict) -> str:
    if "n" not in e:
        return f"| {name} | — | {e.get('status')} | | | | |"
    cs = e["competing_same_area"]
    return (f"| {name} | {e['n']} | {_f(e['accuracy'])} {_ci(e['accuracy_ci95'])} | {cs['correct']}/{cs['n']} = {_f(cs['accuracy'])} | "
            f"{_f(e['same_change_vs_rest_auc'])} ({e['n_duplicate']}) | {_f(e['same_change_or_backport_vs_rest_auc'])} | "
            f"{_f(e['related_vs_unrelated_auc'])} ({e['n_unrelated']}) |")


def _h2_tables(c: dict) -> list[str]:
    L = ["## R8-H2 — relation v2 (100 adjudicated 6B pairs)", ""]
    if c["status"] != "ok":
        return L + [f"**{c['status']}**", "", f"**R8-H2: pending** — bar: {c['R8-H2']['bar']}", ""]
    L += [f"{c['n_pairs']} pairs · gold: " + ", ".join(f"{k} {v}" for k, v in (c["gold_classes"] or {}).items())
          + f" · relation_metrics computed {c['computed_at']}", "", f"Rule: {c['rule']}.", "",
          "| row | n | class accuracy [95% CI] | competing + same_area | same_change vs rest AUC (n dup) | same_change+backport vs rest AUC | related vs unrelated AUC (n unrel) |",
          "|---|---|---|---|---|---|---|"]
    for m in MODELS:
        L.append(_rel_line(f"{m} — relation2", c["models"].get(m) or {"status": NOT_RUN}))
    for m in MODELS:
        L.append(_rel_line(f"{m} — same-day control (run-7 question, today, this rule)", c["control_same_day"].get(m) or {"status": NOT_RUN}))
    for m, e in c["baseline_run7"].items():
        L.append(_rel_line(f"{m} — baseline (run-7 answers, this rule)", e))
        ref = c["run7_post_hoc_reference"].get(m)
        if ref:
            L.append(f"| {m} — run-7 post-hoc (run-7 rule, metadata branch; reference) | {ref.get('n')} | {_f(ref.get('accuracy'))} | | | | |")
    h = c["R8-H2"]
    L += ["", "R8-H2 per model: " + "; ".join(
        f"{m}: {v['status']}" if "clauses" not in v else
        f"{m}: accuracy {_f(v['accuracy'])} ({_mark(v['clauses']['accuracy'])}), competing+same_area {_f(v['competing_same_area'])} "
        f"({_mark(v['clauses']['competing_same_area'])}), same_change AUC {_f(v['same_change_auc'])} vs run 7 {_f(v['run7_same_change_auc'])} − {H2_AUC_TOL} "
        f"({_mark(v['clauses']['same_change_auc'])}) → {_mark(v['pass'])}" for m, v in h["per_model"].items()), "",
          f"**R8-H2: {_mark(h['pass'])}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", "",
          "Per-class accuracy and confusion: results/run8/relation_tables.md.", ""]
    return L


def _h3_tables(c: dict) -> list[str]:
    L = ["## R8-H3 — v4 question set (no bar)", ""]
    for m, e in c["models"].items():
        L += [f"### {m}", ""]
        if e["status"] != "ok":
            L += [f"**{e['status']}**", ""]
            continue
        L += [f"labels: {e['label_source']} · versions: v4 {', '.join(e['versions_v4']) or '—'} · control {', '.join(e['versions_control']) or '—'} · "
              f"run 5 {', '.join(e['versions_run5']) or '—'}", ""]
        a = e["control_vs_run5"]
        L += ["**Same-day v3 control vs run 5, on the eight v4 questions** (drift-free movement of each surviving question)", ""]
        L += _vrows(a, "control", "run 5") if a["status"] == "ok" else [f"**{a['status']}**", ""]
        b = e["v4_vs_control"]
        L += ["**v4 treatment vs same-day v3 control, same eight questions**", ""]
        if b["status"] == "ok":
            t = b["tokens"]
            L += _vrows(b, "v4", "v3 control") + [f"input tokens per request, median / p90: v4 {_tok(t['v4'])} · v3 control {_tok(t['control'])}"
                                                    + (" (same PRs)" if t["same_prs"] else ""), ""]
        else:
            L += [f"**{b['status']}**", ""]
    mv = c["R8-H3"]["mean_v4"]
    return L + ["mean v4 AUC (eight questions): " + "; ".join(f"{m} {_f(v)}" for m, v in mv.items()), "", "**R8-H3: reported (no bar)**", ""]


def _h5_tables(c: dict) -> list[str]:
    L = ["## R8-H5 — drift replay", ""]
    h = c["R8-H5"]
    if c["source"] in ("none", "go-unlabelled"):
        return L + [f"**{h['status']}**", "", f"**R8-H5: pending** — bar: {h['bar']}", ""]
    src = {"go": f"Go replay ({DRIFT_REPLAY}, `mergeone drift-replay --json`)",
           "python-reference": f"Python reference ({DRIFT_PREP}; trips at 0.20 = shift only)"}[c["source"]]
    L += [f"fast signal source: {src}", "", "| comparison | kind | fast signal flags | slow (reference: AUC Δ < −0.05) |", "|---|---|---|---|"]
    ref = c["slow_reference_python"]["flagged"]
    for k, v in c["comparisons"].items():
        L.append(f"| {k} | {v['kind'] or '?'} | {', '.join(v['flagged']) or 'nothing'} | {', '.join(ref.get(k) or []) or ('nothing' if k in ref else '—')} |")
    sl = c["slow"]
    L += ["", f"slow signal: **{sl['status']}**" + ("" if sl["status"] != "ok" else " — " + "; ".join(f"{k}: {', '.join(v) or 'nothing'}" for k, v in sl["flagged"].items())),
          f"(the right-hand column is {c['slow_reference_python']['note']})", "",
          "false positives (flagged with |AUC Δ| < 0.03 on identical rows): " + (", ".join(c["false_positives"]) or "none"), "",
          "clauses: " + "; ".join(f"{k} {_mark(v)}" for k, v in h["clauses"].items()), "",
          f"**R8-H5: {_mark(h['pass'])}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]
    return L


def tables(rep: dict) -> str:
    L = ["# Run 8 — report tables", "", f"computed {rep['computed_at']} · `python -m mergeone.eval.run8_report`", "",
         "## Verdicts", "", "| hypothesis | result | bar |", "|---|---|---|"]
    for h, v in rep["hypotheses"].items():
        L.append(f"| {h} | {_verdict(h, v)}" + (f" ({v['status']})" if v.get("status") and h not in ("R8-H3", "R8-H4") else "") + f" | {v['bar']} |")
    L += ["", "Every comparison is against a same-day full-state control in the same results/run8 directory; earlier runs' numbers "
          f"are shown beside it, never instead of it. Paired bootstrap: seed {rep['bootstrap']['seed']}, {rep['bootstrap']['n']} resamples.", ""]
    L += _h1_tables(rep["h1"]) + _h2_tables(rep["h2"]) + _h3_tables(rep["h3"])
    L += ["## R8-H4 — registry row (self-hosted)", "", f"**{rep['h4']['status']}**", ""]
    L += _h5_tables(rep["h5"])
    L += ["## Inputs", "", "| path | sha256 |", "|---|---|"] + [f"| {i['path']} | `{i['sha256'][:16]}` |" for i in rep["inputs"]]
    return "\n".join(L) + "\n"


def summary(rep: dict) -> str:
    return "\n".join(f"{h}: {_verdict(h, v)}" + (f" — {v['status']}" if v.get("status") and h not in ("R8-H3", "R8-H4") else "")
                     for h, v in rep["hypotheses"].items())


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
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run8_report", description="run-8 report: R8-H1…H5 tables and verdicts (no model call)")
    ap.add_argument("--root", default=".", help="repository root (input paths are relative to it)")
    ap.add_argument("--out-dir", default=None, help=f"output directory (default: <root>/{RUN_DIR})")
    a = ap.parse_args(argv)
    run(Path(a.root), Path(a.out_dir) if a.out_dir else None)


if __name__ == "__main__":
    main()
