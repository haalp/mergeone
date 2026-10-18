"""Run 6 part B: wider candidate pool k ∈ {5, 10, 20} and human-labelled hold precision (PROTOCOL.md "Run 6 addendum, part B").

  python -m mergeone.eval.run6b retrieve --k 20   retrieval v2 (A1 frozen weights → A2 re-rank of the top-20 when patches are cached)
                                                  → results/run6/candidates_k20.jsonl; --k 10 / --k 5 are prefixes of it
                                                  (asserted) → candidates_k{10,5}.jsonl; recall@k → results/run6/retrieval_k.json
  python -m mergeone.eval.run6b pairs --k 20      run 4's pair builder on candidates_k20 → results/run6/pairs.jsonl (qset pair)
                                                  + pairs_todo_<model>.jsonl (retrieved pairs not answered in runs 4–5)
  python -m mergeone.eval.run6b sample            R6B.2-B2: 100 test PRs held by pplx-flash at k=20 (duplicate_of ≥ 0.7), stratified,
                                                  seed 6 → data/run6/hold_sample.jsonl {pr_number, repo, a_number, b_number} only
  python -m mergeone.eval.run6b label             blind labelling app on http://127.0.0.1:8765 → data/run6/hold_labels.jsonl
  python -m mergeone.eval.run6b label --export    label counts
  python -m mergeone.eval.run6b report            (= python -m mergeone.cli report results/run6 --run6b)
                                                  → results/run6/b_metrics.json, b_tables.md

Pair predictions are reused across runs by pair id (sha1 of "repo#A~B"; the run-5 rule): run 4 first, then run 5, then run 6.
`duplicate_of` per PR at k = max `same_change` over its first k candidates. The candidate B of a hold is the top-scoring one
(ties → the better-ranked one).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import types
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATASET = Path("data/run3/dataset.jsonl")  # relative to cwd (repo root), as runs 3–5
MEM_DIR = Path("data/run4/memory")
LABELS = Path("data/run4/labels.jsonl")
PATCH_DIR = Path("data/run5/patches")
RUN3_DIR = Path("results/run3")
RUN4_DIR = Path("results/run4")
RUN5_DIR = Path("results/run5")
RUN_DIR = Path("results/run6")
DATA_DIR = Path("data/run6")
PRICES = Path("results/prices.json")

KS = (5, 10, 20)
K_MAX = 20
MODELS = ("jev", "pplx-flash", "haiku")
PRIMARY = "pplx-flash"            # R6B.2-B2: the sample is drawn from pplx-flash's holds
H1_MODELS = ("jev", "pplx-flash")  # R6B-H1: R4-H3 for at least one of these
RUN3_VERDICTS = ("jev", "haiku")   # as run 5: jev/haiku verdicts from run-3 v2 predictions; pplx-flash from its run-5 v3 answers
DUP_THRESHOLD = 0.7
SAMPLE_N, SAMPLE_SEED = 100, 6
STRATA = ("superseded", "closed_unmarked", "merged")
HOLD_LABELS = ("duplicate", "pointer", "unrelated")
SAMPLE_FIELDS = ("pr_number", "repo", "a_number", "b_number")
SWEEP = (0.6, 0.7, 0.8, 0.9)
H3_COST_BAR = 0.002
CAND_FIELDS = ("number", "score", "jaccard", "bm25", "same_author", "state", "title", "created_at", "merged_at", "author",
               "issue_ref", "dir_jaccard", "a1_rank", "a2_rank", "a2_score", "ident_jaccard", "patch_fetched")
TOP20_FIELDS = ("number", "score", "a1_rank", "a2_rank", "a2_score", "ident_jaccard", "patch_fetched", "issue_ref", "dir_jaccard",
                "jaccard", "bm25", "same_author")


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).open() if l.strip()]


def _write(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hpid(repo: str, a: int, b: int | None = None) -> int:
    key = f"{repo}#{a}" if b is None else f"{repo}#{a}~{b}"
    return int(hashlib.sha1(key.encode()).hexdigest()[:12], 16)


def cand_path(k: int, run_dir: Path | None = None) -> Path:
    return Path(run_dir or RUN_DIR) / f"candidates_k{k}.jsonl"


def _slim(c: dict) -> dict:
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in c.items()}


# ================================================================= retrieve (B1)
def build_k20(ds_rows: list[dict], mems: dict, patch_dir: Path) -> tuple[list[dict], dict]:
    """A1 top-20 per PR, A2 re-rank of those 20 if candidate patches are cached (else A1 order, as rerank_a2 keeps unfetched
    candidates in place). Row format = run 5's candidates.jsonl with `candidates` = all 20."""
    from ..data import patches as P
    from ..memory import retrieval_v2 as R
    patch_dir = Path(patch_dir)
    have = patch_dir.is_dir() and any(patch_dir.iterdir())
    cache: dict = {}
    out = []
    for r in ds_rows:
        m = mems[r["repo"]]
        s = R.parse_state(r["state"])
        q = R.Query.make(r["repo"], r["number"], s["title"], s["body"], s["paths"], r.get("author"), r["created_at"])
        top = m.a1(q, k=K_MAX)
        qi = R.ident_of_diff(s["diff"])
        ci = {}
        if have:
            for c in top:
                key = (r["repo"], c["number"])
                if key not in cache:
                    rec = P.load_patch(*key, out_dir=patch_dir)
                    cache[key] = R.ident_of_patch(rec["files"]) if rec and rec.get("status") == "ok" else None
                ci[c["number"]] = cache[key]
        ranked = R.rerank_a2(top, qi, ci)
        bodies = {}
        for c in ranked:
            mp = m.prs[c["number"]]
            bodies[str(c["number"])] = {"body": mp.body[:1500], "files": sorted(mp.files)[:60]}
        out.append({"pr_number": r["pr_number"], "repo": r["repo"], "number": r["number"], "split": r["split"],
                    "candidates": [_slim({k: c[k] for k in CAND_FIELDS}) for c in ranked], "candidate_bodies": bodies,
                    "query_ident_n": len(qi), "candidates_top20": [_slim({k: c[k] for k in TOP20_FIELDS}) for c in ranked]})
    fetched = sum(1 for v in cache.values() if v is not None)
    return out, {"patch_dir": str(patch_dir), "patch_dir_present": have, "unique_candidates_looked_up": len(cache), "unique_fetched_ok": fetched}


