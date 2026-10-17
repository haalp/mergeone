"""Run 5 component B — declared assumptions (PROTOCOL R5.1-B, R5-H4/H4b).

  python -m mergeone.eval.run5_assumptions labels    comments → data/run5/relation_labels.jsonl
  python -m mergeone.eval.run5_assumptions extract   Haiku 5.5 + prompts/assumptions_v1.md → data/run5/assumptions.jsonl
  python -m mergeone.eval.run5_assumptions pairs     → results/run5/pairs_assumptions.jsonl (rows in dataset format)
  python -m mergeone.cli predict --model haiku --qset pair_assume --run run5_assume \\
         --dataset results/run5/pairs_assumptions.jsonl --split test
  python -m mergeone.eval.run5_assumptions report    → results/run5/assumptions_metrics.json, assumptions_tables.md

Comments: data/run4/comments.jsonl (closed-unmerged, run 4) + data/run5/comments_merged.jsonl (merged, run 5,
`python -m mergeone.data.pr_memory comments --merged`). Same row format; both are read when present.

Assumptions are extracted for every dataset PR and for every non-dataset memory PR that appears as side B of a
pair (`extract` builds the pair skeleton to find them), from title + body only — never the file list or diff.
Rows are keyed by pr_number = sha1("owner/repo#N")[:12] (the dataset's own id scheme).

Pair predictions for assumption_conflict live in results/run5_assume/ so they never collide with run-5
same_change prediction files in results/run5/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from ..data.relation_labels import label as relation_label

DATASET = Path("data/run3/dataset.jsonl")
MEM_DIR = Path("data/run4/memory")
COMMENTS = (Path("data/run4/comments.jsonl"), Path("data/run5/comments_merged.jsonl"))
REL_LABELS = Path("data/run5/relation_labels.jsonl")
ASSUMPTIONS = Path("data/run5/assumptions.jsonl")
PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "assumptions_v1.md"
PRICES = Path("results/prices.json")
CANDS_V2 = Path("results/run5/candidates.jsonl")
OUT_DIR = Path("results/run5")
PAIRS = OUT_DIR / "pairs_assumptions.jsonl"
PRED_DIR = Path("results/run5_assume")
MODEL_ID = "claude-haiku-5-5"
KINDS = ("api", "behaviour", "data", "config", "dependency", "test")
RANDOM_WINDOW_DAYS = 90
POLICY_THRESHOLD = 0.7  # R5.1-B policy: conflict_of >= 0.7 → hold
SCHEMA = {
    "type": "object",
    # max 8 items: the API's structured output rejects `maxItems`, so the prompt states the cap and the client
    # truncates to 8 (`truncated` counts how often that happened).
    "properties": {"assumptions": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": list(KINDS)}, "target": {"type": "string"}, "claim": {"type": "string"}},
        "required": ["kind", "target", "claim"], "additionalProperties": False}}},
    "required": ["assumptions"],
    "additionalProperties": False,
}


def pid_of(repo: str, number: int) -> int:
    return int(hashlib.sha1(f"{repo}#{number}".encode()).hexdigest()[:12], 16)


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).open() if l.strip()]


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def load_memory_raw(mem_dir: Path = MEM_DIR) -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = {}
    for p in sorted(Path(mem_dir).glob("*.jsonl")):
        repo = p.stem.replace("__", "/")
        out[repo] = {int(r["number"]): r for r in _rows(p)}
    return out


# ----------------------------------------------------------------- state parsing
_SUMMARY = re.compile(r"\n\nSUMMARY: \d+ files changed")


def title_body(state: str) -> tuple[str, str]:
    """Title and message body from a rendered dataset state. Never returns the FILES CHANGED block or the diff."""
    first, _, rest = state.partition("\n")
    title = first.replace("PULL REQUEST TITLE: ", "", 1).strip()
    if not rest.startswith("MERGE MESSAGE BODY:\n"):
        return title, ""
    rest = rest[len("MERGE MESSAGE BODY:\n"):]
    ms = list(_SUMMARY.finditer(rest))
    if ms:
        body = rest[:ms[-1].start()]
    else:  # defensive: cut at the first block header the renderer emits after the body
        body = re.split(r"\n\n(?:SUMMARY:|FILES CHANGED|DIFF \()", rest, maxsplit=1)[0]
    return title, body.strip()


def state_paths(state: str) -> list[str]:
    out, on = [], False
    for line in state.split("\n"):
        if line.startswith("FILES CHANGED"):
            on = True
            continue
        if on:
            if line.startswith("  ...") or not line.startswith("  "):
                if not line.strip() or line.startswith("DIFF"):
                    break
                continue
            out.append(line.strip().rsplit(" ", 2)[0])
    return out


def dirs2(paths) -> set[str]:
    """Directories at depth ≤ 2: a/b/c/x.py → a/b; a/x.py → a; x.py → ."""
    out = set()
    for p in paths:
        parts = p.split("/")[:-1]
        out.add("/".join(parts[:2]) if parts else ".")
    return out


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


# ----------------------------------------------------------------- labels
def load_comments() -> dict[int, dict]:
    out = {}
    for p in COMMENTS:
        if p.exists():
            for r in _rows(p):
                out[r["pr_number"]] = r
    return out


def cmd_labels(a):
    ds = _rows(DATASET)
    mem = load_memory_raw()
    comments = load_comments()
    REL_LABELS.parent.mkdir(parents=True, exist_ok=True)
    n, by = Counter(), Counter()
    with REL_LABELS.open("w") as f:
        for r in ds:
            c = comments.get(r["pr_number"])
            if c is None:
                lab, rel, num = "comments_not_fetched", None, None
            else:
                lab, rel, num = relation_label(c["comments"], r["number"])
                if lab == "related_to" and num not in mem.get(r["repo"], {}):
                    lab = "related_to_outside_memory"
            n[lab] += 1
            by[(lab, rel or "-", r["split"], "merged" if r["labels"]["merged"] else "closed")] += 1
            f.write(json.dumps({"pr_number": r["pr_number"], "repo": r["repo"], "number": r["number"], "split": r["split"],
                                "label": lab, "relation": rel, "related_to": num}) + "\n")
    print(f"{len(ds)} dataset PRs; comment rows {len(comments)} from {[str(p) for p in COMMENTS if p.exists()]}")
    print("by label:", dict(n))
    print("label | relation | split | outcome | n")
    for k, v in sorted(by.items()):
        print(" | ".join(k), "|", v)


# ----------------------------------------------------------------- pair skeleton (shared by extract and pairs)
def _candidates_source() -> tuple[str, dict[int, list[int]]]:
    """PR → ordered top-5 candidate numbers. Prefers component A's v2 candidates; falls back to the run-4
    retrieval recomputed in-process (results/run4/candidates.jsonl is not committed and is not overwritten)."""
    if CANDS_V2.exists():
        return str(CANDS_V2), {r["pr_number"]: [int(c["number"]) for c in r["candidates"]][:5] for r in _rows(CANDS_V2)}
    r4 = Path("results/run4/candidates.jsonl")
    if r4.exists():
        return str(r4) + " (fallback: run-4 retrieval; results/run5/candidates.jsonl not present)", \
            {r["pr_number"]: [int(c["number"]) for c in r["candidates"]][:5] for r in _rows(r4)}
    return "", {}


def _run4_candidates_for(prs: list[dict]) -> dict[int, list[int]]:
    from ..memory.retrieval import load_all, TOP_K
    mems = load_all(MEM_DIR)
    out = {}
    for r in prs:
        m = mems.get(r["repo"])
        if m is None:
            continue
        title = r["state"].split("\n", 1)[0].replace("PULL REQUEST TITLE: ", "")
        out[r["pr_number"]] = [c["number"] for c in m.candidates(r["number"], title, "", state_paths(r["state"]), r.get("author"), r["created_at"], k=TOP_K)]
    return out


def skeleton(verbose: bool = True) -> tuple[list[dict], dict]:
    """[{a: dataset row, b: number, kind, relation}] for every labelled relation, plus info."""
    ds = {r["pr_number"]: r for r in _rows(DATASET)}
    labels = [l for l in _rows(REL_LABELS) if l["label"] == "related_to" and l["relation"] in ("conflicts", "depends")] if REL_LABELS.exists() else []
    mem = load_memory_raw()
    src, cands = _candidates_source()
    if not src:
        src = "run-4 retrieval recomputed in-process (fallback: results/run5/candidates.jsonl not present)"
        cands = _run4_candidates_for([ds[l["pr_number"]] for l in labels])
    out, info = [], {"candidates_source": src, "missing_candidates": 0}
    for l in labels:
        pr = ds[l["pr_number"]]
        n_pos = l["related_to"]
        out.append({"a": pr, "b": n_pos, "kind": "labelled", "relation": l["relation"]})
        cl = cands.get(pr["pr_number"])
        if cl is None:
            info["missing_candidates"] += 1
            cl = []
        hard = [c for c in cl if c != n_pos][:5]
        for c in hard:
            out.append({"a": pr, "b": c, "kind": "retrieved", "relation": None})
        # random same-repo pairs within ±90 days, as many as this PR's retrieved negatives; per-PR seed so pairs
        # stay identical when more labels arrive (seed 5)
        t0 = _ts(pr["created_at"])
        excl = {pr["number"], n_pos, *hard}
        pool = sorted(n for n, m in mem.get(pr["repo"], {}).items()
                      if n not in excl and abs((_ts(m["created_at"]) - t0).total_seconds()) <= RANDOM_WINDOW_DAYS * 86400)
        rng = random.Random(f"5:{pr['repo']}#{pr['number']}")
        for c in rng.sample(pool, min(len(hard), len(pool))):
            out.append({"a": pr, "b": c, "kind": "random", "relation": None})
    if verbose:
        print(f"pair skeleton: {len(out)} pairs from {len(labels)} labelled relations; candidates: {src}", file=sys.stderr)
    return out, info


# ----------------------------------------------------------------- extract
class _Extractor:
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic()
        self.system = PROMPT.read_text()
        p = json.loads(PRICES.read_text())["haiku"]
        self.in_price, self.out_price = p["input_per_mtok"] / 1e6, p["output_per_mtok"] / 1e6

    def __call__(self, title: str, body: str) -> dict:
        user = f"TITLE: {title}\n\nDESCRIPTION:\n{body}"
        t0 = time.perf_counter()
        last = None
        for attempt in range(6):
            try:
                resp = self.client.messages.create(
                    # R5.1-B asks for temperature 0; the API rejects `temperature` for Haiku 5.5 ("deprecated for this
                    # model"), so the model default is used — recorded in CHANGELOG as a deviation.
                    model=MODEL_ID, max_tokens=1500, system=self.system,
                    thinking={"type": "disabled"},
                    output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                    messages=[{"role": "user", "content": user}])
                text = next(b.text for b in resp.content if b.type == "text")
                raw_items = json.loads(text)["assumptions"]
                items = raw_items[:8]
                items = [{"kind": x["kind"], "target": x["target"].strip(), "claim": x["claim"].strip()} for x in items if x.get("kind") in KINDS]
                it, ot = resp.usage.input_tokens, resp.usage.output_tokens
                return {"assumptions": items, "model_version": resp.model, "latency_ms": (time.perf_counter() - t0) * 1000,
                        "input_tokens": it, "output_tokens": ot, "cost_usd": it * self.in_price + ot * self.out_price,
                        "stop_reason": resp.stop_reason, "truncated": len(raw_items) > 8, "error": None}
            except Exception as e:  # recorded, never hidden; transient errors retried
                last = f"{type(e).__name__}: {str(e)[:300]}"
                if any(s in last for s in ("429", "529", "overloaded", "500", "502", "503", "Connection", "Timeout")):
                    time.sleep(min(30, 2 ** attempt))
                    continue
                break
        return {"assumptions": [], "model_version": MODEL_ID, "latency_ms": (time.perf_counter() - t0) * 1000,
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": last}


def _targets(include_partners: bool) -> list[dict]:
    ds = _rows(DATASET)
    out = []
    for r in ds:
        t, b = title_body(r["state"])
        out.append({"pr_number": r["pr_number"], "repo": r["repo"], "number": r["number"], "source": "dataset", "title": t, "body": b})
    if include_partners and REL_LABELS.exists():
        have = {r["pr_number"] for r in ds}
        mem = load_memory_raw()
        sk, _ = skeleton()
        for p in sk:
            repo, n = p["a"]["repo"], p["b"]
            pid = pid_of(repo, n)
            if pid in have:
                continue
            have.add(pid)
            m = mem[repo][n]
            out.append({"pr_number": pid, "repo": repo, "number": n, "source": "memory", "title": (m.get("title") or "").strip(),
                        "body": (m.get("body") or "").strip()[:1500]})  # same 1,500-char cut as the dataset renderer
    return out


def cmd_extract(a):
    targets = _targets(not a.no_partners)
    ASSUMPTIONS.parent.mkdir(parents=True, exist_ok=True)
    done = {}
    if ASSUMPTIONS.exists():
        for r in _rows(ASSUMPTIONS):
            if r.get("error") is None:
                done[r["pr_number"]] = r
    todo = [t for t in targets if t["pr_number"] not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(targets)} PRs ({sum(t['source'] == 'memory' for t in targets)} memory partners); {len(done)} done; {len(todo)} to extract", flush=True)
    ex = _Extractor() if any(t["body"] for t in todo) else None
    lock = threading.Lock()
    t0 = time.time()
    n_err = 0

    def work(t):
        if not t["body"]:
            return t, {"assumptions": [], "model_version": None, "latency_ms": 0.0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                       "error": None, "empty_body": True}
        return t, ex(t["title"], t["body"])

    with ASSUMPTIONS.open("a") as f, ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        futs = [pool.submit(work, t) for t in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            t, res = fut.result()
            n_err += res["error"] is not None
            with lock:
                f.write(json.dumps({"pr_number": t["pr_number"], "repo": t["repo"], "number": t["number"], "source": t["source"], **res}) + "\n")
                f.flush()
            if i % 100 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} errors={n_err} elapsed={time.time() - t0:.0f}s", flush=True)
    _extract_summary()


def load_assumptions() -> dict[int, dict]:
    out = {}
    if ASSUMPTIONS.exists():
        for r in _rows(ASSUMPTIONS):
            if r["pr_number"] not in out or r.get("error") is None:
                out[r["pr_number"]] = r
    return out


def _extract_summary() -> dict:
    rows = load_assumptions()
    ds_ids = {r["pr_number"] for r in _rows(DATASET)}
    s = {}
    for name, keep in (("dataset", lambda r: r["pr_number"] in ds_ids), ("memory_partners", lambda r: r["pr_number"] not in ds_ids)):
        rs = [r for r in rows.values() if keep(r)]
        ok = [r for r in rs if r.get("error") is None]
        called = [r for r in ok if not r.get("empty_body")]
        cnt = [len(r["assumptions"]) for r in ok]
        lat = sorted(r["latency_ms"] for r in called)
        s[name] = {"n": len(rs), "errors": len(rs) - len(ok), "empty_body": sum(1 for r in ok if r.get("empty_body")), "calls": len(called),
                   "with_ge1": sum(1 for c in cnt if c), "mean_count": float(np.mean(cnt)) if cnt else None,
                   "kinds": dict(Counter(x["kind"] for r in ok for x in r["assumptions"])), "truncated_to_8": sum(1 for r in ok if r.get("truncated")),
                   "input_tokens": sum(r["input_tokens"] for r in ok), "output_tokens": sum(r["output_tokens"] for r in ok),
                   "cost_usd": round(sum(r["cost_usd"] for r in ok), 4),
                   "latency_p50_ms": lat[len(lat) // 2] if lat else None, "latency_p99_ms": lat[int(len(lat) * 0.99)] if lat else None,
                   "model_versions": dict(Counter(r["model_version"] for r in called))}
    print(json.dumps(s, indent=2))
    return s


# ----------------------------------------------------------------- pairs
def assumption_block(r: dict | None) -> str:
    items = (r or {}).get("assumptions") or []
    if not items:
        return "DECLARED ASSUMPTIONS: none"
    return "DECLARED ASSUMPTIONS:\n" + "\n".join(f"- [{x['kind']}] {x['target']}: {x['claim']}" for x in items)


def _render_mem(m: dict) -> str:
    files = [f["path"] for f in m.get("files") or []]
    lines = [f"PULL REQUEST TITLE: {m.get('title') or ''}", f"MERGE MESSAGE BODY:\n{(m.get('body') or '').strip()[:1200]}", "",
             f"STATE: {(m.get('state') or '').lower()}" + (f", merged {m['merged_at'][:10]}" if m.get("merged_at") else f", opened {m['created_at'][:10]}"),
             f"AUTHOR: {m.get('author')}", "", "FILES CHANGED:"]
    lines += [f"  {p}" for p in files[:60]] or ["  (file list unavailable)"]
    return "\n".join(lines)  # identical to run4._render_b


def pair_state(a_state: str, a_block: str, b_state: str, b_block: str) -> str:
    """Run-4 layout (A ≤ 6,000 chars, B ≤ 3,000) with each side's DECLARED ASSUMPTIONS block appended after the
    cut, so truncation of a long diff never removes it."""
    return ("=== PULL REQUEST A ===\n" + a_state[:6000] + "\n\n" + a_block + "\n\n=== PULL REQUEST B ===\n" + b_state[:3000] + "\n\n" + b_block
            + "\n\nHow does A relate to B?")


def cmd_pairs(a):
    sk, info = skeleton()
    mem = load_memory_raw()
    asm = load_assumptions()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, seen, missing_asm = [], set(), Counter()
    for p in sk:
        pr, b = p["a"], p["b"]
        key = (pr["pr_number"], b)
        if key in seen:
            continue
        seen.add(key)
        m = mem[pr["repo"]][b]
        a_mem = mem[pr["repo"]].get(pr["number"])
        a_files = {f["path"] for f in a_mem["files"]} if a_mem and a_mem.get("files") else set(state_paths(pr["state"]))
        b_files = {f["path"] for f in m.get("files") or []}
        a_pid, b_pid = pr["pr_number"], pid_of(pr["repo"], b)
        for side, x in (("a", a_pid), ("b", b_pid)):
            if x not in asm:
                missing_asm[side] += 1
        rows.append({"pr_number": pid_of(pr["repo"], f"{pr['number']}~{b}"), "split": pr["split"], "repo": pr["repo"], "a": pr["number"], "b": b,
                     "kind": p["kind"], "qset": "pair",
                     "labels": {"positive": p["kind"] == "labelled", "relation": p["relation"], "shares_file": bool(a_files & b_files),
                                "dir_jaccard": round(jaccard(dirs2(a_files), dirs2(b_files)), 6), "file_jaccard": round(jaccard(a_files, b_files), 6),
                                "b_state": m.get("state"), "b_merged_at": m.get("merged_at"),
                                "a_n_assumptions": len((asm.get(a_pid) or {}).get("assumptions") or []),
                                "b_n_assumptions": len((asm.get(b_pid) or {}).get("assumptions") or [])},
                     "features": {}, "state": pair_state(pr["state"], assumption_block(asm.get(a_pid)), _render_mem(m), assumption_block(asm.get(b_pid)))})
    with PAIRS.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    pos = [r for r in rows if r["labels"]["positive"]]
    summ = {"pairs": len(rows), "candidates_source": info["candidates_source"], "prs_without_candidates": info["missing_candidates"],
            "kinds": dict(Counter(r["kind"] for r in rows)), "by_split_kind": {f"{s}/{k}": v for (s, k), v in sorted(Counter((r["split"], r["kind"]) for r in rows).items())},
            "positives_by_relation": dict(Counter(r["labels"]["relation"] for r in pos)),
            "positives_shares_file": dict(Counter(str(r["labels"]["shares_file"]) for r in pos)),
            "positives_by_split_relation_shares_file": {"/".join(map(str, k)): v for k, v in sorted(Counter((r["split"], r["labels"]["relation"], r["labels"]["shares_file"]) for r in pos).items())},
            "missing_assumptions_rows": dict(missing_asm)}
    if missing_asm:
        summ["warning"] = "some sides have no assumptions row: run `extract` (it covers memory partners) before `pairs`; they render as 'none'"
    print(json.dumps(summ, indent=2))
    (OUT_DIR / "pairs_assumptions_summary.json").write_text(json.dumps(summ, indent=2))


# ----------------------------------------------------------------- report
def _load_preds(pred_dir: Path) -> dict[str, dict[int, float]]:
    preds: dict[str, dict[int, float]] = defaultdict(dict)
    for p in sorted(Path(pred_dir).glob("predictions_*_all.jsonl")):
        model = p.stem[len("predictions_"):].rsplit("_", 2)[0]
        for line in p.open():
            r = json.loads(line)
            v = r["decision"]["probs"].get("assumption_conflict")
            if r["decision"].get("error") is None and v is not None and v == v:
                preds[model][r["pr_number"]] = float(v)
    return preds


def _recall_at(y, s, thr) -> float:
    pos = s[y == 1]
    return float((pos >= thr).mean()) if len(pos) else float("nan")


def _floor_threshold(rand_scores: np.ndarray, target_fpr: float) -> tuple[float, float]:
    """Smallest threshold t on the floor score with FPR(random pairs, score ≥ t) ≤ target_fpr."""
    cand = np.unique(np.concatenate([rand_scores, [np.inf]]))
    for t in cand:
        fpr = float((rand_scores >= t).mean()) if len(rand_scores) else 0.0
        if fpr <= target_fpr + 1e-12:
            return float(t), fpr
    return float("inf"), 0.0


def cmd_report(a):
    from .metrics import roc_auc, pr_auc, bootstrap_ci
    pairs = {r["pr_number"]: r for r in _rows(PAIRS)}
    preds = _load_preds(Path(a.pred_dir))
    preds["floor_dir_jaccard"] = {k: r["labels"]["dir_jaccard"] for k, r in pairs.items()}
    rep = {"pairs": json.loads((OUT_DIR / "pairs_assumptions_summary.json").read_text()) if (OUT_DIR / "pairs_assumptions_summary.json").exists() else {},
           "extraction": _extract_summary() if ASSUMPTIONS.exists() else {}, "models": {}, "hypotheses": {},
           "prediction_dir": str(a.pred_dir)}
    if REL_LABELS.exists():
        L = _rows(REL_LABELS)
        rep["relation_labels"] = {"by_label": dict(Counter(l["label"] for l in L)),
                                  "by_relation_split": {f"{s}/{r}": v for (s, r), v in sorted(Counter((l["split"], l["relation"]) for l in L if l["label"] == "related_to").items())}}
    for model, pp in preds.items():
        e = {}
        for split in ("test", "val"):
            keys = [k for k in pairs if pairs[k]["split"] == split and k in pp]
            n_all = sum(1 for k in pairs if pairs[k]["split"] == split)
            y = np.array([int(pairs[k]["labels"]["positive"]) for k in keys]); s = np.array([pp[k] for k in keys], dtype=float)
            ent = {"n": int(len(y)), "n_pairs_in_split": n_all, "coverage": len(y) / n_all if n_all else None, "positives": int(y.sum()) if len(y) else 0}
            if len(y) and 0 < y.sum() < len(y):
                lo, hi = bootstrap_ci(y, s, roc_auc)
                ent.update({"roc_auc": roc_auc(y, s), "ci95": [lo, hi], "pr_auc": pr_auc(y, s)})
            # H4b: labelled relations whose PRs share no changed file
            kind = np.array([pairs[k]["kind"] for k in keys]); shares = np.array([pairs[k]["labels"]["shares_file"] for k in keys], dtype=bool)
            nsf = (y == 1) & ~shares
            rand = kind == "random"
            ent["no_shared_file_positives"] = int(nsf.sum())
            if model != "floor_dir_jaccard" and len(y):
                fpr_m = float((s[rand] >= POLICY_THRESHOLD).mean()) if rand.any() else float("nan")
                caught_m = float((s[nsf] >= POLICY_THRESHOLD).mean()) if nsf.any() else float("nan")
                floor = np.array([pairs[k]["labels"]["dir_jaccard"] for k in keys], dtype=float)
                if rand.any():
                    t_f, fpr_f = _floor_threshold(floor[rand], fpr_m)
                    caught_f = float((floor[nsf] >= t_f).mean()) if nsf.any() else float("nan")
                else:
                    t_f = fpr_f = caught_f = float("nan")
                ent["h4b"] = {"threshold": POLICY_THRESHOLD, "n_no_shared_file_positives": int(nsf.sum()), "n_random_pairs": int(rand.sum()),
                              "model_caught": caught_m, "model_fpr_random": fpr_m,
                              "floor_threshold": t_f, "floor_fpr_random": fpr_f, "floor_caught": caught_f,
                              "delta_pp": 100 * (caught_m - caught_f) if caught_m == caught_m and caught_f == caught_f else float("nan"),
                              "caught_all_positives": _recall_at(y, s, POLICY_THRESHOLD)}
            e[split] = ent
        rep["models"][model] = e
        t = e.get("test", {})
        rep["hypotheses"][f"R5-H4[{model}]"] = {"roc_auc": t.get("roc_auc"), "ci95": t.get("ci95"), "n": t.get("n"), "positives": t.get("positives"),
                                                "coverage": t.get("coverage"), "evaluable": t.get("roc_auc") is not None,
                                                "pass": bool(t.get("roc_auc") is not None and t["roc_auc"] >= 0.75), "bar": ">= 0.75"}
        if "h4b" in t:
            h = t["h4b"]
            rep["hypotheses"][f"R5-H4b[{model}]"] = {"caught": h["model_caught"], "floor_caught": h["floor_caught"], "delta_pp": h["delta_pp"],
                                                     "fpr_random": h["model_fpr_random"], "n": h["n_no_shared_file_positives"],
                                                     "evaluable": bool(h["n_no_shared_file_positives"] and h["n_random_pairs"]),
                                                     "pass": bool(h["delta_pp"] == h["delta_pp"] and h["delta_pp"] >= 15), "bar": ">= +15 pp over dir-Jaccard floor at same random-pair FPR"}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "assumptions_metrics.json").write_text(json.dumps(rep, indent=2, default=float))
    md = _tables(rep)
    (OUT_DIR / "assumptions_tables.md").write_text(md)
    print(md)


def _f(x, nd=3):
    return "—" if x is None or (isinstance(x, float) and x != x) else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _tables(rep) -> str:
    L = ["# Run 5 B — declared assumptions (R5-H4, R5-H4b)", "",
         "Test repositories have been seen in aggregate in runs 3–4 (PROTOCOL R5 status). The floor is depth-2 directory Jaccard (no model, no assumptions).", ""]
    rl = rep.get("relation_labels")
    if rl:
        L += ["## Relation labels (regex over non-bot comments)", "", "| label | n |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(rl["by_label"].items(), key=lambda kv: -kv[1])]
        L += ["", "| split/relation | n |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rl["by_relation_split"].items()] + [""]
    ex = rep.get("extraction") or {}
    if ex:
        L += ["## Assumption extraction (Haiku 5.5, prompts/assumptions_v1.md, title+body only)", "",
              "| set | PRs | calls | empty body | ≥1 assumption | mean count | errors | cost USD | p50 ms |", "|---|---|---|---|---|---|---|---|---|"]
        for k, v in ex.items():
            L.append(f"| {k} | {v['n']} | {v['calls']} | {v['empty_body']} | {v['with_ge1']} | {_f(v['mean_count'], 2)} | {v['errors']} | {v['cost_usd']:.4f} | {_f(v['latency_p50_ms'], 0)} |")
        L.append("")
    ps = rep.get("pairs") or {}
    if ps:
        L += ["## Pairs", "", f"candidates: {ps.get('candidates_source')}", "", "| split/kind | n |", "|---|---|"] + [f"| {k} | {v} |" for k, v in ps.get("by_split_kind", {}).items()]
        L += ["", "| positives split/relation/shares_file | n |", "|---|---|"] + [f"| {k} | {v} |" for k, v in ps.get("positives_by_split_relation_shares_file", {}).items()] + [""]
    L += ["## R5-H4 — ROC-AUC of conflict_of (positives = labelled relations; negatives = other top-5 + random same-repo ±90 d)", "",
          "| model | split | pairs (coverage) | positives | ROC-AUC [95% CI] | PR-AUC |", "|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        for s in ("test", "val"):
            p = e.get(s)
            if p and p.get("n"):
                ci = p.get("ci95") or [None, None]
                L.append(f"| {m} | {s} | {p['n']} ({_f(p['coverage'], 2)}) | {p['positives']} | {_f(p.get('roc_auc'))} [{_f(ci[0])}, {_f(ci[1])}] | {_f(p.get('pr_auc'))} |")
    L += ["", f"## R5-H4b — no-shared-file relations caught at conflict_of ≥ {POLICY_THRESHOLD} vs floor at the same random-pair FPR", "",
          "| model | split | n | caught | FPR random | floor thr | floor FPR | floor caught | Δ pp |", "|---|---|---|---|---|---|---|---|---|"]
    for m, e in rep["models"].items():
        for s in ("test", "val"):
            h = (e.get(s) or {}).get("h4b")
            if h:
                L.append(f"| {m} | {s} | {h['n_no_shared_file_positives']} | {_f(h['model_caught'])} | {_f(h['model_fpr_random'])} | {_f(h['floor_threshold'])} | {_f(h['floor_fpr_random'])} | {_f(h['floor_caught'])} | {_f(h['delta_pp'], 1)} |")
    L += ["", "## Pre-registered criteria (test)", "", "| hypothesis | result | bar | pass |", "|---|---|---|---|"]
    for h, r in rep["hypotheses"].items():
        shown = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k not in ("pass", "bar", "evaluable")}
        L.append(f"| {h} | {json.dumps(shown)} | {r['bar']} | {('✅' if r['pass'] else '❌') if r.get('evaluable') else 'not evaluable'} |")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run5_assumptions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("labels").set_defaults(fn=cmd_labels)
    e = sub.add_parser("extract")
    e.add_argument("--concurrency", type=int, default=8)
    e.add_argument("--limit", type=int)
    e.add_argument("--no-partners", action="store_true", help="dataset PRs only (skip memory PRs that are side B of a pair)")
    e.set_defaults(fn=cmd_extract)
    sub.add_parser("pairs").set_defaults(fn=cmd_pairs)
    r = sub.add_parser("report")
    r.add_argument("--pred-dir", default=str(PRED_DIR))
    r.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
