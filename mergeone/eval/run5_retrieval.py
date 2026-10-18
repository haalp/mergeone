"""Run 5, component A — retrieval v2 (PROTOCOL R5.1-A, R5-H1, R5-H2).

  python -m mergeone.eval.run5_retrieval fit               # A1 weight grid on val gold pairs → results/run5/a1_weight_grid.json
  python -m mergeone.eval.run5_retrieval a1 --split val    # A1 on val PRs only → results/run5/a1_val_metrics.json
  python -m mergeone.eval.run5_retrieval a1 --split all    # A1 top-20 for every PR → results/run5/a1_top20.jsonl, a1_targets.jsonl
  python -m mergeone.data.patches --targets results/run5/a1_targets.jsonl   # A2 fetch (network)
  python -m mergeone.eval.run5_retrieval retrieve          # A1 (timed) + A2 → candidates.jsonl, retrieval_timing.json, retrieval_metrics.json
  python -m mergeone.eval.run5_retrieval go-fixture        # gate/internal/memory/testdata/v2_fixture.json
  python -m mergeone.eval.run5_retrieval go-compare FILE   # agreement of the Go A1 dump with Python

candidates.jsonl keeps run 4's row format (`candidates` = the A1+A2 top-5, `candidate_bodies` for them) so the run-4
pairs builder can be reused, and adds `candidates_top20` (A1 top-20 with a1_rank, a2_rank, ident_jaccard, patch_fetched).
"""
from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..data import patches as P
from ..memory import retrieval_v2 as R

DATASET = Path("data/run3/dataset.jsonl")
MEM_DIR = Path("data/run4/memory")
LABELS = Path("data/run4/labels.jsonl")
RUN_DIR = Path("results/run5")
FIXTURE = Path("gate/internal/memory/testdata/v2_fixture.json")


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).open()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gold(split: str, ds: dict) -> list[dict]:
    return [l for l in _rows(LABELS) if l["label"] == "superseded_by" and ds.get(l["pr_number"], {}).get("split") == split]


def _recall(gold: list[dict], lists: dict[int, list[int]], k: int) -> float | None:
    if not gold:
        return None
    return sum(1 for l in gold if l["superseded_by"] in lists.get(l["pr_number"], [])[:k]) / len(gold)


def _pct(xs: list[float], q: float) -> float | None:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None


def _slim(c: dict) -> dict:
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in c.items()}


# ----------------------------------------------------------------- fit
def cmd_fit(a):
    ds = _rows(DATASET)
    val_rows = [r for r in ds if r["split"] == "val"]  # test rows are never passed to the fit
    mems = R.load_all(MEM_DIR)
    res = R.fit_weights_on_val(mems, val_rows, _rows(LABELS))
    res["fitted_at"] = _now()
    res["window_days"] = R.WINDOW_DAYS
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "a1_weight_grid.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k not in ("grid", "chosen_ranks")}, indent=2))
    for r in res["chosen_ranks"]:
        print(r)


# ----------------------------------------------------------------- A1
def cmd_a1(a):
    ds = _rows(DATASET)
    splits = {"val": ("val",), "test": ("test",), "all": ("val", "test")}[a.split]
    rows = [r for r in ds if r["split"] in splits]
    mems = R.load_all(MEM_DIR)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    top, lat = {}, []
    for r in rows:
        m = mems[r["repo"]]
        s = R.parse_state(r["state"])
        t0 = time.perf_counter()
        q = R.Query.make(r["repo"], r["number"], s["title"], s["body"], s["paths"], r.get("author"), r["created_at"])
        top[r["pr_number"]] = m.a1(q)
        lat.append((time.perf_counter() - t0) * 1000)
    dsd = {r["pr_number"]: r for r in ds}
    lists = {pid: [c["number"] for c in cs] for pid, cs in top.items()}
    met = {"weights": dict(zip(R.FEATURES, R.WEIGHTS)), "computed_at": _now(), "n_prs": len(rows),
           "python_a1_p50_ms": _pct(lat, 0.5), "python_a1_p99_ms": _pct(lat, 0.99)}
    for sp in splits:
        g = _gold(sp, dsd)
        met[sp] = {"n_gold": len(g), "a1_recall_at_5": _recall(g, lists, 5), "a1_recall_at_20": _recall(g, lists, 20)}
    if a.split == "val":
        (RUN_DIR / "a1_val_metrics.json").write_text(json.dumps(met, indent=2))
    else:
        with (RUN_DIR / "a1_top20.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps({"pr_number": r["pr_number"], "repo": r["repo"], "number": r["number"], "split": r["split"],
                                    "candidates": [_slim(c) for c in top[r["pr_number"]]]}) + "\n")
        seen = {}
        for r in rows:
            for c in top[r["pr_number"]]:
                key = (r["repo"], c["number"])
                if key not in seen:
                    seen[key] = {"repo": r["repo"], "number": c["number"], "changed_files": mems[r["repo"]].prs[c["number"]].changed_files}
        with (RUN_DIR / "a1_targets.jsonl").open("w") as f:
            for t in seen.values():
                f.write(json.dumps(t) + "\n")
        met["n_unique_targets"] = len(seen)
    print(json.dumps(met, indent=2))


