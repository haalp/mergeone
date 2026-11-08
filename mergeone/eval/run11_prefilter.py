"""§R11-H2 — the model-free pair-call prefilter, measured on run 9's recorded set-(b) answers. No model call.

  python -m mergeone.eval.run11_prefilter --i-have-frozen-the-protocol [--root .] [--out results/run11/prefilter.json]

Inputs (all exist before this script runs; nothing is fetched):
  results/run9/bf16/pairs_b/predictions_pplx-flash-local_test_all.jsonl   per-candidate same_change answers (committed)
  results/run9/pairs_b.jsonl         pair id → (repo, a, b) and kind   (gatebench-data; gitignored, on the lab machine)
  results/run6/candidates_k20.jsonl  the retrieval features per candidate (score, jaccard, dir_jaccard, ident_jaccard,
                                     bm25, same_author, created_at, title)   (gatebench-data; gitignored)
  data/run6/hold_labels_adjudicated.jsonl   the 100 adjudicated (A, B) pairs with label duplicate | pointer | unrelated

The prefilter scores, fixed here before the freeze and never tuned on set (b) (§R11.3, §R11.4):
  retrieval   the memory's own combined retrieval score (`score`; candidates arrive in this order: retrieval_rank)
  overlap     0.5·jaccard (changed files) + 0.3·dir_jaccard (top-level dirs) + 0.2·ident_jaccard (identifiers)
  blend       0.5·retrieval_rank_score + 0.5·overlap, where retrieval_rank_score = 1 − (rank − 1)/9 for rank 1…10
The adjudicated B that run 9 forced into slot 10 (outside the retrieval top-10; 4 of 100) keeps its features and is
ranked by the score like every other candidate.

For each score and each k' in {3, 5} against the control k' = 10 (all recorded answers):
  recall_dup   share of A's whose adjudicated B is labelled `duplicate` and is among the kept k' (bar ≥ 0.95)
  auc_dup_rest duplicate-vs-rest ROC-AUC of the recorded same_change answers on the kept candidates (bar ≥ control − 0.02);
               positives are the kept adjudicated B's with label duplicate, negatives every other kept candidate
  calls        pair calls per A (k'), the budget the gateway would spend
A score/k' pair passes when both bars hold; the smallest passing k' of the pre-registered primary score (`retrieval`)
is the one that ships. The other scores are reported beside it.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from .metrics import roc_auc

PREDICTIONS = Path("results/run9/bf16/pairs_b/predictions_pplx-flash-local_test_all.jsonl")
PAIRS_B = Path("results/run9/pairs_b.jsonl")
CANDIDATES = Path("results/run6/candidates_k20.jsonl")
ADJUDICATED = Path("data/run6/hold_labels_adjudicated.jsonl")
OUT = Path("results/run11/prefilter.json")

K_CONTROL = 10
K_PRIME = (3, 5)
BAR_RECALL = 0.95
BAR_AUC_DELTA = -0.02
PRIMARY = "retrieval"
WEIGHTS = {"overlap": {"jaccard": 0.5, "dir_jaccard": 0.3, "ident_jaccard": 0.2}, "blend": {"rank": 0.5, "overlap": 0.5}}


def _rows(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(l) for l in f if l.strip()]


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def overlap_score(c: dict) -> float:
    w = WEIGHTS["overlap"]
    return w["jaccard"] * _f(c.get("jaccard")) + w["dir_jaccard"] * _f(c.get("dir_jaccard")) + w["ident_jaccard"] * _f(c.get("ident_jaccard"))


def rank_score(rank) -> float:
    if rank is None:
        return 0.0
    return max(0.0, 1.0 - (float(rank) - 1.0) / 9.0)


def scores_for(c: dict, rank) -> dict[str, float]:
    ov = overlap_score(c)
    return {"retrieval": _f(c.get("score")), "overlap": ov,
            "blend": WEIGHTS["blend"]["rank"] * rank_score(rank) + WEIGHTS["blend"]["overlap"] * ov}


def build_groups(preds: list[dict], pairs_b: list[dict], cands: list[dict], adjudicated: list[dict]) -> list[dict]:
    """One dict per A: [{a_key, label, b, candidates: [{b, p, rank, hold_b, scores}]}]. Pairs without a recorded
    answer (error rows) are dropped and counted by the caller through the returned sizes."""
    answer = {r["pr_number"]: r for r in preds if not (r.get("decision") or {}).get("error")}
    feats = {}
    for cr in cands:
        for c in cr.get("candidates", []):
            feats[(cr["repo"], int(cr["number"]), int(c["number"]))] = c
    label = {(a["repo"], a["a_number"]): (a["label"], a["b_number"]) for a in adjudicated}
    groups: dict[tuple, dict] = {}
    for p in pairs_b:
        key = (p["repo"], int(p["a"]))
        if key not in label:
            continue
        ans = answer.get(p["pr_number"])
        if ans is None:
            continue
        lab, b_adj = label[key]
        g = groups.setdefault(key, {"a_key": f"{key[0]}#{key[1]}", "label": lab, "b": b_adj, "candidates": []})
        c = feats.get((p["repo"], int(p["a"]), int(p["b"])), {})
        rank = (p.get("labels") or {}).get("retrieval_rank")
        g["candidates"].append({"b": int(p["b"]), "p": float(ans["decision"]["probs"]["same_change"]), "rank": rank,
                                "hold_b": int(p["b"]) == b_adj, "scores": scores_for(c, rank)})
    return [groups[k] for k in sorted(groups)]


def evaluate(groups: list[dict]) -> dict:
    out = {"groups": len(groups), "labels": dict(sorted(_count(g["label"] for g in groups).items())), "scores": {}}
    dup_groups = [g for g in groups if g["label"] == "duplicate"]
    # control: every recorded candidate
    y, p = [], []
    for g in groups:
        for c in g["candidates"]:
            y.append(1 if (c["hold_b"] and g["label"] == "duplicate") else 0)
            p.append(c["p"])
    control_auc = roc_auc(y, p) if any(y) and not all(y) else None
    out["control"] = {"k": K_CONTROL, "recall_dup": 1.0 if dup_groups else None, "auc_dup_rest": control_auc,
                      "candidates": len(y), "positives": sum(y), "calls_per_a": _mean(len(g["candidates"]) for g in groups)}
    for name in ("retrieval", "overlap", "blend"):
        per_k = {}
        for k in K_PRIME:
            hit, yy, pp = 0, [], []
            for g in groups:
                kept = sorted(g["candidates"], key=lambda c: (-c["scores"][name], c["rank"] if c["rank"] is not None else 99))[:k]
                if g["label"] == "duplicate" and any(c["hold_b"] for c in kept):
                    hit += 1
                for c in kept:
                    yy.append(1 if (c["hold_b"] and g["label"] == "duplicate") else 0)
                    pp.append(c["p"])
            recall = hit / len(dup_groups) if dup_groups else None
            auc = roc_auc(yy, pp) if any(yy) and not all(yy) else None
            delta = None if (auc is None or control_auc is None) else auc - control_auc
            passed = recall is not None and recall >= BAR_RECALL and delta is not None and delta >= BAR_AUC_DELTA
            per_k[str(k)] = {"recall_dup": recall, "auc_dup_rest": auc, "delta_auc": delta, "kept": len(yy), "positives": sum(yy),
                             "calls_per_a": k, "pass": passed}
        out["scores"][name] = per_k
    ships = None
    for k in K_PRIME:
        if out["scores"][PRIMARY][str(k)]["pass"]:
            ships = k
            break
    out["verdict"] = {"primary": PRIMARY, "bars": {"recall_dup": BAR_RECALL, "delta_auc": BAR_AUC_DELTA},
                      "ships_k": ships, "pass": ships is not None}
    return out


def _count(xs):
    d: dict = defaultdict(int)
    for x in xs:
        d[x] += 1
    return d


def _mean(xs) -> float | None:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--i-have-frozen-the-protocol", action="store_true", help="required: §R11 is frozen and tagged")
    a = ap.parse_args(argv)
    if not a.i_have_frozen_the_protocol:
        ap.error("refusing to compute a pre-registered number before the freeze (pass --i-have-frozen-the-protocol)")
    root = Path(a.root)
    missing = [str(p) for p in (PREDICTIONS, PAIRS_B, CANDIDATES, ADJUDICATED) if not (root / p).exists()]
    if missing:
        raise SystemExit("missing inputs (gatebench-data files live on the lab machine): " + ", ".join(missing))
    groups = build_groups(_rows(root / PREDICTIONS), _rows(root / PAIRS_B), _rows(root / CANDIDATES), _rows(root / ADJUDICATED))
    rep = evaluate(groups)
    rep["inputs"] = {"predictions": str(PREDICTIONS), "pairs_b": str(PAIRS_B), "candidates": str(CANDIDATES), "adjudicated": str(ADJUDICATED)}
    rep["weights"] = WEIGHTS
    out = root / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2))
    v = rep["verdict"]
    print(f"R11-H2 {'pass' if v['pass'] else 'fail'}: primary {v['primary']}, ships k'={v['ships_k']}; control AUC {rep['control']['auc_dup_rest']}")
    for name, per_k in rep["scores"].items():
        for k, r in per_k.items():
            print(f"  {name:9s} k'={k}: recall_dup {r['recall_dup']:.3f}  auc {r['auc_dup_rest']:.3f} (Δ {r['delta_auc']:+.3f})  {'pass' if r['pass'] else 'fail'}")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