def truncate(row: dict, k: int) -> dict:
    x = dict(row)
    x["candidates"] = row["candidates"][:k]
    keep = {str(c["number"]) for c in x["candidates"]}
    x["candidate_bodies"] = {n: b for n, b in (row.get("candidate_bodies") or {}).items() if n in keep}
    return x


def check_prefix(run_dir: Path | None = None) -> dict:
    """Every existing candidates_k{5,10}.jsonl must be, PR by PR, the first k of candidates_k20.jsonl, which must equal its own
    candidates_top20 list. Raises AssertionError otherwise."""
    files = {k: cand_path(k, run_dir) for k in KS if cand_path(k, run_dir).exists()}
    if K_MAX not in files:
        return {"checked": []}
    base = {r["pr_number"]: [c["number"] for c in r["candidates"]] for r in _rows(files[K_MAX])}
    for r in _rows(files[K_MAX]):
        top20 = [c["number"] for c in r.get("candidates_top20") or r["candidates"]]
        if base[r["pr_number"]] != top20[:K_MAX]:
            raise AssertionError(f"k=20 candidates of PR {r['pr_number']} differ from its candidates_top20")
    for k, f in files.items():
        if k == K_MAX:
            continue
        rows = {r["pr_number"]: [c["number"] for c in r["candidates"]] for r in _rows(f)}
        if set(rows) != set(base):
            raise AssertionError(f"{f}: PR set differs from {files[K_MAX]} ({len(rows)} vs {len(base)})")
        for pid, nums in rows.items():
            if nums != base[pid][:k]:
                raise AssertionError(f"{f}: PR {pid} candidates {nums} are not the first {k} of the k=20 list {base[pid][:k]}")
    return {"checked": sorted(files)}


def _gold(ds: dict, split: str) -> list[dict]:
    return [l for l in _rows(LABELS) if l["label"] == "superseded_by" and ds.get(l["pr_number"], {}).get("split") == split]


def recall_table(cand_rows: list[dict], ds: dict) -> dict:
    final = {r["pr_number"]: [c["number"] for c in r["candidates"]] for r in cand_rows}
    a1 = {r["pr_number"]: [c["number"] for c in sorted(r.get("candidates_top20") or r["candidates"], key=lambda c: c.get("a1_rank") or 0)]
          for r in cand_rows}
    out = {}
    for sp in ("val", "test"):
        g = _gold(ds, sp)
        e = {"n_gold": len(g)}
        for k in KS:
            e[f"recall_at_{k}"] = (sum(l["superseded_by"] in final.get(l["pr_number"], [])[:k] for l in g) / len(g)) if g else None
            e[f"a1_recall_at_{k}"] = (sum(l["superseded_by"] in a1.get(l["pr_number"], [])[:k] for l in g) / len(g)) if g else None
        out[sp] = e
    return out


def _a2_status(cand_rows: list[dict]) -> str:
    n = sum(1 for r in cand_rows for c in (r.get("candidates_top20") or r["candidates"]) if c.get("patch_fetched"))
    return "ran" if n else ("not run: no A2 patch cached (data/run5/patches missing or empty); the k=20 list is the A1 top-20 in A1 order, "
                            "so the k=5/10 prefixes are A1-only")


def cmd_retrieve(a) -> None:
    k = a.k
    k20 = cand_path(K_MAX)
    ds_rows = _rows(DATASET)
    ds = {r["pr_number"]: r for r in ds_rows}
    info = None
    if k == K_MAX or not k20.exists():
        from ..memory import retrieval_v2 as R
        mems = R.load_all(MEM_DIR)
        rows, info = build_k20(ds_rows, mems, PATCH_DIR)
        _write(k20, rows)
        print(f"wrote {k20} ({len(rows)} PRs)")
        if not info["patch_dir_present"]:
            print(f"NOTE: {PATCH_DIR} not present — A2 not applied; candidates are the A1 top-20 in A1 order (A1-only).", file=sys.stderr)
    else:
        rows = _rows(k20)
        print(f"derived from {k20}")
    if k != K_MAX:
        _write(cand_path(k), [truncate(r, k) for r in rows])
        print(f"wrote {cand_path(k)}")
    chk = check_prefix()
    met_path = RUN_DIR / "retrieval_k.json"
    prev = json.loads(met_path.read_text()) if met_path.exists() else {}
    rec = recall_table(rows, ds)
    r20 = rec["test"]["recall_at_20"]
    met = {"computed_at": _now(), "a2_status": _a2_status(rows), "prefix_check": chk, "n_prs": len(rows),
           "a2_lookup": info if info is not None else prev.get("a2_lookup"), "recall": rec,
           "R6B-H1_recall": {"recall_at_20_test": r20, "n_gold": rec["test"]["n_gold"], "bar": ">= 0.80",
                             "pass": None if r20 is None else r20 >= 0.80},
           "note": "val is tuned (A1 weights fit on val gold pairs); test is the R6B-H1 number. recall@20 is the same for A1 and A1+A2 "
                   "(A2 re-orders the A1 top-20)."}
    old = RUN5_DIR / "candidates.jsonl"
    if old.exists():  # run 5's A1+A2 top-5 should be our k=5 prefix
        r5 = {r["pr_number"]: [c["number"] for c in r["candidates"]][:5] for r in _rows(old)}
        same = sum(1 for r in rows if r5.get(r["pr_number"]) == [c["number"] for c in r["candidates"]][:5])
        met["agreement_with_run5_top5"] = {"identical": same, "n": len(rows)}
    met_path.write_text(json.dumps(met, indent=2))
    print(json.dumps({k_: v for k_, v in met.items() if k_ != "a2_lookup"}, indent=2))


# ================================================================= pair predictions (shared)
def load_prices() -> dict:
    return json.loads(PRICES.read_text()) if PRICES.exists() else {}


def list_cost(model: str, dec: dict, prices: dict) -> float | None:
    p = prices.get(model)
    if not p:
        return dec.get("cost_usd")
    return (dec.get("input_tokens") or 0) * p["input_per_mtok"] / 1e6 + (dec.get("output_tokens") or 0) * p.get("output_per_mtok", 0) / 1e6


def _ok_pair(r: dict, default_qset: str | None) -> bool:
    if r.get("qset", default_qset) != "pair" or r.get("mode", "joint") != "joint":
        return False
    d = r["decision"]
    v = (d.get("probs") or {}).get("same_change")
    return d.get("error") is None and v is not None and v == v


