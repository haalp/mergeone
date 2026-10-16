"""Run 3 dataset: agent-authored PRs from data/run3/agent_prs.jsonl → data/run3/dataset.jsonl.

PROTOCOL R3.2: split by repository (70% of repos → val, 30% → test), stratified by the
repo's dominant agent, fixed seed. Every row keeps the same `state` rendering that runs 1–2
used (render_state), so the models see exactly the input the gateway would send.
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .extract import FileStat, MergedPR
from .features import render_state, structural_features

SEED = 3
VAL_FRACTION = 0.7


def pr_id(repo: str, number: int) -> int:
    """Stable int id for predictions files (they key on pr_number)."""
    return int(hashlib.sha1(f"{repo}#{number}".encode()).hexdigest()[:12], 16)


def to_merged_pr(r: dict) -> MergedPR:
    files = [FileStat(f["path"], int(f.get("insertions", 0)), int(f.get("deletions", 0))) for f in r.get("files", [])]
    return MergedPR(
        pr_number=pr_id(r["repo"], r["number"]),
        merge_sha=r.get("head_sha") or "",
        merged_at=r.get("merged_at") or r.get("closed_at") or r.get("created_at"),
        title=r.get("title") or "",
        body=r.get("body") or "",
        source_ref=r.get("head_ref") or "",
        files=files,
        diff_head=r.get("diff_head") or "",
        diff_truncated=bool(r.get("diff_truncated")),
        is_revert_pr=(r.get("title") or "").lower().startswith("revert"),
        reverted=bool(r["labels"].get("reverted_within_30d")),
        reverted_by_pr=r["labels"].get("reverted_by"),
    )


def outcome_labels(r: dict) -> dict:
    L = r["labels"]
    merged = bool(L.get("merged"))
    closed = bool(L.get("closed_unmerged"))
    cr = bool(L.get("change_requested"))
    hrc = int(L.get("human_review_comments", 0) or 0)
    fp = int(L.get("force_pushes", 0) or 0)
    rev = bool(L.get("reverted_within_30d"))
    return {
        "merged": merged,
        "closed_unmerged": closed,
        "change_requested": cr,
        "human_review_comments": hrc,
        "discussed": hrc >= 3,
        "iterated": fp >= 2,
        "reverted_within_30d": rev,
        "merged_untouched": merged and hrc == 0,
        "rejected": closed or cr,                      # R3-H1 target
        "bad_outcome": closed or cr or rev,            # R3-H2/H3 target
        "approved_by_human": bool(L.get("approved_by_human")),
    }


def split_repos(repos: list[dict], seed: int = SEED, val_fraction: float = VAL_FRACTION) -> dict[str, str]:
    """repo → 'val' | 'test', stratified by dominant agent, deterministic."""
    by_agent = defaultdict(list)
    for r in sorted(repos, key=lambda x: x["repo"]):
        dom = max(r["by_agent"].items(), key=lambda kv: kv[1])[0]
        by_agent[dom].append(r["repo"])
    rng = random.Random(seed)
    out = {}
    for agent, names in sorted(by_agent.items()):
        rng.shuffle(names)
        n_val = max(1, round(len(names) * val_fraction)) if len(names) > 1 else 1
        for i, name in enumerate(names):
            out[name] = "val" if i < n_val else "test"
    # guarantee a non-empty test split even if every agent group is tiny
    if "test" not in out.values():
        last = sorted(out)[-1]
        out[last] = "test"
    return out


def prepare(src: Path, repos_path: Path, out: Path, manifest_path: Path, per_repo_cap: int | None = None) -> dict:
    repos = json.loads(repos_path.read_text())
    assignment = split_repos(repos)
    rows_in = [json.loads(l) for l in src.open()]
    n_per_repo = Counter()
    rows = []
    for r in rows_in:
        if r["repo"] not in assignment:
            continue
        if per_repo_cap and n_per_repo[r["repo"]] >= per_repo_cap:
            continue
        n_per_repo[r["repo"]] += 1
        p = to_merged_pr(r)
        agent = (r.get("agent") or ["unknown"])[0] if isinstance(r.get("agent"), list) else (r.get("agent") or "unknown")
        rows.append({
            "pr_number": p.pr_number,
            "repo": r["repo"],
            "number": r["number"],
            "agent": agent,
            "author": r.get("author"),
            "base": r.get("base"),
            "head_ref": r.get("head_ref"),
            "default_branch": next((x.get("default_branch", "main") for x in repos if x["repo"] == r["repo"]), "main"),
            "created_at": r.get("created_at"),
            "merged_at": r.get("merged_at"),
            "closed_at": r.get("closed_at"),
            "labels": {**outcome_labels(r), "repo": r["repo"], "agent": agent},
            "features": structural_features(p),
            "state": render_state(p),
            "state_words": render_state(p, numbers_as_words=True),
            "split": assignment[r["repo"]],
        })
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    def stats(sub):
        c = Counter()
        for x in sub:
            for k, v in x["labels"].items():
                if isinstance(v, bool) and v:
                    c[k] += 1
        return {"n": len(sub), "repos": sorted({x["repo"] for x in sub}), "agents": dict(Counter(x["agent"] for x in sub)),
                "label_counts": dict(c)}

    manifest = {
        "source": "GitHub REST/Search API via mergeone.data.agent_prs (discover + fetch)",
        "repos_file": str(repos_path), "n_repos": len(assignment),
        "split_rule": f"by repository, {VAL_FRACTION:.0%} of repos → val, rest → test; stratified by dominant agent; seed {SEED}",
        "split_assignment": assignment,
        "splits": {s: stats([x for x in rows if x["split"] == s]) for s in ("val", "test")},
        "n_rows": len(rows),
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest
