"""Run 9 report: R9-H2…H5 tables and verdicts (PROTOCOL.md §R9.3; decisions in §R9.6). No model call, no key.

  python -m mergeone.eval.run9_report [--root .] [--out-dir results/run9]
        → results/run9/tables.md (markdown tables + verdict lines), results/run9/metrics.json (every number, with the path and
          sha256 of each input read); a short summary on stdout. results/run9/README.md is written by hand.

Derived from run8_report (Inputs, verdict marks, the "not run yet" degradation): every section degrades, so the report can be
regenerated after each step of §R9.5 — in particular between the FP8 step and the load curves, where it prints the H2
configuration the §R9.3 rule picks. Equivalence is run 6A's code (run6.equivalence: argmax at 0.5, |Δp| over the ten v3
questions); load summaries are cli.loadtest's json, as run 6A's report read them. R9-H1 is deferred (§R9.6) and not reported.

Inputs (scripts/run9_gpu.sh writes them; docs/run9-tooling.md)
  rows       results/run9/<arm>/serve_meta.json             arm ∈ bf16, fp8, buckets, shared, h5: revision, GPU, dtype,
                                                            fast_options_effective (what the decider ran with)
             results/run9/<arm>/<step>/server_before.json   /meta + /healthz at the start of each step
  H3         results/run9/{bf16,fp8}/equiv/predictions_pplx-flash-local_test_all.jsonl   joint v3, 705 PRs (7,050 answers)
             results/run9/{bf16,fp8}/equiv/throughput.json   closed loop, concurrency 32
             results/run9/fp8/val/predictions_pplx-flash-local_val_all.jsonl            FP8 isotonic refit input (no bar)
             results/run9/fp8/unavailable.json               written when the FP8 server does not load or run → "not run"
  H2         results/run9/<arm>/load/loadtest_pplx-flash-local_x{1,10,30}.json          arm = bf16 (control) and the chosen one
             results/run9/buckets/equiv/…, buckets/unavailable.json                     the bucket row (only if H3 fails)
             results/run6/pass2-l4/loadtest_pplx-flash-local_x{1,10,30}.json            run 6A pass 2 (reference, beside)
  H4         results/run9/{bf16,shared}/pairs_{a,b}/predictions_pplx-flash-local_test_all.jsonl, …/throughput.json
             results/run6/pairs_eval_test.jsonl (set (a) ids), results/run9/groups_summary.json
  H5         results/run9/h5/hosted/predictions_pplx-flash_test_all.jsonl         hosted, joint v3 (the client's request)
             results/run9/h5/single/predictions_pplx-flash-local_test_all.jsonl   local vendor BF16, --single-question (mode single)
             results/run9/bf16/equiv/predictions_pplx-flash-local_test_all.jsonl   the token ratio's denominator (local joint)

Scoring (fixed here, before any run-9 number is scored)
  * Equivalence (§R9.2): over all answers on the PRs both sides answered without error: argmax agreement ≥ 0.95 and mean
    |Δp| ≤ 0.02, Δp = treatment − control; p99 and max |Δp| and per-question rows reported.
  * Throughput (§R9.2): items_per_s from throughput.json (error-free items ÷ the pass's wall seconds); the server's decide_s beside.
  * R9-H3: FP8 vs BF16 equivalence pass AND FP8 items/s ≥ 1.3 × BF16 items/s. unavailable.json → "not run (unavailable on the pod)".
  * R9-H2: configuration = FP8 if H3 passes; else the bucket row if its equivalence vs BF16 passes; else BF16 (the control).
    Pass = that configuration's ×10 step has 0 errors and p99 ≤ its own ×1 p99 + 200 ms. Saturation = first step in ×1/×10/×30
    whose p99 > 2 × the ×1 p99; "> ×30" if none. p99 is over the error-free requests (cli.loadtest); errors are shown per step.
  * R9-H4: run9_pairs.score's rule: shared ÷ per-candidate candidates/s on set (b) ≥ 2.5; set-(a) same_change AUC (label
    positive, the 187 ids) and set-(b) duplicate-vs-rest AUC (adjudicated duplicate vs pointer/unrelated) each ≥ the
    per-candidate arm's − 0.02. Agreement and mean |Δp| between arms reported, no bar.
  * R9-H5: hosted joint vs local single-question equivalence (Δp = hosted − local) AND the median over PRs of hosted
    input_tokens ÷ the same PR's local joint input_tokens (bf16/equiv) ≥ 6. UTC dates of both sides are shown (§R9.3: same day).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from ..models.base import DECISIONS_V3
from . import run6
from . import run9_pairs as P
from .run7_report import Inputs, NOT_RUN, _all, _ci, _d, _f, _mark, _now

RUN_DIR = Path("results/run9")
LOCAL, HOSTED = "pplx-flash-local", "pplx-flash"
ARMS = ("bf16", "fp8", "buckets", "shared", "h5")
LT_MULTS = (1, 10, 30)
RUN6A_LOAD = Path("results/run6/pass2-l4")
EQ_AGREE, EQ_DP = 0.95, 0.02
H3_SPEED = 1.3
H2_SLACK_MS, SAT_FACTOR = 200.0, 2.0
H5_RATIO = 6.0
DEFAULT_BUCKETS = [2048, 4096, 8192, 16384, 32768]       # FastOptions() in pplx_decider 0.3.0
BUCKET_ROW = [2560, 3072, 4096, 8192, 16384, 32768]      # §R9.3
EXPECT = {"bf16": {"fp8": False, "buckets": DEFAULT_BUCKETS}, "shared": {"fp8": False, "buckets": DEFAULT_BUCKETS},
          "h5": {"fp8": False, "buckets": DEFAULT_BUCKETS}, "fp8": {"fp8": True, "buckets": DEFAULT_BUCKETS},
          "buckets": {"fp8": False, "buckets": BUCKET_ROW}}

BARS = {
    "R9-H2": "chosen configuration carries ×10 (3.47/s): 0 errors and p99 ≤ its ×1 p99 + 200 ms; saturation reported",
    "R9-H3": "FP8 vs same-day BF16: agreement ≥ 0.95, mean |Δp| ≤ 0.02 (7,050 answers) and throughput ≥ 1.3×",
    "R9-H4": "shared vs per-candidate: candidates/s on set (b) ≥ 2.5×; set-(a) AUC and set-(b) dup-vs-rest AUC each ≥ per-candidate − 0.02",
    "R9-H5": "hosted joint vs local single-question: agreement ≥ 0.95, mean |Δp| ≤ 0.02, median hosted/local-joint input tokens ≥ 6",
}


def step(arm: str, name: str) -> Path:
    return RUN_DIR / arm / name


def preds_path(arm: str, name: str, model: str = LOCAL, split: str = "test") -> Path:
    return step(arm, name) / f"predictions_{model}_{split}_all.jsonl"


def decisions(rows: list[dict] | None, qset: str = "v3", mode: str = "joint") -> dict[int, dict]:
    """{pr_number: decision} for error-free rows of one qset and mode; last row wins (run6.load_preds on loaded rows)."""
    out = {}
    for r in rows or []:
        if r.get("qset") == qset and r.get("mode", "joint") == mode and r["decision"].get("error") is None:
            out[r["pr_number"]] = r["decision"]
    return out


def _dates(rows: list[dict] | None, qset: str, mode: str) -> list[str]:
    return sorted({(r.get("ts") or "")[:10] for r in rows or [] if r.get("qset") == qset and r.get("mode", "joint") == mode and r.get("ts")})


def equiv(treat: dict[int, dict], ctrl: dict[int, dict]) -> dict:
    """run6.equivalence (treatment as `local`, control as `hosted`) with this run's bars stated."""
    e = run6.equivalence(treat, ctrl)
    ok = e["agreement"] is not None and e["mean_abs_dp"] is not None
    e["pass"] = (e["agreement"] >= EQ_AGREE - 1e-12 and e["mean_abs_dp"] <= EQ_DP + 1e-12) if ok else None
    e["max_abs_dp"] = max((q["max_abs_dp"] for q in e["per_question"].values() if q["max_abs_dp"] is not None), default=None)
    return e


