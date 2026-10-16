"""Run 3 report: agent-authored PRs, outcome labels (PROTOCOL run3 addendum, R3-H1..H5).

Inputs (results/run3/):
  predictions_<model>_<split>_all.jsonl   from `mergeone predict --run run3 --dataset data/run3/dataset.jsonl --qset v2`
  loadtest_*.json                          from `mergeone loadtest --run run3 ...`   (R3-H5)
  close_reasons.jsonl                      from `mergeone close-reasons`            (reporting only)
Outputs: metrics.json, tables.md, figures/*.png
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml
from sklearn.isotonic import IsotonicRegression

from ..gate.engine import Gate, Calibrator
from ..models.base import DECISIONS_V2, PRIMARY_V2
from .metrics import roc_auc, pr_auc, bootstrap_ci, recall_at_fpr, ece, brier

GATE_DIR = Path(__file__).resolve().parent.parent / "gate"
DATASET = Path("data/run3/dataset.jsonl")  # relative to cwd (repo root)
REJECTED = "rejected"
BAD = "bad_outcome"


def _load_dataset(path: Path = DATASET) -> dict[int, dict]:
    return {r["pr_number"]: r for r in (json.loads(l) for l in path.open())}


def _load_preds(run_dir: Path) -> dict[tuple[str, str], dict[int, dict]]:
    out = defaultdict(dict)
    for p in sorted(run_dir.glob("predictions_*_all.jsonl")):
        _, model, split, _ = p.stem.split("_", 3)
        for line in p.open():
            row = json.loads(line)
            if row.get("qset") == "v2" and row["decision"].get("error") is None:
                out[(model, split)][row["pr_number"]] = row["decision"]["probs"]
    return out


def _calibrate_primary(val_preds: dict[int, dict], ds: dict[int, dict]) -> dict | None:
    """Isotonic map for needs_human_review fitted on val repos against `rejected` (R3.5: refit on val)."""
    keys = [k for k in val_preds if k in ds and val_preds[k].get(PRIMARY_V2) == val_preds[k].get(PRIMARY_V2)]
    if len(keys) < 30:
        return None
    x = np.array([val_preds[k][PRIMARY_V2] for k in keys]); y = np.array([int(ds[k]["labels"][REJECTED]) for k in keys])
    if y.sum() == 0 or y.sum() == len(y):
        return None
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(x, y)
    grid = np.linspace(0, 1, 101)
    return {"x": grid.tolist(), "y": iso.predict(grid).tolist()}


def _gate_for(model: str, cal_primary: dict | None) -> Gate:
    policy = yaml.safe_load((GATE_DIR / "policy.yaml").read_text())
    policy["always_human_paths"] = []  # k8s-specific; not applied to foreign repos
    maps = {}
    shipped = GATE_DIR / f"calibration_{model}_v2.json"
    if shipped.exists():
        maps = json.loads(shipped.read_text())
    if cal_primary:
        maps[PRIMARY_V2] = cal_primary
    return Gate(policy, model=None, calibrator=Calibrator(maps))


def _verdicts(gate: Gate, preds: dict[int, dict]) -> dict[int, tuple[str, str]]:
    out = {}
    for k, probs in preds.items():
        cal = gate.cal(probs)
        rule, verdict, route, reason = gate._match(cal)
        out[k] = (verdict, rule)
    return out


def _rate(keys, ds, label):
    return float(np.mean([ds[k]["labels"][label] for k in keys])) if keys else float("nan")


def _stack_rule(ds: dict[int, dict], verdicts: dict[int, tuple[str, str]]) -> dict:
    """R3-H4: block/hold below ⇒ hold above. A PR is 'above' another when its base ref equals
    that PR's head ref in the same repo (GitHub has no first-class stack metadata via REST)."""
    by_repo_head = {}
    for k, r in ds.items():
        if r.get("head_ref"):
            by_repo_head[(r["repo"], r["head_ref"])] = k
    stacked, changed = [], []
    for k, r in ds.items():
        if k not in verdicts or not r.get("base") or r["base"] == r.get("default_branch"):
            continue
        below = by_repo_head.get((r["repo"], r["base"]))
        stacked.append(k)
        if below is not None and below in verdicts and verdicts[below][0] in ("hold", "block") and verdicts[k][0] == "pass":
            changed.append(k)
    n_non_default_base = sum(1 for r in ds.values() if r.get("base") and r["base"] != r.get("default_branch"))
    return {"n_non_default_base": n_non_default_base, "n_stacked_linked": len(stacked), "n_changed_by_rule": len(changed),
            "frac_changed": (len(changed) / len(stacked)) if stacked else None,
            "note": "linking needs head_ref on the PR below; rows fetched before head_ref was recorded cannot be linked"}