def load_pair_preds(model: str, sources: tuple[tuple[str, Path], ...] | None = None, prices: dict | None = None) -> dict[int, dict]:
    """{pair_id: {p, cost, src}} over error-free pair rows; earlier sources win (run 4, run 5, run 6), last row wins within a file."""
    sources = sources or (("run4", RUN4_DIR), ("run5", RUN5_DIR), ("run6", RUN_DIR))
    prices = load_prices() if prices is None else prices
    out: dict[int, dict] = {}
    for src, d in sources:
        here = {}
        for sp in ("val", "test"):
            f = Path(d) / f"predictions_{model}_{sp}_all.jsonl"
            if not f.exists():
                continue
            for l in f.open():
                r = json.loads(l)
                if _ok_pair(r, "pair" if src == "run4" else None):
                    here[r["pr_number"]] = {"p": float(r["decision"]["probs"]["same_change"]), "cost": list_cost(model, r["decision"], prices), "src": src}
        for k, v in here.items():
            out.setdefault(k, v)
    return out


def answered_before(model: str) -> set[int]:
    """Pair ids this model answered (error-free) in runs 4–5."""
    return set(load_pair_preds(model, (("run4", RUN4_DIR), ("run5", RUN5_DIR)), prices={}))


def pair_ids(cr: dict, k: int) -> list[int]:
    return [_hpid(cr["repo"], cr["number"], c["number"]) for c in cr["candidates"][:k]]


def dup_scores(cand_rows: list[dict], preds: dict[int, dict], k: int, split: str | None = "test") -> dict[int, dict]:
    """{pr_number: {dup, b, missing}}; dup = max same_change over the first k candidates (0.0 if none answered),
    b = the top-scoring candidate's number (ties → better rank)."""
    out = {}
    for cr in cand_rows:
        if split and cr["split"] != split:
            continue
        best, b, miss = None, None, 0
        for c in cr["candidates"][:k]:
            e = preds.get(_hpid(cr["repo"], cr["number"], c["number"]))
            if e is None:
                miss += 1
                continue
            if best is None or e["p"] > best:
                best, b = e["p"], c["number"]
        out[cr["pr_number"]] = {"dup": best if best is not None else 0.0, "b": b, "missing": miss}
    return out


# ================================================================= pairs
def cmd_pairs(a) -> None:
    from . import run4
    cpath = cand_path(a.k)
    if not cpath.exists():
        raise SystemExit(f"{cpath} not found; run `python -m mergeone.eval.run6b retrieve --k {a.k}` first")
    out = RUN_DIR / "pairs.jsonl"
    rows = run4.cmd_pairs(types.SimpleNamespace(candidates=cpath, out=out, top_k=a.k))
    summary = pairs_summary(rows, _rows(cpath), out.parent)
    print(json.dumps(summary, indent=2))


def pairs_summary(pair_rows: list[dict], cand_rows: list[dict], out_dir: Path) -> dict:
    """Writes pairs_todo_<model>.jsonl (retrieved pairs not answered in runs 4–5) and pairs_summary.json."""
    prices = load_prices()
    retrieved = [r for r in pair_rows if r["kind"] == "retrieved"]
    state_len = {r["pr_number"]: len(r["state"]) for r in retrieved}
    by_k = {k: {p for cr in cand_rows for p in pair_ids(cr, k)} for k in KS}
    rep = {"computed_at": _now(), "pair_rows": len(pair_rows), "kinds": dict(Counter(r["kind"] for r in pair_rows)),
           "retrieved_pairs_by_k": {str(k): len(v) for k, v in by_k.items()},
           "todo_rule": "retrieved pairs (A, candidate) whose pair id has no error-free same_change row for this model in "
                        "results/run4 or results/run5 predictions_<model>_{val,test}_all.jsonl; gold_not_retrieved / random_negative rows "
                        "are built (run-4 format) but not asked", "models": {}}
    for m in MODELS:
        before = load_pair_preds(m, (("run4", RUN4_DIR), ("run5", RUN5_DIR)), prices)
        todo = [r["pr_number"] for r in retrieved if r["pr_number"] not in before]
        _write(Path(out_dir) / f"pairs_todo_{m}.jsonl", ({"pr_number": p} for p in todo))
        costs = [e["cost"] for e in before.values() if e["cost"] is not None]
        if costs:
            per_pair, how = sum(costs) / len(costs), f"mean list-price cost of this model's {len(costs)} earlier pair rows"
        else:
            p = prices.get(m, {})
            per_pair = (sum(state_len.get(t, 0) for t in todo) / max(1, len(todo)) / 4) * p.get("input_per_mtok", 0) / 1e6
            how = "≈ chars/4 input tokens × list input price (no earlier pair rows)"
        rep["models"][m] = {"new_pairs_by_k": {str(k): len(v - set(before)) for k, v in by_k.items()}, "todo": len(todo),
                            "todo_by_split": dict(Counter(r["split"] for r in retrieved if r["pr_number"] not in before)),
                            "est_cost_usd_per_pair": per_pair, "est_cost_usd_todo": per_pair * len(todo), "cost_basis": how}
    (Path(out_dir) / "pairs_summary.json").write_text(json.dumps(rep, indent=2))
    return rep