def unavailable(inp: Inputs, arm: str) -> dict | None:
    return inp.json(RUN_DIR / arm / "unavailable.json")


# ----------------------------------------------------------------- rows served
def rows_served(inp: Inputs) -> dict:
    out = {}
    for arm in ARMS:
        m = inp.json(RUN_DIR / arm / "serve_meta.json")
        if m is None:
            out[arm] = {"status": NOT_RUN}
            continue
        eff = m.get("fast_options_effective")
        exp = EXPECT[arm]
        if eff is None:
            check = None
        else:
            check = bool(eff.get("fp8") is exp["fp8"] and list(eff.get("buckets") or []) == exp["buckets"])
        steps = {}
        for p in sorted((inp.root / RUN_DIR / arm).glob("*/server_before.json")):
            s = inp.json(p.relative_to(inp.root)) or {}
            se = (s.get("meta") or {}).get("fast_options_effective")
            steps[p.parent.name] = None if se is None else (se == eff)
        out[arm] = {"status": "ok", "revision": m.get("revision"), "gpu": m.get("gpu_name"), "dtype": m.get("dtype"),
                    "fast_options_flag": m.get("fast_options_flag"), "fast_options_effective": eff, "batch_tokens": m.get("batch_tokens"),
                    "batch_max": m.get("batch_max"), "batch_ms": m.get("batch_ms"), "started_at": m.get("started_at"),
                    "usd_per_hour": m.get("usd_per_hour"), "vm_type": m.get("vm_type"), "expected": exp, "matches_expected": check,
                    "steps_same_options": steps}
    revs = sorted({v["revision"] for v in out.values() if v.get("revision")})
    bt = sorted({v["batch_tokens"] for v in out.values() if v.get("status") == "ok"}, key=str)
    return {"arms": out, "revisions": revs, "one_revision": len(revs) <= 1, "batch_tokens": bt,
            "batch_tokens_131072": all(b == 131072 for b in bt) if bt else None}


