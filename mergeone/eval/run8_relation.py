"""Run 8: relation2 — three binaries the diff can answer, plus metadata — on the 100 run-6B hold pairs.

  python -m mergeone.eval.run8_relation pairs
        data/run6/hold_sample.jsonl → results/run8/relation_pairs.jsonl with run7_relation.build_pairs (same pair ids, same
        text sources, same order); no model call
  python -m mergeone.eval.run8_relation predict --model pplx-flash [--model jev] [--compact] --i-have-frozen-the-protocol
        builds the pairs, then `mergeone.cli predict --qset relation2 --dataset results/run8/relation_pairs.jsonl --split test
        --run run8/relation` per model (prompts/jev_relation2.json / haiku_relation2.md)
        → results/run8/relation/predictions_<model>_test_all[_compact].jsonl. Refuses without the flag.
  python -m mergeone.eval.run8_relation report
        → results/run8/relation_metrics.json, results/run8/relation_tables.md (no model call)

Questions (QUESTION_SETS["relation2"], three independent noul probabilities, nothing the metadata already knows):
  same_change  B makes A unnecessary (B already makes A's change)
  builds_on    A depends on or follows B (continues, extends, fixes or reverts B)
  same_goal    same bug / feature, different method
Run 7's fourth question, same_branch, is gone: the base branch is read from metadata.

Metadata inputs (per pair; no model). For A and B: `base`, `author`, `created_at` from data/run3/dataset.jsonl (keyed by
repo + number), else from the raw PR memory data/run4/memory/<repo>.jsonl — run7_report.pr_fields, the lookup run 7's
post-hoc section B used for the base branch (run7_report.base_branches is pr_fields(..., ("base",)).
  same_base   base(A) == base(B); unknown when either is missing → treated as True (the shared default branch, the common
              case) and counted as `n_base_unknown`
  same_author author(A) == author(B); None when either is missing
  a_newer     created_at(A) > created_at(B); None when either is missing

Class rule (frozen with this module; HIGH = 0.5, LOW = 0.25; "x" means P(x) ≥ HIGH). First matching rule wins:
  1. same_change and not same_base     → backport
  2. same_change and same_base         → duplicate
  3. builds_on                         → follow_up
  4. same_goal (same_change is false)  → competing
  5. any of the three P ≥ LOW          → same_area
  6. otherwise                         → unrelated
same_author and a_newer are inputs of relation2_class (so the signature does not change if a later run uses them) but this
rule does not consult them; the report tabulates them per gold class. LOW = HIGH / 2: a pair the model rates at least
"possibly related" on any question is same_area, not unrelated.

Gold, classes and pair ids: run7_relation (load_gold, GOLD_KEYWORDS, CLASSES, the sha1 pair id).

Scores per model: per gold class accuracy with Wilson 95% intervals, overall accuracy, confusion gold × predicted,
same_change-vs-rest ROC-AUC (score P(same_change), positive = gold duplicate, as run 7) and, secondary, with gold
backport also positive (relation2's same_change is "B makes A unnecessary", which a backport also is), related-vs-unrelated
ROC-AUC (score = max of the three probabilities).

Same-day control: run 7's `relation` question asked again on the run-8 day into the same predictions file
(`--qset relation --run run8/relation`; rows are kept apart by `qset`), scored like the baseline row, so a provider drift
between run 7 and run 8 does not read as a relation2 effect.

Baseline row (the pre-registered comparison point): run 7's relation predictions (results/run7/relation/, qset relation)
re-scored through THIS rule — P(same_change), P(builds_on), P(same_goal) from the run-7 answers, run 7's P(same_branch)
dropped, metadata same_base in its place. Run 7's post-hoc metadata-branch accuracy (results/run7/metrics.json
["post_hoc"]["c5_metadata_branch"], run 7's own rule order) is shown next to it for reference.
"""
from __future__ import annotations

import argparse
import json
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..models.base import RELATION2
from . import run7_relation as R
from . import run7_report as RR
from .run6b import _hpid, _rows, _write, wilson

