"""Run 8 prep: three analyses on predictions already on disk. No model call, no key.

  python scripts/run8_prep.py [--root .]
        → results/run8prep/{routing,v4,drift}.json (every number, with the sha256 of each input read)
        → docs/run8-numbers.md (tables + one-line readings)

Scoring is run 7's (mergeone.eval.run7_report): AUC per v3 question = run5._score (raw answer vs `iterated` from
data/run3/dataset.jsonl); paired bootstrap over PRs, seed 7, 1,000 resamples (metrics.bootstrap_ci).

1. Routing. Per v3 question, send it to the model with the higher AUC; compare the routed mean AUC with each single
   model's mean on the PRs every model answered (all ten questions), with a paired-bootstrap CI on the difference.
   The choice is made on test (upper bound — optimistic by construction) and on val (what a run could pre-register).
2. v4 = v3 minus follows_conventions and message_matches_diff: mean AUC over 10 vs over 8 questions, per model;
   plus every line in the policy surface that names either question.
3. Drift. shift(q) = |mean p_q(now) − mean p_q(then)| and auc_delta(q) = AUC(now) − AUC(then), both on identical rows;
   which questions trip at shift > 0.10 / 0.15 / 0.20.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mergeone.eval.metrics import bootstrap_ci  # noqa: E402
from mergeone.eval.run7_report import (BOOT_N, BOOT_SEED, Inputs, _mean_auc_fn, _p, auc_of, latest,  # noqa: E402
                                       paired_ci, rank_auc)
from mergeone.models.base import DECISIONS_V3  # noqa: E402

DATASET = Path("data/run3/dataset.jsonl")
R5, R6, R7 = Path("results/run5"), Path("results/run6"), Path("results/run7")
OUT_DIR, DOC = Path("results/run8prep"), Path("docs/run8-numbers.md")
DROP = ("follows_conventions", "message_matches_diff")
V4 = tuple(q for q in DECISIONS_V3 if q not in DROP)
THRESHOLDS = (0.10, 0.15, 0.20)
POLICY_PATHS = ("docs/POLICY.md", "mergeone/gate/policy.yaml", "gate/internal/initcmd")
QS = DECISIONS_V3
NQ = len(QS)


def f3(x) -> str:
    return "—" if x is None else f"{x:.3f}"


def d3(x) -> str:
    return "—" if x is None else f"{x:+.3f}"


def ci(c) -> str:
    return "—" if not c else f"[{c[0]:+.3f}, {c[1]:+.3f}]"


# ----------------------------------------------------------------- loading
class Data:
    def __init__(self, root: Path):
        self.inp = Inputs(root)
        ds = self.inp.jsonl(DATASET)
        self.split = {r["pr_number"]: r["split"] for r in ds}
        self.y = {r["pr_number"]: int(bool(r["labels"]["iterated"])) for r in ds}

    def v3(self, *paths: Path, split: str) -> dict[int, dict]:
        """Error-free joint v3 rows on one split of the run-3 dataset; later files win on a repeated PR."""
        rows = []
        for p in paths:
            rows += self.inp.jsonl(p) or []
        return {k: r for k, r in latest(rows, "v3").items() if self.split.get(k) == split}


def answered_all(rows: dict[int, dict], qs=QS) -> set[int]:
    return {k for k, r in rows.items() if all(_p(r, q) is not None for q in qs)}


def aucs(rows: dict[int, dict], y: dict[int, int], keys: set[int] | None = None) -> dict[str, float]:
    sub = rows if keys is None else {k: rows[k] for k in keys}
    return {q: auc_of(sub, y, q)[0] for q in QS}


# ----------------------------------------------------------------- 1. routing
def route(test: dict[str, dict], y: dict[int, int], choice: dict[str, str]) -> dict:
    """Routed predictor (question q answered by model choice[q]) vs each single model on the PRs all models answered."""
    keys = sorted(set.intersection(*(answered_all(r) for r in test.values())) & set(y))
    yy = np.array([y[k] for k in keys], dtype=int)
    col = lambda m, q: [_p(test[m][k], q) for k in keys]
    routed = np.array([col(choice[q], q) for q in QS], dtype=float).T
    per_model = {m: aucs(rows, y, set(keys)) for m, rows in test.items()}
    routed_auc = {q: per_model[choice[q]][q] for q in QS}
    out = {"n_prs": len(keys), "choice": choice, "routed_auc": routed_auc,
           "routed_mean": float(np.mean(list(routed_auc.values()))), "vs": {}}
    fn = _mean_auc_fn(NQ)
    for m in test:
        single = np.array([col(m, q) for q in QS], dtype=float).T
        P = np.hstack([routed, single])
        lo, hi = bootstrap_ci(yy, P, fn, n=BOOT_N, seed=BOOT_SEED)
        out["vs"][m] = {"single_mean": float(np.mean(list(per_model[m].values()))), "delta": fn(yy, P), "ci95": [lo, hi]}
    out["per_model_auc"] = per_model
    return out


def choose(auc_by_model: dict[str, dict[str, float]]) -> dict[str, str]:
    """Per question, the model with the higher AUC (ties → first listed)."""
    ms = list(auc_by_model)
    return {q: max(ms, key=lambda m: (auc_by_model[m][q], -ms.index(m))) for q in QS}


def routing(D: Data) -> dict:
    y = D.y
    ctrl = {m: D.v3(R7 / "control" / f"predictions_{m}_test_all.jsonl", split="test") for m in ("jev", "pplx-flash")}
    r5t = {m: D.v3(R5 / f"predictions_{m}_test_all.jsonl", split="test") for m in ("jev", "pplx-flash", "haiku", "pplx")}
    r5v = {m: D.v3(R5 / f"predictions_{m}_val_all.jsonl", split="val") for m in ("jev", "pplx-flash", "haiku", "pplx")}
    r7v = D.v3(R7 / "tune_r0" / "predictions_pplx-flash_val_all.jsonl",
               R7 / "tune_r0_iter" / "predictions_pplx-flash_val_all.jsonl", split="val")
    val_auc = {f"run5:{m}": aucs(r, y) for m, r in r5v.items() if r} | {"run7r0:pplx-flash": aucs(r7v, y)}
    keys_ctrl = set.intersection(*(answered_all(r) for r in ctrl.values()))
    test_auc_ctrl = {m: aucs(r, y, keys_ctrl) for m, r in ctrl.items()}
    out = {"val_auc": val_auc, "val_n": {f"run5:{m}": len(r) for m, r in r5v.items()} | {"run7r0:pplx-flash": len(r7v)}}
    out["control"] = {
        "test_chosen": route(ctrl, y, choose(test_auc_ctrl)),
        "val_chosen_run5": route(ctrl, y, choose({m: val_auc[f"run5:{m}"] for m in ctrl})),
        "val_chosen_run5jev_run7r0pplx": route(ctrl, y, choose({"jev": val_auc["run5:jev"], "pplx-flash": val_auc["run7r0:pplx-flash"]})),
    }
    pair = {m: r5t[m] for m in ("jev", "pplx-flash")}
    four = {m: r for m, r in r5t.items() if r}
    k2, k4 = set.intersection(*(answered_all(r) for r in pair.values())), set.intersection(*(answered_all(r) for r in four.values()))
    out["run5_reference"] = {
        "jev+pplx-flash": {"test_chosen": route(pair, y, choose({m: aucs(r, y, k2) for m, r in pair.items()})),
                           "val_chosen": route(pair, y, choose({m: val_auc[f"run5:{m}"] for m in pair}))},
        "four_models": {"test_chosen": route(four, y, choose({m: aucs(r, y, k4) for m, r in four.items()})),
                        "val_chosen": route(four, y, choose({m: val_auc[f"run5:{m}"] for m in four}))},
    }
    return out


# ----------------------------------------------------------------- 2. v4
def v4_model(rows: dict[int, dict], y: dict[int, int]) -> dict:
    a = aucs(rows, y)
    keys = sorted(answered_all(rows) & set(y))
    yy = np.array([y[k] for k in keys], dtype=int)
    P = np.array([[_p(rows[k], q) for q in QS] for k in keys], dtype=float)
    idx = [QS.index(q) for q in V4]

    def fn(yb, Pb):
        r = rank_auc(yb, Pb)
        return float(r[idx].mean() - r.mean())
    lo, hi = bootstrap_ci(yy, P, fn, n=BOOT_N, seed=BOOT_SEED)
    return {"n": len(rows), "v3_mean": float(np.mean([a[q] for q in QS])), "v4_mean": float(np.mean([a[q] for q in V4])),
            "dropped_auc": {q: a[q] for q in DROP}, "delta": float(np.mean([a[q] for q in V4]) - np.mean([a[q] for q in QS])),
            "ci95": [lo, hi], "n_ci": len(keys)}


def policy_hits(root: Path) -> list[dict]:
    files = []
    for p in POLICY_PATHS:
        q = root / p
        files += sorted(x for x in q.rglob("*") if x.is_file()) if q.is_dir() else [q] if q.exists() else []
    files += sorted(x for x in root.rglob("*.mergeone.yaml") if ".git" not in x.parts)
    hits = []
    for f in files:
        for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            for q in DROP:
                if q in line:
                    kind = "rule condition" if "when:" in line else "comment/doc"
                    hits.append({"path": str(f.relative_to(root)), "line": i, "question": q, "kind": kind, "text": line.strip()})
    return hits


def v4(D: Data, root: Path) -> dict:
    y = D.y
    sets = {"run7 control test": {m: D.v3(R7 / "control" / f"predictions_{m}_test_all.jsonl", split="test") for m in ("jev", "pplx-flash")},
            "run5 test": {m: D.v3(R5 / f"predictions_{m}_test_all.jsonl", split="test") for m in ("jev", "pplx-flash", "haiku", "pplx", "rules")}}
    return {"questions_v4": list(V4), "dropped": list(DROP),
            "models": {s: {m: v4_model(r, y) for m, r in ms.items() if r} for s, ms in sets.items()},
            "policy_hits": policy_hits(root)}


# ----------------------------------------------------------------- 3. drift
def drift_pair(now: dict[int, dict], then: dict[int, dict], y: dict[int, int]) -> dict:
    per = {}
    for q in QS:
        ks = {k for k in now if k in then and _p(now[k], q) is not None and _p(then[k], q) is not None}
        mn, mt = (float(np.mean([_p(r[k], q) for k in ks])) for r in (now, then))
        an, at = auc_of({k: now[k] for k in ks}, y, q)[0], auc_of({k: then[k] for k in ks}, y, q)[0]
        per[q] = {"n": len(ks), "mean_now": mn, "mean_then": mt, "shift": abs(mn - mt), "auc_now": an, "auc_then": at,
                  "auc_delta": an - at, "auc_delta_ci95": paired_ci(now, then, y, (q,))["ci95"]}
    trips = {f"{t:.2f}": [q for q in QS if per[q]["shift"] > t] for t in THRESHOLDS}
    moved = lambda q: per[q]["auc_delta_ci95"] is not None and (per[q]["auc_delta_ci95"][0] > 0 or per[q]["auc_delta_ci95"][1] < 0)
    return {"questions": per, "trips": trips, "max_shift": max(v["shift"] for v in per.values()),
            # AUC moved (paired CI excludes 0) — and whether the shift saw it at the lowest threshold
            "auc_moved": [q for q in QS if moved(q)],
            "auc_moved_shift_silent": [q for q in QS if moved(q) and per[q]["shift"] <= THRESHOLDS[0]],
            "tripped_auc_held": {f"{t:.2f}": [q for q in trips[f"{t:.2f}"] if not moved(q)] for t in THRESHOLDS}}


def drift(D: Data) -> dict:
    y = D.y
    r7v = D.v3(R7 / "tune_r0" / "predictions_pplx-flash_val_all.jsonl",
               R7 / "tune_r0_iter" / "predictions_pplx-flash_val_all.jsonl", split="val")
    cmp = {
        "pplx-flash val: run 5 (2026-10-18) → run 7 r0 (2026-10-23)":
            (r7v, D.v3(R5 / "predictions_pplx-flash_val_all.jsonl", split="val")),
        "jev test: run 5 (jev-1.13.0) → run 7 control (jev-1.14.0)":
            (D.v3(R7 / "control" / "predictions_jev_test_all.jsonl", split="test"), D.v3(R5 / "predictions_jev_test_all.jsonl", split="test")),
        "pplx-flash test: run 5 → run 7 control":
            (D.v3(R7 / "control" / "predictions_pplx-flash_test_all.jsonl", split="test"), D.v3(R5 / "predictions_pplx-flash_test_all.jsonl", split="test")),
        "reference — pplx-flash test: run 5 hosted → run 6A self-hosted (same weights)":
            (D.v3(R6 / "predictions_pplx-flash-local_test_all.jsonl", split="test"), D.v3(R5 / "predictions_pplx-flash_test_all.jsonl", split="test")),
    }
    return {"definition": "shift(q) = |mean p_q(now) − mean p_q(then)|, auc_delta(q) = AUC(now) − AUC(then), on the PRs both answered",
            "thresholds": list(THRESHOLDS), "comparisons": {name: drift_pair(n, t, y) for name, (n, t) in cmp.items()}}


# ----------------------------------------------------------------- markdown
def md_route(title: str, r: dict, models: list[str]) -> list[str]:
    L = [f"**{title}** (n {r['n_prs']} PRs answered by every model)", "",
         "| question | " + " | ".join(f"{m} AUC" for m in models) + " | routed to |", "|---|" + "---|" * (len(models) + 1)]
    for q in QS:
        L.append(f"| {q} | " + " | ".join(f3(r["per_model_auc"][m][q]) for m in models) + f" | {r['choice'][q]} |")
    L.append(f"| **mean** | " + " | ".join(f3(r["vs"][m]["single_mean"]) for m in models) + f" | **{f3(r['routed_mean'])}** |")
    L += ["", "| routed − single | Δ mean AUC | 95% CI (paired) |", "|---|---|---|"]
    L += [f"| vs {m} | {d3(v['delta'])} | {ci(v['ci95'])} |" for m, v in r["vs"].items()]
    return L + [""]


def doc(rt: dict, v: dict, dr: dict) -> str:
    L = ["# Run 8 prep — routing, v4 and drift from existing predictions", "",
         "No model call. `python scripts/run8_prep.py` → `results/run8prep/{routing,v4,drift}.json`. AUC per v3 question vs "
         "`iterated` (run5._score, labels data/run3/dataset.jsonl); CIs are paired bootstraps over PRs, seed 7, 1,000 resamples. "
         "Everything here is post-hoc on data already read once; nothing is a verdict.", ""]

    # 1
    L += ["## 1. Per-question routing", "",
          "Route each v3 question to the model with the higher AUC; compare the routed mean with each single model on the same PRs. "
          "Chosen on test it is an upper bound (the choice has seen the labels it is scored on); chosen on val it is what run 8 could pre-register.", ""]
    c = rt["control"]
    L += md_route("Run 7 same-day controls (test) — choice made on test (upper bound)", c["test_chosen"], ["jev", "pplx-flash"])
    L += md_route("Run 7 same-day controls (test) — choice made on run 5 val (both models)", c["val_chosen_run5"], ["jev", "pplx-flash"])
    L += md_route("Run 7 same-day controls (test) — choice made on val: jev run 5, pplx-flash run 7 round 0", c["val_chosen_run5jev_run7r0pplx"], ["jev", "pplx-flash"])
    best = lambda r: max(r["vs"], key=lambda m: r["vs"][m]["single_mean"])
    tc, vc, vc7 = c["test_chosen"], c["val_chosen_run5"], c["val_chosen_run5jev_run7r0pplx"]
    bm = best(tc)
    flips = [q for q in QS if vc["choice"][q] != tc["choice"][q]]
    flips7 = [q for q in QS if vc7["choice"][q] != tc["choice"][q]]
    L += [f"*Reading: against the better single model ({bm}, mean {f3(tc['vs'][bm]['single_mean'])}), routing chosen on test gains "
          f"{d3(tc['vs'][bm]['delta'])} {ci(tc['vs'][bm]['ci95'])}; chosen on run 5 val {d3(vc['vs'][bm]['delta'])} {ci(vc['vs'][bm]['ci95'])} "
          f"({len(flips)} of 10 choices differ from the test choice: {', '.join(flips) or 'none'}); chosen on val with run 7 r0 for "
          f"pplx-flash {d3(vc7['vs'][bm]['delta'])} {ci(vc7['vs'][bm]['ci95'])} ({len(flips7)} differ). The pre-registrable version "
          "gains nothing on the same-day controls: on most questions the two models are within 0.01–0.02 and val does not pick the winner.*", ""]
    L += ["Val AUC used for the choice (run 5 val, n 1,491 per model; run 7 r0 pplx-flash n "
          f"{rt['val_n']['run7r0:pplx-flash']}):", "",
          "| question | " + " | ".join(rt["val_auc"]) + " |", "|---|" + "---|" * len(rt["val_auc"])]
    L += [f"| {q} | " + " | ".join(f3(a[q]) for a in rt["val_auc"].values()) + " |" for q in QS]
    L += ["", "### Reference: run 5 test (2026-10-18, jev-1.13.0)", ""]
    ref = rt["run5_reference"]
    L += md_route("Run 5 test, jev + pplx-flash — choice on test", ref["jev+pplx-flash"]["test_chosen"], ["jev", "pplx-flash"])
    L += md_route("Run 5 test, jev + pplx-flash — choice on run 5 val", ref["jev+pplx-flash"]["val_chosen"], ["jev", "pplx-flash"])
    fm = list(ref["four_models"]["test_chosen"]["vs"])
    L += md_route("Run 5 test, four models — choice on test", ref["four_models"]["test_chosen"], fm)
    L += md_route("Run 5 test, four models — choice on run 5 val", ref["four_models"]["val_chosen"], fm)
    a2, a4 = ref["jev+pplx-flash"]["val_chosen"], ref["four_models"]["val_chosen"]
    b2, b4 = best(a2), best(a4)
    L += [f"*Reading: before the drift, val-chosen routing over jev + pplx-flash was {d3(a2['vs'][b2]['delta'])} {ci(a2['vs'][b2]['ci95'])} "
          f"vs {b2}, over four models {d3(a4['vs'][b4]['delta'])} {ci(a4['vs'][b4]['ci95'])} vs {b4}; most of the larger test-chosen gains "
          "come from picking the least-bad answer to the two questions that score below 0.5 (section 2).*", ""]

    # 2
    L += ["## 2. v4 = v3 minus `follows_conventions` and `message_matches_diff`", "",
          "| set | model | n | mean v3 AUC (10) | mean v4 AUC (8) | Δ | 95% CI (paired) | follows_conventions | message_matches_diff |",
          "|---|---|---|---|---|---|---|---|---|"]
    for s, ms in v["models"].items():
        for m, x in ms.items():
            L.append(f"| {s} | {m} | {x['n']} | {f3(x['v3_mean'])} | {f3(x['v4_mean'])} | {d3(x['delta'])} | {ci(x['ci95'])} | "
                     f"{f3(x['dropped_auc']['follows_conventions'])} | {f3(x['dropped_auc']['message_matches_diff'])} |")
    ms5, ms7 = v["models"]["run5 test"], v["models"]["run7 control test"]
    dv = lambda m, k: ms7[m][k] - ms5[m][k]
    L += ["", "*Reading: v4 is higher for every model, mechanically — the two dropped questions score below the other eight "
          "(below 0.5 everywhere except pplx-flash in run 5), so no question answers better. What v4 does change: run 5 → run 7 control, "
          f"pplx-flash mean moves {d3(dv('pplx-flash', 'v3_mean'))} on v3 but {d3(dv('pplx-flash', 'v4_mean'))} on v4 (the drift sits in the "
          f"two dropped questions); jev moves {d3(dv('jev', 'v3_mean'))} on v3, {d3(dv('jev', 'v4_mean'))} on v4 (tests_cover_change).*", "",
          "Policy surface naming either question (" + ", ".join(f"`{p}`" for p in POLICY_PATHS) + ", any `*.mergeone.yaml`):", "",
          "| path:line | question | kind | text |", "|---|---|---|---|"]
    L += [f"| `{h['path']}:{h['line']}` | {h['question']} | {h['kind']} | `{h['text']}` |" for h in v["policy_hits"]]
    rules = [h for h in v["policy_hits"] if h["kind"] == "rule condition"]
    L += ["", f"*Reading: {len(rules)} rule condition(s) use a dropped question"
          + (f" — the `message-mismatch` hold ({', '.join(sorted({h['path'] for h in rules}))}) reads `message_matches_diff` (a v2 "
             "question, in the default v2 set). Both engines treat an unanswered question as a non-match, so under a v4 question set "
             "that rule would silently never fire: v4 needs the rule dropped or the question kept. `follows_conventions` is in no rule "
             "(POLICY.md already recommends against it).*" if rules else ".*"), ""]

    # 3
    L += ["## 3. Drift metric on existing data", "", f"{dr['definition']}. Trip = shift strictly above the threshold.", ""]
    for name, x in dr["comparisons"].items():
        L += [f"**{name}**", "", "| question | n | mean p then | mean p now | shift | AUC then | AUC now | auc_delta | 95% CI |",
              "|---|---|---|---|---|---|---|---|---|"]
        for q in QS:
            r = x["questions"][q]
            mark = "**" if r["shift"] > THRESHOLDS[0] else ""
            L.append(f"| {q} | {r['n']} | {f3(r['mean_then'])} | {f3(r['mean_now'])} | {mark}{f3(r['shift'])}{mark} | "
                     f"{f3(r['auc_then'])} | {f3(r['auc_now'])} | {d3(r['auc_delta'])} | {ci(r['auc_delta_ci95'])} |")
        L += [""]
    L += ["Which questions trip:", "", "| comparison | " + " | ".join(f"shift > {t:.2f}" for t in THRESHOLDS) + " |",
          "|---|" + "---|" * len(THRESHOLDS)]
    for name, x in dr["comparisons"].items():
        L.append(f"| {name} | " + " | ".join(", ".join(x["trips"][f"{t:.2f}"]) or "none" for t in THRESHOLDS) + " |")
    L += ["", "Shift vs AUC (AUC moved = paired CI on auc_delta excludes 0):", "",
          "| comparison | max shift | AUC moved | AUC moved, shift ≤ 0.10 | " + " | ".join(f"tripped > {t:.2f}, AUC held" for t in THRESHOLDS) + " |",
          "|---|---|---|---|" + "---|" * len(THRESHOLDS)]
    for name, x in dr["comparisons"].items():
        L.append(f"| {name} | {f3(x['max_shift'])} | {', '.join(x['auc_moved']) or 'none'} | {', '.join(x['auc_moved_shift_silent']) or 'none'} | "
                 + " | ".join(", ".join(x["tripped_auc_held"][f"{t:.2f}"]) or "none" for t in THRESHOLDS) + " |")
    ref_max = max(x["max_shift"] for n, x in dr["comparisons"].items() if n.startswith("reference"))
    L += ["", f"*Reading: different serving of the same weights (run 6A) shifts at most {f3(ref_max)}, so 0.10 has headroom over that; "
          "0.20 catches exactly the two questions that broke, 0.15 and 0.10 also flag `exceeds_scope` (and `weakens_tests`), whose "
          "AUC held. Shift is blind to a ranking change at a constant mean: jev `tests_cover_change` lost 0.11 AUC with a 0.015 shift, "
          "so a mean-shift monitor needs the gatelog-label AUC check beside it.*", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path("."))
    a = ap.parse_args(argv)
    D = Data(a.root)
    rt, v, dr = routing(D), v4(D, a.root), drift(D)
    out = a.root / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    inputs = dict(sorted(D.inp.seen.items()))
    for name, obj in (("routing", rt), ("v4", v), ("drift", dr)):
        (out / f"{name}.json").write_text(json.dumps({**obj, "inputs_sha256": inputs}, indent=1) + "\n")
    (a.root / DOC).write_text(doc(rt, v, dr))
    print(f"wrote {OUT_DIR}/routing.json, v4.json, drift.json and {DOC}")


if __name__ == "__main__":
    main()