# ----------------------------------------------------------------- A1 + A2
def _cand_ident(repo: str, number: int) -> tuple[frozenset | None, dict | None]:
    rec = P.load_patch(repo, number)
    if not rec or rec.get("status") != "ok":
        return None, rec
    return R.ident_of_patch(rec["files"]), rec


def cmd_retrieve(a):
    ds = _rows(DATASET)
    dsd = {r["pr_number"]: r for r in ds}
    mems = R.load_all(MEM_DIR)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    lat, calls_per_pr, fetches_per_pr = [], [], []
    unfetch = Counter()
    a1_lists, a2_lists = {}, {}
    ident_cache: dict = {}
    sym = None
    if not a.no_symbols:
        from ..memory import symbols as S
        sym = S if S.available() else None
    sym_lists = {}
    tmp_out = RUN_DIR / "candidates.jsonl.tmp"
    with tmp_out.open("w") as f:
        for r in ds:
            m = mems[r["repo"]]
            s = R.parse_state(r["state"])
            t0 = time.perf_counter()
            q = R.Query.make(r["repo"], r["number"], s["title"], s["body"], s["paths"], r.get("author"), r["created_at"])
            top = m.a1(q)
            lat.append((time.perf_counter() - t0) * 1000)
            qi = R.ident_of_diff(s["diff"])
            ci, calls, fetched = {}, 0, 0
            for c in top:
                key = (r["repo"], c["number"])
                if key not in ident_cache:
                    ident_cache[key] = _cand_ident(*key)
                ids, rec = ident_cache[key]
                ci[c["number"]] = ids
                calls += (rec or {}).get("calls", 0)
                fetched += 1
                if ids is None:
                    unfetch[(rec or {}).get("status", "missing")] += 1
            calls_per_pr.append(calls)
            fetches_per_pr.append(fetched)
            ranked = R.rerank_a2(top, qi, ci)
            a1_lists[r["pr_number"]] = [c["number"] for c in top]
            a2_lists[r["pr_number"]] = [c["number"] for c in ranked]
            if sym is not None:
                sym_lists[r["pr_number"]] = sym.rerank_numbers(top, s["diff"], r["repo"])
            top5 = ranked[:R.TOP_N]
            cands = []
            for c in top5:
                x = {k: c[k] for k in ("number", "score", "jaccard", "bm25", "same_author", "state", "title", "created_at", "merged_at", "author",
                                       "issue_ref", "dir_jaccard", "a1_rank", "a2_rank", "a2_score", "ident_jaccard", "patch_fetched")}
                cands.append(_slim(x))
            bodies = {}
            for c in top5:
                mp = m.prs[c["number"]]
                bodies[str(c["number"])] = {"body": mp.body[:1500], "files": sorted(mp.files)[:60]}
            f.write(json.dumps({"pr_number": r["pr_number"], "repo": r["repo"], "number": r["number"], "split": r["split"],
                                "candidates": cands, "candidate_bodies": bodies, "query_ident_n": len(qi),
                                "candidates_top20": [_slim({k: c[k] for k in ("number", "score", "a1_rank", "a2_rank", "a2_score", "ident_jaccard", "patch_fetched",
                                                                               "issue_ref", "dir_jaccard", "jaccard", "bm25", "same_author")}) for c in ranked]}) + "\n")
    n_cand = sum(fetches_per_pr)
    uniq = {k for k in ident_cache}
    uniq_fetched = sum(1 for k, (ids, _) in ident_cache.items() if ids is not None)
    uniq_status = Counter((rec or {}).get("status", "not_attempted") for _, rec in ident_cache.values())
    a2_ran = uniq_fetched > 0
    # candidates.jsonl is reserved for the A1+A2 top-5; without any fetched patch the A1-only list goes elsewhere
    out = RUN_DIR / ("candidates.jsonl" if a2_ran else "candidates_a1_only.jsonl")
    tmp_out.replace(out)
    timing = {"computed_at": _now(), "n": len(lat), "python_a1_p50_ms": _pct(lat, 0.5), "python_a1_p99_ms": _pct(lat, 0.99),
              "python_a1_max_ms": max(lat), "measured": "Query.make + Memory.a1 (tokenise, window, score, top-20); memory pre-loaded",
              "a2_patch_fetches_per_pr": {"mean": n_cand / len(ds), "max": max(fetches_per_pr), "bar": "<= 20"},
              "a2_api_calls_per_pr_uncached": {"mean": sum(calls_per_pr) / len(ds), "max": max(calls_per_pr), "note": "pulls/{n}/files pages (<=3 per candidate) if no candidate were cached"},
              "a2_unique_candidates": len(uniq), "a2_unique_fetched_ok": uniq_fetched, "a2_unique_by_status": dict(uniq_status)}
    (RUN_DIR / "retrieval_timing.json").write_text(json.dumps(timing, indent=2))
    met = {"computed_at": _now(), "weights": dict(zip(R.FEATURES, R.WEIGHTS)), "window_days": R.WINDOW_DAYS,
           "note": "val numbers are tuned (A1 weights were fit on the val gold pairs); test is the R5-H1 number",
           "a2_status": "ran" if a2_ran else "not run: no candidate patch was fetched (GitHub REST pulls/{n}/files not reachable from this session)",
           "candidates_file": str(out), "a2_unique_candidates": len(uniq), "a2_unique_by_status": dict(uniq_status),
           "unfetchable_candidate_slots": dict(unfetch), "unfetchable_candidate_slots_total": sum(unfetch.values()), "candidate_slots": n_cand}
    for sp in ("val", "test"):
        g = _gold(sp, dsd)
        met[sp] = {"n_gold": len(g),
                   "a1": {"recall_at_5": _recall(g, a1_lists, 5), "recall_at_20": _recall(g, a1_lists, 20)},
                   "a1_a2": {"recall_at_5": _recall(g, a2_lists, 5), "recall_at_20": _recall(g, a2_lists, 20)} if a2_ran else None,
                   "gold_ranks": [{"repo": l["repo"], "number": l["number"], "gold": l["superseded_by"],
                                   "a1_rank": (a1_lists.get(l["pr_number"], []).index(l["superseded_by"]) + 1) if l["superseded_by"] in a1_lists.get(l["pr_number"], []) else None,
                                   "a2_rank": None if not a2_ran else (a2_lists.get(l["pr_number"], []).index(l["superseded_by"]) + 1) if l["superseded_by"] in a2_lists.get(l["pr_number"], []) else None}
                                  for l in g] if sp == "val" else None}
        if sym is not None and a2_ran:
            met[sp]["exploratory_tree_sitter"] = {"recall_at_5": _recall(g, sym_lists, 5), "recall_at_20": _recall(g, sym_lists, 20)}
    r5 = met["test"]["a1_a2"]["recall_at_5"] if a2_ran else None
    met["R5-H1"] = {"recall_at_5_a1_a2_test": r5, "n_gold": met["test"]["n_gold"], "bar": ">= 0.70 (below 0.60: no improvement)",
                    "pass": (r5 >= 0.70) if r5 is not None else None, "no_improvement": (r5 < 0.60) if r5 is not None else None,
                    "evaluable": a2_ran, "a1_only_recall_at_5_test": met["test"]["a1"]["recall_at_5"]}
    met["exploratory_tree_sitter"] = sym.describe() if sym is not None else {"status": "not available"}
    if sym is not None and not a2_ran:
        met["exploratory_tree_sitter"]["status"] = "parsers available; not run (needs the A2 candidate patches)"
    (RUN_DIR / "retrieval_metrics.json").write_text(json.dumps(met, indent=2))
    print(json.dumps({k: v for k, v in met.items()}, indent=2, default=str))
    print(json.dumps(timing, indent=2))


