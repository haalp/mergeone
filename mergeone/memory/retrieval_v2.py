"""Retrieval v2 (PROTOCOL run-5 addendum, R5.1-A). Run-4 retrieval v1 (`retrieval.py`) is kept untouched.

A1, metadata stage (no model, no network). For a query PR, every memory PR created or merged within
±WINDOW_DAYS (90) of the query is scored by

    W_ISSUE·[share ≥ 1 issue/PR reference] + W_DIR·jaccard(dirs, depth 2) + W_FILE·jaccard(files)
    + W_TEXT·bm25_norm(title+body) + W_AUTHOR·[same author]

and the top-20 are kept (sorted by score descending, then PR number ascending). BM25 and its normalisation
(divide by the maximum over the in-window candidates) are as in v1; the query's BM25 sum runs over its
terms in sorted order so the Go store (gate/internal/memory/retrieval_v2.go) can reproduce it exactly.

Issue/PR references (title + body): `#N`, `GH-N`, `owner/repo#N`, and full github.com issue/pull URLs.
A bare `#N` / `GH-N` means this repository. References are compared as (owner/repo lower-cased, N); a PR's
reference to its own number is dropped.

Same author: logins are compared lower-cased with a trailing `[bot]` removed, because the dataset rows carry
REST logins (`claude[bot]`) and the memory carries GraphQL logins (`claude`) for the same account.

A2, content stage: see `rerank_a2()`; `ident()` uses the keyword stop-list `IDENT_STOP` below, committed
with this module before any patch was fetched.
"""
from __future__ import annotations

import itertools
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

WINDOW_DAYS = 90
TOP_K = 20  # A1 keeps the top-20
TOP_N = 5   # A1+A2 hands the top-5 to the pair question

# ---------------------------------------------------------------- frozen A1 weights
# FROZEN 2026-10-17T23:18:08Z by fit_weights_on_val() on the 22 val gold pairs only (grid of 826 combinations,
# results/run5/a1_weight_grid.json), before any test PR was scored. Val (tuned): recall@20 = 20/22 = 0.909,
# recall@5 = 19/22 = 0.864, MRR@20 = 0.582; 4 combinations tied on (recall@20, recall@5), broken by val MRR@20.
W_ISSUE, W_DIR, W_FILE, W_TEXT, W_AUTHOR = 0.5, 0.1, 0.2, 0.2, 0.0
WEIGHTS = (W_ISSUE, W_DIR, W_FILE, W_TEXT, W_AUTHOR)
FEATURES = ("issue", "dir", "file", "text", "author")

# ---------------------------------------------------------------- text (v1 tokenisation)
_TOKEN = re.compile(r"[a-z0-9_]{3,}")
_STOP = set("the and for with that this from into are was were have has not but use using add fix fixes fixed update updates updated remove removes change changes changed support feature bug issue pull request when then than also".split())
BODY_CHARS = 2000


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP]


def text_counter(title: str, body: str) -> Counter:
    return Counter(tokens(title + " " + title + " " + (body or "")[:BODY_CHARS]))  # title counted twice, as v1


# ---------------------------------------------------------------- issue / PR references
_REF_HASH = re.compile(r"(?:^|[^\w/.\-])#(\d{1,9})\b", re.ASCII)
_REF_GH = re.compile(r"(?:^|[^\w/.\-])[Gg][Hh]-(\d{1,9})\b", re.ASCII)
_REF_REPO = re.compile(r"(?:^|[^\w/.\-])([\w.\-]+/[\w.\-]+)#(\d{1,9})\b", re.ASCII)
_REF_URL = re.compile(r"https?://(?:www\.)?github\.com/([\w.\-]+/[\w.\-]+)/(?:issues|pull|pulls)/(\d{1,9})\b", re.ASCII | re.IGNORECASE)


def refs(text: str, repo: str, self_number: int | None) -> frozenset[tuple[str, int]]:
    """Issue/PR references in `text` as (owner/repo lower-cased, number), excluding the PR itself."""
    text = text or ""
    me = repo.lower()
    out = set()
    for n in _REF_HASH.findall(text):
        out.add((me, int(n)))
    for n in _REF_GH.findall(text):
        out.add((me, int(n)))
    for r, n in _REF_REPO.findall(text):
        out.add((r.lower(), int(n)))
    for r, n in _REF_URL.findall(text):
        out.add((r.lower(), int(n)))
    out.discard((me, self_number))
    return frozenset(out)


