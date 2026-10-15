"""SQLite-backed store for the gateway: decisions, jobs, and swarm state.

One file, WAL mode, safe for one process with a worker pool. 30k decisions/day
is ~1.3 MB/hour of gatelog; a year fits in a few GB. For multi-process or
multi-node deployments, swap this for Postgres behind the same interface.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  repo TEXT NOT NULL,
  pr INTEGER,
  head_sha TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  verdict TEXT NOT NULL,
  route TEXT NOT NULL,
  rule TEXT NOT NULL,
  reason TEXT,
  probs TEXT NOT NULL,
  raw_probs TEXT NOT NULL,
  model_version TEXT,
  latency_ms REAL,
  cost_usd REAL,
  error TEXT,
  context TEXT,
  state TEXT
);
CREATE INDEX IF NOT EXISTS ix_dec_repo_pr ON decisions(repo, pr, ts);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dec_head ON decisions(repo, head_sha, input_sha256);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,        -- repo:pr:head_sha  (idempotency)
  ts REAL NOT NULL,
  status TEXT NOT NULL,            -- queued | running | done | failed
  payload TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status, ts);

CREATE TABLE IF NOT EXISTS open_prs (
  repo TEXT NOT NULL,
  pr INTEGER NOT NULL,
  head_sha TEXT NOT NULL,
  paths TEXT NOT NULL,             -- json list
  updated REAL NOT NULL,
  PRIMARY KEY (repo, pr)
);
"""


class Store:
    def __init__(self, path: str | Path = ".mergeone/gate.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)

    # ---- decisions
    def record(self, repo: str, pr: int | None, head_sha: str, result, context: dict | None, state: str | None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR REPLACE INTO decisions (ts,repo,pr,head_sha,input_sha256,verdict,route,rule,reason,probs,raw_probs,model_version,latency_ms,cost_usd,error,context,state)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), repo, pr, head_sha, result.input_sha256, result.verdict, result.route, result.rule, result.reason,
                 json.dumps(result.probs), json.dumps(result.raw_probs), result.model_version, result.latency_ms, result.cost_usd,
                 result.error, json.dumps(context or {}), state))
            return cur.lastrowid

    def latest(self, repo: str, head_sha: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM decisions WHERE repo=? AND head_sha=? ORDER BY ts DESC LIMIT 1", (repo, head_sha)).fetchone()
        return self._row(row)

    def latest_for_pr(self, repo: str, pr: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM decisions WHERE repo=? AND pr=? ORDER BY ts DESC LIMIT 1", (repo, pr)).fetchone()
        return self._row(row)

    def _row(self, row):
        if not row:
            return None
        cols = [d[0] for d in self._conn.execute("SELECT * FROM decisions LIMIT 0").description]
        d = dict(zip(cols, row))
        for k in ("probs", "raw_probs", "context"):
            d[k] = json.loads(d[k]) if d.get(k) else {}
        return d

    def stats(self, since_s: float = 86400) -> dict:
        t0 = time.time() - since_s
        rows = self._conn.execute("SELECT verdict, route, rule, latency_ms, cost_usd, error FROM decisions WHERE ts>=?", (t0,)).fetchall()
        n = len(rows)
        lat = sorted(r[3] for r in rows if r[3] is not None and r[5] is None)
        from collections import Counter
        return {
            "window_s": since_s, "decisions": n, "per_hour": n / (since_s / 3600),
            "verdicts": dict(Counter(r[0] for r in rows)), "routes": dict(Counter(r[1] for r in rows)), "rules": dict(Counter(r[2] for r in rows)),
            "error_rate": sum(1 for r in rows if r[5]) / n if n else 0.0,
            "p50_ms": lat[len(lat) // 2] if lat else None, "p99_ms": lat[min(int(len(lat) * 0.99), len(lat) - 1)] if lat else None,
            "cost_usd": sum(r[4] or 0 for r in rows),
        }

    # ---- jobs (idempotent by key)
    def enqueue(self, key: str, payload: dict) -> bool:
        with self._lock:
            try:
                self._conn.execute("INSERT INTO jobs (key, ts, status, payload) VALUES (?,?,?,?)", (key, time.time(), "queued", json.dumps(payload)))
                return True
            except sqlite3.IntegrityError:
                return False

    def claim(self) -> tuple[int, dict] | None:
        with self._lock:
            row = self._conn.execute("SELECT id, payload FROM jobs WHERE status='queued' ORDER BY ts LIMIT 1").fetchone()
            if not row:
                return None
            self._conn.execute("UPDATE jobs SET status='running', attempts=attempts+1 WHERE id=?", (row[0],))
            return row[0], json.loads(row[1])

    def finish(self, job_id: int, ok: bool, error: str | None = None, max_attempts: int = 3):
        with self._lock:
            if ok:
                self._conn.execute("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
            else:
                att = self._conn.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                self._conn.execute("UPDATE jobs SET status=?, last_error=? WHERE id=?", ("failed" if att >= max_attempts else "queued", error, job_id))

    def queue_depth(self) -> dict:
        from collections import Counter
        return dict(Counter(r[0] for r in self._conn.execute("SELECT status FROM jobs").fetchall()))

    # ---- swarm
    def upsert_open_pr(self, repo: str, pr: int, head_sha: str, paths: list[str]):
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO open_prs (repo,pr,head_sha,paths,updated) VALUES (?,?,?,?,?)", (repo, pr, head_sha, json.dumps(paths), time.time()))

    def close_pr(self, repo: str, pr: int):
        with self._lock:
            self._conn.execute("DELETE FROM open_prs WHERE repo=? AND pr=?", (repo, pr))

    def open_prs(self, repo: str) -> list[dict]:
        return [{"pr": r[0], "head_sha": r[1], "paths": json.loads(r[2]), "updated": r[3]} for r in
                self._conn.execute("SELECT pr, head_sha, paths, updated FROM open_prs WHERE repo=? ORDER BY updated", (repo,)).fetchall()]
