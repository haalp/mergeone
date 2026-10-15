"""mergeone serve: the always-on merge gateway.

    mergeone serve --policy .mergeone.yaml --db .mergeone/gate.db --workers 16

Inputs
  POST /webhook/github      GitHub pull_request events (opened, synchronize, reopened, closed)
  POST /decide              agent-native: {"repo","pr","head_sha","state","paths"} -> verdict (sync, or async with "wait": false)
  GET  /verdict/{repo}/{sha}  latest decision for a head sha
  GET  /verdict/pr/{repo}/{pr}
  GET  /stats?window=86400
  GET  /healthz             queue depth, worker count, model

Pipeline (per PR head sha, idempotent):
  webhook -> job (SQLite) -> worker: fetch PR state (GitHub API) -> gate.decide -> store -> check-run + labels

Fail-closed: any exception in the worker records an on_error decision (hold/human)
and posts a neutral/failed check so the PR cannot auto-merge silently.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..gate.engine import Gate, GateResult
from ..models.base import DECISIONS_V2
from .store import Store

GITHUB_API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str | None):
        self.token = token
        self.http = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"} if token else {})

    def pr_state(self, repo: str, pr: int) -> tuple[str, list[str], str]:
        """Render the same state the benchmark used, from the GitHub API (no clone)."""
        from ..data.extract import FileStat, MergedPR
        from ..data.features import render_state
        from datetime import datetime, timezone
        meta = self.http.get(f"{GITHUB_API}/repos/{repo}/pulls/{pr}").json()
        files = []
        page = 1
        while True:
            fs = self.http.get(f"{GITHUB_API}/repos/{repo}/pulls/{pr}/files", params={"per_page": 100, "page": page}).json()
            if not isinstance(fs, list) or not fs:
                break
            files += fs
            if len(fs) < 100:
                break
            page += 1
        diff = self.http.get(f"{GITHUB_API}/repos/{repo}/pulls/{pr}", headers={"Accept": "application/vnd.github.diff"}).text
        stats = [FileStat(f["filename"], f.get("additions", 0), f.get("deletions", 0)) for f in files]
        m = MergedPR(pr, meta["head"]["sha"], datetime.now(timezone.utc).isoformat(), meta.get("title") or "", meta.get("body") or "",
                     meta["head"].get("label", ""), stats, diff[:4000], len(diff) > 4000, False)
        return render_state(m), [s.path for s in stats], meta["head"]["sha"]

    def post_check(self, repo: str, head_sha: str, res: GateResult, details_url: str | None = None):
        if not self.token:
            return
        conclusion = {"pass": "success", "hold": "neutral", "block": "failure"}[res.verdict]
        summary = f"**{res.verdict.upper()}** → route `{res.route}` (rule `{res.rule}`)\n\n{res.reason}\n\n" + \
                  "\n".join(f"- `{q}`: {res.probs.get(q, float('nan')):.2f}" for q in DECISIONS_V2 if q in res.probs) + \
                  f"\n\nmodel `{res.model_version}` · {res.latency_ms:.0f} ms"
        body = {"name": "mergeone", "head_sha": head_sha, "status": "completed", "conclusion": conclusion,
                "output": {"title": f"{res.verdict} · {res.route}", "summary": summary[:60000]}}
        if details_url:
            body["details_url"] = details_url
        self.http.post(f"{GITHUB_API}/repos/{repo}/check-runs", json=body)

    def set_labels(self, repo: str, pr: int, res: GateResult):
        if not self.token:
            return
        try:
            cur = self.http.get(f"{GITHUB_API}/repos/{repo}/issues/{pr}/labels").json()
            keep = [l["name"] for l in cur if not (l["name"].startswith("gate:") or l["name"].startswith("route:"))]
            self.http.put(f"{GITHUB_API}/repos/{repo}/issues/{pr}/labels", json={"labels": keep + [f"gate:{res.verdict}", f"route:{res.route}"]})
        except Exception:
            pass


class Gateway:
    def __init__(self, policy_path: str, db_path: str, workers: int, model=None, github_token: str | None = None, public_url: str | None = None):
        self.store = Store(db_path)
        self.gate = Gate.from_policy(policy_path, log_path=None, model=model)  # gatelog lives in SQLite here
        self.gh = GitHubClient(github_token)
        self.public_url = public_url
        self.n_workers = workers
        # sync /decide calls run here, sized like the async workers, so HTTP concurrency is not capped by the ASGI threadpool
        self.sync_pool = ThreadPoolExecutor(max_workers=workers * 4, thread_name_prefix="gate-sync")
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._worker, name=f"gate-worker-{i}", daemon=True) for i in range(workers)]
        for t in self._threads:
            t.start()

    # ---- core decision (sync)
    def decide(self, repo: str, pr: int | None, head_sha: str, state: str, paths: list[str], context: dict | None = None) -> GateResult:
        existing = self.store.latest(repo, head_sha)
        sha = hashlib.sha256(state.encode()).hexdigest()
        if existing and existing["input_sha256"] == sha and existing["error"] is None:
            return GateResult(existing["verdict"], existing["route"], existing["reason"], existing["rule"], existing["probs"], existing["raw_probs"],
                              existing["model_version"], existing["latency_ms"], existing["cost_usd"], sha)
        res = self.gate.decide(state, paths, context=context)
        self.store.record(repo, pr, head_sha, res, context, state)
        return res

    # ---- async path
    def submit(self, repo: str, pr: int, head_sha: str, extra: dict | None = None) -> bool:
        return self.store.enqueue(f"{repo}:{pr}:{head_sha}", {"repo": repo, "pr": pr, "head_sha": head_sha, **(extra or {})})

    def _worker(self):
        while not self._stop.is_set():
            job = self.store.claim()
            if not job:
                time.sleep(0.05)
                continue
            job_id, p = job
            try:
                if "state" in p:
                    state, paths, head = p["state"], p.get("paths", []), p["head_sha"]
                else:
                    state, paths, head = self.gh.pr_state(p["repo"], p["pr"])
                res = self.decide(p["repo"], p["pr"], head, state, paths, context={"source": p.get("source", "webhook")})
                self.store.upsert_open_pr(p["repo"], p["pr"], head, paths)
                self.gh.post_check(p["repo"], head, res, f"{self.public_url}/verdict/{p['repo']}/{head}" if self.public_url else None)
                self.gh.set_labels(p["repo"], p["pr"], res)
                self.store.finish(job_id, True)
            except Exception as e:  # fail closed
                oe = self.gate.policy.get("on_error", {"verdict": "hold", "route": "human", "reason": "gateway error"})
                res = GateResult(oe["verdict"], oe["route"], f"{oe['reason']}: {type(e).__name__}", "on_error", {}, {}, "", 0.0, 0.0, "", error=str(e)[:500])
                try:
                    self.store.record(p["repo"], p.get("pr"), p.get("head_sha", ""), res, {"source": "worker-error"}, None)
                    self.gh.post_check(p["repo"], p.get("head_sha", ""), res)
                except Exception:
                    pass
                self.store.finish(job_id, False, str(e)[:500])

    def stop(self):
        self._stop.set()


def create_app(gw: Gateway, webhook_secret: str | None = None) -> FastAPI:
    app = FastAPI(title="mergeone", version="0.1.0")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "workers": gw.n_workers, "queue": gw.store.queue_depth(), "model": getattr(gw.gate.model, "name", "?")}

    @app.get("/stats")
    def stats(window: int = 86400):
        return gw.store.stats(window)

    @app.get("/verdict/{owner}/{repo}/{sha}")
    def verdict(owner: str, repo: str, sha: str):
        d = gw.store.latest(f"{owner}/{repo}", sha)
        if not d:
            raise HTTPException(404, "no decision for that sha")
        d.pop("state", None)
        return d

    @app.get("/verdict/pr/{owner}/{repo}/{pr}")
    def verdict_pr(owner: str, repo: str, pr: int):
        d = gw.store.latest_for_pr(f"{owner}/{repo}", pr)
        if not d:
            raise HTTPException(404, "no decision for that PR")
        d.pop("state", None)
        return d

    @app.get("/plan/{owner}/{repo}")
    def plan(owner: str, repo: str, semantic: bool = False):
        from .plan import landing_plan
        full = f"{owner}/{repo}"
        return landing_plan(gw.store.open_prs(full), gw.gate.model if semantic else None, gw.store, full)

    @app.post("/decide")
    async def decide(req: Request):
        """Agent-native entry point. Body: {repo, head_sha, state, paths?, pr?, wait?: true}"""
        body = await req.json()
        for k in ("repo", "head_sha", "state"):
            if k not in body:
                raise HTTPException(400, f"missing {k}")
        if body.get("wait", True):
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(gw.sync_pool, lambda: gw.decide(body["repo"], body.get("pr"), body["head_sha"], body["state"], body.get("paths", []), context={"source": "api"}))
            return res.to_dict()
        queued = gw.store.enqueue(f"{body['repo']}:{body.get('pr','-')}:{body['head_sha']}",
                                  {"repo": body["repo"], "pr": body.get("pr"), "head_sha": body["head_sha"], "state": body["state"], "paths": body.get("paths", []), "source": "api"})
        return JSONResponse({"queued": queued, "poll": f"/verdict/{body['repo']}/{body['head_sha']}"}, status_code=202)

    @app.post("/webhook/github")
    async def webhook(req: Request):
        raw = await req.body()
        if webhook_secret:
            sig = req.headers.get("X-Hub-Signature-256", "")
            mac = "sha256=" + hmac.new(webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, mac):
                raise HTTPException(401, "bad signature")
        event = req.headers.get("X-GitHub-Event", "")
        if event != "pull_request":
            return {"ignored": event}
        p = json.loads(raw)
        action = p.get("action")
        repo = p["repository"]["full_name"]
        pr = p["pull_request"]["number"]
        head = p["pull_request"]["head"]["sha"]
        if action in ("opened", "synchronize", "reopened", "ready_for_review"):
            return {"queued": gw.submit(repo, pr, head, {"source": "webhook"})}
        if action == "closed":
            gw.store.close_pr(repo, pr)
            return {"closed": True}
        return {"ignored": action}

    return app


def main(argv=None):
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(prog="mergeone serve")
    ap.add_argument("--policy", default=".mergeone.yaml")
    ap.add_argument("--db", default=".mergeone/gate.db")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--mock", action="store_true", help="use the mock model (no API key; for demos/load tests)")
    ap.add_argument("--public-url", default=os.environ.get("MERGEONE_PUBLIC_URL"))
    a = ap.parse_args(argv)
    model = None
    if a.mock:
        from ..models.mock import MockModel
        model = MockModel()
    gw = Gateway(a.policy, a.db, a.workers, model=model, github_token=os.environ.get("GITHUB_TOKEN"), public_url=a.public_url)
    app = create_app(gw, webhook_secret=os.environ.get("MERGEONE_WEBHOOK_SECRET"))
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