def dirs(paths) -> frozenset[str]:
    """Directory of each path truncated to depth 2 ('src/a/b/c.py' → 'src/a'); root-level files map to '.'."""
    out = set()
    for p in paths:
        parts = p.split("/")[:-1]
        out.add("/".join(parts[:2]) or ".")
    return frozenset(out)


def norm_author(a: str | None) -> str | None:
    if not a:
        return None
    a = a.lower()
    if a.endswith("[bot]"):
        a = a[: -len("[bot]")]
    return a or None


def _jac(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


# ---------------------------------------------------------------- query parsing from a dataset row's rendered state
def parse_state(state: str) -> dict:
    """Title, PR body, file paths and diff text from a run-3 rendered state (run 4 passed an empty body)."""
    first = state.split("\n", 1)[0]
    title = first[len("PULL REQUEST TITLE: "):] if first.startswith("PULL REQUEST TITLE: ") else first
    body = ""
    i = state.find("MERGE MESSAGE BODY:\n")
    if i >= 0:
        s = i + len("MERGE MESSAGE BODY:\n")
        j = state.find("\n\nSUMMARY:", s)
        body = state[s:j] if j >= 0 else state[s:]
    diff = ""
    k = state.find("\nDIFF (")
    if k >= 0:
        e = state.find("\n", k + 1)
        diff = state[e + 1:] if e >= 0 else ""
    return {"title": title, "body": body, "paths": state_paths(state), "diff": diff}


def state_paths(state: str) -> list[str]:
    """Port of mergeone.eval.run4._paths (FILES CHANGED block)."""
    out, on = [], False
    for line in state.split("\n"):
        if line.startswith("FILES CHANGED"):
            on = True
            continue
        if on:
            if not line.startswith("  ") or line.startswith("  ..."):
                if line.strip() == "" or line.startswith("DIFF"):
                    break
                continue
            out.append(line.strip().split(" ")[0])
    return out


# ---------------------------------------------------------------- memory
@dataclass
class MemPR:
    number: int
    title: str
    body: str
    author: str | None
    author_n: str | None
    state: str
    created_at: datetime
    merged_at: datetime | None
    closed_at: datetime | None
    files: frozenset[str]
    dirs: frozenset[str]
    refs: frozenset
    changed_files: int | None
    toks: Counter = field(default_factory=Counter)
    terms: frozenset = frozenset()
    dl: int = 0


@dataclass
class Query:
    repo: str
    number: int | None
    title: str
    body: str
    files: frozenset
    dirs: frozenset
    refs: frozenset
    author_n: str | None
    created_at: datetime
    terms: tuple  # sorted unique query terms

    @classmethod
    def make(cls, repo, number, title, body, paths, author, created_at) -> "Query":
        q = text_counter(title, body)
        return cls(repo=repo, number=number, title=title, body=body, files=frozenset(paths), dirs=dirs(paths),
                   refs=refs(title + "\n" + (body or ""), repo, number), author_n=norm_author(author),
                   created_at=created_at if isinstance(created_at, datetime) else _parse(created_at), terms=tuple(sorted(q)))

    @classmethod
    def from_row(cls, r: dict) -> "Query":
        s = parse_state(r["state"])
        return cls.make(r["repo"], r["number"], s["title"], s["body"], s["paths"], r.get("author"), r["created_at"])


class Memory:
    """One repository's PR memory for A1. Candidates are pruned by a time index (sorted created / anchor)."""

    def __init__(self, repo: str):
        self.repo = repo
        self.prs: dict[int, MemPR] = {}
        self.df: Counter = Counter()
        self.avg_len = 1.0
        self.N = 1

    @classmethod
    def load(cls, path: Path, repo: str | None = None) -> "Memory":
        m = cls(repo or Path(path).stem.replace("__", "/"))
        for l in Path(path).open():
            m.add(json.loads(l))
        m.finalize()
        return m

    def add(self, r: dict) -> None:
        n = int(r["number"])
        title, body = r.get("title") or "", r.get("body") or ""
        paths = [f["path"] for f in r.get("files") or []]
        p = MemPR(number=n, title=title, body=body[:BODY_CHARS], author=r.get("author"), author_n=norm_author(r.get("author")),
                  state=r.get("state") or "", created_at=_parse(r["created_at"]), merged_at=_parse(r.get("merged_at")),
                  closed_at=_parse(r.get("closed_at")), files=frozenset(paths), dirs=dirs(paths),
                  refs=refs(title + "\n" + body, self.repo, n), changed_files=r.get("changed_files"))
        p.toks = text_counter(title, body)
        p.terms = frozenset(p.toks)
        p.dl = sum(p.toks.values())
        self.prs[n] = p

    def finalize(self) -> None:
        self.df = Counter()
        for p in self.prs.values():
            self.df.update(p.toks.keys())
        self.N = max(1, len(self.prs))
        self.avg_len = max(1.0, sum(p.dl for p in self.prs.values()) / max(1, len(self.prs)))
        # time index: every PR enters the window iff created ∈ [lo,hi] or anchor (merged or created) ∈ [lo,hi]
        self._by_created = sorted(self.prs.values(), key=lambda p: (p.created_at, p.number))
        self._created_keys = [p.created_at for p in self._by_created]
        self._by_anchor = sorted(self.prs.values(), key=lambda p: (p.merged_at or p.created_at, p.number))
        self._anchor_keys = [p.merged_at or p.created_at for p in self._by_anchor]

    def bm25(self, terms: tuple, d: MemPR, k1: float = 1.2, b: float = 0.75) -> float:
        s = 0.0
        for t in terms:  # sorted order, so Go sums in the same order
            tf = d.toks.get(t)
            if not tf:
                continue
            idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
            s += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * d.dl / self.avg_len))
        return s

    def window(self, t0: datetime, window_days: int = WINDOW_DAYS) -> list[MemPR]:
        from bisect import bisect_left, bisect_right
        lo, hi = t0 - timedelta(days=window_days), t0 + timedelta(days=window_days)
        seen = {}
        for keys, arr in ((self._created_keys, self._by_created), (self._anchor_keys, self._by_anchor)):
            for p in arr[bisect_left(keys, lo):bisect_right(keys, hi)]:
                seen[p.number] = p
        return [seen[n] for n in sorted(seen)]

    def features(self, q: Query, window_days: int = WINDOW_DAYS) -> list[tuple[MemPR, tuple[float, float, float, float, float]]]:
        """Feature vectors (issue, dir, file, text_norm, author) for every in-window candidate except the query itself."""
        rows = []
        for c in self.window(q.created_at, window_days):
            if c.number == q.number:
                continue
            rows.append((c, float(bool(q.refs & c.refs)), _jac(q.dirs, c.dirs), _jac(q.files, c.files), self.bm25(q.terms, c),
                         float(q.author_n is not None and c.author_n == q.author_n)))
        if not rows:
            return []
        bm_max = max(r[4] for r in rows) or 1.0
        return [(c, (iss, dj, fj, bm / bm_max, au)) for c, iss, dj, fj, bm, au in rows]

    def a1(self, q: Query, k: int = TOP_K, weights: tuple = None, window_days: int = WINDOW_DAYS) -> list[dict]:
        w = weights or WEIGHTS
        out = []
        for c, f in self.features(q, window_days):
            score = w[0] * f[0] + w[1] * f[1] + w[2] * f[2] + w[3] * f[3] + w[4] * f[4]
            out.append((score, c, f))
        out.sort(key=lambda x: (-x[0], x[1].number))
        res = []
        for rank, (score, c, f) in enumerate(out[:k], 1):
            res.append({"number": c.number, "score": score, "a1_rank": rank, "issue_ref": bool(f[0]), "dir_jaccard": f[1], "jaccard": f[2],
                        "bm25": f[3], "same_author": bool(f[4]), "state": c.state, "title": c.title, "created_at": c.created_at.isoformat(),
                        "merged_at": c.merged_at.isoformat() if c.merged_at else None, "author": c.author})
        return res


