"""Build metrics.json, tables.md and figures from raw predictions in a run dir.

Threshold discipline: every threshold (R@FPR5 operating point, cascade
escalation threshold) is chosen on the `val` predictions and applied
unchanged to `test`. If a model has no val predictions, its thresholds are
found on test and flagged as such in the output.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from ..models.base import DECISIONS, PRIMARY, Decision
from ..models.cascade import compose, uncertainty
from . import metrics as M

PROTOCOL_H = {
    "H1_recall_at_fpr5_min": 0.30,
    "H1_pr_auc_vs_base_rate_mult": 2.0,
    "H2_pr_auc_margin": 0.05,
    "H4_max_escalation": 0.25,
    "H4_pr_auc_margin": 0.02,
    "H4_cost_ratio": 0.40,
    "H5_p99_ratio": 2.0,
    "H5_error_rate": 0.01,
}


def _load(run_dir: Path) -> dict[tuple[str, str, str], dict[int, dict]]:
    """(model, split, variant) -> {pr_number: row}; later rows override earlier (retries)."""
    out: dict[tuple[str, str, str], dict[int, dict]] = defaultdict(dict)
    for p in sorted(run_dir.glob("predictions_*.jsonl")):
        _, model, split, variant = p.stem.split("_", 3)
        for line in p.open():
            row = json.loads(line)
            out[(model, split, variant)][row["pr_number"]] = row
    return out


def _arrays(rows: dict[int, dict], decision: str):
    prs = sorted(rows)
    y = [int(rows[k]["labels"][decision]) for k in prs]
    p = [rows[k]["decision"]["probs"].get(decision, float("nan")) for k in prs]
    p = [float("nan") if v is None else float(v) for v in p]
    return prs, y, p


def _lat_cost(rows: dict[int, dict]) -> dict:
    ds = [r["decision"] for r in rows.values() if r["decision"].get("error") is None]
    lat = sorted(d["latency_ms"] for d in ds)
    cost = sum(d["cost_usd"] for d in ds)
    it = sum(d["input_tokens"] for d in ds)
    ot = sum(d["output_tokens"] for d in ds)
    n = len(ds)
    return {
        "n_ok": n,
        "error_rate": 1 - n / max(len(rows), 1),
        "p50_ms": lat[n // 2] if n else None,
        "p99_ms": lat[min(int(n * 0.99), n - 1)] if n else None,
        "mean_input_tokens": it / n if n else 0,
        "mean_output_tokens": ot / n if n else 0,
        "usd_per_1k": cost / n * 1000 if n else 0.0,
    }


def _primary_block(val_rows, test_rows) -> dict:
    out = {}
    thr = None
    thr_src = "none"
    if val_rows:
        _, yv, pv = _arrays(val_rows, PRIMARY)
        if sum(yv) > 0:
            thr = M.threshold_for_fpr(yv, pv, 0.05)
            thr_src = "val"
            out["val"] = M.summarize(yv, pv)
    if test_rows:
        _, yt, pt = _arrays(test_rows, PRIMARY)
        s = M.summarize(yt, pt, fpr_threshold=thr)
        s["threshold_source"] = thr_src if thr is not None else "test (no val predictions!)"
        lo, hi = M.bootstrap_ci(yt, pt, M.pr_auc)
        s["pr_auc_ci95"] = [lo, hi]
        lo, hi = M.bootstrap_ci(yt, pt, M.ece)
        s["ece_ci95"] = [lo, hi]
        s["reliability"] = M.reliability(yt, pt)
        out["test"] = s
    return out


def _secondary_block(test_rows) -> dict:
    out = {}
    for d in DECISIONS[1:]:
        _, y, p = _arrays(test_rows, d)
        out[d] = {"accuracy": M.accuracy(y, p), "ece": M.ece(y, p), "pr_auc": M.pr_auc(y, p), "positives": int(sum(y))}
    return out


def _decision_from_row(row: dict) -> Decision:
    d = row["decision"]
    return Decision(probs=d["probs"], confidence=d.get("confidence", {}), latency_ms=d["latency_ms"], input_tokens=d["input_tokens"],
                    output_tokens=d["output_tokens"], cost_usd=d["cost_usd"], model_version=d["model_version"], error=d.get("error"))


def _cascade(cheap_val, exp_val, cheap_test, exp_test, max_escalation: float) -> dict | None:
    if not (cheap_val and exp_val and cheap_test and exp_test):
        return None
    common_val = sorted(set(cheap_val) & set(exp_val))
    if not common_val:
        return None
    # choose t on val: the smallest uncertainty threshold such that escalation <= budget
    unc = sorted(uncertainty(_decision_from_row(cheap_val[k])) for k in common_val)
    t = unc[int((1 - max_escalation) * (len(unc) - 1))]
    common_test = sorted(set(cheap_test) & set(exp_test))
    rows = {}
    esc = 0
    for k in common_test:
        d, e = compose(_decision_from_row(cheap_test[k]), _decision_from_row(exp_test[k]), t)
        esc += e
        rows[k] = {"labels": cheap_test[k]["labels"], "decision": d.to_dict()}
    _, y, p = _arrays(rows, PRIMARY)
    # R@FPR5 threshold for cascade also from val
    vrows = {}
    for k in common_val:
        d, _ = compose(_decision_from_row(cheap_val[k]), _decision_from_row(exp_val[k]), t)
        vrows[k] = {"labels": cheap_val[k]["labels"], "decision": d.to_dict()}
    _, yv, pv = _arrays(vrows, PRIMARY)
    thr = M.threshold_for_fpr(yv, pv) if sum(yv) else None
    s = M.summarize(y, p, fpr_threshold=thr)
    s["escalation_rate"] = esc / len(common_test)
    s["uncertainty_threshold"] = t
    s.update({f"lat_{k}": v for k, v in _lat_cost(rows).items()})
    return s


def run(run_dir: Path) -> None:
    preds = _load(run_dir)
    models = sorted({m for (m, _, _) in preds})
    variants = sorted({v for (_, _, v) in preds})
    report: dict = {"run_dir": str(run_dir), "models": {}, "cascade": {}, "hypotheses": {}, "protocol_thresholds": PROTOCOL_H}

    for m in models:
        report["models"][m] = {}
        for v in variants:
            val_rows = preds.get((m, "val", v), {})
            test_rows = preds.get((m, "test", v), {})
            if not test_rows and not val_rows:
                continue
            block = {"primary": _primary_block(val_rows, test_rows)}
            if test_rows:
                block["latency_cost_test"] = _lat_cost(test_rows)
                if v.startswith("all"):
                    block["secondary"] = _secondary_block(test_rows)
            report["models"][m][v] = block

    # cascade jev -> haiku on the primary-only variant, and on 'all' as fallback
    for v in ("primary", "all"):
        c = _cascade(preds.get(("jev", "val", v), {}), preds.get(("haiku", "val", v), {}),
                     preds.get(("jev", "test", v), {}), preds.get(("haiku", "test", v), {}), PROTOCOL_H["H4_max_escalation"])
        if c:
            report["cascade"][f"jev_then_haiku_{v}"] = c

    # load tests
    for p in run_dir.glob("loadtest_*.json"):
        report.setdefault("loadtests", {})[p.stem] = json.loads(p.read_text())["summary"]

    report["hypotheses"] = _hypotheses(report)
    (run_dir / "metrics.json").write_text(json.dumps(report, indent=2, default=float))
    (run_dir / "tables.md").write_text(_tables(report))
    _figures(report, run_dir)
    print((run_dir / "tables.md").read_text())


def _get(report, model, variant, *path):
    try:
        x = report["models"][model][variant]
        for k in path:
            x = x[k]
        return x
    except KeyError:
        return None


def _hypotheses(report: dict) -> dict:
    H = PROTOCOL_H
    out = {}
    v = "primary" if any("primary" in report["models"][m] for m in report["models"]) else "all"
    tests = {m: _get(report, m, v, "primary", "test") for m in report["models"]}
    tests = {m: t for m, t in tests.items() if t}
    # H1
    h1 = {}
    for m, t in tests.items():
        h1[m] = bool(t["recall_at_fpr5"] >= H["H1_recall_at_fpr5_min"] and t["pr_auc"] >= H["H1_pr_auc_vs_base_rate_mult"] * t["base_rate"])
    out["H1_useful"] = h1
    # H2
    if "jev" in tests and "haiku" in tests:
        j, h = tests["jev"], tests["haiku"]
        out["H2_jev_vs_haiku"] = {
            "competitive": bool(j["pr_auc"] >= h["pr_auc"] - H["H2_pr_auc_margin"] and j["ece"] < h["ece"]),
            "better": bool(j["pr_auc"] > h["pr_auc"] and j["ece"] < h["ece"]),
            "jev": {"pr_auc": j["pr_auc"], "ece": j["ece"]}, "haiku": {"pr_auc": h["pr_auc"], "ece": h["ece"]},
        }
    # H3
    if "gbm" in tests:
        g = tests["gbm"]["pr_auc"]
        out["H3_gbm_matches_or_beats"] = {m: bool(g >= tests[m]["pr_auc"]) for m in ("jev", "haiku") if m in tests}
    # H4
    c = report["cascade"].get(f"jev_then_haiku_{v}")
    if c and "haiku" in tests:
        hc = _get(report, "haiku", v, "latency_cost_test", "usd_per_1k") or float("nan")
        out["H4_cascade_worthwhile"] = bool(c["escalation_rate"] <= H["H4_max_escalation"] and c["pr_auc"] >= tests["haiku"]["pr_auc"] - H["H4_pr_auc_margin"]
                                            and c["lat_usd_per_1k"] <= H["H4_cost_ratio"] * hc)
        out["H4_detail"] = {"escalation": c["escalation_rate"], "cascade_pr_auc": c["pr_auc"], "haiku_pr_auc": tests["haiku"]["pr_auc"],
                            "cascade_usd_per_1k": c["lat_usd_per_1k"], "haiku_usd_per_1k": hc}
    # H5
    lt = report.get("loadtests", {})
    h5 = {}
    for name, s in lt.items():
        if s["multiplier"] >= 100:
            base = next((b for b in lt.values() if b["model"] == s["model"] and b["multiplier"] == 1), None)
            if base and base["p99_ms"] and s["p99_ms"]:
                h5[s["model"]] = bool(s["p99_ms"] <= H["H5_p99_ratio"] * base["p99_ms"] and s["error_rate"] < H["H5_error_rate"])
    if h5:
        out["H5_holds_at_scale"] = h5
    return out


def _f(x, nd=3):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def _tables(r: dict) -> str:
    lines = ["# Results", ""]
    for v in ("primary", "all", "all_words", "primary_words"):
        rows = []
        for m, blocks in r["models"].items():
            b = blocks.get(v)
            if not b or "test" not in b["primary"]:
                continue
            t = b["primary"]["test"]
            lc = b.get("latency_cost_test", {})
            rows.append(f"| {m} | {_f(t['pr_auc'])} [{_f(t['pr_auc_ci95'][0])}, {_f(t['pr_auc_ci95'][1])}] | {_f(t['roc_auc'])} | {_f(t['recall_at_fpr5'])} ({_f(t['actual_fpr_at_threshold'])}) | {_f(t['ece'])} | {_f(t['brier'])} | {_f(lc.get('p50_ms'),0)} / {_f(lc.get('p99_ms'),0)} | {_f(lc.get('usd_per_1k'),4)} | {_f(lc.get('error_rate'))} |")
        if rows:
            t0 = next(iter(r["models"].values()))
            base = None
            for m, blocks in r["models"].items():
                if v in blocks and "test" in blocks[v]["primary"]:
                    base = blocks[v]["primary"]["test"]; break
            lines += [f"## Primary decision (`will_be_reverted`), test split, variant `{v}`", "",
                      f"Test n = {base['n']}, positives = {base['positives']}, base rate = {_f(base['base_rate'],4)}. R@FPR5 threshold chosen on val; actual test FPR in parentheses.", "",
                      "| model | PR-AUC [95% CI] | ROC-AUC | R@FPR5 (FPR) | ECE | Brier | p50/p99 ms | USD / 1k | err |",
                      "|---|---|---|---|---|---|---|---|---|", *rows, ""]
    sec = []
    for m, blocks in r["models"].items():
        for v, b in blocks.items():
            if "secondary" in b:
                s = b["secondary"]
                sec.append(f"| {m} | {v} | {_f(s['touches_sensitive_area']['accuracy'])} | {_f(s['touches_sensitive_area']['ece'])} | {_f(s['large_blast_radius']['accuracy'])} | {_f(s['large_blast_radius']['ece'])} |")
    if sec:
        lines += ["## Secondary decisions (test)", "", "| model | variant | sensitive acc | sensitive ECE | blast acc | blast ECE |", "|---|---|---|---|---|---|", *sec, ""]
    if r["cascade"]:
        lines += ["## Cascade (jev → haiku)", "", "| variant | escalation | PR-AUC | R@FPR5 | ECE | p50/p99 ms | USD / 1k |", "|---|---|---|---|---|---|---|"]
        for k, c in r["cascade"].items():
            lines.append(f"| {k} | {_f(c['escalation_rate'])} | {_f(c['pr_auc'])} | {_f(c['recall_at_fpr5'])} | {_f(c['ece'])} | {_f(c['lat_p50_ms'],0)} / {_f(c['lat_p99_ms'],0)} | {_f(c['lat_usd_per_1k'],4)} |")
        lines.append("")
    if r.get("loadtests"):
        lines += ["## Load tests (primary only)", "", "| model | multiplier | eff. rate/s | p50 ms | p99 ms | error rate |", "|---|---|---|---|---|---|"]
        for s in sorted(r["loadtests"].values(), key=lambda s: (s["model"], s["multiplier"])):
            lines.append(f"| {s['model']} | {s['multiplier']}x | {_f(s['effective_rate_per_s'],2)} | {_f(s['p50_ms'],0)} | {_f(s['p99_ms'],0)} | {_f(s['error_rate'])} |")
        lines.append("")
    lines += ["## Pre-registered hypotheses", "", "```json", json.dumps(r["hypotheses"], indent=2), "```", ""]
    return "\n".join(lines)


def _figures(r: dict, run_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    for v in ("primary", "all"):
        series = [(m, b[v]["primary"]["test"]["reliability"]) for m, b in r["models"].items() if v in b and "test" in b[v]["primary"]]
        if not series:
            continue
        fig, ax = plt.subplots(figsize=(5.5, 5))
        ax.plot([0, 1], [0, 1], ls="--", c="#999", lw=1, label="perfect")
        for m, rel in series:
            ax.plot([b["mean_pred"] for b in rel], [b["frac_pos"] for b in rel], marker="o", ms=4, label=m)
        ax.set_xlabel("mean predicted P(revert)")
        ax.set_ylabel("observed revert rate")
        ax.set_title(f"Reliability, test split ({v})")
        ax.set_xscale("log"); ax.set_yscale("log")
        ax.set_xlim(1e-3, 1); ax.set_ylim(1e-3, 1)
        ax.legend(frameon=False)
        fig.tight_layout()
        fig.savefig(run_dir / f"reliability_{v}.png", dpi=160)
        plt.close(fig)
    # cost vs PR-AUC
    pts = [(m, b["primary"]["latency_cost_test"]["usd_per_1k"], b["primary"]["primary"]["test"]["pr_auc"]) for m, b in
           ((m, {"primary": bb["primary"] if "primary" in bb else bb.get("all")}) for m, bb in r["models"].items()) if b["primary"] and "latency_cost_test" in b["primary"] and "test" in b["primary"]["primary"]]
    if pts:
        fig, ax = plt.subplots(figsize=(5.5, 4))
        for m, c, a in pts:
            ax.scatter(max(c, 1e-5), a, s=40)
            ax.annotate(m, (max(c, 1e-5), a), textcoords="offset points", xytext=(5, 3))
        ax.set_xscale("log"); ax.set_xlabel("USD per 1,000 decisions (log)"); ax.set_ylabel("PR-AUC (test)")
        ax.set_title("Cost vs accuracy")
        fig.tight_layout(); fig.savefig(run_dir / "cost_vs_prauc.png", dpi=160); plt.close(fig)