# ================================================================= sample (B2)
def stratum(pid: int, ds: dict, labels4: dict) -> str:
    if (labels4.get(pid) or {}).get("label", "").startswith("superseded"):
        return "superseded"
    return "closed_unmarked" if ds[pid]["labels"]["closed_unmerged"] else "merged"


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Proportional allocation, largest remainder (ties in STRATA order); all of a stratum if the population is ≤ n."""
    total = sum(sizes.values())
    if total <= n:
        return dict(sizes)
    q = {s: n * sizes[s] / total for s in sizes}
    alloc = {s: int(math.floor(q[s])) for s in sizes}
    order = sorted(sizes, key=lambda s: (-(q[s] - alloc[s]), STRATA.index(s) if s in STRATA else 99))
    i = 0
    while sum(alloc.values()) < n:
        s = order[i % len(order)]
        if alloc[s] < sizes[s]:
            alloc[s] += 1
        i += 1
    return alloc


def draw_sample(held: list[int], strata: dict[int, str], n: int = SAMPLE_N, seed: int = SAMPLE_SEED) -> tuple[list[int], dict]:
    rng = random.Random(seed)
    pops = {s: sorted(p for p in held if strata[p] == s) for s in STRATA}
    alloc = allocate({s: len(v) for s, v in pops.items()}, n)
    picked = []
    for s in STRATA:
        picked += rng.sample(pops[s], alloc[s])
    rng.shuffle(picked)  # strata interleaved so the labelling order reveals nothing
    return picked, {"population": {s: len(v) for s, v in pops.items()}, "sampled": alloc}


def missing_pairs(cand_rows: list[dict], preds_by_model: dict[str, dict], k: int = K_MAX, split: str = "test") -> dict[str, int]:
    ids = [p for cr in cand_rows if cr["split"] == split for p in pair_ids(cr, k)]
    return {m: sum(1 for p in ids if p not in preds) for m, preds in preds_by_model.items()}


def cmd_sample(a) -> None:
    cpath = cand_path(K_MAX)
    if not cpath.exists():
        raise SystemExit(f"REFUSING: {cpath} not found (retrieve --k 20 first)")
    cands = _rows(cpath)
    preds = {m: load_pair_preds(m) for m in MODELS}
    miss = missing_pairs(cands, preds)
    if any(v > a.max_missing for v in miss.values()):
        raise SystemExit("REFUSING to draw the sample: k=20 test pair predictions are incomplete "
                         f"(missing per model: {json.dumps(miss)}; allowed {a.max_missing}). Finish the pair predictions "
                         "(scripts/run6b_mac.sh) — the sample is drawn once (R6B.4).")
    ds = {r["pr_number"]: r for r in _rows(DATASET)}
    labels4 = {r["pr_number"]: r for r in _rows(LABELS)}
    dup = dup_scores(cands, preds[PRIMARY], K_MAX, "test")
    held = [p for p, e in dup.items() if e["dup"] >= DUP_THRESHOLD and e["b"] is not None]
    strata = {p: stratum(p, ds, labels4) for p in held}
    picked, counts = draw_sample(held, strata, a.n, a.seed)
    rows = [{"pr_number": p, "repo": ds[p]["repo"], "a_number": ds[p]["number"], "b_number": dup[p]["b"]} for p in picked]
    out = DATA_DIR / "hold_sample.jsonl"
    if out.exists():
        if _rows(out) == rows:
            print(f"{out} exists and is identical to this draw; unchanged")
        else:
            raise SystemExit(f"REFUSING: {out} exists and differs from this draw (the sample is drawn once, R6B.4). "
                             "Pair predictions changed after the draw? Investigate; do not overwrite.")
    else:
        _write(out, rows)
        print(f"wrote {out} ({len(rows)} rows; fields {', '.join(SAMPLE_FIELDS)})")
    meta = {"drawn_at": _now(), "seed": a.seed, "n": a.n, "k": K_MAX, "model_holds_from": PRIMARY, "threshold": DUP_THRESHOLD,
            "n_test_prs": len(dup), "n_held_test": len(held), "strata": counts, "allocation": "proportional, largest remainder",
            "missing_pair_predictions_test_k20": miss}
    (RUN_DIR / "sample_meta.json").parent.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "sample_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps({"held_test_prs": len(held), "strata": counts}, indent=2))


# ================================================================= label (blind)
def read_labels(path: Path) -> dict[tuple, dict]:
    """Last row per (pr_number, a_number, b_number) wins."""
    out = {}
    if Path(path).exists():
        for r in _rows(path):
            out[(r["pr_number"], r["a_number"], r["b_number"])] = r
    return out


def _mem_rows(repo: str, numbers: set[int]) -> dict[int, dict]:
    p = MEM_DIR / (repo.replace("/", "__") + ".jsonl")
    out = {}
    if p.exists():
        for l in p.open():
            x = json.loads(l)
            if x.get("number") in numbers:
                out[x["number"]] = x
    return out


def build_items(sample: list[dict]) -> list[dict]:
    """What the labeller sees: repository, PR A (title, body, files, diff excerpt), PR B (title, body, files, link). Nothing else."""
    from ..memory.retrieval_v2 import parse_state
    need = {r["pr_number"] for r in sample}
    ds = {}
    for l in DATASET.open():
        x = json.loads(l)
        if x["pr_number"] in need:
            ds[x["pr_number"]] = x
    by_repo = defaultdict(set)
    for r in sample:
        by_repo[r["repo"]].add(r["b_number"])
    mem = {repo: _mem_rows(repo, nums) for repo, nums in by_repo.items()}
    items = []
    for i, r in enumerate(sample):
        s = parse_state(ds[r["pr_number"]]["state"]) if r["pr_number"] in ds else {"title": "(not found)", "body": "", "paths": [], "diff": ""}
        b = mem.get(r["repo"], {}).get(r["b_number"])
        items.append({"i": i, "repo": r["repo"],
                      "a": {"number": r["a_number"], "title": s["title"], "body": s["body"][:6000], "files": s["paths"][:200],
                            "diff": s["diff"][:12000]},
                      "b": {"number": r["b_number"], "title": (b or {}).get("title") or "(not in memory)",
                            "body": ((b or {}).get("body") or "")[:6000], "files": [f["path"] for f in (b or {}).get("files") or []][:200],
                            "url": f"https://github.com/{r['repo']}/pull/{r['b_number']}"}})
    return items


LABEL_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hold labels</title><style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--bd:#ddd;--card:#fff;--acc:#2456c9}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecece8;--mut:#9a9a94;--bd:#333;--card:#1f1f1d;--acc:#7da2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--bd);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:1}
header b{font-size:15px}#prog{color:var(--mut)}button{font:inherit;padding:6px 12px;border:1px solid var(--bd);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer}
button.l{font-weight:600}button.on{outline:2px solid var(--acc)}textarea{font:inherit;width:min(420px,100%);height:34px;background:var(--card);color:var(--fg);border:1px solid var(--bd);border-radius:6px;padding:6px}
main{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px}@media (max-width:900px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:12px 14px;min-width:0}
h2{margin:0 0 6px;font-size:16px}h3{margin:12px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
pre{white-space:pre-wrap;word-break:break-word;margin:0;font:12px/1.4 ui-monospace,monospace;max-height:420px;overflow:auto}
a{color:var(--acc)}.hint{color:var(--mut);font-size:12px}
</style></head><body>
<header><b id="repo"></b><span id="prog"></span>
<button class="l" data-l="duplicate">1 duplicate</button><button class="l" data-l="pointer">2 pointer</button><button class="l" data-l="unrelated">3 unrelated</button>
<textarea id="note" placeholder="note (optional)"></textarea>
<button id="prev">&larr;</button><button id="next">&rarr;</button>
<span class="hint">duplicate: same change / B makes A unnecessary · pointer: A's author should have looked at B first · unrelated: false alarm (also if B is gone)</span></header>
<main><section><h2 id="at"></h2><h3>Body</h3><pre id="ab"></pre><h3>Files</h3><pre id="af"></pre><h3>Diff (excerpt)</h3><pre id="ad"></pre></section>
<section><h2 id="bt"></h2><div><a id="bl" target="_blank" rel="noopener"></a></div><h3>Body</h3><pre id="bb"></pre><h3>Files</h3><pre id="bf"></pre></section></main>
<script>
let st={n:0,labels:[]},cur=0;
const $=id=>document.getElementById(id);
async function state(){st=await (await fetch('/api/state')).json();}
function render(){const done=st.labels.filter(x=>x).length;$('prog').textContent=`item ${cur+1} / ${st.n} · ${done} labelled`+(done===st.n?' · all done':'');
 document.querySelectorAll('button.l').forEach(b=>b.classList.toggle('on',st.labels[cur]&&st.labels[cur].label===b.dataset.l));}
async function show(i){if(st.n===0)return;cur=Math.max(0,Math.min(st.n-1,i));const it=await (await fetch('/api/item?i='+cur)).json();
 $('repo').textContent=it.repo;$('at').textContent=`A  #${it.a.number}  ${it.a.title}`;$('ab').textContent=it.a.body||'(empty)';
 $('af').textContent=it.a.files.join('\n');$('ad').textContent=it.a.diff||'(none)';
 $('bt').textContent=`B  #${it.b.number}  ${it.b.title}`;$('bl').href=it.b.url;$('bl').textContent=it.b.url;$('bb').textContent=it.b.body||'(empty)';
 $('bf').textContent=it.b.files.join('\n')||'(none)';$('note').value=(st.labels[cur]&&st.labels[cur].note)||'';render();window.scrollTo(0,0);}
async function label(l){const r=await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({i:cur,label:l,note:$('note').value})});
 if(!r.ok){alert('save failed: '+await r.text());return;}st=await r.json();let nx=st.labels.findIndex((x,j)=>!x&&j>cur);if(nx<0)nx=st.labels.findIndex(x=>!x);show(nx<0?cur:nx);}
document.querySelectorAll('button.l').forEach(b=>b.onclick=()=>label(b.dataset.l));$('prev').onclick=()=>show(cur-1);$('next').onclick=()=>show(cur+1);
document.addEventListener('keydown',e=>{if(e.target.tagName==='TEXTAREA'){if(e.key==='Escape')e.target.blur();return;}
 const m={'1':'duplicate','2':'pointer','3':'unrelated'};if(m[e.key])label(m[e.key]);else if(e.key==='ArrowLeft')show(cur-1);else if(e.key==='ArrowRight')show(cur+1);});
state().then(()=>{const f=st.labels.findIndex(x=>!x);show(f<0?0:f);});
</script></body></html>"""