def load_all(mem_dir: Path) -> dict[str, Memory]:
    out = {}
    for p in sorted(Path(mem_dir).glob("*.jsonl")):
        m = Memory.load(p)
        out[m.repo] = m
    return out


# ---------------------------------------------------------------- weight fit (val gold pairs only)
def weight_grid(step: int = 1, cap: int = 6, total: int = 10):
    """All (w_issue, w_dir, w_file, w_text, w_author) in {0, 0.1, …, 0.6} summing to 1 (integer tenths)."""
    for c in itertools.product(range(0, cap + 1, step), repeat=5):
        if sum(c) == total:
            yield c


def fit_weights_on_val(mems: dict[str, Memory], dataset_rows: list[dict], labels: list[dict]) -> dict:
    """Grid over weights on the val-split gold `superseded_by` pairs ONLY. Test rows are never touched.

    Objective: val recall@20, then val recall@5. Remaining ties (not specified by the protocol): higher val
    mean reciprocal rank (rank capped at 20, 0 if outside), then the first combination in grid order.
    """
    import numpy as np
    by_pid = {r["pr_number"]: r for r in dataset_rows if r["split"] == "val"}
    gold = [l for l in labels if l["label"] == "superseded_by" and l["pr_number"] in by_pid]
    per = []
    for l in sorted(gold, key=lambda l: l["pr_number"]):
        r = by_pid[l["pr_number"]]
        m = mems[r["repo"]]
        feats = m.features(Query.from_row(r))
        nums = np.array([c.number for c, _ in feats], dtype=np.int64)
        F = np.array([f for _, f in feats], dtype=np.float64).reshape(-1, 5)
        gi = np.where(nums == l["superseded_by"])[0]
        per.append({"pr_number": l["pr_number"], "repo": r["repo"], "number": r["number"], "gold": l["superseded_by"],
                    "n_window": int(len(nums)), "gold_in_window": bool(len(gi)), "nums": nums, "F": F, "gi": int(gi[0]) if len(gi) else -1})
    grid = []
    for c in weight_grid():
        w = tuple(x / 10 for x in c)
        ranks = []
        for p in per:
            if p["gi"] < 0:
                ranks.append(None)
                continue
            F = p["F"]
            s = w[0] * F[:, 0] + w[1] * F[:, 1] + w[2] * F[:, 2] + w[3] * F[:, 3] + w[4] * F[:, 4]
            g = s[p["gi"]]
            ranks.append(int(np.sum(s > g) + np.sum((s == g) & (p["nums"] < p["gold"])) + 1))
        n = len(per)
        r20 = sum(1 for x in ranks if x is not None and x <= 20) / n
        r5 = sum(1 for x in ranks if x is not None and x <= 5) / n
        mrr = sum(1 / x for x in ranks if x is not None and x <= 20) / n
        grid.append({"weights": dict(zip(FEATURES, w)), "w": w, "recall_at_20": r20, "recall_at_5": r5, "mrr_at_20": mrr, "ranks": ranks})
    best_key = lambda g: (g["recall_at_20"], g["recall_at_5"], g["mrr_at_20"])
    best_i = max(range(len(grid)), key=lambda i: (best_key(grid[i]), -i))
    best = grid[best_i]
    top = max(best_key(g)[:2] for g in grid)
    return {"n_val_gold": len(per), "n_grid": len(grid), "objective": "max val recall@20, then val recall@5; then val MRR@20, then grid order",
            "chosen": best["weights"], "chosen_val": {k: best[k] for k in ("recall_at_20", "recall_at_5", "mrr_at_20")},
            "chosen_ranks": [{"pr_number": p["pr_number"], "repo": p["repo"], "number": p["number"], "gold": p["gold"], "n_window": p["n_window"],
                              "gold_in_window": p["gold_in_window"], "rank": rk} for p, rk in zip(per, best["ranks"])],
            "n_tied_on_r20_r5": sum(1 for g in grid if best_key(g)[:2] == top),
            "n_tied_on_r20_r5_mrr": sum(1 for g in grid if best_key(g) == best_key(best)),
            "grid": [{k: g[k] for k in ("weights", "recall_at_20", "recall_at_5", "mrr_at_20")} for g in grid]}


