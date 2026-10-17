"""Gateway leaderboard: rank every decision model that has been run under PROTOCOL.md.

This is REPORTING, not a pre-registered hypothesis. It reads the frozen per-run artefacts
(results/run*/metrics.json, predictions_*_test_all.jsonl, loadtest_*_x100.json) and never re-scores.

Four axes, each the thing a merge gateway actually pays for:
  quality      mean ROC AUC vs the run's ground truth, averaged over the runs the model took part in
               (run1 revert-within-30d; run2 judge labels, 5 questions; run3 agent-PR rejection; run4 same_change pairs)
  calibration  mean ECE after the run's calibration step (lower is better)
  speed        p99 latency in ms at 100× the base PR rate if a loadtest exists, else in-run p99
  cost         USD per 1 000 PRs at the run's recorded list prices (results/prices.json)
plus a hard gate:
  scale        passed the run's H5/R3-H5 bar (0 errors and p99 within +200 ms of single-call p99 at 100×)
               in every run where it was load-tested. A model that fails scale is listed but cannot rank above
               one that passes, however good its AUC: a gate that queues PRs is not a gate.

Overall = Borda count over the four axes (rank 1 = best, missing axis = last), scale-pass models first.
Models in prompts/models.yaml that have not been run appear in a separate "not yet run" table with
vendor/leaderboard claims only, clearly marked as unmeasured.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

RESULTS = Path(__file__).resolve().parents[2] / "results"
RUNS = ("run1", "run2", "run3", "run4")
KIND = {"rules": "floor (no model)", "gbm": "floor (trained classifier)", "mock": "floor"}
RUN_LABEL = {"run1": "revert (k8s)", "run2": "judge labels ×5", "run3": "agent-PR outcome", "run4": "same_change pairs"}


def _q(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def _pred_stats(run_dir: Path, model: str) -> dict | None:
    p = run_dir / f"predictions_{model}_test_all.jsonl"
    if not p.exists():
        return None
    lat, cost, n, err = [], 0.0, 0, 0
    for line in p.open():
        d = json.loads(line)["decision"]
        n += 1
        if d.get("error"):
            err += 1
            continue
        lat.append(d.get("latency_ms", 0.0)); cost += d.get("cost_usd", 0.0)
    if not lat:
        return None
    return {"n": n, "error_rate": err / n, "p50_ms": float(np.percentile(lat, 50)), "p99_ms": float(np.percentile(lat, 99)), "usd_per_1k": 1000 * cost / len(lat)}


def _quality(run: str, m: dict, model: str) -> tuple[float | None, float | None]:
    """(mean ROC AUC vs ground truth, mean calibrated ECE) for this run's primary labelled task."""
    try:
        if run == "run1":
            t = m["models"][model]["all"]["primary"]["test"]
            return _q(t["roc_auc"]), _q(t.get("ece"))
        if run == "run2":
            qs = m["models"][model]["all"]["questions"]
            return float(np.mean([v["roc_auc"] for v in qs.values()])), float(np.mean([v["ece_cal"] for v in qs.values()]))
        if run == "run3":
            t = m["models"][model]["primary"]
            return _q(t["roc_auc"]), _q(t.get("ece"))
        if run == "run4":
            t = m["models"][model]["pair_test"]
            return _q(t["roc_auc"]), None
    except KeyError:
        return None, None
    return None, None


def _scale(run: str, m: dict, model: str) -> bool | None:
    h = m.get("hypotheses", {})
    if run == "run1":
        v = h.get("H5_holds_at_scale", {}).get(model)
        return None if v is None else bool(v)
    if run == "run3":
        v = h.get(f"R3-H5[{model} x100]")
        return None if v is None else bool(v["pass"])
    return None


def _loadtest_p99(run_dir: Path, model: str) -> float | None:
    p = run_dir / f"loadtest_{model}_x100.json"
    if not p.exists():
        return None
    return float(json.loads(p.read_text())["summary"]["p99_ms"])


