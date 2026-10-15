"""Run 3 data: agent-authored PRs from public GitHub, with outcome labels.

    export GITHUB_TOKEN=ghp_...            # read-only public token; 5,000 req/h
    python3 -m mergeone.data.agent_prs discover --min-stars 500 --out data/run3/repos.json
    python3 -m mergeone.data.agent_prs fetch --repos data/run3/repos.json --per-repo 150 --out data/run3/agent_prs.jsonl

Discovery: for each agent signature, search merged AND closed-unmerged PRs,
count per repository, keep repos with >= --min-prs agent PRs and >= --min-stars.

Fetch: for each kept repo, pull up to --per-repo agent PRs (half merged, half
closed-unmerged when available), the files list, the diff head (4,000 chars,
same as gatebench), review/comment counts, force-push count (from the
timeline), and whether the PR was later reverted (title search).

Labels are OUTCOMES, not a judge:
  merged                 bool
  closed_unmerged        bool
  human_review_comments  int   (review comments by non-bot users)
  change_requested       bool  (any CHANGES_REQUESTED review by a human)
  force_pushes           int
  reverted_within_30d    bool  (a later merged PR titled Revert "... <title> ..." or referencing #N)
  stack                  dict|None (GitHub stacked-PR metadata if present)

Nothing here calls a model. Rate limits are respected via the X-RateLimit headers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

API = "https://api.github.com"

# Agent signatures: (name, search qualifier). Extend freely; each is reported separately.
AGENTS = [
    ("copilot", "author:app/copilot-swe-agent"),
    ("claude-code", '"Generated with Claude Code"'),
    ("claude-app", "author:app/claude"),
    ("devin", "author:app/devin-ai-integration"),
    ("codex", "author:app/chatgpt-codex-connector"),
    ("cursor", "author:app/cursor"),
]

BOT_SUFFIX = "[bot]"


class GH:
    def __init__(self):
        tok = os.environ.get("GITHUB_TOKEN")
        h = {"Accept": "application/vnd.github+json", "User-Agent": "mergeone-gatebench"}
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        self.c = httpx.Client(base_url=API, headers=h, timeout=60)
        self.calls = 0
        r = self.c.get("/rate_limit")
        lim = r.json().get("resources", {}) if r.status_code == 200 else {}
        core, search = lim.get("core", {}), lim.get("search", {})
        print(f"auth: {'token' if tok else 'ANONYMOUS'}; core {core.get('remaining')}/{core.get('limit')}/h, search {search.get('remaining')}/{search.get('limit')}/min", file=sys.stderr, flush=True)
        if not tok:
            print("WARNING: no GITHUB_TOKEN in this shell; anonymous limits are 60/h core and 10/min search. export GITHUB_TOKEN=ghp_... in the same terminal.", file=sys.stderr, flush=True)

    def get(self, path, **params):
        for attempt in range(6):
            try:
                r = self.c.get(path, params=params)
            except (httpx.TransportError, httpx.RemoteProtocolError) as e:
                print(f"  network error ({type(e).__name__}); retry {attempt+1}/6", file=sys.stderr, flush=True)
                time.sleep(5 * (attempt + 1))
                continue
            self.calls += 1
            if r.status_code == 200:
                return r
            if r.status_code in (403, 429):
                reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(5, reset - int(time.time()) + 1)
                if "secondary" in r.text.lower() or r.headers.get("Retry-After"):
                    wait = int(r.headers.get("Retry-After", 60))
                print(f"  rate limited; sleeping {wait}s", file=sys.stderr, flush=True)
                time.sleep(min(wait, 900))
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"{path}: {r.status_code} {r.text[:200]}")
        raise RuntimeError(f"{path}: gave up")

    def search_prs(self, q, pages=10):
        items = []
        for page in range(1, pages + 1):
            r = self.get("/search/issues", q=q, per_page=100, page=page, sort="updated", order="desc")
            if not r:
                break
            d = r.json()
            items += d.get("items", [])
            if len(d.get("items", [])) < 100 or len(items) >= 1000:
                break
            time.sleep(2.2)  # search API: 30/min with token
        return items


def _repo_agent_total(gh, repo, qual):
    """True number of closed agent PRs in a repo (one search call, per_page=1 → total_count)."""
    r = gh.get("/search/issues", q=f"repo:{repo} is:pr is:closed {qual}", per_page=1)
    time.sleep(2.2)
    return int(r.json().get("total_count", 0)) if r else 0


def cmd_discover(a):
    """Two stages. (1) Broad search: the most recently updated agent PRs per (agent, state)
    give *candidate* repos. (2) For every candidate (or --seed repo) ask GitHub for the
    true count of closed agent PRs with repo:-scoped searches. Stage 1 alone under-counts
    badly: it only sees the last pages*100 PRs per query."""
    gh = GH()
    seen = defaultdict(Counter)
    for name, qual in AGENTS:
        for state in ("is:merged", "is:unmerged is:closed"):
            q = f"is:pr {state} {qual}"
            print(f"search: {q}", file=sys.stderr, flush=True)
            for it in gh.search_prs(q, pages=a.pages):
                repo = it["repository_url"].split("/repos/")[1]
                seen[repo][name] += 1
    candidates = {r for r, c in seen.items() if sum(c.values()) >= a.min_seen}
    candidates |= set(a.seed or [])
    print(f"stage 1: {len(seen)} repos seen, {len(candidates)} candidates (min_seen={a.min_seen}, +{len(a.seed or [])} seed)", file=sys.stderr, flush=True)

    repos = []
    for repo in sorted(candidates, key=lambda r: -sum(seen[r].values())):
        r = gh.get(f"/repos/{repo}")
        if not r:
            continue
        meta = r.json()
        if meta.get("stargazers_count", 0) < a.min_stars or meta.get("fork") or meta.get("archived"):
            continue
        # stage 2: true per-agent totals, only for agents seen here (or all, for seed repos)
        agents = [(n, q) for n, q in AGENTS if n in seen[repo]] or AGENTS
        byagent = {}
        for name, qual in agents:
            n = _repo_agent_total(gh, repo, qual)
            if n:
                byagent[name] = n
        total = sum(byagent.values())
        print(f"  {repo:45s} ★{meta['stargazers_count']:<7d} agent PRs: {total:5d} {byagent}", file=sys.stderr, flush=True)
        if total < a.min_prs:
            continue
        repos.append({"repo": repo, "stars": meta["stargazers_count"], "language": meta.get("language"), "agent_prs_seen": total, "by_agent": byagent,
                      "default_branch": meta.get("default_branch", "main")})
    repos.sort(key=lambda r: -r["agent_prs_seen"])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(repos, indent=1))
    print(f"{len(repos)} repos kept (min_prs={a.min_prs}, min_stars={a.min_stars}); api calls {gh.calls}")
    for r in repos[:25]:
        print(f"  {r['agent_prs_seen']:4d}  ★{r['stars']:<7d} {r['repo']}  {r['by_agent']}")


def _diff_head(gh, repo, num):
    try:
        r = gh.c.get(f"/repos/{repo}/pulls/{num}", headers={"Accept": "application/vnd.github.diff"})
    except httpx.TransportError:
        return "", False
    gh.calls += 1
    if r.status_code != 200:
        return "", False
    t = r.text
    return t[:4000], len(t) > 4000


def _reverts(gh, repo, since_iso):
    """Merged PRs titled Revert since `since`, for revert matching."""
    out = []
    for it in gh.search_prs(f'repo:{repo} is:pr is:merged "Revert" in:title merged:>={since_iso}', pages=5):
        out.append({"number": it["number"], "title": it["title"], "body": it.get("body") or "", "merged_at": it.get("pull_request", {}).get("merged_at")})
    return out


def cmd_fetch(a):
    gh = GH()
    repos = json.loads(Path(a.repos).read_text())
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for l in out.open():
            d = json.loads(l)
            done.add((d["repo"], d["number"]))
    with out.open("a") as f:
        for ri, rmeta in enumerate(repos):
            repo = rmeta["repo"]
            print(f"[{ri+1}/{len(repos)}] {repo}", file=sys.stderr, flush=True)
            agent_quals = [q for n, q in AGENTS if n in rmeta["by_agent"]]
            cands = []
            for qual in agent_quals:
                for state in ("is:merged", "is:unmerged is:closed"):
                    cands += [(it, state) for it in gh.search_prs(f"repo:{repo} is:pr {state} {qual}", pages=2)]
            seen = set()
            per_state = {"is:merged": 0, "is:unmerged is:closed": 0}
            half = a.per_repo // 2
            reverts = None
            for it, state in cands:
                num = it["number"]
                if num in seen or (repo, num) in done or per_state[state] >= (half if state == "is:merged" else a.per_repo - half):
                    continue
                seen.add(num)
                pr = gh.get(f"/repos/{repo}/pulls/{num}")
                if not pr:
                    continue
                pr = pr.json()
                files = []
                for page in range(1, 6):
                    fr = gh.get(f"/repos/{repo}/pulls/{num}/files", per_page=100, page=page)
                    if not fr:
                        break
                    fs = fr.json()
                    files += [{"path": x["filename"], "insertions": x.get("additions", 0), "deletions": x.get("deletions", 0)} for x in fs]
                    if len(fs) < 100:
                        break
                diff_head, truncated = _diff_head(gh, repo, num)
                reviews = (gh.get(f"/repos/{repo}/pulls/{num}/reviews", per_page=100) or httpx.Response(200, json=[])).json()
                comments = (gh.get(f"/repos/{repo}/pulls/{num}/comments", per_page=100) or httpx.Response(200, json=[])).json()
                human = lambda u: u and not u.get("login", "").endswith(BOT_SUFFIX) and u.get("type") != "Bot"
                human_reviews = [r for r in reviews if human(r.get("user"))]
                force_pushes = 0
                ev = gh.get(f"/repos/{repo}/issues/{num}/timeline", per_page=100)
                if ev:
                    force_pushes = sum(1 for e in ev.json() if e.get("event") == "head_ref_force_pushed")
                merged_at = pr.get("merged_at")
                reverted, reverted_by = False, None
                if merged_at:
                    if reverts is None:
                        reverts = _reverts(gh, repo, (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d"))
                    t_norm = re.sub(r"\s+", " ", pr["title"].strip().lower())
                    m_at = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
                    for rv in reverts:
                        if not rv["merged_at"]:
                            continue
                        r_at = datetime.fromisoformat(rv["merged_at"].replace("Z", "+00:00"))
                        if 0 < (r_at - m_at).days <= 30 and (f"#{num}" in (rv["title"] + rv["body"]) or t_norm in rv["title"].lower()):
                            reverted, reverted_by = True, rv["number"]
                            break
                row = {
                    "repo": repo, "number": num, "agent": [n for n, q in AGENTS if q in agent_quals and (q.split(":")[-1] in (pr.get("user") or {}).get("login", "") or q.strip('"') in (pr.get("body") or ""))][:1] or [n for n, q in AGENTS if q in agent_quals][:1],
                    "title": pr["title"], "body": (pr.get("body") or "")[:4000], "author": (pr.get("user") or {}).get("login"),
                    "created_at": pr["created_at"], "merged_at": merged_at, "closed_at": pr.get("closed_at"),
                    "head_sha": pr["head"]["sha"], "base": pr["base"]["ref"], "files": files, "diff_head": diff_head, "diff_truncated": truncated,
                    "additions": pr.get("additions"), "deletions": pr.get("deletions"), "changed_files": pr.get("changed_files"),
                    "stack": pr.get("stack"),
                    "labels": {
                        "merged": bool(merged_at), "closed_unmerged": bool(pr.get("closed_at")) and not merged_at,
                        "human_review_comments": len([c for c in comments if human(c.get("user"))]) + len(human_reviews),
                        "change_requested": any(r.get("state") == "CHANGES_REQUESTED" for r in human_reviews),
                        "approved_by_human": any(r.get("state") == "APPROVED" for r in human_reviews),
                        "force_pushes": force_pushes, "reverted_within_30d": reverted, "reverted_by": reverted_by,
                    },
                }
                f.write(json.dumps(row) + "\n")
                f.flush()
                per_state[state] += 1
            print(f"   merged={per_state['is:merged']} closed={per_state['is:unmerged is:closed']} api_calls={gh.calls}", file=sys.stderr, flush=True)
    print("done; api calls", gh.calls)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover")
    d.add_argument("--min-stars", type=int, default=500)
    d.add_argument("--min-prs", type=int, default=20)
    d.add_argument("--pages", type=int, default=3, help="search pages per (agent,state); 100 PRs each, max 10")
    d.add_argument("--min-seen", type=int, default=3, help="stage-1 PRs seen before a repo becomes a candidate")
    d.add_argument("--seed", nargs="*", default=[], help="repos to count regardless of stage 1, e.g. microsoft/vscode")
    d.add_argument("--out", default="data/run3/repos.json")
    d.set_defaults(fn=cmd_discover)
    f = sub.add_parser("fetch")
    f.add_argument("--repos", default="data/run3/repos.json")
    f.add_argument("--per-repo", type=int, default=150)
    f.add_argument("--out", default="data/run3/agent_prs.jsonl")
    f.set_defaults(fn=cmd_fetch)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
