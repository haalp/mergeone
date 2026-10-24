"""Run 4 pipeline (PROTOCOL run-4 addendum).

  mergeone run4-labels     comments + memory → data/run4/labels.jsonl        (R4.2 human labels)
  mergeone run4-retrieve   dataset + memory  → results/run4/candidates.jsonl  (R4.3-1, timed for R4-H5)
  mergeone run4-pairs      candidates + labels → results/run4/pairs.jsonl     (rows in dataset format, qset "pair")
  mergeone predict --model jev --qset pair --dataset results/run4/pairs.jsonl --split test --run run4
  mergeone report results/run4 --run4
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from ..data.supersede_labels import label as supersede_label
from ..memory.retrieval import Memory, load_all, TOP_K
from .metrics import roc_auc, pr_auc, bootstrap_ci

DATASET = Path("data/run3/dataset.jsonl")
MEM_DIR = Path("data/run4/memory")
COMMENTS = Path("data/run4/comments.jsonl")
LABELS = Path("data/run4/labels.jsonl")
RUN_DIR = Path("results/run4")
DUP_THRESHOLD = 0.7  # policy: duplicate_of >= 0.7 → hold, route dedup (R4.3-3)


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).open()]


# ----------------------------------------------------------------- labels
def cmd_labels(a):
    mems = load_all(MEM_DIR)
    out = LABELS
    out.parent.mkdir(parents=True, exist_ok=True)
    n = Counter()
    with out.open("w") as f:
        for r in _rows(COMMENTS):
            lab, num = supersede_label(r["comments"], r["number"])
            in_mem = bool(num and r["repo"] in mems and num in mems[r["repo"]].prs)
            if lab == "superseded_by" and not in_mem:
                lab = "superseded_by_outside_memory"
            n[lab] += 1
            f.write(json.dumps({"repo": r["repo"], "number": r["number"], "pr_number": r["pr_number"], "label": lab, "superseded_by": num,
                                "closed_by": r.get("closed_by"), "n_human_comments": sum(1 for c in r["comments"] if c.get("author") and "[bot]" not in c["author"])}) + "\n")
    print(dict(n))


# ----------------------------------------------------------------- retrieval
def cmd_retrieve(a):
    mems = load_all(MEM_DIR)
    ds = _rows(DATASET)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    out = RUN_DIR / "candidates.jsonl"
    lat = []
    miss = Counter()
    with out.open("w") as f:
        for r in ds:
            m = mems.get(r["repo"])
            if m is None:
                miss[r["repo"]] += 1
                continue
            t0 = time.perf_counter()
            cands = m.candidates(r["number"], r["state"].split("\n", 1)[0].replace("PULL REQUEST TITLE: ", ""), "", [x for x in _paths(r)], r.get("author"), r["created_at"], k=TOP_K)
            lat.append((time.perf_counter() - t0) * 1000)
            f.write(json.dumps({"pr_number": r["pr_number"], "repo": r["repo"], "number": r["number"], "split": r["split"],
                                "candidates": [{k: v for k, v in c.items() if k not in ("body", "files")} for c in cands],
                                "candidate_bodies": {str(c["number"]): {"body": c["body"], "files": c["files"]} for c in cands}}) + "\n")
    lat.sort()
    summary = {"n": len(lat), "p50_ms": lat[len(lat) // 2] if lat else None, "p99_ms": lat[int(len(lat) * 0.99)] if lat else None,
               "memory_sizes": {k: len(v.prs) for k, v in mems.items()}, "missing_memory": dict(miss)}
    (RUN_DIR / "retrieval_timing.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "memory_sizes"}, indent=2))


def _paths(r: dict) -> list[str]:
    # dataset rows carry the rendered state; file paths are in the FILES CHANGED block
    out, on = [], False
    for line in r["state"].split("\n"):
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


# ----------------------------------------------------------------- pairs
def _render_b(c: dict, body: str, files: list[str]) -> str:
    lines = [f"PULL REQUEST TITLE: {c['title']}", f"MERGE MESSAGE BODY:\n{(body or '').strip()[:1200]}", "",
             f"STATE: {c['state'].lower()}" + (f", merged {c['merged_at'][:10]}" if c.get('merged_at') else f", opened {c['created_at'][:10]}"),
             f"AUTHOR: {c.get('author')}", "", "FILES CHANGED:"]
    lines += [f"  {p}" for p in files[:60]]
    if not files:
        lines.append("  (file list unavailable)")
    return "\n".join(lines)


def pair_state(a_state: str, b_state: str) -> str:
    return "=== PULL REQUEST A ===\n" + a_state[:6000] + "\n\n=== PULL REQUEST B ===\n" + b_state[:3000] + "\n\nHow does A relate to B?"


def _mem_meta(m) -> dict:
    return {"number": m.number, "title": m.title, "state": m.state, "created_at": m.created_at.isoformat(), "merged_at": m.merged_at.isoformat() if m.merged_at else None, "author": m.author, "score": None}


def cmd_pairs(a):
    """Build pair rows. Run 5 reuses this with `a.candidates`, `a.out`, `a.top_k` (all optional; defaults = run 4)."""
    cands_path = Path(getattr(a, "candidates", None) or RUN_DIR / "candidates.jsonl")
    out = Path(getattr(a, "out", None) or RUN_DIR / "pairs.jsonl")
    top_k = getattr(a, "top_k", None)
    ds = {r["pr_number"]: r for r in _rows(DATASET)}
    cands = {r["pr_number"]: r for r in _rows(cands_path)}
    labels = {r["pr_number"]: r for r in _rows(LABELS)} if LABELS.exists() else {}
    mems = load_all(MEM_DIR)
    rng = random.Random(4)
    rows, seen = [], set()

    def add(pr, c_num, c_meta, c_body, c_files, kind):
        key = (pr["pr_number"], c_num)
        if key in seen:
            return
        seen.add(key)
        pid = int(hashlib.sha1(f"{pr['repo']}#{pr['number']}~{c_num}".encode()).hexdigest()[:12], 16)
        lab = labels.get(pr["pr_number"], {})
        rows.append({"pr_number": pid, "split": pr["split"], "repo": pr["repo"], "a": pr["number"], "b": c_num, "kind": kind,
                     "labels": {"positive": lab.get("superseded_by") == c_num, "a_label": lab.get("label", "not_marked"), "retrieval_score": c_meta.get("score"),
                                "a_closed_unmerged": pr["labels"]["closed_unmerged"], "a_bad_outcome": pr["labels"]["bad_outcome"], "a_rejected": pr["labels"]["rejected"]},
                     "features": {}, "state": pair_state(pr["state"], _render_b(c_meta, c_body, c_files))})

    for pid, pr in ds.items():
        cr = cands.get(pid)
        if not cr:
            continue
        top = cr["candidates"][:top_k]
        for c in top:
            cb = (cr.get("candidate_bodies") or {}).get(str(c["number"]))
            mm = mems[pr["repo"]].prs.get(c["number"]) if (cb is None or "title" not in c) and pr["repo"] in mems else None
            if mm is not None:  # candidates file without rendered bodies/metadata (run 5): take them from the memory
                c = {**_mem_meta(mm), **c}
                cb = cb or {"body": mm.body, "files": sorted(mm.files)}
            cb = cb or {}
            add(pr, c["number"], c, cb.get("body", ""), cb.get("files", []), "retrieved")
        lab = labels.get(pid)
        if lab and lab["label"] == "superseded_by" and lab["superseded_by"] not in {c["number"] for c in top}:
            m = mems[pr["repo"]].prs.get(lab["superseded_by"])
            if m:
                add(pr, m.number, _mem_meta(m), m.body, sorted(m.files), "gold_not_retrieved")
    # random same-repo negatives, as many as positives
    n_pos = sum(1 for r in rows if r["labels"]["positive"])
    by_repo = defaultdict(list)
    for pid, pr in ds.items():
        by_repo[pr["repo"]].append(pid)
    pos_prs = [r for r in rows if r["labels"]["positive"]]
    for r in pos_prs:
        pr = ds[next(p for p, x in ds.items() if x["repo"] == r["repo"] and x["number"] == r["a"])]
        m = mems[pr["repo"]]
        pool = [n for n in m.prs if n not in (pr["number"], r["b"])]
        if pool:
            n = rng.choice(pool); c = m.prs[n]
            add(pr, n, _mem_meta(c), c.body, sorted(c.files), "random_negative")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print({"pairs": len(rows), "positives": n_pos, "kinds": dict(Counter(r["kind"] for r in rows)), "by_split": dict(Counter(r["split"] for r in rows))})
    return rows


# ----------------------------------------------------------------- report
def run(run_dir: Path) -> None:
    ds = {r["pr_number"]: r for r in _rows(DATASET)}
    labels = {r["pr_number"]: r for r in _rows(LABELS)}
    cands = {r["pr_number"]: r for r in _rows(run_dir / "candidates.jsonl")}
    pairs = {r["pr_number"]: r for r in _rows(run_dir / "pairs.jsonl")}
    rep = {"labels": dict(Counter(l["label"] for l in labels.values())), "hypotheses": {}, "models": {}}

    # R4-H1 retrieval recall@5 on test (and val, reported)
    for split in ("test", "val"):
        gold = [l for l in labels.values() if l["label"] == "superseded_by" and ds.get(l["pr_number"], {}).get("split") == split]
        hit = sum(1 for l in gold if l["superseded_by"] in {c["number"] for c in cands.get(l["pr_number"], {}).get("candidates", [])})
        rep[f"retrieval_{split}"] = {"n_gold": len(gold), "recall_at_5": (hit / len(gold)) if gold else None}
    r5 = rep["retrieval_test"]["recall_at_5"]
    rep["hypotheses"]["R4-H1"] = {"recall_at_5": r5, "n_gold": rep["retrieval_test"]["n_gold"], "pass": r5 is not None and r5 >= 0.60,
                                  "not_working": r5 is not None and r5 < 0.40, "bar": ">= 0.60 (not working < 0.40)"}

    # pair predictions per model
    preds = defaultdict(dict)
    for p in sorted(run_dir.glob("predictions_*_all.jsonl")):
        _, model, split, _ = p.stem.split("_", 3)
        for line in p.open():
            row = json.loads(line)
            if row["decision"].get("error") is None and row["decision"]["probs"].get("same_change") == row["decision"]["probs"].get("same_change"):
                preds[model][row["pr_number"]] = row["decision"]["probs"]["same_change"]
    # rules floor: retrieval score as duplicate_of
    preds["rules"] = {pid: (r["labels"]["retrieval_score"] or 0.0) for pid, r in pairs.items()}

    base_bad = float(np.mean([r["labels"]["bad_outcome"] for r in ds.values() if r["split"] == "test"]))
    for model, pp in preds.items():
        entry = {}
        # R4-H2: pair AUC on test pairs with a positive and (hard + random) negatives
        for split in ("test", "val"):
            keys = [k for k in pp if k in pairs and pairs[k]["split"] == split and pairs[k]["kind"] != "retrieved" or (k in pairs and pairs[k]["split"] == split and pairs[k]["kind"] == "retrieved" and pairs[k]["labels"]["a_label"] == "superseded_by")]
            y = np.array([int(pairs[k]["labels"]["positive"]) for k in keys]); x = np.array([pp[k] for k in keys], dtype=float)
            if len(y) and 0 < y.sum() < len(y):
                lo, hi = bootstrap_ci(y, x, roc_auc)
                entry[f"pair_{split}"] = {"n": int(len(y)), "positives": int(y.sum()), "roc_auc": roc_auc(y, x), "ci95": [lo, hi], "pr_auc": pr_auc(y, x)}
        # verdict level (R4-H3/H4): duplicate_of per PR = max over retrieved candidates; combine with run-3 verdicts
        dup = defaultdict(float)
        for k, v in pp.items():
            if k in pairs and pairs[k]["kind"] == "retrieved":
                a_pid = _a_pid(pairs[k], ds)
                dup[a_pid] = max(dup[a_pid], v)
        entry["duplicate_of_coverage"] = {"n_prs_with_candidates": len(dup)}
        rep["models"][model] = entry
        # R4-H3/H4 need run-3 verdicts; computed by combining with results/run3 predictions (same model) if present
        r3 = Path("results/run3") / f"predictions_{model if model != 'rules' else 'rules'}_test_all.jsonl"
        if r3.exists():
            entry.update(_verdict_level(model, r3, dup, ds, base_bad))
            H = rep["hypotheses"]
            H[f"R4-H3[{model}]"] = {"pass_bad_rate": entry["with_dedup"]["pass_bad_rate"], "base": base_bad, "run3_pass_bad_rate": entry["without_dedup"]["pass_bad_rate"],
                                   "pass": entry["with_dedup"]["pass_bad_rate"] <= 0.5 * base_bad, "bar": "<= 0.5 x base"}
            d = entry["with_dedup"]["closed_nonpass_share"] - entry["without_dedup"]["closed_nonpass_share"]
            H[f"R4-H4[{model}]"] = {"closed_nonpass_share_run3": entry["without_dedup"]["closed_nonpass_share"], "with_dedup": entry["with_dedup"]["closed_nonpass_share"],
                                   "delta_pp": 100 * d, "pass": d >= 0.15, "bar": ">= +15 pp"}
        if model != "rules":
            pa = entry.get("pair_test", {}).get("roc_auc")
            rep["hypotheses"][f"R4-H2[{model}]"] = {"roc_auc": pa, "n": entry.get("pair_test", {}).get("n"), "pass": pa is not None and pa >= 0.80, "bar": ">= 0.80"}
    t = json.loads((run_dir / "retrieval_timing.json").read_text()) if (run_dir / "retrieval_timing.json").exists() else {}
    rep["retrieval_timing"] = t
    rep["hypotheses"]["R4-H5"] = {"p99_ms": t.get("p99_ms"), "pass": t.get("p99_ms") is not None and t["p99_ms"] <= 20, "bar": "retrieval p99 <= 20 ms; <= 1+k model calls per PR"}
    (run_dir / "metrics.json").write_text(json.dumps(rep, indent=2, default=float))
    (run_dir / "tables.md").write_text(_tables(rep))
    print(_tables(rep))


def _a_pid(pair: dict, ds: dict) -> int:
    return int(hashlib.sha1(f"{pair['repo']}#{pair['a']}".encode()).hexdigest()[:12], 16)


def _verdict_level(model: str, r3_path: Path, dup: dict, ds: dict, base_bad: float) -> dict:
    """Replay the run-3 default policy for this model, then add the dedup rule on top."""
    from ..gate.engine import Gate, Calibrator
    from .report_run3 import GATE_DIR, _calibrate_primary, historical_policy
    policy = historical_policy(); policy["always_human_paths"] = []
    maps = json.loads((GATE_DIR / f"calibration_{model}_v2.json").read_text()) if (GATE_DIR / f"calibration_{model}_v2.json").exists() else {}
    val_p = {json.loads(l)["pr_number"]: json.loads(l)["decision"]["probs"] for l in (r3_path.parent / f"predictions_{model}_val_all.jsonl").open()} if (r3_path.parent / f"predictions_{model}_val_all.jsonl").exists() else {}
    cal = _calibrate_primary(val_p, ds)
    if cal:
        maps["needs_human_review"] = cal
    gate = Gate(policy, model=None, calibrator=Calibrator(maps))
    test = {json.loads(l)["pr_number"]: json.loads(l)["decision"]["probs"] for l in r3_path.open() if json.loads(l)["decision"].get("error") is None}
    out = {}
    for variant in ("without_dedup", "with_dedup"):
        verdicts = {}
        for pid, probs in test.items():
            if pid not in ds:
                continue
            v = gate._match(gate.cal(probs))[1]
            if variant == "with_dedup" and dup.get(pid, 0.0) >= DUP_THRESHOLD:
                v = "hold"
            verdicts[pid] = v
        passed = [p for p, v in verdicts.items() if v == "pass"]
        closed = [p for p in verdicts if ds[p]["labels"]["closed_unmerged"]]
        out[variant] = {"n": len(verdicts), "pass_frac": len(passed) / max(1, len(verdicts)),
                        "pass_bad_rate": float(np.mean([ds[p]["labels"]["bad_outcome"] for p in passed])) if passed else float("nan"),
                        "closed_nonpass_share": float(np.mean([verdicts[p] != "pass" for p in closed])) if closed else float("nan"),
                        "dedup_holds": sum(1 for p in verdicts if dup.get(p, 0.0) >= DUP_THRESHOLD) if variant == "with_dedup" else 0}
    return out


def _f(x, nd=3):
    return "—" if x is None or x != x else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _tables(rep) -> str:
    L = ["# Run 4 — PR memory and duplicate detection", "", "## Human labels on closed-unmerged PRs (regex over maintainer comments)", "", "| label | n |", "|---|---|"]
    for k, v in sorted(rep["labels"].items(), key=lambda kv: -kv[1]):
        L.append(f"| {k} | {v} |")
    L += ["", "## Retrieval (top-5, deterministic)", "", "| split | gold pairs | recall@5 |", "|---|---|---|"]
    for s in ("test", "val"):
        r = rep.get(f"retrieval_{s}", {}); L.append(f"| {s} | {r.get('n_gold')} | {_f(r.get('recall_at_5'))} |")
    t = rep.get("retrieval_timing", {}); L += ["", f"retrieval latency p50 {_f(t.get('p50_ms'), 2)} ms · p99 {_f(t.get('p99_ms'), 2)} ms over {t.get('n')} PRs", ""]
    L += ["## Pair question `same_change` → duplicate_of", "", "| model | split | pairs | positives | ROC-AUC [95% CI] | PR-AUC |", "|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        for s in ("test", "val"):
            p = e.get(f"pair_{s}")
            if p:
                L.append(f"| {m} | {s} | {p['n']} | {p['positives']} | {_f(p['roc_auc'])} [{_f(p['ci95'][0])}, {_f(p['ci95'][1])}] | {_f(p['pr_auc'])} |")
    L += ["", "## Verdict level on run-3 test PRs (default policy, then + dedup rule duplicate_of ≥ 0.7 → hold)", "",
          "| model | variant | pass % | pass bad-rate | closed PRs not passed | dedup holds |", "|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        for v in ("without_dedup", "with_dedup"):
            if v in e:
                x = e[v]; L.append(f"| {m} | {v} | {_f(100 * x['pass_frac'], 1)} | {_f(x['pass_bad_rate'])} | {_f(100 * x['closed_nonpass_share'], 1)}% | {x['dedup_holds']} |")
    L += ["", "## Pre-registered criteria", "", "| hypothesis | result | bar | pass |", "|---|---|---|---|"]
    for h, r in rep["hypotheses"].items():
        shown = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k not in ("pass", "bar")}
        L.append(f"| {h} | {json.dumps(shown)} | {r['bar']} | {'✅' if r['pass'] else '❌'} |")
    return "\n".join(L) + "\n"