RUN_DIR = Path("results/run8")
REL_RUN = "run8/relation"           # cmd_predict --run
RUN7_REL_DIR = Path("results/run7/relation")
RUN7_METRICS = Path("results/run7/metrics.json")
MODELS = ("pplx-flash", "jev")
FROZEN_FLAG = R.FROZEN_FLAG
QSET = "relation2"
HIGH = 0.5
LOW = 0.25
CLASSES = R.CLASSES
META_FIELDS = ("base", "author", "created_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------- pairs
def cmd_pairs(a) -> list[dict]:
    rows = R.build_pairs(_rows(R.DATA6 / "hold_sample.jsonl"))
    out = RUN_DIR / "relation_pairs.jsonl"
    _write(out, rows)
    print(json.dumps({"wrote": str(out), "n": len(rows), "state_source": dict(Counter(r["state_source"] for r in rows))}, indent=2))
    return rows


# ----------------------------------------------------------------- predict (guarded)
def predict_args(model: str, dataset: Path, resume: bool, concurrency: int, compact: bool = False) -> types.SimpleNamespace:
    """The argparse namespace `mergeone.cli predict` would build for this call."""
    out = Path(__import__("mergeone.cli", fromlist=["x"]).RESULTS) / REL_RUN / f"predictions_{model}_test_all{'_compact' if compact else ''}.jsonl"
    return types.SimpleNamespace(model=model, split="test", run=REL_RUN, limit=None, concurrency=concurrency, questions="all", words=False,
                                 append=resume, only_errors_from=None, only_missing_from=str(out) if resume else None, qset=QSET,
                                 subset=None, dataset=str(dataset), single_question=False, compact=compact, raw=None, prompt_file=None)


def cmd_predict(a) -> None:
    if not a.frozen:
        raise SystemExit(f"REFUSING to call any model: pass {FROZEN_FLAG} only after the run-8 protocol (with prompts/jev_relation2.json, "
                         "prompts/haiku_relation2.md and the class rule of this module) is committed.")
    from .. import cli
    rows = cmd_pairs(a)
    for m in a.model or MODELS:
        cli.cmd_predict(predict_args(m, RUN_DIR / "relation_pairs.jsonl", a.resume, a.concurrency, getattr(a, "compact", False)))
    print(f"{len(rows)} pairs asked of {', '.join(a.model or MODELS)}")


# ----------------------------------------------------------------- metadata
def _ts(x) -> datetime | None:
    if not x:
        return None
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None


def pair_metadata(inp: RR.Inputs, pairs: list[dict]) -> tuple[dict[int, dict], dict]:
    """{pair_id: {same_base, same_author, a_newer}} for `pairs` ({repo, a_number, b_number}) and the per-field source counts."""
    got, src = RR.pr_fields(inp, pairs, META_FIELDS)
    out = {}
    for r in pairs:
        ka, kb = (r["repo"], int(r["a_number"])), (r["repo"], int(r["b_number"]))
        ba, bb = got["base"].get(ka), got["base"].get(kb)
        ua, ub = got["author"].get(ka), got["author"].get(kb)
        ta, tb = _ts(got["created_at"].get(ka)), _ts(got["created_at"].get(kb))
        out[_hpid(r["repo"], r["a_number"], r["b_number"])] = {
            "same_base": None if ba is None or bb is None else ba == bb,
            "same_author": None if ua is None or ub is None else ua == ub,
            "a_newer": None if ta is None or tb is None else ta > tb}
    return out, src


# ----------------------------------------------------------------- class rule
def relation2_class(p: dict[str, float], same_base: bool | None, same_author: bool | None = None, a_newer: bool | None = None,
                    high: float = HIGH, low: float = LOW) -> str:
    """Three probabilities + metadata → one class (rule table in the module docstring). same_base None counts as True;
    same_author and a_newer are accepted and not consulted by this frozen rule."""
    v = {q: float(p.get(q) or 0.0) for q in RELATION2}
    hi = {q: v[q] >= high for q in RELATION2}
    sb = True if same_base is None else bool(same_base)
    if hi["same_change"]:
        return "duplicate" if sb else "backport"
    if hi["builds_on"]:
        return "follow_up"
    if hi["same_goal"]:
        return "competing"
    if any(x >= low for x in v.values()):
        return "same_area"
    return "unrelated"


def related_score(p: dict[str, float]) -> float:
    return max(float(p.get(q) or 0.0) for q in RELATION2)


# ----------------------------------------------------------------- scoring
def _ok(x) -> bool:
    return x is not None and x == x


def load_preds(path: Path, qset: str = QSET) -> dict[int, dict]:
    """{pair_id: decision} over error-free rows of `qset` with all three relation2 probabilities (last row wins)."""
    out = {}
    if Path(path).exists():
        for l in Path(path).open():
            if not l.strip():
                continue
            r = json.loads(l)
            d = r["decision"]
            pr = d.get("probs") or {}
            if r.get("qset") == qset and d.get("error") is None and all(_ok(pr.get(q)) for q in RELATION2):
                out[r["pr_number"]] = d
    return out


def score(gold: dict[int, dict], preds: dict[int, dict], meta: dict[int, dict]) -> dict:
    keys = [k for k in gold if k in preds]
    m = {k: meta.get(k) or {} for k in keys}
    pc = {k: relation2_class(preds[k]["probs"], m[k].get("same_base"), m[k].get("same_author"), m[k].get("a_newer")) for k in keys}
    g = {k: gold[k]["relation"] for k in keys}
    per = {}
    for c in CLASSES:
        ks = [k for k in keys if g[k] == c]
        hit = sum(pc[k] == c for k in ks)
        per[c] = {"n": len(ks), "correct": hit, "accuracy": hit / len(ks) if ks else None, "ci95": wilson(hit, len(ks))}
    conf = {gc: {p: sum(1 for k in keys if g[k] == gc and pc[k] == p) for p in CLASSES} for gc in CLASSES}
    hit = sum(pc[k] == g[k] for k in keys)
    sc = [float(preds[k]["probs"]["same_change"]) for k in keys]
    y_dup = [int(g[k] == "duplicate") for k in keys]
    y_rel = [int(g[k] != "unrelated") for k in keys]
    return {"n": len(keys), "missing": len(set(gold) - set(preds)),
            "n_base_unknown": sum(1 for k in keys if m[k].get("same_base") is None),
            "accuracy": hit / len(keys) if keys else None, "accuracy_ci95": wilson(hit, len(keys)),
            "same_change_vs_rest_auc": R._auc(y_dup, sc), "n_duplicate": sum(y_dup),
            "same_change_or_backport_vs_rest_auc": R._auc([int(g[k] in ("duplicate", "backport")) for k in keys], sc),
            "related_vs_unrelated_auc": R._auc(y_rel, [related_score(preds[k]["probs"]) for k in keys]), "n_unrelated": len(keys) - sum(y_rel),
            "per_class": per, "confusion": conf, "predicted": dict(Counter(pc.values()))}


def run7_as_relation2(preds: dict[int, dict]) -> dict[int, dict]:
    """Run 7 relation decisions with only the three relation2 probabilities (run 7's same_branch answer dropped)."""
    return {k: {**d, "probs": {q: d["probs"][q] for q in RELATION2}} for k, d in preds.items()}


def metadata_by_class(gold: dict[int, dict], meta: dict[int, dict]) -> dict:
    out = {}
    for c in CLASSES:
        ks = [k for k, g in gold.items() if g["relation"] == c]
        out[c] = {"n": len(ks), **{f: {"true": sum(1 for k in ks if (meta.get(k) or {}).get(f) is True),
                                       "unknown": sum(1 for k in ks if (meta.get(k) or {}).get(f) is None)}
                                   for f in ("same_base", "same_author", "a_newer")}}
    return out


# ----------------------------------------------------------------- report
def _run7_post_hoc(inp: RR.Inputs) -> dict:
    m = inp.json(RUN7_METRICS)
    ph = ((m or {}).get("post_hoc") or {}).get("c5_metadata_branch") or {}
    return {mm: {"accuracy": (e.get("after") or {}).get("accuracy"), "n": (e.get("after") or {}).get("n")}
            for mm, e in (ph.get("models") or {}).items() if e.get("status") == "ok"}


def run(models=None, run_dir: Path | None = None) -> dict:
    run_dir = Path(run_dir or RUN_DIR)
    inp = RR.Inputs(Path("."))
    adj_rel = R.DATA6 / "hold_labels_adjudicated.jsonl"
    for p in (adj_rel, R.DATA6 / "hold_labels.jsonl", R.DATA6 / "hold_labels_fable.jsonl", R.DATA7 / "relation_gold.jsonl"):
        inp._path(p)
    gold = R.load_gold()
    adj = _rows(adj_rel)
    meta, msrc = pair_metadata(inp, adj)
    rep = {"computed_at": _now(), "n_pairs": len(gold), "qset": QSET, "questions": list(RELATION2), "high": HIGH, "low": LOW,
           "rule": "same_change & !same_base → backport; same_change & same_base → duplicate; builds_on → follow_up; "
                   "same_goal → competing; any P ≥ low → same_area; else unrelated (same_base unknown → True)",
           "gold_classes": dict(Counter(v["relation"] for v in gold.values())),
           "gold_sources": dict(Counter(v["source"] for v in gold.values())),
           "metadata_source": msrc, "metadata_by_gold_class": metadata_by_class(gold, meta),
           "models": {}, "baseline_run7": {}, "control_same_day": {}, "run7_post_hoc_reference": _run7_post_hoc(inp)}
    for m in models or MODELS + ("haiku",):
        f = run_dir / "relation" / f"predictions_{m}_test_all.jsonl"
        inp._path(f)
        preds = load_preds(f)
        if preds:
            rep["models"][m] = score(gold, preds, meta)
        elif m in MODELS:
            rep["models"][m] = {"status": f"no {QSET} predictions in {f}"}
        f7 = RUN7_REL_DIR / f"predictions_{m}_test_all.jsonl"
        inp._path(f7)
        p7 = R.load_relation_preds(m, RUN7_REL_DIR)
        if p7:
            rep["baseline_run7"][m] = score(gold, run7_as_relation2(p7), meta)
        pc = R.load_relation_preds(m, run_dir / "relation")  # same-day control: qset relation rows in the run-8 file
        if pc:
            rep["control_same_day"][m] = score(gold, run7_as_relation2(pc), meta)
    rep["inputs"] = [{"path": p, "sha256": s} for p, s in sorted(inp.seen.items())]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "relation_metrics.json").write_text(json.dumps(rep, indent=2))
    t = tables(rep)
    (run_dir / "relation_tables.md").write_text(t)
    print(t)
    return rep


