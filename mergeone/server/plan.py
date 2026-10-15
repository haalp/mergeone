"""Swarm view: landing plan for many open PRs.

Agent-scale merging is not one PR at a time. Given the open PRs the gateway
knows about, build a conflict graph and emit *waves*: sets of PRs that touch
disjoint files and can land in parallel, plus the edges that force ordering.

Two conflict signals:
  mechanical  shared file paths (free, from git / webhook payload)
  semantic    optional, one typed question per candidate pair to the decision
              model: "do these two changes affect the same behaviour?"
              Only asked for pairs that share a *directory* but not a file,
              which keeps the question count small (and bounded by --max-pairs).

Output is deterministic for a given input; waves are greedy graph colouring
ordered by PR age (older first), which is what a queue would do anyway.
"""
from __future__ import annotations

import os
from collections import defaultdict
from itertools import combinations

SEMANTIC_Q = {
    "same_behaviour": {
        "type": "noul",
        "instructions": "These two pull requests modify the same behaviour, feature, data structure, or code path, such that landing both without re-review could produce a logical conflict even if their files do not overlap.",
    }
}


def _dirs(paths: list[str]) -> set[str]:
    return {os.path.dirname(p) for p in paths}


def landing_plan(open_prs: list[dict], model=None, store=None, repo: str = "", max_pairs: int = 200, semantic_threshold: float = 0.6) -> dict:
    prs = sorted(open_prs, key=lambda p: p["updated"])
    by_pr = {p["pr"]: p for p in prs}
    conflicts: dict[int, set[int]] = defaultdict(set)
    edges = []
    # mechanical
    file_owner: dict[str, list[int]] = defaultdict(list)
    for p in prs:
        for f in p["paths"]:
            file_owner[f].append(p["pr"])
    for f, owners in file_owner.items():
        for a, b in combinations(sorted(owners), 2):
            if b not in conflicts[a]:
                conflicts[a].add(b); conflicts[b].add(a)
                edges.append({"a": a, "b": b, "kind": "file", "evidence": f})
    # semantic (optional)
    asked = 0
    if model is not None:
        for a, b in combinations([p["pr"] for p in prs], 2):
            if b in conflicts[a] or asked >= max_pairs:
                continue
            da, db = _dirs(by_pr[a]["paths"]), _dirs(by_pr[b]["paths"])
            if not (da & db):
                continue
            state = (f"PR A #{a} changes files:\n  " + "\n  ".join(by_pr[a]["paths"][:40]) +
                     f"\n\nPR B #{b} changes files:\n  " + "\n  ".join(by_pr[b]["paths"][:40]))
            try:
                d = model.decide(state, {}, ("same_behaviour",)) if getattr(model, "name", "") != "jev" else _jev_pair(model, state)
                p = d.probs.get("same_behaviour", float("nan"))
            except Exception:
                p = float("nan")
            asked += 1
            if p == p and p >= semantic_threshold:
                conflicts[a].add(b); conflicts[b].add(a)
                edges.append({"a": a, "b": b, "kind": "semantic", "p": round(p, 3), "evidence": sorted(da & db)[:3]})
    # greedy waves: oldest first, a PR goes into the first wave with no conflict
    waves: list[list[int]] = []
    for p in prs:
        for w in waves:
            if not any(q in conflicts[p["pr"]] for q in w):
                w.append(p["pr"]); break
        else:
            waves.append([p["pr"]])
    return {"repo": repo, "open_prs": len(prs), "waves": waves, "parallelism": max((len(w) for w in waves), default=0),
            "conflict_edges": edges, "semantic_pairs_asked": asked,
            "advice": "Land each wave in parallel; wait for CI between waves. PRs sharing an edge must be rebased after the earlier one lands."}


def _jev_pair(model, state):
    """Jev needs its question set to include the pair question; add it ad hoc."""
    from ..models.base import Decision
    qs = dict(model.questions)
    model.questions = {**qs, **SEMANTIC_Q}
    try:
        return model.decide(state, {}, ("same_behaviour",))
    finally:
        model.questions = qs