# ----------------------------------------------------------------- H3 (+ bucket row equivalence)
def throughput_of(inp: Inputs, arm: str, name: str) -> dict | None:
    return inp.json(step(arm, name) / "throughput.json")


def equiv_row(inp: Inputs, arm: str, ctrl: dict[int, dict]) -> dict:
    un = unavailable(inp, arm)
    if un is not None:
        return {"status": "not run (unavailable on the pod)", "unavailable": un, "pass": None}
    tr = decisions(inp.jsonl(preds_path(arm, "equiv")))
    if not tr:
        return {"status": f"{NOT_RUN} ({preds_path(arm, 'equiv')})", "pass": None}
    if not ctrl:
        return {"status": f"control {NOT_RUN} ({preds_path('bf16', 'equiv')})", "pass": None, "n_treatment": len(tr)}
    return {"status": "ok", **equiv(tr, ctrl)}


def h3(inp: Inputs, ctrl: dict[int, dict]) -> dict:
    e = equiv_row(inp, "fp8", ctrl)
    out = {"equivalence": e}
    t_c, t_f = throughput_of(inp, "bf16", "equiv"), throughput_of(inp, "fp8", "equiv")
    rc, rf = (t_c or {}).get("items_per_s"), (t_f or {}).get("items_per_s")
    out["throughput"] = {"bf16": t_c, "fp8": t_f, "ratio": (rf / rc) if (rc and rf) else None}
    val = decisions(inp.jsonl(preds_path("fp8", "val", split="val")))
    out["val_refit_input"] = {"n_val_prs": len(val), "note": "FP8 joint v3 on val for its own isotonic refit; calibration is not a bar"}
    if e["status"].startswith("not run (unavailable"):
        out["R9-H3"] = {"pass": None, "unavailable": True, "status": "not run (unavailable on the pod)", "bar": BARS["R9-H3"]}
        return out
    r = out["throughput"]["ratio"]
    speed_ok = None if r is None else r >= H3_SPEED - 1e-12
    p = _all([e.get("pass"), speed_ok])
    st = []
    if e["status"] != "ok":
        st.append(e["status"])
    if r is None:
        st.append("throughput " + ("bf16 " if rc is None else "") + ("fp8 " if rf is None else "") + NOT_RUN)
    out["R9-H3"] = {"pass": p, "equivalence_pass": e.get("pass"), "speed_pass": speed_ok, "speed_ratio": r, "bar": BARS["R9-H3"],
                    **({"status": "; ".join(st)} if st else {})}
    return out


# ----------------------------------------------------------------- H2
def load_curve(inp: Inputs, base: Path) -> dict:
    lts = {}
    for x in LT_MULTS:
        d = inp.json(base / f"loadtest_{LOCAL}_x{x}.json")
        if d is not None:
            s = d.get("summary", d)
            n_err = s.get("n_errors")
            if n_err is None and s.get("error_rate") is not None and s.get("n") is not None:
                n_err = int(round(s["error_rate"] * s["n"]))
            lts[f"x{x}"] = {"multiplier": s.get("multiplier"), "rate_per_s": s.get("rate_per_s"), "n": s.get("n"), "p50_ms": s.get("p50_ms"),
                            "p99_ms": s.get("p99_ms"), "n_errors": n_err, "error_rate": s.get("error_rate"),
                            "errors_by_kind": s.get("errors_by_kind"), "p99_schedule_lag_s": s.get("p99_schedule_lag_s"),
                            "concurrency": s.get("concurrency"), "retry_429": s.get("retry_429"), "finished_at": s.get("finished_at"),
                            "fast_options": (s.get("server_meta") or {}).get("fast_options_effective")}
    x1 = (lts.get("x1") or {}).get("p99_ms")
    sat = None
    if x1 is not None:
        for x in LT_MULTS:
            t = lts.get(f"x{x}")
            if t and t["p99_ms"] is not None and t["p99_ms"] > SAT_FACTOR * x1:
                sat = f"×{x}"
                break
        if sat is None and all(f"x{x}" in lts for x in LT_MULTS):
            sat = f"> ×{LT_MULTS[-1]}"
    return {"steps": lts, "x1_p99_ms": x1, "saturation": sat}