def _f(x, nd=3):
    return "—" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _ci(c) -> str:
    return f"[{_f(c[0])}, {_f(c[1])}]" if c else "[—, —]"


def _row(name: str, e: dict) -> str:
    return (f"| {name} | {e['n']} | {_f(e['accuracy'])} {_ci(e['accuracy_ci95'])} | {_f(e['same_change_vs_rest_auc'])} ({e['n_duplicate']}) | "
            f"{_f(e['same_change_or_backport_vs_rest_auc'])} | {_f(e['related_vs_unrelated_auc'])} ({e['n_unrelated']}) | {e['n_base_unknown']} |")


def _class_tables(name: str, e: dict) -> list[str]:
    L = ["", f"## {name}: accuracy per gold class (Wilson 95%)", "", "| gold class | n | correct | accuracy [95% CI] |", "|---|---|---|---|"]
    for c in CLASSES:
        p = e["per_class"][c]
        L.append(f"| {c} | {p['n']} | {p['correct']} | {_f(p['accuracy'])} {_ci(p['ci95'])} |")
    L += ["", f"### {name}: confusion (rows gold, columns predicted)", "", "| gold \\ pred | " + " | ".join(CLASSES) + " |",
          "|---|" + "---|" * len(CLASSES)]
    for gc in CLASSES:
        L.append(f"| {gc} | " + " | ".join(str(e["confusion"][gc][p]) for p in CLASSES) + " |")
    return L