def make_server(sample_path: Path, labels_path: Path, host: str = "127.0.0.1", port: int = 8765):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    sample = _rows(sample_path)
    for r in sample:
        if set(r) != set(SAMPLE_FIELDS):
            raise SystemExit(f"{sample_path}: unexpected fields {sorted(r)} (blind rule: only {SAMPLE_FIELDS})")
    items = build_items(sample)
    lock = threading.Lock()
    labels_path = Path(labels_path)

    def state():
        lab = read_labels(labels_path)
        out = []
        for r in sample:
            x = lab.get((r["pr_number"], r["a_number"], r["b_number"]))
            out.append({"label": x["label"], "note": x.get("note", "")} if x else None)
        return {"n": len(sample), "labels": out}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._send(200, LABEL_HTML, "text/html")
            if u.path == "/api/state":
                return self._send(200, json.dumps(state()))
            if u.path == "/api/item":
                try:
                    i = int(parse_qs(u.query).get("i", ["0"])[0])
                    return self._send(200, json.dumps(items[i]))
                except (ValueError, IndexError):
                    return self._send(404, '{"error":"no such item"}')
            self._send(404, '{"error":"not found"}')

        def do_POST(self):
            if urlparse(self.path).path != "/api/label":
                return self._send(404, '{"error":"not found"}')
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                i, lab = int(body["i"]), body["label"]
                if lab not in HOLD_LABELS or not 0 <= i < len(sample):
                    raise ValueError("bad label or index")
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                return self._send(400, json.dumps({"error": str(e)}))
            r = sample[i]
            row = {"pr_number": r["pr_number"], "a_number": r["a_number"], "b_number": r["b_number"], "label": lab,
                   "note": str(body.get("note") or "")[:2000], "labelled_at": _now()}
            with lock:
                labels_path.parent.mkdir(parents=True, exist_ok=True)
                with labels_path.open("a") as f:
                    f.write(json.dumps(row) + "\n")
            self._send(200, json.dumps(state()))

    return ThreadingHTTPServer((host, port), H)


def label_counts(sample_path: Path, labels_path: Path) -> dict:
    sample = _rows(sample_path) if Path(sample_path).exists() else []
    keys = {(r["pr_number"], r["a_number"], r["b_number"]) for r in sample}
    lab = {k: v for k, v in read_labels(labels_path).items() if k in keys}
    return {"sample": len(sample), "labelled": len(lab), "counts": {l: sum(1 for v in lab.values() if v["label"] == l) for l in HOLD_LABELS},
            "rows_in_file": sum(1 for _ in Path(labels_path).open()) if Path(labels_path).exists() else 0}


