"""Holdout-2 (PROTOCOL R12.0a): a test set no model and no hypothesis has seen.

The 705 test PRs have been every run's test split since run 3. Holdout-2 is collected from the **same eight test
repositories** (run 3's by-repository split, so the calibration maps fit on the val repositories stay out-of-repo),
from PRs **created after run 3's collection date**, with the same collector, the same label rules and the same
`render_state`. It is not scored by any model until a frozen PROTOCOL section names it.

    # 1. collect (lab machine; public read-only GITHUB_TOKEN; no model call)
    python -m mergeone.data.prepare_holdout2 collect
    # 2. build the dataset and its manifest
    python -m mergeone.data.prepare_holdout2 prepare

`collect` runs `mergeone.data.agent_prs fetch` with --created-after <run 3's last closed_at date>, --exclude
data/run3/agent_prs.jsonl and --only <the test repositories>, into data/run12/agent_prs.jsonl (gitignored, like run 3's).
`prepare` writes data/run12/holdout2.jsonl (every row split = "holdout2", same schema as data/run3/dataset.jsonl) and
data/run12/manifest.json: the cut-off, per-repository counts, and the count of each outcome label — the number a
frozen section needs for its power statement (R12.0b). PRs still open are never collected (the search asks for merged
or closed); `reverted_within_30d` is not yet final for PRs closed in the last 30 days and the manifest says how many.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import agent_prs
from .features import render_state, structural_features
from .focused import DIFF_BUDGET, FULL_DIFF_CAP
from .prepare_run3 import outcome_labels, to_merged_pr

RUN3_RAW = Path("data/run3/agent_prs.jsonl")
RUN3_MANIFEST = Path("data/run3/manifest.json")
RUN3_REPOS = Path("data/run3/repos.json")
RAW = Path("data/run12/agent_prs.jsonl")
OUT = Path("data/run12/holdout2.jsonl")
MANIFEST = Path("data/run12/manifest.json")
CANDIDATES = Path("data/run12/repos_candidates.json")   # agent_prs discover --out … (fresh discovery, run-3 thresholds)
REPOS12 = Path("data/run12/repos.json")                 # the widened repository list (R12.0a as amended 2026-11-14)
DIFFS = Path("data/run12/diffs.jsonl")                  # whole diffs, one per PR (gitignored); both R12-H1 renderings read it
N_NEW_REPOS = 40
SPLIT = "holdout2"


def test_repos(manifest_path: Path = RUN3_MANIFEST) -> list[str]:
    m = json.loads(manifest_path.read_text())
    return sorted(r for r, s in m["split_assignment"].items() if s == "test")


def cutoff_date(raw_path: Path = RUN3_RAW) -> str:
    """The UTC date of the last PR run 3 collected (by closed/merged time): holdout-2 PRs are created after it."""
    last = ""
    for l in raw_path.open():
        d = json.loads(l)
        t = d.get("closed_at") or d.get("merged_at") or d.get("created_at") or ""
        last = max(last, t)
    if not last:
        raise SystemExit(f"{raw_path}: no rows")
    return last[:10]


def select_repos(candidates: Path = CANDIDATES, run3_repos: Path = RUN3_REPOS, run3_manifest: Path = RUN3_MANIFEST,
                 out: Path = REPOS12, n_new: int = N_NEW_REPOS) -> list[dict]:
    """R12.0a as amended: run 3's test repositories plus the first `n_new` freshly discovered repositories by agent-PR
    count, after removing every repository run 3 used (validation or test). Nothing else about a repository is read."""
    r3 = json.loads(run3_repos.read_text())
    used = {r["repo"].lower() for r in r3}
    tests = set(test_repos(run3_manifest))
    new = [r for r in json.loads(candidates.read_text()) if r["repo"].lower() not in used]
    new.sort(key=lambda r: (-int(r["agent_prs_seen"]), r["repo"].lower()))
    rows = [{**r, "origin": "run3_test"} for r in r3 if r["repo"] in tests] + [{**r, "origin": "new"} for r in new[:n_new]]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1))
    return rows


def holdout_repos(repos_path: Path, run3_manifest: Path) -> dict[str, str]:
    """repo → origin. A repository list written by select_repos carries `origin`; run 3's own list means its test split."""
    rows = json.loads(repos_path.read_text())
    if rows and all("origin" in r for r in rows):
        return {r["repo"]: r["origin"] for r in rows}
    return {r: "run3_test" for r in test_repos(run3_manifest)}


def collect(a) -> int:
    cut = a.created_after or cutoff_date(Path(a.run3_raw))
    argv = ["fetch", "--repos", str(a.repos), "--per-repo", str(a.per_repo), "--out", str(a.raw),
            "--created-after", cut, "--exclude", str(a.run3_raw), "--only", *sorted(holdout_repos(Path(a.repos), Path(a.run3_manifest)))]
    print("python -m mergeone.data.agent_prs " + " ".join(argv))
    agent_prs.main(argv)
    return 0


def fetch_diffs(raw: Path = RAW, out: Path = DIFFS, gh=None, cap: int = FULL_DIFF_CAP) -> dict:
    """One whole diff per collected PR (resumable). `prefix_matches` says whether its first 4,000 characters are the
    row's stored diff_head; a PR whose diff changed since collection is reported and not rendered."""
    done = set()
    if out.exists():
        for l in out.open():
            d = json.loads(l)
            done.add((d["repo"], d["number"]))
    gh = gh or agent_prs.GH()
    n = Counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as f:
        for l in raw.open():
            r = json.loads(l)
            if (r["repo"], r["number"]) in done:
                continue
            t = agent_prs.full_diff(gh, r["repo"], r["number"])
            if t is None:
                n["unavailable"] += 1
                continue
            row = {"repo": r["repo"], "number": r["number"], "head_sha": r.get("head_sha"), "diff": t[:cap], "capped": len(t) > cap,
                   "chars": len(t), "prefix_matches": t[:DIFF_BUDGET] == (r.get("diff_head") or "")}
            n["fetched"] += 1
            n["prefix_mismatch"] += 0 if row["prefix_matches"] else 1
            f.write(json.dumps(row) + "\n")
            f.flush()
    return dict(n)