def run(run_dir: Path) -> None:
    ds = _load_dataset()
    preds = _load_preds(run_dir)
    models = sorted({m for m, _ in preds})
    test_keys_all = [k for k, r in ds.items() if r["split"] == "test"]
    base_rej = _rate(test_keys_all, ds, REJECTED)
    base_bad = _rate(test_keys_all, ds, BAD)
    rep = {"dataset": {"n_val": sum(1 for r in ds.values() if r["split"] == "val"), "n_test": len(test_keys_all),
                       "repos_test": sorted({ds[k]["repo"] for k in test_keys_all}),
                       "base_rate_rejected_test": base_rej, "base_rate_bad_outcome_test": base_bad,
                       "agents_test": dict(Counter(ds[k]["agent"] for k in test_keys_all))},
           "models": {}, "hypotheses": {}}

    for m in models:
        val_p, test_p = preds.get((m, "val"), {}), preds.get((m, "test"), {})
        cal_primary = _calibrate_primary(val_p, ds)
        gate = _gate_for(m, cal_primary)
        keys = [k for k in test_p if k in ds]
        probs_cal = {k: gate.cal(test_p[k]) for k in keys}
        y = np.array([int(ds[k]["labels"][REJECTED]) for k in keys])
        entry = {"n_test": len(keys), "calibrated_primary_on_val": cal_primary is not None, "questions": {}}
        # every question vs every outcome label (reporting); primary vs rejected is the pre-registered one
        for q in DECISIONS_V2:
            p = np.array([probs_cal[k].get(q, np.nan) for k in keys], dtype=float); msk = ~np.isnan(p)
            qrow = {}
            for lab in (REJECTED, BAD, "closed_unmerged", "change_requested", "discussed", "iterated", "reverted_within_30d", "merged_untouched"):
                yy = np.array([int(ds[k]["labels"][lab]) for k in keys])[msk]
                if 0 < yy.sum() < len(yy):
                    qrow[lab] = {"roc_auc": roc_auc(yy, p[msk]), "pr_auc": pr_auc(yy, p[msk]), "base": float(yy.mean())}
            entry["questions"][q] = qrow
        p = np.array([probs_cal[k].get(PRIMARY_V2, np.nan) for k in keys], dtype=float); msk = ~np.isnan(p)
        if msk.sum() and 0 < y[msk].sum() < msk.sum():
            lo, hi = bootstrap_ci(y[msk], p[msk], roc_auc)
            r5, thr = recall_at_fpr(y[msk], p[msk], 0.05)
            entry["primary"] = {"roc_auc": roc_auc(y[msk], p[msk]), "roc_auc_ci95": [lo, hi], "pr_auc": pr_auc(y[msk], p[msk]),
                                "recall_at_fpr5": r5, "ece": ece(y[msk], p[msk]), "brier": brier(y[msk], p[msk])}
        # verdicts under the default policy
        v = _verdicts(gate, test_p)
        by_verdict = defaultdict(list)
        for k, (verdict, rule) in v.items():
            if k in ds:
                by_verdict[verdict].append(k)
        entry["verdicts"] = {vd: {"n": len(ks), "frac": len(ks) / max(1, len(keys)),
                                  "bad_outcome_rate": _rate(ks, ds, BAD), "rejected_rate": _rate(ks, ds, REJECTED),
                                  "rules": dict(Counter(v[k][1] for k in ks))} for vd, ks in by_verdict.items()}
        hold_block = by_verdict.get("hold", []) + by_verdict.get("block", [])
        entry["pass_bad_rate"] = _rate(by_verdict.get("pass", []), ds, BAD)
        entry["holdblock_bad_rate"] = _rate(hold_block, ds, BAD)
        # per-agent / per-repo breakdown of the primary signal
        entry["by_agent"], entry["by_repo"] = {}, {}
        for grp, key in (("by_agent", "agent"), ("by_repo", "repo")):
            groups = defaultdict(list)
            for k in keys:
                groups[ds[k][key]].append(k)
            for g, ks in sorted(groups.items()):
                yy = np.array([int(ds[k]["labels"][REJECTED]) for k in ks]); pp = np.array([probs_cal[k].get(PRIMARY_V2, np.nan) for k in ks])
                mm = ~np.isnan(pp)
                row = {"n": len(ks), "rejected_rate": float(yy.mean()), "pass_frac": sum(1 for k in ks if v.get(k, ("",))[0] == "pass") / len(ks),
                       "pass_bad_rate": _rate([k for k in ks if v.get(k, ("",))[0] == "pass"], ds, BAD)}
                if mm.sum() >= 20 and 0 < yy[mm].sum() < mm.sum():
                    row["roc_auc"] = roc_auc(yy[mm], pp[mm])
                entry[grp][g] = row
        entry["stacks"] = _stack_rule({k: ds[k] for k in keys}, v)
        entry["latency_cost"] = _lat_cost(run_dir, m)
        rep["models"][m] = entry

    # hypotheses
    H = rep["hypotheses"]
    for m in models:
        e = rep["models"][m]
        auc = e.get("primary", {}).get("roc_auc")
        H[f"R3-H1[{m}]"] = {"roc_auc": auc, "pass": (auc is not None and auc >= 0.70),
                            "not_transferring": (auc is not None and auc < 0.60), "bar": ">= 0.70 (fail < 0.60)"}
        H[f"R3-H2[{m}]"] = {"pass_bad_rate": e["pass_bad_rate"], "base": base_bad, "pass": e["pass_bad_rate"] == e["pass_bad_rate"] and e["pass_bad_rate"] <= 0.5 * base_bad, "bar": "<= 0.5 x base"}
        H[f"R3-H3[{m}]"] = {"holdblock_bad_rate": e["holdblock_bad_rate"], "base": base_bad, "pass": e["holdblock_bad_rate"] == e["holdblock_bad_rate"] and e["holdblock_bad_rate"] >= 1.5 * base_bad, "bar": ">= 1.5 x base"}
        st = e["stacks"]
        H[f"R3-H4[{m}]"] = {**st, "pass": (st["frac_changed"] is not None and st["frac_changed"] >= 0.05), "bar": ">= 5% of linked stacked PRs change verdict (reported, no correctness threshold)"}
    for p in sorted(run_dir.glob("loadtest_*.json")):
        s = json.loads(p.read_text())["summary"]
        m = s["model"]
        model_p99 = (rep["models"].get(m, {}).get("latency_cost") or {}).get("p99_ms")
        H[f"R3-H5[{m} x{s['multiplier']:g}]"] = {"p99_ms": s["p99_ms"], "model_p99_ms": model_p99, "error_rate": s["error_rate"],
                                                 "pass": s["error_rate"] == 0 and (model_p99 is None or s["p99_ms"] <= model_p99 + 200),
                                                 "bar": "p99 <= model p99 + 200 ms and 0 errors at 100x"}
    cr = run_dir / "close_reasons.jsonl"
    if cr.exists():
        rows = [json.loads(l) for l in cr.open()]
        rep["close_reasons"] = {"n": len(rows), "by_reason": dict(Counter(r["reason"] for r in rows)),
                                "note": "classified by Claude Fable 5.1 on a sample, for reporting only (PROTOCOL R3.4); never used as a label"}
    (run_dir / "metrics.json").write_text(json.dumps(rep, indent=2, default=float))
    (run_dir / "tables.md").write_text(_tables(rep))
    try:
        _figs(rep, ds, preds, run_dir)
    except Exception as e:  # figures are nice-to-have
        print("figures skipped:", e)
    print(_tables(rep))