def cmd_label(a) -> None:
    sp, lp = DATA_DIR / "hold_sample.jsonl", DATA_DIR / "hold_labels.jsonl"
    if a.export:
        print(json.dumps(label_counts(sp, lp), indent=2))
        return
    if not sp.exists():
        raise SystemExit(f"{sp} not found; run `python -m mergeone.eval.run6b sample` first")
    srv = make_server(sp, lp, "127.0.0.1", a.port)
    print(f"labelling {sp} → {lp}\nopen http://127.0.0.1:{srv.server_address[1]}/  (keys 1/2/3, ←/→; Ctrl-C to stop; resumable)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        print(json.dumps(label_counts(sp, lp), indent=2))


# ================================================================= report
def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def precision(labels: list[str]) -> dict:
    n = len(labels)
    c = Counter(labels)
    d, p = c["duplicate"], c["pointer"]
    return {"n": n, "duplicate": d, "pointer": p, "unrelated": c["unrelated"],
            "union": (d + p) / n if n else None, "union_ci95": wilson(d + p, n),
            "duplicate_share": d / n if n else None, "duplicate_ci95": wilson(d, n)}


def label_metrics(sample: list[dict], labels: dict[tuple, dict], dup: dict[str, dict[int, dict[int, dict]]], strata: dict[int, str] | None = None) -> dict:
    """R6B-H2, R6B-H4, threshold sweep, other models' precision on the PRs they also hold.
    dup[model][k][pr_number] = {dup, b}. Requires every sample row to be labelled."""
    lab = {r["pr_number"]: labels[(r["pr_number"], r["a_number"], r["b_number"])]["label"] for r in sample}
    b20 = {r["pr_number"]: r["b_number"] for r in sample}
    pf20, pf5 = dup[PRIMARY][K_MAX], dup[PRIMARY][5]
    out = {"n_sample": len(sample)}
    h2 = precision(list(lab.values()))
    h2.update({"pass": h2["union"] >= 0.60 and h2["duplicate_share"] >= 0.30, "demote_to_annotate": h2["union"] < 0.40,
               "bar": "union >= 0.60 and duplicate >= 0.30 (pplx-flash, k=20); union < 0.40 → demote dedup to annotate",
               "sample_b_still_top": sum(1 for p in lab if (pf20.get(p) or {}).get("b") == b20[p]),
               "sample_still_held": sum(1 for p in lab if (pf20.get(p) or {}).get("dup", 0) >= DUP_THRESHOLD)})
    if strata:
        h2["by_stratum"] = {s: precision([l for p, l in lab.items() if strata.get(p) == s]) for s in STRATA}
    out["R6B-H2"] = h2
    both = [p for p in lab if (pf5.get(p) or {}).get("dup", 0) >= DUP_THRESHOLD]
    same = [p for p in both if pf5[p]["b"] == b20[p]]
    out["R6B-H4"] = {"held_at_k5_and_k20": precision([lab[p] for p in both]), "same_b_at_k5": precision([lab[p] for p in same]),
                     "note": "labels are for (A, B at k=20); for PRs whose k=5 top candidate differs the label judges the hold of A, not B5"}
    out["threshold_sweep"] = {f"{t:.1f}": precision([lab[p] for p in lab if (pf20.get(p) or {}).get("dup", 0) >= t]) for t in SWEEP}
    out["threshold_sweep_note"] = ("sample drawn from duplicate_of >= 0.7, so 0.6 equals 0.7 here (PRs in [0.6, 0.7) are not in the sample); "
                                   "reporting only, the threshold is not changed (R6B.2-B3)")
    out["other_models"] = {}
    for m in MODELS:
        if m == PRIMARY or m not in dup:
            continue
        h = [p for p in lab if (dup[m][K_MAX].get(p) or {}).get("dup", 0) >= DUP_THRESHOLD]
        out["other_models"][m] = {"k20_held_in_sample": precision([lab[p] for p in h]),
                                  "same_b": sum(1 for p in h if dup[m][K_MAX][p]["b"] == b20[p]),
                                  "note": "on the sample PRs this model also holds at k=20 (R6B.4)"}
    return out


def cost_per_pr(model: str, cand_rows: list[dict], preds: dict[int, dict], prices: dict) -> dict:
    """1 gate call (mean list-price cost of this model's run-5 v3 rows) + k pair calls (each PR's own pair costs; missing → mean)."""
    main = []
    for sp in ("val", "test"):
        f = RUN5_DIR / f"predictions_{model}_{sp}_all.jsonl"
        if f.exists():
            for l in f.open():
                r = json.loads(l)
                if r.get("qset") == "v3" and r["decision"].get("error") is None:
                    main.append(list_cost(model, r["decision"], prices) or 0.0)
    main_mean = sum(main) / len(main) if main else None
    pc = [e["cost"] for e in preds.values() if e["cost"] is not None]
    pair_mean = sum(pc) / len(pc) if pc else None
    test = [cr for cr in cand_rows if cr["split"] == "test"]
    out = {"gate_call_usd": main_mean, "pair_call_usd_mean": pair_mean, "k": {}}
    for k in KS:
        tot = []
        for cr in test:
            s = 0.0
            for p in pair_ids(cr, k):
                e = preds.get(p)
                s += e["cost"] if e and e["cost"] is not None else (pair_mean or 0.0)
            tot.append(s)
        pairs = sum(tot) / len(tot) if tot else None
        out["k"][str(k)] = {"calls_per_pr": 1 + k, "usd_per_pr": None if pairs is None or main_mean is None else main_mean + pairs,
                            "pair_usd_per_pr": pairs}
    return out


def run(run_dir: Path | None = None) -> dict:
    import numpy as np
    run_dir = Path(run_dir or RUN_DIR)
    ds = {r["pr_number"]: r for r in _rows(DATASET)}
    base_bad = float(np.mean([r["labels"]["bad_outcome"] for r in ds.values() if r["split"] == "test"]))
    rep = {"computed_at": _now(), "base_rate_bad_outcome_test": base_bad, "hypotheses": {}, "notes": []}
    cpath = cand_path(K_MAX, run_dir)
    if not cpath.exists():
        rep["status"] = f"retrieval pending: {cpath} not found"
        _emit(run_dir, rep)
        return rep
    check_prefix(run_dir)
    cands = _rows(cpath)
    rep["a2_status"] = _a2_status(cands)
    rep["recall"] = recall_table(cands, ds)
    srcs = (("run4", RUN4_DIR), ("run5", RUN5_DIR), ("run6", run_dir))
    prices = load_prices()
    preds = {m: load_pair_preds(m, srcs, prices) for m in MODELS}
    dup = {m: {k: dup_scores(cands, preds[m], k, "test") for k in KS} for m in MODELS}

    # ---- R6B-H1 verdict level per k (run4._verdict_level; verdict sources as run 5)
    from . import run4, run5
    vl = {}
    v3_only = {m: run5._v3_only(RUN5_DIR, m) for m in MODELS if m not in RUN3_VERDICTS}
    for m in MODELS:
        vl[m] = {}
        vpath = RUN3_DIR / f"predictions_{m}_test_all.jsonl" if m in RUN3_VERDICTS else RUN5_DIR / f"predictions_{m}_test_all.jsonl"
        for k in KS:
            d = {p: e["dup"] for p, e in dup[m][k].items()}
            srcc = Counter()
            for cr in cands:
                if cr["split"] == "test":
                    for p in pair_ids(cr, k):
                        srcc[preds[m][p]["src"] if p in preds[m] else "missing"] += 1
            e = {"pair_sources_test": dict(srcc)}
            if not vpath.exists():
                e["status"] = f"verdicts pending ({vpath} not found)"
            else:
                e["status"] = "ok" if not srcc.get("missing") else f"pair predictions pending ({srcc['missing']} test pairs missing)"
                e.update(run4._verdict_level(m, vpath if m in RUN3_VERDICTS else v3_only[m], d, ds, base_bad))
            vl[m][str(k)] = e
    rep["verdict_level"] = vl
    H = rep["hypotheses"]
    h3_any = {}
    for m in MODELS:
        for k in KS:
            e = vl[m][str(k)]
            if "with_dedup" not in e:
                H[f"R4-H3[{m},k={k}]"] = {"status": e["status"], "pass": None, "bar": "pass bad-rate <= 0.5 x base"}
                continue
            ok = e["status"] == "ok"
            pb = e["with_dedup"]["pass_bad_rate"]
            dd = e["with_dedup"]["closed_nonpass_share"] - e["without_dedup"]["closed_nonpass_share"]
            H[f"R4-H3[{m},k={k}]"] = {"pass_bad_rate": pb, "base": base_bad, "dedup_holds": e["with_dedup"]["dedup_holds"], "status": e["status"],
                                     "pass": (ok and pb <= 0.5 * base_bad) if ok else None, "bar": "pass bad-rate <= 0.5 x base"}
            H[f"R4-H4[{m},k={k}]"] = {"delta_pp": 100 * dd, "status": e["status"], "pass": (ok and dd >= 0.15) if ok else None, "bar": ">= +15 pp"}
            h3_any[(m, k)] = H[f"R4-H3[{m},k={k}]"]["pass"]
    r20 = rep["recall"]["test"]["recall_at_20"]
    h1_k20 = [h3_any.get((m, K_MAX)) for m in H1_MODELS]
    h1_all = [h3_any.get((m, k)) for m in H1_MODELS for k in KS]
    evaluable = all(v is not None for v in h1_k20)
    H["R6B-H1"] = {"recall_at_20_test": r20, "recall_pass": r20 is not None and r20 >= 0.80,
                   "r4h3_k20_pass": {m: h3_any.get((m, K_MAX)) for m in H1_MODELS},
                   "pool_not_the_lever": (not any(h1_all)) if all(v is not None for v in h1_all) else None,
                   "pass": (r20 is not None and r20 >= 0.80 and any(h1_k20)) if evaluable else None,
                   "bar": "recall@20 >= 0.80 and R4-H3 at k=20 for >= 1 of jev, pplx-flash"}

    # ---- R6B-H3 cost of k
    rep["cost"] = {m: cost_per_pr(m, cands, preds[m], prices) for m in MODELS}
    c20 = rep["cost"][PRIMARY]["k"][str(K_MAX)]["usd_per_pr"]
    H["R6B-H3"] = {"pplx_flash_usd_per_pr_k20": c20, "calls_per_pr": {str(k): 1 + k for k in KS},
                   "pass": None if c20 is None else c20 <= H3_COST_BAR,
                   "consequence_if_fail": "k = 10 is the product default regardless of H1",
                   "bar": "pplx-flash k=20 <= $0.002 per PR at list price"}

    # ---- labels: R6B-H2, R6B-H4, sweep
    sp, lp = DATA_DIR / "hold_sample.jsonl", DATA_DIR / "hold_labels.jsonl"
    if not sp.exists():
        rep["labels_status"] = "sample pending"
    else:
        sample = _rows(sp)
        lab = read_labels(lp)
        have = [r for r in sample if (r["pr_number"], r["a_number"], r["b_number"]) in lab]
        if len(have) < len(sample):
            rep["labels_status"] = f"labels incomplete ({len(have)}/{len(sample)}); precision not computed (R6B.2-B2: all labels first)"
        else:
            labels4 = {r["pr_number"]: r for r in _rows(LABELS)}
            strata = {r["pr_number"]: stratum(r["pr_number"], ds, labels4) for r in sample}
            rep["labels_status"] = "ok"
            rep["labels_written"] = {"first": min(lab[(r["pr_number"], r["a_number"], r["b_number"])]["labelled_at"] for r in sample),
                                     "last": max(lab[(r["pr_number"], r["a_number"], r["b_number"])]["labelled_at"] for r in sample)}
            rep["hold_precision"] = label_metrics(sample, lab, dup, strata)
    hp = rep.get("hold_precision")
    if hp:
        h2 = hp["R6B-H2"]
        H["R6B-H2"] = {"union": h2["union"], "union_ci95": h2["union_ci95"], "duplicate": h2["duplicate_share"], "n": h2["n"],
                       "demote_to_annotate": h2["demote_to_annotate"], "pass": h2["pass"], "bar": h2["bar"]}
        h4 = hp["R6B-H4"]["held_at_k5_and_k20"]
        H["R6B-H4"] = {"n_held_both": h4["n"], "union": h4["union"], "duplicate": h4["duplicate_share"], "pass": None, "bar": "reported, not a bar"}
    else:
        H["R6B-H2"] = {"status": rep["labels_status"], "pass": None, "bar": "union >= 0.60 and duplicate >= 0.30 (pplx-flash, k=20)"}
        H["R6B-H4"] = {"status": rep["labels_status"], "pass": None, "bar": "reported, not a bar"}
    rep["notes"] += [
        "Pair predictions: union by pair id, run 4 first, then run 5, then run 6 (error-free same_change rows).",
        "Verdicts: jev/haiku replay their run-3 v2 predictions, pplx-flash its run-5 v3 answers (run-3 rule), as R5-H3.",
        "Precision is the unweighted share on the sample (proportional stratified allocation ⇒ self-weighting up to rounding).",
        f"A2: {rep['a2_status']}"]
    _emit(run_dir, rep)
    return rep


def _emit(run_dir: Path, rep: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "b_metrics.json").write_text(json.dumps(rep, indent=2, default=float))
    t = _tables(rep)
    (run_dir / "b_tables.md").write_text(t)
    print(t)


def _f(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and x != x) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _prow(name: str, p: dict) -> str:
    ci = p.get("union_ci95")
    return (f"| {name} | {p['n']} | {p['duplicate']} | {p['pointer']} | {p['unrelated']} | {_f(p['union'])} "
            f"[{_f(ci[0]) if ci else '—'}, {_f(ci[1]) if ci else '—'}] | {_f(p['duplicate_share'])} |")


def _tables(rep: dict) -> str:
    L = ["# Run 6 part B — candidate pool k and hold precision", ""]
    if rep.get("status"):
        return "\n".join(L + [f"**{rep['status']}**"]) + "\n"
    L += [f"A2: {rep['a2_status']}", "", "## R6B-H1 retrieval recall@k (gold superseding PR)", "",
          "| split | gold | @5 | @10 | @20 |", "|---|---|---|---|---|"]
    for sp in ("test", "val"):
        r = rep["recall"][sp]
        L.append(f"| {sp} | {r['n_gold']} | {_f(r['recall_at_5'])} | {_f(r['recall_at_10'])} | {_f(r['recall_at_20'])} |")
    L += ["", f"## Verdict level per k (test; base bad-outcome rate {_f(rep['base_rate_bad_outcome_test'])}, R4-H3 bar ≤ {_f(0.5 * rep['base_rate_bad_outcome_test'])})", "",
          "| model | k | status | pass % | pass bad-rate | closed not passed | Δ vs no dedup | dedup holds |", "|---|---|---|---|---|---|---|---|"]
    for m, byk in rep["verdict_level"].items():
        for k, e in byk.items():
            if "with_dedup" not in e:
                L.append(f"| {m} | {k} | {e['status']} | — | — | — | — | — |")
                continue
            w, wo = e["with_dedup"], e["without_dedup"]
            L.append(f"| {m} | {k} | {e['status']} | {_f(100 * w['pass_frac'], 1)} | {_f(w['pass_bad_rate'])} | {_f(100 * w['closed_nonpass_share'], 1)}% | "
                     f"{_f(100 * (w['closed_nonpass_share'] - wo['closed_nonpass_share']), 1)} pp | {w['dedup_holds']} |")
    L += ["", "## R6B-H3 cost per PR (list price; calls = 1 + k)", "", "| model | gate call $ | pair call $ | k=5 | k=10 | k=20 |", "|---|---|---|---|---|---|"]
    for m, c in rep["cost"].items():
        L.append(f"| {m} | {_f(c['gate_call_usd'], 6)} | {_f(c['pair_call_usd_mean'], 6)} | " + " | ".join(_f(c["k"][str(k)]["usd_per_pr"], 5) for k in KS) + " |")
    L += ["", "## Hold precision (human labels, blind)", ""]
    hp = rep.get("hold_precision")
    if not hp:
        L.append(f"**{rep.get('labels_status')}**")
    else:
        hdr = ["| subset | n | duplicate | pointer | unrelated | dup ∪ pointer [95% CI] | duplicate share |", "|---|---|---|---|---|---|---|"]
        L += ["### R6B-H2 pplx-flash, k=20", ""] + hdr + [_prow("all", hp["R6B-H2"])]
        L += [_prow(f"stratum: {s}", p) for s, p in (hp["R6B-H2"].get("by_stratum") or {}).items()]
        L += ["", f"sample B still the top candidate: {hp['R6B-H2']['sample_b_still_top']}/{hp['n_sample']}; still held: {hp['R6B-H2']['sample_still_held']}/{hp['n_sample']}",
              "", "### R6B-H4 k=5 on the same labels", ""] + hdr
        L += [_prow("held at k=5 and k=20", hp["R6B-H4"]["held_at_k5_and_k20"]), _prow("… same B at k=5", hp["R6B-H4"]["same_b_at_k5"])]
        L += ["", "### Threshold sweep (reporting only)", ""] + hdr + [_prow(f"duplicate_of ≥ {t}", p) for t, p in hp["threshold_sweep"].items()]
        L += ["", hp["threshold_sweep_note"], "", "### Other models, on sample PRs they also hold at k=20", ""] + hdr
        L += [_prow(m, e["k20_held_in_sample"]) + f" same B: {e['same_b']}" for m, e in hp["other_models"].items()]
    L += ["", "## Pre-registered criteria", "", "| hypothesis | result | bar | pass |", "|---|---|---|---|"]
    for h, r in rep["hypotheses"].items():
        shown = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k not in ("pass", "bar")}
        mark = "pending" if r["pass"] is None else ("✅" if r["pass"] else "❌")
        L.append(f"| {h} | {json.dumps(shown, default=str)} | {r['bar']} | {mark} |")
    L += ["", "## Notes", ""] + [f"- {n}" for n in rep["notes"]]
    return "\n".join(L) + "\n"