def choose_config(h3v: dict, bucket_eq: dict) -> tuple[str | None, str]:
    """§R9.3: FP8 if H3 passes both bars; else the bucket row if it meets the equivalence bars vs the control; else the control."""
    p = h3v.get("pass")
    if p is True:
        return "fp8", "R9-H3 passed both bars"
    if p is None and not h3v.get("unavailable"):
        return None, "pending: R9-H3 not scored yet"
    why = "R9-H3 not run (unavailable)" if h3v.get("unavailable") else "R9-H3 failed"
    b = bucket_eq.get("pass")
    if b is True:
        return "buckets", f"{why}; bucket row meets the equivalence bars vs BF16"
    if b is False:
        return "bf16", f"{why}; bucket row failed the equivalence bars"
    if bucket_eq.get("status", "").startswith("not run (unavailable"):
        return "bf16", f"{why}; bucket row not run (unavailable)"
    return None, f"pending: {why}; bucket-row equivalence {NOT_RUN}"


def h2(inp: Inputs, h3v: dict, ctrl: dict[int, dict]) -> dict:
    beq = equiv_row(inp, "buckets", ctrl)
    cfg, why = choose_config(h3v, beq)
    curves = {arm: load_curve(inp, step(arm, "load")) for arm in ("bf16", "fp8", "buckets")}
    ref = load_curve(inp, RUN6A_LOAD)
    out = {"bucket_equivalence": beq, "configuration": cfg, "configuration_rule": why, "curves": curves, "run6a_reference": ref,
           "pairs_included": {"status": "not run (cli loadtest has no v3 + pair mix; §R9.3 'measured alongside, no bar')"}}
    if cfg is None:
        out["R9-H2"] = {"pass": None, "status": why, "bar": BARS["R9-H2"]}
        return out
    c = curves[cfg]
    x1, x10 = c["steps"].get("x1"), c["steps"].get("x10")
    if not x1 or not x10:
        out["R9-H2"] = {"pass": None, "configuration": cfg, "status": f"{cfg} load curve ×1/×10 {NOT_RUN}", "bar": BARS["R9-H2"]}
        return out
    bad_retry = x10.get("retry_429") not in (0, None)
    ok = (x10["n_errors"] == 0 and x10["p99_ms"] is not None and x1["p99_ms"] is not None
          and x10["p99_ms"] <= x1["p99_ms"] + H2_SLACK_MS and not bad_retry)
    out["R9-H2"] = {"pass": bool(ok), "configuration": cfg, "x1_p99_ms": x1["p99_ms"], "x10_p99_ms": x10["p99_ms"],
                    "x10_errors": x10["n_errors"], "saturation": c["saturation"], "bar": BARS["R9-H2"],
                    **({"status": "invalid: 429 retry was not disabled"} if bad_retry else {})}
    return out


# ----------------------------------------------------------------- H4
def h4(inp: Inputs) -> dict:
    ids = inp.jsonl(P.PAIR_KEYS)
    keys = {r["pr_number"] for r in ids} if ids is not None else None
    arms = {}
    for (arm, s), d in P.ARM_DIRS.items():
        f = d / f"predictions_{LOCAL}_test_all.jsonl"
        arms[(arm, s)] = {"rows": P.pair_rows(inp.jsonl(f), P.QSET[arm]), "file": str(f), "throughput": inp.json(d / "throughput.json")}
    out = {"groups_summary": inp.json(P.GROUPS_SUMMARY)}
    auc_a = {}
    for arm in ("per_candidate", "shared"):
        rows = arms[(arm, "a")]["rows"]
        if not rows:
            auc_a[arm] = {"status": f"{NOT_RUN} ({arms[(arm, 'a')]['file']})", "auc": None}
        elif keys is None:
            auc_a[arm] = {"status": f"not evaluable: {P.PAIR_KEYS} not present", "auc": None}
        else:
            auc_a[arm] = P.auc_block(rows, P.label_positive(keys))
    auc_b = {arm: (P.auc_block(arms[(arm, "b")]["rows"], P.label_duplicate) if arms[(arm, "b")]["rows"]
                   else {"status": f"{NOT_RUN} ({arms[(arm, 'b')]['file']})", "auc": None}) for arm in ("per_candidate", "shared")}
    speed = P.speed_ratio(arms[("shared", "b")]["throughput"], arms[("per_candidate", "b")]["throughput"])
    tok = {}
    for (arm, s), a in arms.items():
        t = [r["decision"].get("input_tokens") for r in a["rows"].values() if r["decision"].get("input_tokens")]
        tok[f"{arm}_{s}"] = {"n": len(t), "median_per_candidate": float(np.median(t)) if t else None}
    out.update(auc_set_a=auc_a, dup_vs_rest_set_b=auc_b, speed_set_b=speed,
               agreement={s: P.agreement(arms[("shared", s)]["rows"], arms[("per_candidate", s)]["rows"]) for s in ("a", "b")},
               tokens=tok, throughput={f"{arm}_{s}": arms[(arm, s)]["throughput"] for (arm, s) in arms},
               shared_group_sizes={s: {str(k): v for k, v in sorted(
                   Counter(r.get("group_size") for r in arms[("shared", s)]["rows"].values()).items())} for s in ("a", "b")})
    v = P.h4_verdict(speed, auc_a, auc_b)
    st = [f"{k} pending" for k, c in v["clauses"].items() if c is None]
    out["R9-H4"] = {**v, "bar": BARS["R9-H4"], **({"status": "; ".join(st)} if st else {})}
    return out