def _lat_cost(run_dir: Path, model: str):
    rows = []
    for p in run_dir.glob(f"predictions_{model}_*_all.jsonl"):
        for l in p.open():
            d = json.loads(l)["decision"]
            if d.get("error") is None:
                rows.append(d)
    if not rows:
        return None
    lat = sorted(r["latency_ms"] for r in rows)
    return {"n": len(rows), "p50_ms": lat[len(lat) // 2], "p99_ms": lat[int(len(lat) * 0.99)],
            "cost_usd_per_1k": 1000 * sum(r.get("cost_usd") or 0 for r in rows) / len(rows)}


def _f(x, nd=3):
    return "—" if x is None or x != x else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _tables(rep) -> str:
    L = ["# Run 3 — agent-authored PRs, outcome labels", ""]
    d = rep["dataset"]
    L += [f"val {d['n_val']} PRs · test {d['n_test']} PRs across {len(d['repos_test'])} test repos · "
          f"test base rates: rejected {_f(d['base_rate_rejected_test'])}, bad outcome {_f(d['base_rate_bad_outcome_test'])}", "",
          "## Primary: calibrated `needs_human_review` → rejected (closed-unmerged ∨ change-requested), test repos", "",
          "| model | n | ROC-AUC [95% CI] | PR-AUC | R@FPR5 | ECE | p50 ms | $/1k |", "|---|---|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        p = e.get("primary", {}); lc = e.get("latency_cost") or {}
        ci = p.get("roc_auc_ci95")
        L.append(f"| {m} | {e['n_test']} | {_f(p.get('roc_auc'))} [{_f(ci[0]) if ci else '—'}, {_f(ci[1]) if ci else '—'}] | {_f(p.get('pr_auc'))} | "
                 f"{_f(p.get('recall_at_fpr5'))} | {_f(p.get('ece'))} | {_f(lc.get('p50_ms'), 0)} | {_f(lc.get('cost_usd_per_1k'))} |")
    L += ["", "## Default policy verdicts on test (bad outcome = closed ∨ change-requested ∨ reverted)", "",
          "| model | pass % | pass bad-rate | hold+block % | hold+block bad-rate | base |", "|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        v = e["verdicts"]; hb = v.get("hold", {"n": 0}).get("n", 0) + v.get("block", {"n": 0}).get("n", 0)
        L.append(f"| {m} | {_f(100 * v.get('pass', {}).get('frac', 0), 1)} | {_f(e['pass_bad_rate'])} | {_f(100 * hb / max(1, e['n_test']), 1)} | "
                 f"{_f(e['holdblock_bad_rate'])} | {_f(rep['dataset']['base_rate_bad_outcome_test'])} |")
    L += ["", "## Every question vs every outcome (ROC-AUC, test)", ""]
    for m, e in rep["models"].items():
        labs = ["rejected", "closed_unmerged", "change_requested", "discussed", "iterated", "reverted_within_30d", "merged_untouched"]
        L += [f"### {m}", "", "| question | " + " | ".join(labs) + " |", "|---|" + "---|" * len(labs)]
        for q, qr in e["questions"].items():
            L.append(f"| {q} | " + " | ".join(_f(qr.get(l, {}).get("roc_auc")) for l in labs) + " |")
        L.append("")
    L += ["## By agent (test)", "", "| model | agent | n | rejected rate | ROC-AUC | pass % | pass bad-rate |", "|---|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        for a, r in e["by_agent"].items():
            L.append(f"| {m} | {a} | {r['n']} | {_f(r['rejected_rate'])} | {_f(r.get('roc_auc'))} | {_f(100 * r['pass_frac'], 1)} | {_f(r['pass_bad_rate'])} |")
    L += ["", "## By repo (test)", "", "| model | repo | n | rejected rate | ROC-AUC | pass % | pass bad-rate |", "|---|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        for a, r in e["by_repo"].items():
            L.append(f"| {m} | {a} | {r['n']} | {_f(r['rejected_rate'])} | {_f(r.get('roc_auc'))} | {_f(100 * r['pass_frac'], 1)} | {_f(r['pass_bad_rate'])} |")
    L += ["", "## Pre-registered criteria", "", "| hypothesis | result | bar | pass |", "|---|---|---|---|"]
    for h, r in rep["hypotheses"].items():
        shown = {k: v for k, v in r.items() if k not in ("pass", "bar", "note")}
        L.append(f"| {h} | {json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in shown.items()})} | {r['bar']} | {'✅' if r['pass'] else '❌'} |")
    if "close_reasons" in rep:
        L += ["", "## Close reasons (sample, Claude Fable 5.1, reporting only)", "", "| reason | n |", "|---|---|"]
        for k, v in sorted(rep["close_reasons"]["by_reason"].items(), key=lambda kv: -kv[1]):
            L.append(f"| {k} | {v} |")
    return "\n".join(L) + "\n"


def _figs(rep, ds, preds, run_dir: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import roc_curve
    fd = run_dir / "figures"; fd.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(5, 5))
    for m in rep["models"]:
        tp = preds.get((m, "test"), {}); keys = [k for k in tp if k in ds and tp[k].get(PRIMARY_V2) == tp[k].get(PRIMARY_V2)]
        y = [int(ds[k]["labels"][REJECTED]) for k in keys]; p = [tp[k][PRIMARY_V2] for k in keys]
        if len(set(y)) == 2:
            fpr, tpr, _ = roc_curve(y, p); ax.plot(fpr, tpr, label=f"{m} (AUC {rep['models'][m].get('primary', {}).get('roc_auc', float('nan')):.2f})")
    ax.plot([0, 1], [0, 1], "k:", lw=0.8); ax.set_xlabel("FPR"); ax.set_ylabel("TPR"); ax.set_title("needs_human_review → rejected (test repos)"); ax.legend()
    fig.tight_layout(); fig.savefig(fd / "roc_primary.png", dpi=150); plt.close(fig)
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ms = list(rep["models"]); x = np.arange(len(ms)); w = 0.38
    ax.bar(x - w / 2, [rep["models"][m]["pass_bad_rate"] for m in ms], w, label="pass")
    ax.bar(x + w / 2, [rep["models"][m]["holdblock_bad_rate"] for m in ms], w, label="hold+block")
    ax.axhline(rep["dataset"]["base_rate_bad_outcome_test"], color="k", ls="--", lw=0.8, label="base rate")
    ax.set_xticks(x); ax.set_xticklabels(ms); ax.set_ylabel("bad-outcome rate"); ax.legend(); ax.set_title("Default policy, test repos")
    fig.tight_layout(); fig.savefig(fd / "verdict_bad_rate.png", dpi=150); plt.close(fig)