def collect() -> dict[str, dict]:
    rows: dict[str, dict] = defaultdict(lambda: {"runs": {}, "scale": None, "auc": [], "ece": [], "p99": [], "p99_100x": [], "usd": []})
    for run in RUNS:
        rd = RESULTS / run
        mp = rd / ("metrics_v2.json" if run == "run2" else "metrics.json")
        if not mp.exists():
            continue
        m = json.loads(mp.read_text())
        models = set(m.get("models", {}).keys())
        for model in sorted(models):
            auc, ece = _quality(run, m, model)
            ps = _pred_stats(rd, model)
            sc = _scale(run, m, model)
            lt = _loadtest_p99(rd, model)
            r = rows[model]
            r["runs"][run] = {"auc": auc, "ece": ece, "scale": sc, "p99_100x": lt, **(ps or {})}
            if auc is not None: r["auc"].append(auc)
            if ece is not None: r["ece"].append(ece)
            if ps: r["p99"].append(ps["p99_ms"]); r["usd"].append(ps["usd_per_1k"])
            if lt is not None: r["p99_100x"].append(lt)
            if sc is not None:
                r["scale"] = sc if r["scale"] is None else (r["scale"] and sc)
    out = {}
    for model, r in rows.items():
        out[model] = {
            "runs": r["runs"],
            "n_runs": len(r["runs"]),
            "quality": float(np.mean(r["auc"])) if r["auc"] else None,
            "calibration": float(np.mean(r["ece"])) if r["ece"] else None,
            "speed_p99_ms": float(max(r["p99_100x"])) if r["p99_100x"] else (float(np.mean(r["p99"])) if r["p99"] else None),
            "speed_source": "loadtest x100" if r["p99_100x"] else ("in-run" if r["p99"] else None),
            "cost_usd_per_1k": float(np.mean(r["usd"])) if r["usd"] else None,
            "scale_pass": r["scale"],
        }
    return out


def rank(board: dict[str, dict]) -> list[tuple[str, dict]]:
    axes = {"quality": (lambda v: -v if v is not None else None), "calibration": (lambda v: v), "speed_p99_ms": (lambda v: v), "cost_usd_per_1k": (lambda v: v)}
    names = list(board)
    borda = {n: 0 for n in names}
    for ax, key in axes.items():
        vals = [(key(board[n][ax]) if board[n][ax] is not None else None, n) for n in names]
        present = sorted([v for v in vals if v[0] is not None])
        last = len(names)
        for i, (_, n) in enumerate(present):
            borda[n] += i + 1
            board[n].setdefault("rank", {})[ax] = i + 1
        for v, n in vals:
            if v is None:
                borda[n] += last
                board[n].setdefault("rank", {})[ax] = None
    for n in names:
        board[n]["borda"] = borda[n]
    # scale-pass first (True > None > False), then Borda ascending, then quality
    order = sorted(names, key=lambda n: ({True: 0, None: 1, False: 2}[board[n]["scale_pass"]], board[n]["borda"], -(board[n]["quality"] or 0)))
    return [(n, board[n]) for n in order]


def _f(v, fmt="{:.3f}", na="—"):
    return na if v is None else fmt.format(v)