# ----------------------------------------------------------------- H5
def token_ratio(hosted: dict[int, dict], local_joint: dict[int, dict]) -> dict:
    ks = [k for k in hosted if k in local_joint and hosted[k].get("input_tokens") and local_joint[k].get("input_tokens")]
    if not ks:
        return {"n": 0, "median": None}
    r = np.array([hosted[k]["input_tokens"] / local_joint[k]["input_tokens"] for k in ks], dtype=float)
    return {"n": len(ks), "median": float(np.median(r)), "p10": float(np.percentile(r, 10)), "p90": float(np.percentile(r, 90)),
            "hosted_median_tokens": float(np.median([hosted[k]["input_tokens"] for k in ks])),
            "local_joint_median_tokens": float(np.median([local_joint[k]["input_tokens"] for k in ks]))}


def h5(inp: Inputs, local_joint: dict[int, dict]) -> dict:
    hp, lp = preds_path("h5", "hosted", HOSTED), preds_path("h5", "single")
    hrows, lrows = inp.jsonl(hp), inp.jsonl(lp)
    hosted, single = decisions(hrows, "v3", "joint"), decisions(lrows, "v3", "single")
    out = {"dates": {"hosted": _dates(hrows, "v3", "joint"), "local_single": _dates(lrows, "v3", "single")},
           "versions": {"hosted": sorted({d.get("model_version") for d in hosted.values()} - {None, ""})}}
    out["same_utc_day"] = (out["dates"]["hosted"] == out["dates"]["local_single"] and len(out["dates"]["hosted"]) == 1) if hosted and single else None
    if not hosted:
        out["equivalence"] = {"status": f"hosted {NOT_RUN} ({hp})", "pass": None}
    elif not single:
        out["equivalence"] = {"status": f"local single-question {NOT_RUN} ({lp})", "pass": None}
    else:
        out["equivalence"] = {"status": "ok", **equiv(hosted, single)}
    if not hosted:
        out["tokens"] = {"status": f"hosted {NOT_RUN}", "median": None}
    elif not local_joint:
        out["tokens"] = {"status": f"local joint {NOT_RUN} ({preds_path('bf16', 'equiv')})", "median": None}
    else:
        out["tokens"] = {"status": "ok", **token_ratio(hosted, local_joint)}
        ls = [d.get("input_tokens") for d in single.values() if d.get("input_tokens")]
        out["tokens"]["local_single_median_tokens"] = float(np.median(ls)) if ls else None
    m = out["tokens"].get("median")
    tok_ok = None if m is None else m >= H5_RATIO - 1e-12
    p = _all([out["equivalence"].get("pass"), tok_ok])
    st = list(dict.fromkeys(s for s in (out["equivalence"]["status"], out["tokens"]["status"]) if s != "ok"))
    if out["same_utc_day"] is False:
        st.append(f"not the same UTC day (hosted {', '.join(out['dates']['hosted'])}; local {', '.join(out['dates']['local_single'])})")
    out["R9-H5"] = {"pass": p, "equivalence_pass": out["equivalence"].get("pass"), "token_ratio_pass": tok_ok, "median_token_ratio": m,
                    "bar": BARS["R9-H5"], **({"status": "; ".join(st)} if st else {})}
    return out


