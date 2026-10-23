"""Run 7 C5: typed relation question on the 100 run-6B hold pairs (docs/run7-addendum-draft.md R7.2 C5, criterion R7-H5).

  python -m mergeone.eval.run7_relation pairs
        data/run6/hold_sample.jsonl → results/run7/relation_pairs.jsonl: the 100 (A, B) pair rows in run 4's pair-row
        format, pr_number = the run-6 pair id sha1("repo#A~B"); no model call
  python -m mergeone.eval.run7_relation predict --model pplx-flash [--model jev] --i-have-frozen-the-protocol
        builds the pairs, then `mergeone.cli predict --qset relation --dataset results/run7/relation_pairs.jsonl
        --split test --run run7/relation` per model — the path run 6B's pair predictions took (cmd_predict, the
        registry adapters, prompts/jev_relation.json / haiku_relation.md) → results/run7/relation/predictions_<model>_test_all.jsonl.
        Refuses without the flag: nothing is called before PROTOCOL.md §R7 is committed.
  python -m mergeone.eval.run7_relation report
        → results/run7/relation_metrics.json, results/run7/relation_tables.md

Pair text. The pair question (run 6B) saw run4.pair_state(A's dataset `state`, run4._render_b(candidate meta,
body[:1500], sorted(files)[:60])) built by run4.cmd_pairs over results/run6/candidates_k20.jsonl (run6b.build_k20). Both
files are gitignored and rebuildable; build_pairs() takes, in order: the row of results/run6/pairs.jsonl with the
same pair id (verbatim); else the candidate's entry in results/run6/candidates_k20.jsonl (the run-4 builder's inputs);
else the PR memory data/run4/memory/<repo>.jsonl via Memory.load + run4._mem_meta — the same MemPR fields build_k20
copies into candidates_k20 (title, state, created_at/merged_at isoformat, author, body[:1500], sorted files[:60]), so
the text is identical by construction. Each row records `state_source`.
Run 6B reused pair answers by pair id (run 4 first, then 5, then 6): of the 100 sample pairs, pplx-flash's answers come
from run 4 (90) and run 5 (8); 2 run-6 answers are not in this checkout. Runs 4 and 5 built B through the same
run4._render_b from the same memory fields (run-4 module mergeone.memory.retrieval: body[:2000] → [:1500], sorted
files[:60]; run 5 via run4._mem_meta), and data/run4/memory/*.jsonl predate the run-4 predictions (2026-10-17 02:41 vs
05:16), so the rebuilt text is the text those answers were given.

Ground truth. data/run6/hold_labels_adjudicated.jsonl (primary; R6B labels duplicate / pointer / unrelated). The
adjudicated file has no sub-class, so the sub-class of a `pointer` is read from the labellers' notes with a frozen rule
(gold_class): sources in order — adjudication rationale, the human labeller's final note (data/run6/hold_labels.jsonl,
last row per pair), the second labeller's note (hold_labels_fable.jsonl); the first source that contains a GOLD_KEYWORDS
match decides, and within it the keyword that occurs earliest wins; no match anywhere → same_area. duplicate →
duplicate, unrelated → unrelated. A hand-set file data/run7/relation_gold.jsonl {pr_number, a_number, b_number,
relation}, if present, overrides per pair.

Class names: duplicate (draft: same_change), competing (competing_fix), backport (backport / promote),
follow_up, same_area, unrelated.

Prediction → class (RELATION_THRESHOLD = 0.5 on each probability; "x" means P(x) ≥ 0.5). First matching rule wins:
  1. same_change and not same_branch           → backport
  2. same_change                               → duplicate
  3. same_goal and same_branch                 → competing   (same_change is false here)
  4. builds_on                                 → follow_up
  5. none of the four                          → unrelated   ("all low")
  6. otherwise                                 → same_area
Precedence: same_change is checked first, split by same_branch. With the order as first written (same_change →
duplicate before the backport rule) backport would be unreachable, because backport requires same_change; rules 1–2
keep "same_change first" and make backport reachable. "All low" is over all four answers, so a pair whose only high
answer is same_branch is same_area, and unrelated needs a different branch as well.

Scores (per model with relation rows): related (gold ≠ unrelated) vs unrelated ROC-AUC with score = max of the four
probabilities (the quantity rule 5 thresholds); same_change vs rest ROC-AUC with score = P(same_change) and gold =
duplicate; per gold class accuracy (share of that class predicted as that class) with Wilson 95% intervals; overall
accuracy; confusion matrix gold × predicted. Run 6's pair-question same_change on the same pairs is shown as a baseline.
Cost per PR at k = 20 = mean run-5 v3 gate call + 20 × mean relation call (list price), next to R6B-H3's $0.002.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..models.base import RELATION
from . import run6b
from .run6b import _hpid, _rows, _write, wilson

DATASET = Path("data/run3/dataset.jsonl")
MEM_DIR = Path("data/run4/memory")
RUN6_DIR = Path("results/run6")
DATA6 = Path("data/run6")
RUN_DIR = Path("results/run7")
DATA7 = Path("data/run7")
REL_RUN = "run7/relation"          # cmd_predict --run (results/<run>/predictions_<model>_test_all.jsonl)
MODELS = ("pplx-flash", "jev")     # R7.2 C5; haiku can be added with --model
FROZEN_FLAG = "--i-have-frozen-the-protocol"
RELATION_THRESHOLD = 0.5
CLASSES = ("duplicate", "competing", "backport", "follow_up", "same_area", "unrelated")
SUBCLASSES = ("competing", "backport", "follow_up", "same_area")
H5_BAR = 0.80
H3_COST_BAR = run6b.H3_COST_BAR
K = 20

# Frozen with the C5 report code. Matched case-insensitively against a labeller note.
GOLD_KEYWORDS = (
    ("competing", r"compet"),
    ("backport", r"backport|cherry[- ]?pick|promot|edge\s*(→|->)\s*stable"),
    ("follow_up", r"follow[- ]?up|\brevert|geri çevir"),
    ("same_area", r"same[- ]area|same class|same files|touches same"),
)
_GOLD_RE = [(c, re.compile(p, re.I)) for c, p in GOLD_KEYWORDS]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(r: dict) -> tuple:
    return (r["pr_number"], r["a_number"], r["b_number"])


# ----------------------------------------------------------------- pairs
def build_pairs(sample: list[dict], ds_path: Path | None = None, run6_dir: Path | None = None, mem_dir: Path | None = None) -> list[dict]:
    """One run-4-format pair row per sample row (order kept). See the module docstring for the state sources."""
    from . import run4
    ds_path, run6_dir, mem_dir = Path(ds_path or DATASET), Path(run6_dir or RUN6_DIR), Path(mem_dir or MEM_DIR)
    need = {r["pr_number"] for r in sample}
    ds = {}
    for l in ds_path.open():
        x = json.loads(l)
        if x["pr_number"] in need:
            ds[x["pr_number"]] = x
    pids = {_hpid(r["repo"], r["a_number"], r["b_number"]) for r in sample}
    verbatim = {}
    if (run6_dir / "pairs.jsonl").exists():
        for l in (run6_dir / "pairs.jsonl").open():
            x = json.loads(l)
            if x["pr_number"] in pids:
                verbatim.setdefault(x["pr_number"], x["state"])
    k20 = {}
    if (run6_dir / "candidates_k20.jsonl").exists():
        for l in (run6_dir / "candidates_k20.jsonl").open():
            x = json.loads(l)
            if x["pr_number"] in need:
                k20[x["pr_number"]] = x
    mems = {}
    out = []
    for i, r in enumerate(sample, 1):
        pid = _hpid(r["repo"], r["a_number"], r["b_number"])
        a = ds.get(r["pr_number"])
        if a is None:
            raise SystemExit(f"sample PR {r['pr_number']} ({r['repo']}#{r['a_number']}) not in {ds_path}")
        if pid in verbatim:
            state, src = verbatim[pid], "results/run6/pairs.jsonl"
        else:
            cr = k20.get(r["pr_number"])
            c = next((c for c in (cr or {}).get("candidates", []) if c["number"] == r["b_number"]), None)
            cb = ((cr or {}).get("candidate_bodies") or {}).get(str(r["b_number"]))
            if c is not None and cb is not None and "title" in c:
                state, src = run4.pair_state(a["state"], run4._render_b(c, cb.get("body", ""), cb.get("files", []))), "results/run6/candidates_k20.jsonl"
            else:
                if r["repo"] not in mems:
                    from ..memory.retrieval_v2 import Memory
                    p = mem_dir / (r["repo"].replace("/", "__") + ".jsonl")
                    if not p.exists():
                        raise SystemExit(f"no pairs.jsonl row, no candidates_k20 entry and no memory file {p} for {r['repo']}#{r['a_number']}~{r['b_number']}")
                    mems[r["repo"]] = Memory.load(p)
                mp = mems[r["repo"]].prs.get(r["b_number"])
                if mp is None:
                    raise SystemExit(f"candidate {r['repo']}#{r['b_number']} not in the PR memory")
                state = run4.pair_state(a["state"], run4._render_b(run4._mem_meta(mp), mp.body[:1500], sorted(mp.files)[:60]))
                src = "data/run4/memory (as run6b.build_k20)"
        out.append({"pr_number": pid, "split": "test", "repo": r["repo"], "a": r["a_number"], "b": r["b_number"], "kind": "hold_sample",
                    "labels": {"item": i, "a_pr_number": r["pr_number"]}, "features": {}, "state": state, "state_source": src})
    return out


def cmd_pairs(a) -> list[dict]:
    sp = DATA6 / "hold_sample.jsonl"
    rows = build_pairs(_rows(sp))
    out = RUN_DIR / "relation_pairs.jsonl"
    _write(out, rows)
    print(json.dumps({"wrote": str(out), "n": len(rows), "state_source": dict(Counter(r["state_source"] for r in rows))}, indent=2))
    return rows


# ----------------------------------------------------------------- predict (guarded)
def predict_args(model: str, dataset: Path, resume: bool, concurrency: int) -> types.SimpleNamespace:
    """The argparse namespace `mergeone.cli predict` would build for this call."""
    out = Path(__import__("mergeone.cli", fromlist=["x"]).RESULTS) / REL_RUN / f"predictions_{model}_test_all.jsonl"
    return types.SimpleNamespace(model=model, split="test", run=REL_RUN, limit=None, concurrency=concurrency, questions="all", words=False,
                                 append=resume, only_errors_from=None, only_missing_from=str(out) if resume else None, qset="relation",
                                 subset=None, dataset=str(dataset), single_question=False, compact=False, raw=None, prompt_file=None)


def cmd_predict(a) -> None:
    if not a.frozen:
        raise SystemExit(f"REFUSING to call any model: pass {FROZEN_FLAG} only after PROTOCOL.md §R7 (with the relation prompt and the "
                         "class rule of this module) is committed — R7.4: nothing is called before the protocol exists.")
    from .. import cli
    rows = cmd_pairs(a)
    for m in a.model or MODELS:
        cli.cmd_predict(predict_args(m, RUN_DIR / "relation_pairs.jsonl", a.resume, a.concurrency))
    print(f"{len(rows)} pairs asked of {', '.join(a.model or MODELS)}")


# ----------------------------------------------------------------- gold
def _notes(adj: list[dict], human: dict, second: dict) -> dict[tuple, list[str]]:
    out = {}
    for r in adj:
        k = _key(r)
        out[k] = [r.get("rationale") or "", (human.get(k) or {}).get("note") or "", (second.get(k) or {}).get("note") or ""]
    return out


def gold_class(label: str, notes: list[str]) -> str:
    """duplicate / unrelated pass through; pointer → sub-class from the first note with a keyword (earliest keyword wins)."""
    if label == "duplicate":
        return "duplicate"
    if label == "unrelated":
        return "unrelated"
    for n in notes:
        hits = [(m.start(), i, c) for i, (c, rx) in enumerate(_GOLD_RE) for m in [rx.search(n or "")] if m]
        if hits:
            return min(hits)[2]
    return "same_area"


def load_gold(adj_path: Path | None = None, human_path: Path | None = None, second_path: Path | None = None,
              override_path: Path | None = None) -> dict[int, dict]:
    """{pair_id: {item, label, relation, source}} for every adjudicated pair."""
    adj_path = Path(adj_path or DATA6 / "hold_labels_adjudicated.jsonl")
    human = run6b.read_labels(Path(human_path or DATA6 / "hold_labels.jsonl"))
    sp = Path(second_path or DATA6 / "hold_labels_fable.jsonl")
    second = {_key(r): r for r in _rows(sp)} if sp.exists() else {}
    op = Path(override_path or DATA7 / "relation_gold.jsonl")
    override = {_key(r): r["relation"] for r in _rows(op)} if op.exists() else {}
    adj = _rows(adj_path)
    notes = _notes(adj, human, second)
    out = {}
    for r in adj:
        k = _key(r)
        rel, src = (override[k], "override") if k in override else (gold_class(r["label"], notes[k]), "notes rule")
        if rel not in CLASSES:
            raise ValueError(f"{op}: unknown relation {rel!r} for {k}")
        out[_hpid(r["repo"], r["a_number"], r["b_number"])] = {"item": r.get("item"), "label": r["label"], "relation": rel, "source": src}
    return out


# ----------------------------------------------------------------- class rule
def relation_class(p: dict[str, float], t: float = RELATION_THRESHOLD) -> str:
    """Four probabilities → one class (rule table in the module docstring)."""
    hi = {q: (p.get(q) or 0.0) >= t for q in RELATION}
    if hi["same_change"] and not hi["same_branch"]:
        return "backport"
    if hi["same_change"]:
        return "duplicate"
    if hi["same_goal"] and hi["same_branch"]:
        return "competing"
    if hi["builds_on"]:
        return "follow_up"
    if not any(hi.values()):
        return "unrelated"
    return "same_area"


def related_score(p: dict[str, float]) -> float:
    return max(float(p.get(q) or 0.0) for q in RELATION)


# ----------------------------------------------------------------- report
def load_relation_preds(model: str, run_dir: Path | None = None) -> dict[int, dict]:
    """{pair_id: decision} over error-free relation rows with all four probabilities (last row wins)."""
    f = Path(run_dir or RUN_DIR / "relation") / f"predictions_{model}_test_all.jsonl"
    out = {}
    if f.exists():
        for l in f.open():
            r = json.loads(l)
            d = r["decision"]
            pr = d.get("probs") or {}
            if r.get("qset") == "relation" and d.get("error") is None and all(pr.get(q) is not None and pr[q] == pr[q] for q in RELATION):
                out[r["pr_number"]] = d
    return out


def _auc(y: list[int], s: list[float]) -> float | None:
    from .run7_evidence import auc
    return auc(y, s)


def score_model(gold: dict[int, dict], preds: dict[int, dict]) -> dict:
    keys = [k for k in gold if k in preds]
    pc = {k: relation_class(preds[k]["probs"]) for k in keys}
    g = {k: gold[k]["relation"] for k in keys}
    y_rel = [int(g[k] != "unrelated") for k in keys]
    y_dup = [int(g[k] == "duplicate") for k in keys]
    per = {}
    for c in CLASSES:
        ks = [k for k in keys if g[k] == c]
        hit = sum(pc[k] == c for k in ks)
        per[c] = {"n": len(ks), "correct": hit, "accuracy": hit / len(ks) if ks else None, "ci95": wilson(hit, len(ks))}
    conf = {gc: {p: sum(1 for k in keys if g[k] == gc and pc[k] == p) for p in CLASSES} for gc in CLASSES}
    hit = sum(pc[k] == g[k] for k in keys)
    return {"n": len(keys), "missing": len(set(gold) - set(preds)),
            "related_vs_unrelated_auc": _auc(y_rel, [related_score(preds[k]["probs"]) for k in keys]),
            "n_unrelated": len(keys) - sum(y_rel),
            "same_change_vs_rest_auc": _auc(y_dup, [float(preds[k]["probs"]["same_change"]) for k in keys]),
            "n_duplicate": sum(y_dup), "accuracy": hit / len(keys) if keys else None, "accuracy_ci95": wilson(hit, len(keys)),
            "per_class": per, "confusion": conf, "predicted": dict(Counter(pc.values()))}


def cost_k20(model: str, preds: dict[int, dict]) -> dict:
    prices = run6b.load_prices()
    calls = [run6b.list_cost(model, d, prices) for d in preds.values()]
    calls = [c for c in calls if c is not None]
    rel = sum(calls) / len(calls) if calls else None
    gate = []
    for sp in ("val", "test"):
        f = run6b.RUN5_DIR / f"predictions_{model}_{sp}_all.jsonl"
        if f.exists():
            for l in f.open():
                r = json.loads(l)
                if r.get("qset") == "v3" and r["decision"].get("error") is None:
                    gate.append(run6b.list_cost(model, r["decision"], prices) or 0.0)
    g = sum(gate) / len(gate) if gate else None
    return {"relation_call_usd": rel, "gate_call_usd": g, "k": K,
            "usd_per_pr": None if rel is None or g is None else g + K * rel, "bar_R6B-H3": H3_COST_BAR}


def run(models=None, run_dir: Path | None = None) -> dict:
    run_dir = Path(run_dir or RUN_DIR)
    gold = load_gold()
    rep = {"computed_at": _now(), "n_pairs": len(gold), "threshold": RELATION_THRESHOLD,
           "gold_classes": dict(Counter(v["relation"] for v in gold.values())),
           "gold_sources": dict(Counter(v["source"] for v in gold.values())), "models": {}, "pair_question_baseline": {}, "hypotheses": {}}
    for m in models or MODELS + ("haiku",):
        preds = load_relation_preds(m, run_dir / "relation")
        if not preds:
            if m in MODELS:
                rep["models"][m] = {"status": f"no relation predictions in {run_dir / 'relation'}"}
            continue
        e = score_model(gold, preds)
        e["cost"] = cost_k20(m, preds)
        rep["models"][m] = e
    for m in MODELS:  # run 6's pair question on the same pairs (same_change vs rest)
        pp = run6b.load_pair_preds(m)
        ks = [k for k in gold if k in pp]
        if ks:
            rep["pair_question_baseline"][m] = {"n": len(ks), "same_change_vs_rest_auc": _auc([int(gold[k]["relation"] == "duplicate") for k in ks],
                                                                                            [pp[k]["p"] for k in ks])}
    scored = {m: e for m, e in rep["models"].items() if "n" in e}
    rel = {m: e["related_vs_unrelated_auc"] for m, e in scored.items()}
    dup = {m: e["same_change_vs_rest_auc"] for m, e in scored.items()}
    ok = lambda d: any(v is not None and v >= H5_BAR for v in d.values())
    rep["hypotheses"]["R7-H5"] = {"related_vs_unrelated_auc": rel, "same_change_vs_rest_auc": dup,
                                  "pass": (ok(rel) and ok(dup)) if scored else None,
                                  "bar": f"related-vs-unrelated AUC >= {H5_BAR} for >= 1 model and same_change-vs-rest >= {H5_BAR}"}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "relation_metrics.json").write_text(json.dumps(rep, indent=2))
    t = tables(rep)
    (run_dir / "relation_tables.md").write_text(t)
    print(t)
    return rep


def _f(x, nd=3):
    return "—" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def tables(rep: dict) -> str:
    L = ["# Run 7 C5 — typed relation on the run-6B hold pairs", "",
         f"{rep['n_pairs']} pairs · gold classes: " + ", ".join(f"{c} {rep['gold_classes'].get(c, 0)}" for c in CLASSES)
         + f" · gold source: " + ", ".join(f"{k} {v}" for k, v in rep["gold_sources"].items()) + f" · threshold {rep['threshold']}", "",
         "## R7-H5", "", "| model | n | related vs unrelated AUC (n unrelated) | same_change vs rest AUC (n duplicate) | accuracy [95% CI] | $/PR at k=20 |",
         "|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        if "n" not in e:
            L.append(f"| {m} | — | {e['status']} | | | |")
            continue
        ci = e["accuracy_ci95"]
        L.append(f"| {m} | {e['n']} | {_f(e['related_vs_unrelated_auc'])} ({e['n_unrelated']}) | {_f(e['same_change_vs_rest_auc'])} ({e['n_duplicate']}) | "
                 f"{_f(e['accuracy'])} [{_f(ci[0]) if ci else '—'}, {_f(ci[1]) if ci else '—'}] | {_f(e['cost']['usd_per_pr'], 5)} |")
    for m, b in rep["pair_question_baseline"].items():
        L.append(f"| {m} — run-6 pair question (baseline) | {b['n']} | — | {_f(b['same_change_vs_rest_auc'])} | — | — |")
    h = rep["hypotheses"]["R7-H5"]
    L += ["", f"R7-H5 bar: {h['bar']} → **{'pending' if h['pass'] is None else ('pass' if h['pass'] else 'fail')}**",
          f"(cost bar for reference: R6B-H3 ≤ ${H3_COST_BAR} per PR)"]
    for m, e in rep["models"].items():
        if "n" not in e:
            continue
        L += ["", f"## {m}: accuracy per gold class (Wilson 95%)", "", "| gold class | n | correct | accuracy [95% CI] |", "|---|---|---|---|"]
        for c in CLASSES:
            p = e["per_class"][c]
            ci = p["ci95"]
            L.append(f"| {c} | {p['n']} | {p['correct']} | {_f(p['accuracy'])} [{_f(ci[0]) if ci else '—'}, {_f(ci[1]) if ci else '—'}] |")
        L += ["", f"### {m}: confusion (rows gold, columns predicted)", "", "| gold \\ pred | " + " | ".join(CLASSES) + " |",
              "|---|" + "---|" * len(CLASSES)]
        for gc in CLASSES:
            L.append(f"| {gc} | " + " | ".join(str(e["confusion"][gc][p]) for p in CLASSES) + " |")
    L += ["", "## Notes", "",
          "- Gold sub-classes come from the labellers' notes by a frozen keyword rule (run7_relation.GOLD_KEYWORDS); "
          "data/run7/relation_gold.jsonl overrides per pair when present.",
          "- Class rule and precedence: run7_relation module docstring. Sub-class accuracy is reported, not a bar (n is small).",
          "- related-vs-unrelated AUC rests on very few unrelated pairs (the sample is drawn from holds); read it with that n."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run7_relation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pairs", help="build the 100 pair rows → results/run7/relation_pairs.jsonl (no model call)").set_defaults(fn=cmd_pairs)
    s = sub.add_parser("predict", help="ask the relation question (guarded)")
    s.add_argument("--model", action="append", help=f"repeatable; default {', '.join(MODELS)}")
    s.add_argument("--concurrency", type=int, default=8)
    s.add_argument("--resume", action="store_true", help="append, skipping pairs already answered without error")
    s.add_argument(FROZEN_FLAG, dest="frozen", action="store_true")
    s.set_defaults(fn=cmd_predict)
    s = sub.add_parser("report"); s.add_argument("--model", action="append"); s.set_defaults(fn=lambda a: run(tuple(a.model) if a.model else None))
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
