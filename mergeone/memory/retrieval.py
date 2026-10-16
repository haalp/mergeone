"""Deterministic candidate retrieval over a repository's PR memory (PROTOCOL R4.3-1).

score(q, c) = 0.55 · jaccard(files) + 0.35 · bm25_norm(title+body) + 0.10 · [same author]
restricted to candidates created or merged within ±WINDOW_DAYS of the query PR, excluding the query itself.
Weights were fixed on 2026-10-17 before any run-4 label existed; they are not tuned on run-4 data.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

WINDOW_DAYS = 45
TOP_K = 5
W_FILES, W_TEXT, W_AUTHOR = 0.55, 0.35, 0.10
_TOKEN = re.compile(r"[a-z0-9_]{3,}")
_STOP = set("the and for with that this from into are was were have has not but use using add fix fixes fixed update updates updated remove removes change changes changed support feature bug issue pull request when then than also".split())


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP]


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


@dataclass
class MemPR:
    number: int
    title: str
    body: str
    author: str | None
    state: str
    created_at: datetime
    merged_at: datetime | None
    closed_at: datetime | None
    files: frozenset[str]
    toks: Counter = field(default_factory=Counter)


class Memory:
    """One repository's PR memory with a tiny BM25 index. Build once (~ms per 1k PRs), query in sub-ms."""

    def __init__(self, repo: str):
        self.repo = repo
        self.prs: dict[int, MemPR] = {}
        self.df: Counter = Counter()
        self.avg_len = 1.0

    @classmethod
    def load(cls, path: Path, repo: str | None = None) -> "Memory":
        m = cls(repo or path.stem.replace("__", "/"))
        for l in Path(path).open():
            m.add(json.loads(l))
        m.finalize()
        return m

    def add(self, r: dict) -> None:
        p = MemPR(number=int(r["number"]), title=r.get("title") or "", body=(r.get("body") or "")[:2000], author=r.get("author"),
                  state=r.get("state") or "", created_at=_parse(r["created_at"]), merged_at=_parse(r.get("merged_at")), closed_at=_parse(r.get("closed_at")),
                  files=frozenset(f["path"] for f in r.get("files") or []))
        p.toks = Counter(tokens(p.title + " " + p.title + " " + p.body))  # title counted twice
        self.prs[p.number] = p

    def finalize(self) -> None:
        self.df = Counter()
        for p in self.prs.values():
            self.df.update(p.toks.keys())
        self.avg_len = max(1.0, sum(sum(p.toks.values()) for p in self.prs.values()) / max(1, len(self.prs)))

    def _bm25(self, q: Counter, d: Counter, k1: float = 1.2, b: float = 0.75) -> float:
        N = max(1, len(self.prs)); dl = sum(d.values()); s = 0.0
        for t in q:
            if t not in d:
                continue
            idf = math.log(1 + (N - self.df[t] + 0.5) / (self.df[t] + 0.5))
            tf = d[t]
            s += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / self.avg_len))
        return s

    def candidates(self, number: int | None, title: str, body: str, files: list[str], author: str | None, created_at: str | datetime,
                   k: int = TOP_K, window_days: int = WINDOW_DAYS) -> list[dict]:
        """Top-k memory PRs for a query PR. The query need not be in memory (live gateway use)."""
        t0 = created_at if isinstance(created_at, datetime) else _parse(created_at)
        lo, hi = t0 - timedelta(days=window_days), t0 + timedelta(days=window_days)
        qf = frozenset(files); qt = Counter(tokens(title + " " + title + " " + body))
        scored = []
        for c in self.prs.values():
            if c.number == number:
                continue
            anchor = c.merged_at or c.created_at
            if not (lo <= c.created_at <= hi or (anchor and lo <= anchor <= hi)):
                continue
            jac = len(qf & c.files) / len(qf | c.files) if (qf or c.files) else 0.0
            bm = self._bm25(qt, c.toks)
            scored.append((jac, bm, c))
        if not scored:
            return []
        bm_max = max(s[1] for s in scored) or 1.0
        out = []
        for jac, bm, c in scored:
            score = W_FILES * jac + W_TEXT * (bm / bm_max) + W_AUTHOR * float(author is not None and c.author == author)
            out.append({"number": c.number, "score": round(score, 4), "jaccard": round(jac, 4), "bm25": round(bm / bm_max, 4),
                        "same_author": c.author == author, "state": c.state, "title": c.title, "created_at": c.created_at.isoformat(),
                        "merged_at": c.merged_at.isoformat() if c.merged_at else None, "files": sorted(c.files)[:60], "body": c.body[:1500], "author": c.author})
        out.sort(key=lambda x: -x["score"])
        return out[:k]


def load_all(mem_dir: Path) -> dict[str, Memory]:
    out = {}
    for p in sorted(Path(mem_dir).glob("*.jsonl")):
        m = Memory.load(p)
        out[m.repo] = m
    return out