# ----------------------------------------------------------------- report
def build(root: Path = Path(".")) -> dict:
    inp = Inputs(root)
    ctrl = decisions(inp.jsonl(preds_path("bf16", "equiv")))
    rep = {"computed_at": _now(), "rows": rows_served(inp), "control_equiv_prs": len(ctrl)}
    rep["h3"] = h3(inp, ctrl)
    rep["h2"] = h2(inp, rep["h3"]["R9-H3"], ctrl)
    rep["h4"] = h4(inp)
    rep["h5"] = h5(inp, ctrl)
    rep["hypotheses"] = {"R9-H1": {"pass": None, "status": "deferred (§R9.6: no engine port exists)", "bar": "—"},
                         **{f"R9-H{i}": rep[f"h{i}"][f"R9-H{i}"] for i in (2, 3, 4, 5)}}
    rep["inputs"] = [{"path": p, "sha256": s} for p, s in sorted(inp.seen.items())]
    return rep


def _cell(x: str) -> str:
    return str(x).replace("|", "\\|")


def _verdict(h: str, v: dict) -> str:
    if h == "R9-H1":
        return "deferred"
    if v.get("unavailable"):
        return "not run (unavailable on the pod)"
    return _mark(v.get("pass"))


def _ms(x) -> str:
    return "—" if x is None else f"{x:,.0f}"


def _eq_table(name: str, e: dict) -> list[str]:
    if e.get("status") != "ok":
        return [f"{name}: **{e.get('status')}**", ""]
    L = [f"{name}: {e['n_prs_joined']} PRs joined ({e['n_local']} treatment, {e['n_hosted']} control), {e['n_answers']} answers", "",
         "| question | n | agreement | mean \\|Δp\\| | max \\|Δp\\| | mean Δp | yes-rate treatment | yes-rate control |",
         "|---|---|---|---|---|---|---|---|"]
    for q in DECISIONS_V3:
        r = e["per_question"][q]
        L.append(f"| {q} | {r['n']} | {_f(r['agreement'])} | {_f(r['mean_abs_dp'], 4)} | {_f(r['max_abs_dp'])} | {_d(r['mean_dp'], 4)} | "
                 f"{_f(r['yes_rate_local'])} | {_f(r['yes_rate_hosted'])} |")
    L += [f"| **all** | {e['n_answers']} | **{_f(e['agreement'])}** | **{_f(e['mean_abs_dp'], 4)}** | {_f(e['max_abs_dp'])} | | | |", "",
          f"p99 \\|Δp\\| {_f(e['p99_abs_dp'], 4)} · bars: agreement ≥ {EQ_AGREE}, mean \\|Δp\\| ≤ {EQ_DP} → **{_mark(e['pass'])}**", ""]
    return L


def _rows_table(r: dict) -> list[str]:
    L = ["## Rows served (serve_meta.json per arm)", "",
         "| arm | revision | GPU | dtype | fast options in effect | expected | matches | batch tokens | started |", "|---|---|---|---|---|---|---|---|---|"]
    for arm, e in r["arms"].items():
        if e["status"] != "ok":
            L.append(f"| {arm} | {e['status']} | | | | | | | |")
            continue
        fo = e["fast_options_effective"]
        fos = "—" if fo is None else ", ".join(f"{k}={json.dumps(v)}" for k, v in fo.items())
        L.append(f"| {arm} | {(e['revision'] or '—')[:12]} | {e['gpu'] or '—'} | {(e['dtype'] or '—').split(' (')[0]} | {fos} | "
                 f"fp8={json.dumps(e['expected']['fp8'])}, buckets={e['expected']['buckets']} | {_mark(e['matches_expected']).replace('pending', 'unknown')} | "
                 f"{e['batch_tokens']} | {e['started_at'] or '—'} |")
    L += ["", f"one revision across arms: {r['one_revision']} ({', '.join(x[:12] for x in r['revisions']) or '—'}) · "
              f"batch tokens 131072 everywhere: {r['batch_tokens_131072']}", ""]
    bad = {a: s for a, e in r["arms"].items() for s, ok in (e.get("steps_same_options") or {}).items() if ok is False}
    if bad:
        L += ["**steps whose /meta options differ from the arm's serve_meta: " + ", ".join(f"{a}/{s}" for a, s in bad.items()) + "**", ""]
    return L


def _tp(t: dict | None) -> str:
    if not t:
        return NOT_RUN
    return (f"{t.get('n_items')} items in {_f(t.get('wall_s'), 1)} s = **{_f(t.get('items_per_s'), 2)}/s** (errors {t.get('errors')}, "
            f"concurrency {t.get('concurrency')}, decide_s {_f(t.get('decide_s'), 1)})")