def tables(rep: dict) -> str:
    L = ["# Run 8 — relation2 (three binaries + metadata) on the run-6B hold pairs", "",
         f"{rep['n_pairs']} pairs · gold classes: " + ", ".join(f"{c} {rep['gold_classes'].get(c, 0)}" for c in CLASSES)
         + " · gold source: " + ", ".join(f"{k} {v}" for k, v in rep["gold_sources"].items()) + f" · HIGH {rep['high']}, LOW {rep['low']}", "",
         f"Rule: {rep['rule']}.", "",
         "| model | n | accuracy [95% CI] | same_change vs rest AUC (n duplicate) | same_change+backport vs rest AUC | related vs unrelated AUC (n unrelated) | base unknown |",
         "|---|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        L.append(_row(m, e) if "n" in e else f"| {m} | — | {e['status']} | | | | |")
    for m, e in rep["baseline_run7"].items():
        ref = rep["run7_post_hoc_reference"].get(m) or {}
        L.append(_row(f"{m} — baseline: run-7 relation answers, this rule", e))
        if ref:
            L.append(f"| {m} — run-7 post-hoc (run-7 rule, metadata branch; reference) | {ref.get('n')} | {_f(ref.get('accuracy'))} | | | | |")
    for m, e in rep["control_same_day"].items():
        L.append(_row(f"{m} — same-day control: run-7 relation question asked today, this rule", e))
    for m, e in rep["models"].items():
        if "n" in e:
            L += _class_tables(m, e)
    for m, e in rep["baseline_run7"].items():
        L += _class_tables(f"{m} — baseline (run-7 answers)", e)
    L += ["", "## Metadata per gold class", "", "| gold class | n | same_base | same_author | a_newer | unknown (base / author / created) |",
          "|---|---|---|---|---|---|"]
    for c in CLASSES:
        x = rep["metadata_by_gold_class"][c]
        L.append(f"| {c} | {x['n']} | {x['same_base']['true']} | {x['same_author']['true']} | {x['a_newer']['true']} | "
                 f"{x['same_base']['unknown']} / {x['same_author']['unknown']} / {x['a_newer']['unknown']} |")
    L += ["", "## Notes", "",
          f"- Metadata source (dataset → PR memory): " + "; ".join(f"{f} {v}" for f, v in rep["metadata_source"].items()) + ".",
          "- same_author and a_newer are inputs of the rule's signature but not consulted by the frozen rule; tabulated above.",
          "- Baseline row: run 7's relation answers (four questions) with same_branch dropped, re-scored through the run-8 rule "
          "and metadata — the pre-registered comparison point for run 8's relation2 number.",
          "- Gold sub-classes: run7_relation.GOLD_KEYWORDS rule; data/run7/relation_gold.jsonl overrides per pair when present."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run8_relation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pairs", help="build the 100 pair rows → results/run8/relation_pairs.jsonl (no model call)").set_defaults(fn=cmd_pairs)
    s = sub.add_parser("predict", help="ask the relation2 question (guarded)")
    s.add_argument("--model", action="append", help=f"repeatable; default {', '.join(MODELS)}")
    s.add_argument("--concurrency", type=int, default=8)
    s.add_argument("--compact", action="store_true", help="compact pair state (run 7 C1 rules); file suffix _compact")
    s.add_argument("--resume", action="store_true", help="append, skipping pairs already answered without error")
    s.add_argument(FROZEN_FLAG, dest="frozen", action="store_true")
    s.set_defaults(fn=cmd_predict)
    s = sub.add_parser("report"); s.add_argument("--model", action="append"); s.set_defaults(fn=lambda a: run(tuple(a.model) if a.model else None))
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
