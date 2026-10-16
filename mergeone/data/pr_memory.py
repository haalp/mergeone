"""Run 4 data: per-repository PR memory and human comments on closed PRs (PROTOCOL R4.2).

  python -m mergeone.data.pr_memory memory   --repos data/run3/repos.json --dataset data/run3/dataset.jsonl
  python -m mergeone.data.pr_memory comments --dataset data/run3/dataset.jsonl

memory:   every PR (any author) created in the 180 days ending at the newest sampled PR of that repo:
          number, title, body, author, state, timestamps, base/head refs, changed paths (+/-). GraphQL,
          repository.pullRequests ordered by CREATED_AT desc, 50 per page. No diffs. Resumable per repo.
comments: for every closed-unmerged PR in the dataset, issue comments + review bodies + review comments
          (author login + body). Labels are assigned later by supersede_labels.py; nothing is interpreted here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

GQL = "https://api.github.com/graphql"
WINDOW_DAYS = 180


class GQLClient:
    def __init__(self):
        tok = os.environ.get("GITHUB_TOKEN")
        if not tok:
            sys.exit("GITHUB_TOKEN is required for GraphQL")
        self.c = httpx.Client(headers={"Authorization": f"Bearer {tok}", "User-Agent": "mergeone-gatebench"}, timeout=90)
        self.calls = 0

    def run(self, query: str, variables: dict) -> dict:
        for attempt in range(8):
            try:
                r = self.c.post(GQL, json={"query": query, "variables": variables})
            except (httpx.TransportError, httpx.RemoteProtocolError) as e:
                print(f"  network error ({type(e).__name__}); retry {attempt+1}", file=sys.stderr, flush=True)
                time.sleep(5 * (attempt + 1))
                continue
            self.calls += 1
            if r.status_code in (502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code in (403, 429):
                reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(5, reset - int(time.time()) + 1)
                print(f"  rate limited; sleeping {min(wait, 900)}s", file=sys.stderr, flush=True)
                time.sleep(min(wait, 900))
                continue
            d = r.json()
            if "errors" in d and not d.get("data"):
                msg = json.dumps(d["errors"])[:300]
                if "RATE_LIMITED" in msg:
                    time.sleep(60)
                    continue
                raise RuntimeError(msg)
            rl = d.get("data", {}).get("rateLimit") if isinstance(d.get("data"), dict) else None
            if rl and rl.get("remaining", 1000) < 100:
                reset_at = datetime.fromisoformat(rl["resetAt"].replace("Z", "+00:00"))
                wait = max(5, (reset_at - datetime.now(timezone.utc)).total_seconds() + 2)
                print(f"  graphql budget low ({rl['remaining']}); sleeping {wait:.0f}s", file=sys.stderr, flush=True)
                time.sleep(wait)
            return d["data"]
        raise RuntimeError("graphql: gave up")


MEMORY_QUERY = """
query($owner:String!,$name:String!,$after:String){
  rateLimit{remaining resetAt}
  repository(owner:$owner,name:$name){
    pullRequests(first:50,after:$after,orderBy:{field:CREATED_AT,direction:DESC}){
      pageInfo{hasNextPage endCursor}
      nodes{
        number title body author{login} state isDraft createdAt mergedAt closedAt updatedAt
        baseRefName headRefName additions deletions changedFiles
        files(first:100){totalCount nodes{path additions deletions}}
      }
    }
  }
}"""


def cmd_memory(a):
    gh = GQLClient()
    repos = json.loads(Path(a.repos).read_text())
    newest = defaultdict(lambda: "1970-01-01T00:00:00Z")
    for l in Path(a.dataset).open():
        r = json.loads(l)
        newest[r["repo"]] = max(newest[r["repo"]], r.get("created_at") or "")
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for ri, meta in enumerate(repos):
        repo = meta["repo"]
        owner, name = repo.split("/")
        end = datetime.fromisoformat(newest[repo].replace("Z", "+00:00"))
        start = end - timedelta(days=WINDOW_DAYS)
        out = out_dir / (repo.replace("/", "__") + ".jsonl")
        done_file = out.with_suffix(".done")
        if done_file.exists():
            print(f"[{ri+1}/{len(repos)}] {repo}: done", file=sys.stderr, flush=True)
            continue
        print(f"[{ri+1}/{len(repos)}] {repo}: window {start.date()} → {end.date()}", file=sys.stderr, flush=True)
        seen = set()
        if out.exists():
            seen = {json.loads(l)["number"] for l in out.open()}
        after, n, stop = None, 0, False
        with out.open("a") as f:
            while not stop:
                d = gh.run(MEMORY_QUERY, {"owner": owner, "name": name, "after": after})
                prs = d["repository"]["pullRequests"]
                for p in prs["nodes"]:
                    created = datetime.fromisoformat(p["createdAt"].replace("Z", "+00:00"))
                    if created < start:
                        stop = True
                        break
                    if created > end or p["number"] in seen:
                        continue
                    row = {"repo": repo, "number": p["number"], "title": p["title"], "body": (p.get("body") or "")[:4000],
                           "author": (p.get("author") or {}).get("login"), "state": p["state"], "draft": p["isDraft"],
                           "created_at": p["createdAt"], "merged_at": p["mergedAt"], "closed_at": p["closedAt"], "updated_at": p["updatedAt"],
                           "base": p["baseRefName"], "head_ref": p["headRefName"], "additions": p["additions"], "deletions": p["deletions"],
                           "changed_files": p["changedFiles"], "files_truncated": (p["files"] or {}).get("totalCount", 0) > 100,
                           "files": [{"path": x["path"], "insertions": x["additions"], "deletions": x["deletions"]} for x in ((p["files"] or {}).get("nodes") or [])]}
                    f.write(json.dumps(row) + "\n")
                    n += 1
                f.flush()
                if not prs["pageInfo"]["hasNextPage"]:
                    break
                after = prs["pageInfo"]["endCursor"]
                if n and n % 1000 == 0:
                    print(f"   {n} PRs…", file=sys.stderr, flush=True)
        done_file.write_text(datetime.now(timezone.utc).isoformat())
        print(f"   {n} new PRs (total {n + len(seen)}); graphql calls {gh.calls}", file=sys.stderr, flush=True)
    print("memory done; graphql calls", gh.calls)


def _comments_query(items: list[tuple[str, str, int]]) -> str:
    parts = []
    for i, (owner, name, num) in enumerate(items):
        parts.append(f'''p{i}: repository(owner:"{owner}",name:"{name}"){{ pullRequest(number:{num}){{ number
          comments(first:100){{nodes{{body author{{login}} createdAt}}}}
          reviews(first:50){{nodes{{body author{{login}} submittedAt state comments(first:30){{nodes{{body author{{login}}}}}}}}}}
          timelineItems(itemTypes:[CLOSED_EVENT],last:1){{nodes{{... on ClosedEvent{{actor{{login}} createdAt}}}}}}
        }} }}''')
    return "query{ rateLimit{remaining resetAt} " + " ".join(parts) + " }"


def cmd_comments(a):
    gh = GQLClient()
    rows = [json.loads(l) for l in Path(a.dataset).open()]
    targets = [r for r in rows if r["labels"].get("closed_unmerged")]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {(json.loads(l)["repo"], json.loads(l)["number"]) for l in out.open()} if out.exists() else set()
    todo = [r for r in targets if (r["repo"], r["number"]) not in done]
    print(f"{len(targets)} closed-unmerged PRs, {len(todo)} to fetch", file=sys.stderr, flush=True)
    B = 15
    with out.open("a") as f:
        for i in range(0, len(todo), B):
            batch = todo[i:i + B]
            items = [(r["repo"].split("/")[0], r["repo"].split("/")[1], r["number"]) for r in batch]
            d = gh.run(_comments_query(items), {})
            for j, r in enumerate(batch):
                pr = (d.get(f"p{j}") or {}).get("pullRequest") or {}
                cs = []
                for c in (pr.get("comments") or {}).get("nodes") or []:
                    cs.append({"kind": "comment", "body": c.get("body") or "", "author": (c.get("author") or {}).get("login"), "at": c.get("createdAt")})
                for rv in (pr.get("reviews") or {}).get("nodes") or []:
                    if rv.get("body"):
                        cs.append({"kind": "review", "body": rv["body"], "author": (rv.get("author") or {}).get("login"), "at": rv.get("submittedAt"), "state": rv.get("state")})
                    for c in (rv.get("comments") or {}).get("nodes") or []:
                        cs.append({"kind": "review_comment", "body": c.get("body") or "", "author": (c.get("author") or {}).get("login")})
                closed = ((pr.get("timelineItems") or {}).get("nodes") or [{}])
                closer = ((closed[-1] if closed else {}).get("actor") or {}).get("login") if closed else None
                f.write(json.dumps({"repo": r["repo"], "number": r["number"], "pr_number": r["pr_number"], "closed_by": closer, "comments": cs}) + "\n")
            f.flush()
            if (i // B) % 10 == 0:
                print(f"  {min(i + B, len(todo))}/{len(todo)} graphql calls {gh.calls}", file=sys.stderr, flush=True)
    print("comments done; graphql calls", gh.calls)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("memory")
    m.add_argument("--repos", default="data/run3/repos.json")
    m.add_argument("--dataset", default="data/run3/dataset.jsonl")
    m.add_argument("--out-dir", default="data/run4/memory")
    m.set_defaults(fn=cmd_memory)
    c = sub.add_parser("comments")
    c.add_argument("--dataset", default="data/run3/dataset.jsonl")
    c.add_argument("--out", default="data/run4/comments.jsonl")
    c.set_defaults(fn=cmd_comments)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