def build(raw: Path, repos_path: Path, run3_raw: Path, run3_manifest: Path, out: Path, manifest_path: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    origin = holdout_repos(repos_path, run3_manifest)
    allowed = set(origin)
    cut = cutoff_date(run3_raw)
    seen_before = set()
    for l in run3_raw.open():
        d = json.loads(l)
        seen_before.add((d["repo"], d["number"]))
    repos = {r["repo"]: r for r in json.loads(repos_path.read_text())}
    rows, dropped = [], Counter()
    seen = set()
    for l in raw.open():
        r = json.loads(l)
        key = (r["repo"], r["number"])
        if r["repo"] not in allowed:
            dropped["not_a_holdout_repo"] += 1
        elif key in seen_before:
            dropped["in_run3"] += 1
        elif (r.get("created_at") or "")[:10] <= cut:
            dropped["created_on_or_before_cutoff"] += 1
        elif key in seen:
            dropped["duplicate"] += 1
        else:
            seen.add(key)
            p = to_merged_pr(r)
            agent = (r.get("agent") or ["unknown"])[0] if isinstance(r.get("agent"), list) else (r.get("agent") or "unknown")
            rows.append({
                "pr_number": p.pr_number, "repo": r["repo"], "number": r["number"], "agent": agent, "author": r.get("author"),
                "base": r.get("base"), "head_ref": r.get("head_ref"),
                "default_branch": (repos.get(r["repo"]) or {}).get("default_branch", "main"),
                "created_at": r.get("created_at"), "merged_at": r.get("merged_at"), "closed_at": r.get("closed_at"),
                "labels": {**outcome_labels(r), "repo": r["repo"], "agent": agent},
                "features": structural_features(p), "state": render_state(p), "state_words": render_state(p, numbers_as_words=True),
                "split": SPLIT, "origin": origin[r["repo"]],
            })
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    labels = Counter()
    for x in rows:
        for k, v in x["labels"].items():
            if isinstance(v, bool) and v:
                labels[k] += 1
    recent = (now - timedelta(days=30)).isoformat()
    manifest = {
        "protocol": "PROTOCOL.md R12.0a", "split": SPLIT, "source": "mergeone.data.agent_prs fetch --created-after … --exclude run 3 --only <test repos>",
        "created_after": cut, "repos": sorted(allowed), "n_rows": len(rows),
        "per_repo": dict(sorted(Counter(x["repo"] for x in rows).items())),
        "labels": dict(sorted(labels.items())),
        "iterated_positives": labels.get("iterated", 0),
        "by_origin": {o: {"rows": sum(1 for x in rows if x["origin"] == o), "repos": len({x["repo"] for x in rows if x["origin"] == o}),
                          "iterated_positives": sum(1 for x in rows if x["origin"] == o and x["labels"].get("iterated"))}
                      for o in sorted(set(origin.values()))},
        "revert_label_not_final": sum(1 for x in rows if (x.get("closed_at") or x.get("merged_at") or "") > recent),
        "dropped": dict(dropped), "scored_by_any_model": False, "prepared_at": now.isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    default_repos = str(REPOS12 if REPOS12.exists() else RUN3_REPOS)
    for name in ("select", "collect", "diffs", "prepare"):
        s = sub.add_parser(name)
        s.add_argument("--repos", default=default_repos if name != "select" else str(RUN3_REPOS))
        s.add_argument("--run3-raw", default=str(RUN3_RAW))
        s.add_argument("--run3-manifest", default=str(RUN3_MANIFEST))
        s.add_argument("--raw", default=str(RAW))
        if name == "select":
            s.add_argument("--candidates", default=str(CANDIDATES))
            s.add_argument("--out", default=str(REPOS12))
        elif name == "collect":
            s.add_argument("--per-repo", type=int, default=200)
            s.add_argument("--created-after", default=None, help="override the cut-off (default: run 3's last closed date)")
        elif name == "diffs":
            s.add_argument("--out", default=str(DIFFS))
        else:
            s.add_argument("--out", default=str(OUT))
            s.add_argument("--manifest", default=str(MANIFEST))
    a = ap.parse_args(argv)
    if a.cmd == "select":
        rows = select_repos(Path(a.candidates), Path(a.repos), Path(a.run3_manifest), Path(a.out))
        print(f"{a.out}: {sum(r['origin'] == 'run3_test' for r in rows)} run-3 test repositories + {sum(r['origin'] == 'new' for r in rows)} new")
        return 0
    if a.cmd == "collect":
        return collect(a)
    if a.cmd == "diffs":
        print(f"{a.out}: {fetch_diffs(Path(a.raw), Path(a.out))}")
        return 0
    m = build(Path(a.raw), Path(a.repos), Path(a.run3_raw), Path(a.run3_manifest), Path(a.out), Path(a.manifest))
    print(f"holdout-2: {m['n_rows']} PRs created after {m['created_after']} from {len(m['per_repo'])} of {len(m['repos'])} repositories; "
          f"iterated positives {m['iterated_positives']} {m['by_origin']}; dropped {m['dropped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