# ---------------------------------------------------------------- A2 content stage
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_DIFF_HEADER = re.compile(r"^(\+\+\+|---) (a/|b/|/dev/null)")
# Fixed keyword / noise stop-list (compared lower-cased). Python, TS/JS, Go, C#, Java, Ruby, Rust, C++ + common noise.
IDENT_STOP = frozenset("""
self this true false null none nil undefined return import from
and as assert async await break class continue def del elif else except finally for global if in is lambda nonlocal not or pass raise try while with yield print
abstract any boolean case catch const constructor debugger declare default delete do enum export extends function get implements infer instanceof interface keyof let module namespace never new number object package private protected public readonly require set static string super switch symbol throw type typeof unique unknown var void
chan defer fallthrough func go goto map range select struct byte rune int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 uintptr float32 float64 complex64 complex128 error bool len cap make append panic recover iota
base checked decimal double event explicit extern fixed float foreach implicit internal lock long operator out override params ref sbyte sealed short sizeof stackalloc ulong unchecked unsafe ushort using virtual volatile partial where dynamic nameof value var record init required task
final native strictfp synchronized throws transient char permits sealed non
begin end ensure rescue retry redo undef unless until then elsif alias defined attr_reader attr_accessor attr_writer puts
crate dyn impl loop match mod move mut pub trait unsafe use usize isize u8 u16 u32 u64 u128 i8 i16 i32 i64 i128 f32 f64 str vec box option result some ok err
auto bool_t constexpr consteval constinit decltype friend inline mutable noexcept nullptr register reinterpret_cast static_cast dynamic_cast const_cast signed template typedef typename union unsigned wchar_t std include define ifdef ifndef endif pragma
todo fixme xxx the and for not but are was were has have will can should would could may might must also into that with when then than
""".split())