# ----------------------------------------------------------------- Go agreement
def cmd_go_fixture(a):
    """~200 (query, candidate) pairs with Python A1 feature values and scores, for the Go agreement test.

    Queries are synthetic or drawn from val rows; memory entries are embedded so the Go test needs no data files."""
    rng = random.Random(5)
    ds = [r for r in _rows(DATASET) if r["split"] == "val"]
    mems = R.load_all(MEM_DIR)
    repos = sorted({r["repo"] for r in ds})
    cases = []
    for repo in rng.sample(repos, 4):
        rows = [r for r in ds if r["repo"] == repo]
        m = mems[repo]
        for r in rng.sample(rows, min(3, len(rows))):
            q = R.Query.from_row(r)
            top = m.a1(q, k=10)
            if not top:
                continue
            s = R.parse_state(r["state"])
            # embed a small memory: the top-10 plus 10 random in-window PRs, so BM25 df/avg_len are computed on the fixture memory
            win = [p.number for p in m.window(q.created_at)]
            nums = sorted({c["number"] for c in top} | set(rng.sample(win, min(10, len(win)))))
            raw = {}
            for line in (MEM_DIR / (repo.replace("/", "__") + ".jsonl")).open():
                x = json.loads(line)
                if x["number"] in nums:
                    raw[x["number"]] = {k: x.get(k) for k in ("number", "title", "body", "author", "state", "created_at", "merged_at", "closed_at", "changed_files")}
                    raw[x["number"]]["body"] = (x.get("body") or "")[:1500]  # keep the fixture small; expected values are computed on this row
                    raw[x["number"]]["files"] = [{"path": f["path"]} for f in x.get("files") or []]
            sub = R.Memory(repo)
            for n in nums:
                sub.add(raw[n])
            sub.finalize()
            res = sub.a1(q, k=len(nums))
            cases.append({"repo": repo, "query": {"number": r["number"], "state": r["state"], "author": r.get("author"), "created_at": r["created_at"],
                                                  "title": s["title"], "body": s["body"], "paths": s["paths"]},
                          "memory": [raw[n] for n in nums],
                          "expect": [{"number": c["number"], "score": c["score"], "issue": float(c["issue_ref"]), "dir": c["dir_jaccard"], "file": c["jaccard"],
                                      "text": c["bm25"], "author": float(c["same_author"]), "rank": c["a1_rank"]} for c in res]})
    # synthetic edge cases for the reference parser and author normalisation
    syn_mem = [
        {"number": 10, "title": "Fix GH-7 crash in parser", "body": "See https://github.com/Acme/Widget/issues/42 and acme/other#9", "author": "Bot-A", "state": "MERGED",
         "created_at": "2026-05-01T00:00:00Z", "merged_at": "2026-05-02T00:00:00Z", "closed_at": "2026-05-02T00:00:00Z", "changed_files": 2,
         "files": [{"path": "src/parser/a.py"}, {"path": "README.md"}]},
        {"number": 11, "title": "Refs #42 (#11 self) – naïve İstanbul KELVIN K", "body": "color #123abc, x#5, path/to#6", "author": "someone", "state": "OPEN",
         "created_at": "2026-02-01T00:00:00Z", "merged_at": None, "closed_at": None, "changed_files": 1, "files": [{"path": "src/parser/deep/b.py"}]},
        {"number": 12, "title": "old", "body": "", "author": "x", "state": "MERGED", "created_at": "2025-01-01T00:00:00Z", "merged_at": "2026-05-03T00:00:00Z",
         "closed_at": "2026-05-03T00:00:00Z", "changed_files": 1, "files": [{"path": "docs/x.md"}]},
        {"number": 13, "title": "far away", "body": "#42", "author": "bot-a", "state": "MERGED", "created_at": "2025-01-01T00:00:00Z",
         "merged_at": "2025-01-02T00:00:00Z", "closed_at": "2025-01-02T00:00:00Z", "changed_files": 0, "files": []},
    ]
    sq = {"number": 99, "author": "bot-a[bot]", "created_at": "2026-05-05T00:00:00Z", "title": "parser crash on GH-7 again",
          "body": "Follow-up to acme/widget#42; also #99 (self).", "paths": ["src/parser/a.py", "src/parser/c.py"], "state": ""}
    sub = R.Memory("acme/widget")
    for x in syn_mem:
        sub.add(x)
    sub.finalize()
    q = R.Query.make("acme/widget", 99, sq["title"], sq["body"], sq["paths"], sq["author"], sq["created_at"])
    res = sub.a1(q, k=10)
    cases.append({"repo": "acme/widget", "query": sq, "memory": syn_mem,
                  "expect": [{"number": c["number"], "score": c["score"], "issue": float(c["issue_ref"]), "dir": c["dir_jaccard"], "file": c["jaccard"],
                              "text": c["bm25"], "author": float(c["same_author"]), "rank": c["a1_rank"]} for c in res]})
    refs_cases = [{"text": t, "repo": "acme/widget", "self": 11, "refs": sorted([f"{r}#{n}" for r, n in R.refs(t, "acme/widget", 11)])}
                  for t in (syn_mem[0]["title"] + "\n" + syn_mem[0]["body"], syn_mem[1]["title"] + "\n" + syn_mem[1]["body"], sq["body"],
                            "see #1, #2;#3 (#4) [#5] GH-6 gh-7 x/y#8 https://github.com/a/b/pull/9/files #1234567890 a#10 _#11")]
    fx = {"generated_at": _now(), "weights": dict(zip(R.FEATURES, R.WEIGHTS)), "window_days": R.WINDOW_DAYS,
          "n_pairs": sum(len(c["expect"]) for c in cases), "cases": cases, "refs": refs_cases,
          "state_parse": [{"state": c["query"]["state"], **{k: v for k, v in R.parse_state(c["query"]["state"]).items() if k != "diff"}} for c in cases[:6] if c["query"]["state"]]}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fx, indent=0))
    print({"cases": len(cases), "pairs": fx["n_pairs"]})