def _h3_tables(c: dict) -> list[str]:
    L = ["## R9-H3 — FP8 row vs same-day BF16 control", ""]
    L += _eq_table("Equivalence (FP8 − BF16, joint v3, test)", c["equivalence"])
    t = c["throughput"]
    L += [f"Throughput, equivalence set: BF16 {_tp(t['bf16'])} · FP8 {_tp(t['fp8'])} → ratio **{_f(t['ratio'], 2)}×** (bar ≥ {H3_SPEED}×)", "",
          f"FP8 val rows for the isotonic refit: {c['val_refit_input']['n_val_prs']} PRs (not a bar).", ""]
    h = c["R9-H3"]
    return L + [f"**R9-H3: {_verdict('R9-H3', h)}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]


def _curve_rows(name: str, c: dict) -> list[str]:
    if not c["steps"]:
        return [f"| {name} | {NOT_RUN} | | | | | | |"]
    L = []
    for x in LT_MULTS:
        s = c["steps"].get(f"x{x}")
        if s is None:
            L.append(f"| {name} | ×{x} | {NOT_RUN} | | | | | |")
            continue
        ek = ", ".join(f"{k} {v}" for k, v in (s.get("errors_by_kind") or {}).items())
        L.append(f"| {name} | ×{x} | {_f(s['rate_per_s'], 2)} | {s['n']} | {_ms(s['p50_ms'])} | {_ms(s['p99_ms'])} | "
                 f"{s['n_errors']}{f' ({ek})' if ek else ''} | {_f(s['p99_schedule_lag_s'], 2)} |")
    return L


def _h2_tables(c: dict) -> list[str]:
    L = ["## R9-H2 — L4 at ×10", ""]
    L += _eq_table("Bucket row equivalence (buckets − BF16; only if R9-H3 fails)", c["bucket_equivalence"])
    L += [f"Configuration: **{c['configuration'] or 'pending'}** ({c['configuration_rule']})", "",
          "| row | step | rate /s | n | p50 ms | p99 ms | errors | p99 schedule lag s |", "|---|---|---|---|---|---|---|---|"]
    names = {"bf16": "BF16 control (same day)", "fp8": "FP8", "buckets": "BF16 + bucket row"}
    for arm, cur in c["curves"].items():
        if arm == "bf16" or cur["steps"]:
            L += _curve_rows(names[arm], cur)
    L += _curve_rows("run 6A pass 2, L4 (2026-10-18; reference, not the comparison)", c["run6a_reference"])
    L += ["", "saturation (first step with p99 > 2 × its ×1 p99): " + "; ".join(
        f"{names[a]} {cur['saturation'] or '—'}" for a, cur in c["curves"].items() if cur["steps"]) +
          f"; run 6A {c['run6a_reference']['saturation'] or '—'}", "",
          f"with the gateway's pair calls included: {c['pairs_included']['status']}", ""]
    h = c["R9-H2"]
    detail = "" if h.get("x10_p99_ms") is None else (f" ×10 p99 {_ms(h['x10_p99_ms'])} ms vs ×1 {_ms(h['x1_p99_ms'])} + {H2_SLACK_MS:.0f}; "
                                                    f"×10 errors {h['x10_errors']}; saturation {h['saturation'] or '—'}.")
    return L + [f"**R9-H2: {_mark(h['pass'])}**" + (f" ({h['status']})" if h.get("status") else "") + detail + f" — bar: {h['bar']}", ""]


def _auc(e: dict) -> str:
    if e.get("auc") is None:
        return e.get("status") or "—"
    return f"{_f(e['auc'])} {_ci(e['ci95'])} (n {e['n']}, pos {e['positives']})"


def _h4_tables(c: dict) -> list[str]:
    L = ["## R9-H4 — shared-A pair call (k = 10) vs one request per candidate, both BF16", ""]
    gs = c.get("groups_summary") or {}
    if gs:
        L += [f"groups: set (a) {(gs.get('a') or {}).get('groups', '—')} requests, sizes {(gs.get('a') or {}).get('group_sizes', '—')}; "
              f"set (b) {(gs.get('b') or {}).get('groups', '—')} requests, adjudicated B forced into slot 10: {(gs.get('b') or {}).get('b_forced_into_slot_10', '—')}", ""]
    L += ["| measure | per-candidate | shared | shared − per-candidate | bar |", "|---|---|---|---|---|"]
    a, b = c["auc_set_a"], c["dup_vs_rest_set_b"]
    da, db = c["R9-H4"]["auc_a_delta"], c["R9-H4"]["dup_b_delta"]
    s = c["speed_set_b"]
    L += [f"| same_change AUC, set (a) (positive) | {_auc(a['per_candidate'])} | {_auc(a['shared'])} | {_d(da)} | ≥ −0.02 |",
          f"| duplicate vs rest AUC, set (b) | {_auc(b['per_candidate'])} | {_auc(b['shared'])} | {_d(db)} | ≥ −0.02 |",
          f"| candidates/s, set (b) (timed, c = 32) | {_f(s['per_candidate_candidates_per_s'], 2)} | {_f(s['shared_candidates_per_s'], 2)} | "
          f"ratio **{_f(s['ratio'], 2)}×** | ≥ 2.5× |",
          f"| input tokens per candidate, set (b), median | {_f(c['tokens']['per_candidate_b']['median_per_candidate'], 0)} | "
          f"{_f(c['tokens']['shared_b']['median_per_candidate'], 0)} | | — |", "",
          "Between the arms (reported, no bar; a different request by design): " + "; ".join(
              f"set ({k}) n {v['n']}, agreement {_f(v['agreement'])}, mean |Δp| {_f(v['mean_abs_dp'], 4)}, p99 |Δp| {_f(v['p99_abs_dp'], 4)}"
              for k, v in c["agreement"].items()), ""]
    h = c["R9-H4"]
    return L + ["clauses: " + "; ".join(f"{k} {_mark(v)}" for k, v in h["clauses"].items()), "",
                f"**R9-H4: {_mark(h['pass'])}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]


def _h5_tables(c: dict) -> list[str]:
    L = ["## R9-H5 — hosted request decomposition (a check)", "",
         f"UTC dates: hosted {', '.join(c['dates']['hosted']) or '—'} · local single-question {', '.join(c['dates']['local_single']) or '—'} · "
         f"same day: {c['same_utc_day']} · hosted version {', '.join(c['versions']['hosted']) or '—'}", ""]
    L += _eq_table("Equivalence (hosted joint − local single-question)", c["equivalence"])
    t = c["tokens"]
    if t.get("status") != "ok":
        L += [f"token ratio: **{t.get('status')}**", ""]
    else:
        L += ["| row | n PRs | median | p10 | p90 |", "|---|---|---|---|---|",
              f"| hosted input_tokens ÷ local joint input_tokens (per PR) | {t['n']} | **{_f(t['median'], 2)}** | {_f(t['p10'], 2)} | {_f(t['p90'], 2)} |",
              "", f"median tokens per PR: hosted {_f(t['hosted_median_tokens'], 0)} · local joint {_f(t['local_joint_median_tokens'], 0)} · "
                  f"local single-question (10 requests) {_f(t.get('local_single_median_tokens'), 0)}; bar: median ratio ≥ {H5_RATIO:g}", ""]
    h = c["R9-H5"]
    return L + [f"**R9-H5: {_mark(h['pass'])}**" + (f" ({h['status']})" if h.get("status") else "") + f" — bar: {h['bar']}", ""]


def tables(rep: dict) -> str:
    L = ["# Run 9 — report tables", "", f"computed {rep['computed_at']} · `python -m mergeone.eval.run9_report`", "",
         "## Verdicts", "", "| hypothesis | result | bar |", "|---|---|---|"]
    for h, v in rep["hypotheses"].items():
        L.append(f"| {h} | {_verdict(h, v)}" + (f" ({_cell(v['status'])})" if v.get("status") and h != "R9-H1" else "") + f" | {_cell(v['bar'])} |")
    L += ["", "Every comparison is against the same-day vendor-BF16 control on the same pod (results/run9/bf16); run-6A numbers are "
              "shown beside it, never instead of it.", ""]
    L += _rows_table(rep["rows"]) + _h3_tables(rep["h3"]) + _h2_tables(rep["h2"]) + _h4_tables(rep["h4"]) + _h5_tables(rep["h5"])
    L += ["## Inputs", "", "| path | sha256 |", "|---|---|"] + [f"| {i['path']} | `{i['sha256'][:16]}` |" for i in rep["inputs"]]
    return "\n".join(L) + "\n"


def summary(rep: dict) -> str:
    return "\n".join(f"{h}: {_verdict(h, v)}" + (f" — {v['status']}" if v.get("status") and h != "R9-H1" else "")
                     for h, v in rep["hypotheses"].items()) + f"\nH2 configuration: {rep['h2']['configuration'] or 'pending'} ({rep['h2']['configuration_rule']})"


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
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run9_report", description="run-9 report: R9-H2…H5 tables and verdicts (no model call)")
    ap.add_argument("--root", default=".", help="repository root (input paths are relative to it)")
    ap.add_argument("--out-dir", default=None, help=f"output directory (default: <root>/{RUN_DIR})")
    a = ap.parse_args(argv)
    run(Path(a.root), Path(a.out_dir) if a.out_dir else None)


if __name__ == "__main__":
    main()