# ================================================================= main
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run6b")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("retrieve", help="retrieval v2 top-k → results/run6/candidates_k<k>.jsonl (k=5/10 are prefixes of k=20)")
    s.add_argument("--k", type=int, choices=KS, default=K_MAX); s.set_defaults(fn=cmd_retrieve)
    s = sub.add_parser("pairs", help="pairs.jsonl + pairs_todo_<model>.jsonl (not answered in runs 4–5)")
    s.add_argument("--k", type=int, choices=KS, default=K_MAX); s.set_defaults(fn=cmd_pairs)
    s = sub.add_parser("sample", help="draw the 100-PR blind hold sample (needs all k=20 test pair predictions)")
    s.add_argument("--n", type=int, default=SAMPLE_N); s.add_argument("--seed", type=int, default=SAMPLE_SEED)
    s.add_argument("--max-missing", type=int, default=0, help="tolerated missing k=20 test pair predictions per model (default 0)")
    s.set_defaults(fn=cmd_sample)
    s = sub.add_parser("label", help="local blind labelling app on 127.0.0.1")
    s.add_argument("--port", type=int, default=8765); s.add_argument("--export", action="store_true", help="print label counts and exit")
    s.set_defaults(fn=cmd_label)
    s = sub.add_parser("report"); s.add_argument("run_dir", nargs="?", default=str(RUN_DIR)); s.set_defaults(fn=lambda a: run(Path(a.run_dir)))
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