def cmd_go_compare(a):
    """Compare a Go A1 dump (JSONL: pr_number, candidates[{number, score}]) with Python on every PR."""
    go = {r["pr_number"]: r["candidates"] or [] for r in _rows(Path(a.file))}
    ds = _rows(DATASET)
    mems = R.load_all(MEM_DIR)
    max_diff, same_lists, n, n_pairs = 0.0, 0, 0, 0
    max_feat, flag_mismatch = {"dir_jaccard": 0.0, "jaccard": 0.0, "bm25": 0.0}, Counter()
    bad = []
    for r in ds:
        if r["pr_number"] not in go:
            continue
        n += 1
        py = mems[r["repo"]].a1(R.Query.from_row(r))
        g = go[r["pr_number"]]
        if [c["number"] for c in py] == [c["number"] for c in g]:
            same_lists += 1
        elif len(bad) < 5:
            bad.append({"repo": r["repo"], "number": r["number"], "py": [(c["number"], c["score"]) for c in py][:5], "go": [(c["number"], c["score"]) for c in g][:5]})
        gs = {c["number"]: c for c in g}
        for c in py:
            if c["number"] in gs:
                n_pairs += 1
                x = gs[c["number"]]
                max_diff = max(max_diff, abs(x["score"] - c["score"]))
                for k in ("dir_jaccard", "jaccard", "bm25"):
                    max_feat[k] = max(max_feat[k], abs(x[k] - c[k]))
                for k in ("issue_ref", "same_author"):
                    flag_mismatch[k] += int(bool(x[k]) != bool(c[k]))
    res = {"n_prs": n, "identical_top20_lists": same_lists, "n_scored_pairs_compared": n_pairs, "max_abs_score_diff": max_diff,
           "max_abs_feature_diff": max_feat, "flag_mismatches": dict(flag_mismatch), "mismatches": bad, "computed_at": _now()}
    print(json.dumps(res, indent=2))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run5_retrieval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fit")
    s = sub.add_parser("a1"); s.add_argument("--split", choices=["val", "test", "all"], default="val")
    s = sub.add_parser("retrieve"); s.add_argument("--no-symbols", action="store_true")
    sub.add_parser("go-fixture")
    s = sub.add_parser("go-compare"); s.add_argument("file"); s.add_argument("--out")
    a = ap.parse_args(argv)
    {"fit": cmd_fit, "a1": cmd_a1, "retrieve": cmd_retrieve, "go-fixture": cmd_go_fixture, "go-compare": cmd_go_compare}[a.cmd](a)


if __name__ == "__main__":
    main()
