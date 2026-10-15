"""Run2 report: semantic typed triage against LLM-judge labels, with calibration.

Inputs in a run dir:
  judge_val.jsonl, judge_test.jsonl        (labels_v2 per PR)
  judge_test_repeat.jsonl                  (optional; judge self-agreement)
  handcheck_v2.jsonl                       (optional; author labels, blind)
  predictions_<model>_<split>_<variant>.jsonl with qset == "v2"
Outputs: metrics_v2.json, tables_v2.md, reliability_v2_<q>.png
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression

from ..models.base import DECISIONS_V2, PRIMARY_V2
from . import metrics as M

R2 = {"H1_roc_auc_min": 0.80, "H1_ece_cal_max": 0.05, "H2_roc_margin": 0.03, "H2_ece_margin": 0.02, "H3_multi_cost_ratio": 1.3,
      "H4_escalation": 0.25, "H4_capture_min": 0.70}


def _load_labels(run_dir: Path, split: str) -> dict[int, dict]:
    p = run_dir / f"judge_{split}.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.open():
        r = json.loads(line)
        out[r["pr_number"]] = r["labels_v2"]
    return out


def _load_preds(run_dir: Path) -> dict[tuple[str, str, str], dict[int, dict]]:
    out = defaultdict(dict)
    for p in sorted(run_dir.glob("predictions_*.jsonl")):
        _, model, split, variant = p.stem.split("_", 3)
        for line in p.open():
            row = json.loads(line)
            if row.get("qset") != "v2":
                continue
            out[(model, split, variant)][row["pr_number"]] = row
    return out


def _xy(preds: dict[int, dict], labels: dict[int, dict], q: str):
    keys = sorted(set(preds) & set(labels))
    y = np.array([int(labels[k][q]) for k in keys])
    p = np.array([preds[k]["decision"]["probs"].get(q, float("nan")) for k in keys], dtype=float)
    m = ~np.isnan(p)
    return y[m], p[m]


def _calibrate(yv, pv, pt):
    if len(yv) < 30 or yv.sum() == 0 or yv.sum() == len(yv):
        return pt, False
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(pv, yv)
    return iso.predict(pt), True


def _agreement(a: dict[int, dict], b: dict[int, dict]) -> dict:
    keys = sorted(set(a) & set(b))
    out = {"n": len(keys)}
    for q in DECISIONS_V2:
        if keys:
            out[q] = float(np.mean([a[k][q] == b[k][q] for k in keys]))
    return out


def run(run_dir: Path) -> None:
    lab_v, lab_t = _load_labels(run_dir, "val"), _load_labels(run_dir, "test")
    preds = _load_preds(run_dir)
    rep = {"run_dir": str(run_dir), "thresholds": R2, "labels": {"val_n": len(lab_v), "test_n": len(lab_t)},
           "label_prevalence_test": {q: float(np.mean([v[q] for v in lab_t.values()])) if lab_t else None for q in DECISIONS_V2},
           "models": {}, "judge_reliability": {}, "hypotheses": {}}

    rpt = run_dir / "judge_test_repeat.jsonl"
    if rpt.exists():
        rep["judge_reliability"]["self_agreement"] = _agreement(lab_t, {json.loads(l)["pr_number"]: json.loads(l)["labels_v2"] for l in rpt.open()})
    hc = run_dir / "handcheck_v2.jsonl"
    if hc.exists():
        human = {}
        for l in hc.open():
            r = json.loads(l)
            if r.get("labels_v2") and all(v is not None for v in r["labels_v2"].values()):
                human[r["pr_number"]] = r["labels_v2"]
        rep["judge_reliability"]["judge_vs_human"] = _agreement(lab_t, human)

    unreliable = {q for q, a in rep["judge_reliability"].get("judge_vs_human", {}).items() if q != "n" and a < 0.80}
    rep["label_unreliable_questions"] = sorted(unreliable)

    models = sorted({m for (m, _, _) in preds})
    for m in models:
        rep["models"][m] = {}
        for variant in ("all", "primary", "all_words"):
            pv_rows, pt_rows = preds.get((m, "val", variant), {}), preds.get((m, "test", variant), {})
            if not pt_rows:
                continue
            block = {"questions": {}, "latency_cost_test": _lat_cost(pt_rows)}
            qs = DECISIONS_V2 if variant.startswith("all") else (PRIMARY_V2,)
            for q in qs:
                yt, pt = _xy(pt_rows, lab_t, q)
                if len(yt) == 0:
                    continue
                yv, pv = _xy(pv_rows, lab_v, q) if pv_rows else (np.array([]), np.array([]))
                pc, calibrated = _calibrate(yv, pv, pt)
                d = {
                    "n": int(len(yt)), "positives": int(yt.sum()),
                    "roc_auc": M.roc_auc(yt, pt), "pr_auc": M.pr_auc(yt, pt),
                    "acc_raw_0.5": M.accuracy(yt, pt), "acc_cal_0.5": M.accuracy(yt, pc),
                    "ece_raw": M.ece(yt, pt), "ece_cal": M.ece(yt, pc), "brier_raw": M.brier(yt, pt), "brier_cal": M.brier(yt, pc),
                    "calibrated_on_val": calibrated, "reliability_raw": M.reliability(yt, pt), "reliability_cal": M.reliability(yt, pc),
                }
                lo, hi = M.bootstrap_ci(yt, pt, M.roc_auc)
                d["roc_auc_ci95"] = [lo, hi]
                if q == PRIMARY_V2:
                    # routing: escalate top 25% by calibrated P(needs_human); capture of judge-positives
                    k = int(math.ceil(R2["H4_escalation"] * len(pc)))
                    idx = np.argsort(-pc)[:k]
                    d["routing_capture_at_25pct"] = float(yt[idx].sum() / max(yt.sum(), 1))
                block["questions"][q] = d
            rep["models"][m][variant] = block

    rep["hypotheses"] = _hyp(rep)
    (run_dir / "metrics_v2.json").write_text(json.dumps(rep, indent=2, default=float))
    (run_dir / "tables_v2.md").write_text(_tables(rep))
    _figs(rep, run_dir)
    print((run_dir / "tables_v2.md").read_text())


def _lat_cost(rows):
    ds = [r["decision"] for r in rows.values() if r["decision"].get("error") is None]
    lat = sorted(d["latency_ms"] for d in ds)
    n = len(ds)
    return {"n_ok": n, "error_rate": 1 - n / max(len(rows), 1), "p50_ms": lat[n // 2] if n else None,
            "p99_ms": lat[min(int(n * 0.99), n - 1)] if n else None,
            "usd_per_1k": sum(d["cost_usd"] for d in ds) / n * 1000 if n else 0.0,
            "mean_input_tokens": sum(d["input_tokens"] for d in ds) / n if n else 0}


def _hyp(rep):
    H = {}
    unrel = set(rep["label_unreliable_questions"])
    get = lambda m, v, q: rep["models"].get(m, {}).get(v, {}).get("questions", {}).get(q)
    H["R2_H1_useful"] = {m: {q: bool(d["roc_auc"] >= R2["H1_roc_auc_min"] and d["ece_cal"] <= R2["H1_ece_cal_max"]) for q, d in blk["all"]["questions"].items() if q not in unrel}
                        for m, blk in rep["models"].items() if "all" in blk}
    if "jev" in rep["models"] and "haiku" in rep["models"]:
        H["R2_H2_jev_vs_haiku"] = {}
        for q in DECISIONS_V2:
            j, h = get("jev", "all", q), get("haiku", "all", q)
            if j and h and q not in unrel:
                H["R2_H2_jev_vs_haiku"][q] = {"competitive": bool(j["roc_auc"] >= h["roc_auc"] - R2["H2_roc_margin"] and j["ece_cal"] <= h["ece_cal"] + R2["H2_ece_margin"]),
                                              "better": bool(j["roc_auc"] > h["roc_auc"] and j["ece_cal"] < h["ece_cal"]),
                                              "jev_roc": j["roc_auc"], "haiku_roc": h["roc_auc"], "jev_ece_cal": j["ece_cal"], "haiku_ece_cal": h["ece_cal"]}
    for m in ("jev", "haiku"):
        a, p = rep["models"].get(m, {}).get("all", {}).get("latency_cost_test"), rep["models"].get(m, {}).get("primary", {}).get("latency_cost_test")
        if a and p and p["p50_ms"] and p["usd_per_1k"]:
            H.setdefault("R2_H3_multi_question_cost", {})[m] = {"p50_ratio": a["p50_ms"] / p["p50_ms"], "usd_ratio": a["usd_per_1k"] / p["usd_per_1k"],
                                                                "holds": bool(a["p50_ms"] / p["p50_ms"] <= R2["H3_multi_cost_ratio"] and a["usd_per_1k"] / p["usd_per_1k"] <= R2["H3_multi_cost_ratio"])}
    H["R2_H4_routing"] = {m: {"capture_at_25pct": d["routing_capture_at_25pct"], "holds": bool(d["routing_capture_at_25pct"] >= R2["H4_capture_min"])}
                          for m in rep["models"] for d in [get(m, "all", PRIMARY_V2)] if d and PRIMARY_V2 not in unrel}
    return H


def _f(x, nd=3):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def _tables(rep):
    L = ["# Run 2 results: semantic typed triage", ""]
    L += [f"Judge-labelled test PRs: {rep['labels']['test_n']}, val: {rep['labels']['val_n']}. Prevalence (test): " +
          ", ".join(f"{q}={_f(v,2)}" for q, v in rep["label_prevalence_test"].items()), ""]
    jr = rep["judge_reliability"]
    if jr:
        L += ["## Judge reliability", "", "| check | n | " + " | ".join(DECISIONS_V2) + " |", "|---|---|" + "---|" * len(DECISIONS_V2)]
        for name, a in jr.items():
            L.append(f"| {name} | {a.get('n')} | " + " | ".join(_f(a.get(q), 2) for q in DECISIONS_V2) + " |")
        if rep["label_unreliable_questions"]:
            L.append(f"\nQuestions excluded as label-unreliable (<0.80 judge-vs-human): {rep['label_unreliable_questions']}")
        L.append("")
    for q in DECISIONS_V2:
        rows = []
        for m, blk in rep["models"].items():
            d = blk.get("all", {}).get("questions", {}).get(q)
            if d:
                rows.append(f"| {m} | {_f(d['roc_auc'])} [{_f(d['roc_auc_ci95'][0])}, {_f(d['roc_auc_ci95'][1])}] | {_f(d['pr_auc'])} | {_f(d['acc_raw_0.5'])} / {_f(d['acc_cal_0.5'])} | {_f(d['ece_raw'])} / {_f(d['ece_cal'])} | {_f(d['brier_raw'])} / {_f(d['brier_cal'])} |")
        if rows:
            L += [f"## `{q}` (test, all five questions in one request)", "", "| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |", "|---|---|---|---|---|---|", *rows, ""]
    L += ["## Latency and cost (test)", "", "| model | variant | p50 ms | p99 ms | USD / 1k | mean input tok | err |", "|---|---|---|---|---|---|---|"]
    for m, blk in rep["models"].items():
        for v, b in blk.items():
            lc = b["latency_cost_test"]
            L.append(f"| {m} | {v} | {_f(lc['p50_ms'],0)} | {_f(lc['p99_ms'],0)} | {_f(lc['usd_per_1k'],4)} | {_f(lc['mean_input_tokens'],0)} | {_f(lc['error_rate'])} |")
    L += ["", "## Pre-registered hypotheses (R2)", "", "```json", json.dumps(rep["hypotheses"], indent=2), "```", ""]
    return "\n".join(L)


def _figs(rep, run_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    for q in DECISIONS_V2:
        series = [(m, blk["all"]["questions"][q]) for m, blk in rep["models"].items() if "all" in blk and q in blk["all"]["questions"]]
        if not series:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(9, 4), sharey=True)
        for ax, key, title in ((axes[0], "reliability_raw", "raw"), (axes[1], "reliability_cal", "calibrated on val")):
            ax.plot([0, 1], [0, 1], ls="--", c="#999", lw=1)
            for m, d in series:
                ax.plot([b["mean_pred"] for b in d[key]], [b["frac_pos"] for b in d[key]], marker="o", ms=4, label=m)
            ax.set_title(f"{q} ({title})"); ax.set_xlabel("mean predicted P"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        axes[0].set_ylabel("observed rate"); axes[0].legend(frameon=False)
        fig.tight_layout(); fig.savefig(run_dir / f"reliability_v2_{q}.png", dpi=150); plt.close(fig)