def ident(text_lines) -> frozenset[str]:
    """Identifiers on added/removed lines of a unified diff, minus IDENT_STOP. `text_lines` is an iterable of lines."""
    out = set()
    for line in text_lines:
        if not line or line[0] not in "+-" or _DIFF_HEADER.match(line):
            continue
        for t in _IDENT.findall(line[1:]):
            if t.lower() not in IDENT_STOP:
                out.add(t)
    return frozenset(out)


def ident_of_diff(diff: str) -> frozenset[str]:
    return ident((diff or "").split("\n"))


def ident_of_patch(files: list[dict]) -> frozenset[str]:
    out = set()
    for f in files:
        out |= ident((f.get("patch") or "")[:4000].split("\n"))
    return frozenset(out)


def rerank_a2(top20: list[dict], q_ident: frozenset, cand_ident: dict[int, frozenset | None], n: int = TOP_N) -> list[dict]:
    """0.5·A1_norm + 0.5·jaccard(ident). A1_norm = A1 score / max A1 score in the top-20. Candidates whose patch could
    not be fetched (cand_ident[num] is None) keep their A1 position; fetched candidates are re-ordered among the rest.
    Returns all 20 with a2_rank, a2_score, ident_jaccard, patch_fetched set; the first `n` are the A1+A2 top-n."""
    if not top20:
        return []
    smax = max(c["score"] for c in top20) or 1.0
    rows, fetched = [], []
    for c in top20:
        x = dict(c)
        ids = cand_ident.get(c["number"])
        x["patch_fetched"] = ids is not None
        x["ident_jaccard"] = _jac(q_ident, ids) if ids is not None else None
        x["a2_score"] = 0.5 * (c["score"] / smax) + 0.5 * x["ident_jaccard"] if ids is not None else None
        rows.append(x)
        if ids is not None:
            fetched.append(x)
    fetched.sort(key=lambda x: (-x["a2_score"], x["a1_rank"]))
    it = iter(fetched)
    out = [x if not x["patch_fetched"] else next(it) for x in rows]
    for i, x in enumerate(out, 1):
        x["a2_rank"] = i
    return out
