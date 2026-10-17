"""A2 content fetch (PROTOCOL R5.1-A2): changed-line content of memory PRs from REST `pulls/{n}/files`.

One JSON file per PR under data/run5/patches/<owner>__<repo>/<n>.json:
  {"repo", "number", "status": ok|not_found|too_many_files|error, "files": [{"filename", "status", "additions",
   "deletions", "patch" (first 4,000 chars)}], "n_files", "calls", "fetched_at", "error"?}
per_page=100, at most 3 pages (300 files). A PR whose memory row says changed_files > 300 is recorded as
too_many_files without a call. Resumable: files with status ok / not_found / too_many_files are not refetched;
status error is retried on the next run. Rate limit: sleeps until reset when X-RateLimit-Remaining is low,
backs off on 403/429 (secondary limit) and 5xx.

  python -m mergeone.data.patches --targets results/run5/a1_targets.jsonl [--workers 4]
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

OUT_DIR = Path("data/run5/patches")
LOG = Path("data/run5/patches.log")
MAX_PAGES = 3
PER_PAGE = 100
MAX_FILES = MAX_PAGES * PER_PAGE
PATCH_CHARS = 4000
DONE_STATUSES = {"ok", "not_found", "too_many_files"}


def path_for(repo: str, number: int, out_dir: Path = OUT_DIR) -> Path:
    return Path(out_dir) / repo.replace("/", "__") / f"{number}.json"


def load_patch(repo: str, number: int, out_dir: Path = OUT_DIR) -> dict | None:
    p = path_for(repo, number, out_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


class _Limiter:
    """Shared rate-limit state across worker threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.resume_at = 0.0

    def wait(self):
        while True:
            with self.lock:
                d = self.resume_at - time.time()
            if d <= 0:
                return
            time.sleep(min(d, 30))

    def pause_until(self, t: float):
        with self.lock:
            self.resume_at = max(self.resume_at, t)


class Fetcher:
    def __init__(self, token: str, out_dir: Path = OUT_DIR, log: Path = LOG):
        self.client = httpx.Client(base_url="https://api.github.com", timeout=60,
                                   headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "mergeone-run5"})
        self.out_dir, self.log = Path(out_dir), Path(log)
        self.lim = _Limiter()
        self.counts = Counter()
        self.lock = threading.Lock()

    def _get(self, url: str, params: dict) -> httpx.Response:
        delay = 5.0
        for attempt in range(8):
            self.lim.wait()
            try:
                r = self.client.get(url, params=params)
            except httpx.HTTPError:
                time.sleep(delay); delay = min(delay * 2, 300)
                continue
            with self.lock:
                self.counts["calls"] += 1
            rem, reset = r.headers.get("x-ratelimit-remaining"), r.headers.get("x-ratelimit-reset")
            if rem is not None and reset is not None and int(rem) < 30:
                self.lim.pause_until(int(reset) + 2)
            if r.status_code in (403, 429):
                ra = r.headers.get("retry-after")
                if rem == "0" and reset:
                    self.lim.pause_until(int(reset) + 2)
                else:
                    self.lim.pause_until(time.time() + (int(ra) if ra and ra.isdigit() else delay))
                    delay = min(delay * 2, 600)
                continue
            if r.status_code >= 500:
                time.sleep(delay); delay = min(delay * 2, 300)
                continue
            return r
        return r

    def fetch_one(self, repo: str, number: int, changed_files: int | None = None) -> str:
        p = path_for(repo, number, self.out_dir)
        old = load_patch(repo, number, self.out_dir)
        if old and old.get("status") in DONE_STATUSES:
            with self.lock:
                self.counts["cached_" + old["status"]] += 1
            return old["status"]
        rec = {"repo": repo, "number": number, "files": [], "calls": 0}
        if changed_files is not None and changed_files > MAX_FILES:
            rec["status"] = "too_many_files"; rec["n_files"] = changed_files
        else:
            status = "ok"
            for page in range(1, MAX_PAGES + 1):
                r = self._get(f"/repos/{repo}/pulls/{number}/files", {"per_page": PER_PAGE, "page": page})
                rec["calls"] += 1
                if r.status_code == 404:
                    status = "not_found"; break
                if r.status_code != 200:
                    status = "error"; rec["error"] = f"HTTP {r.status_code}: {r.text[:200]}"; break
                items = r.json()
                for f in items:
                    rec["files"].append({"filename": f.get("filename"), "status": f.get("status"), "additions": f.get("additions"),
                                         "deletions": f.get("deletions"), "patch": (f.get("patch") or "")[:PATCH_CHARS] if f.get("patch") is not None else None})
                if len(items) < PER_PAGE:
                    break
            rec["status"] = status
            rec["n_files"] = len(rec["files"])
        rec["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec))
        tmp.replace(p)
        with self.lock:
            self.counts[rec["status"]] += 1
        return rec["status"]

    def run(self, targets: list[dict], workers: int = 4) -> Counter:
        t0 = time.time()
        n = len(targets)
        self._logline(f"start: {n} targets, workers={workers}")
        done = 0

        def job(t):
            return self.fetch_one(t["repo"], int(t["number"]), t.get("changed_files"))

        with ThreadPoolExecutor(workers) as ex:
            for _ in ex.map(job, targets):
                done += 1
                if done % 500 == 0:
                    self._logline(f"progress {done}/{n} {dict(self.counts)} elapsed {time.time() - t0:.0f}s")
        self._logline(f"finished: {done}/{n} {dict(self.counts)} elapsed {time.time() - t0:.0f}s")
        return self.counts

    def _logline(self, s: str):
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {s}"
        print(line, flush=True)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("a") as f:
            f.write(line + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True, help="JSONL of {repo, number, changed_files}")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    tok = os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise SystemExit("GITHUB_TOKEN not set")
    targets = [json.loads(l) for l in Path(a.targets).open()]
    Fetcher(tok, Path(a.out)).run(targets, a.workers)


if __name__ == "__main__":
    main()