def render(ranked: list[tuple[str, dict]], registry: dict) -> str:
    L = ["# Gateway leaderboard", "",
         "Reporting only (not pre-registered). Ranks every decision model that has been run under PROTOCOL.md on the four things a merge",
         "gateway pays for, with the scale bar as a hard gate: a model that fails H5 (errors or queueing at 100× load) is listed but cannot",
         "rank above one that passes. Numbers are copied from the frozen per-run artefacts; nothing is re-scored here.",
         "", "Axes: **quality** = mean ROC AUC vs the run's ground truth over the runs the model took part in · **calibration** = mean ECE after",
         "calibration (lower better) · **speed** = p99 ms at 100× base rate (loadtest) or in-run p99 · **cost** = USD per 1 000 PRs at recorded",
         "list prices. **Overall** = Borda over the four axes (lower better); missing axis counts last.", "",
         "| # | model | kind | scale (H5) | quality AUC | calibration ECE | speed p99 ms | cost $/1k PRs | runs | overall |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for i, (n, b) in enumerate(ranked, 1):
        sc = {True: "pass", False: "**fail**", None: "n/a"}[b["scale_pass"]]
        rk = b.get("rank", {})
        kind = KIND.get(n) or ("LLM judge" if getattr(registry.get(n), "dialect", "") == "anthropic" else "decision model")
        L.append(f"| {i} | `{n}` | {kind} | {sc} | {_f(b['quality'])} (#{rk.get('quality') or '—'}) | {_f(b['calibration'])} (#{rk.get('calibration') or '—'}) | "
                 f"{_f(b['speed_p99_ms'], '{:.0f}')} {('(' + b['speed_source'] + ')') if b['speed_source'] else ''} (#{rk.get('speed_p99_ms') or '—'}) | "
                 f"{_f(b['cost_usd_per_1k'], '{:.3f}')} (#{rk.get('cost_usd_per_1k') or '—'}) | {b['n_runs']} | {b['borda']} |")
    L += ["", "## Per run", "", "| model | run | task | ROC AUC | ECE | p99 ms | p99 @100× | $/1k | errors | H5 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for n, b in ranked:
        for run, r in b["runs"].items():
            sc = {True: "pass", False: "fail", None: "—"}[r.get("scale")]
            L.append(f"| `{n}` | {run} | {RUN_LABEL[run]} | {_f(r.get('auc'))} | {_f(r.get('ece'))} | {_f(r.get('p99_ms'), '{:.0f}')} | {_f(r.get('p99_100x'), '{:.0f}')} | {_f(r.get('usd_per_1k'), '{:.3f}')} | {_f(r.get('error_rate'), '{:.1%}')} | {sc} |")
    ran = {n for n, _ in ranked}
    pending = [s for k, s in registry.items() if k not in ran and k != "local"]
    if pending:
        L += ["", "## In the registry, not yet run", "",
              "Listed so the next run can pick them up with `--model <name>`; the numbers here are vendor or public-leaderboard claims, **not gatebench measurements**.", "",
              "| model | dialect | weights | params (B) | list $/M in | claim |", "|---|---|---|---|---|---|"]
        for s in pending:
            L.append(f"| `{s.name}` | {s.dialect} | {s.weights or '—'} | {_f(s.params_b, '{:g}')} | {_f(s.input_per_mtok, '{:.3f}')} | {s.notes} |")
    L += ["", "## How to read it", "",
          "- Quality AUCs are **not comparable across runs** (different tasks, different base rates); the average is a tie-breaker, not a score.",
          "  Run 3's AUCs (~0.55 for everyone) say the task is unpredictable from the diff, not that the models are equal.",
          "- The scale gate is the decisive column. In every run so far the LLM-judge baseline (`haiku`) fails it and the System One models pass it;",
          "  that, not a few AUC points, is why the gateway defaults to a System One model and keeps the judge for escalation.",
          "- `rules` is the free floor: no model call, ~0 ms, cost 0. Any paid model that does not beat it on quality in a run is not earning its latency.",
          "- Cost is at list price for hosted models; self-hosted open weights show 0 and are compared on p99 and quality only.", ""]
    return "\n".join(L)


def run() -> None:
    from ..models.registry import load_registry
    board = collect()
    ranked = rank(board)
    (RESULTS / "leaderboard.json").write_text(json.dumps({"ranked": [n for n, _ in ranked], "models": board}, indent=2, default=float))
    md = render(ranked, load_registry())
    (RESULTS / "leaderboard.md").write_text(md)
    print(md)
