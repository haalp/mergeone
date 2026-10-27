"""Run 9 R9-H4: the shared-A pair call (PROTOCOL.md §R9.2, §R9.3, §R9.6; prompts/jev_pair_shared.json, frozen).

  python -m mergeone.eval.run9_pairs groups       no model call. Builds the two pair sets of §R9.2 as groups (one request each):
        set (a): the 187 R4-H2 test pairs (results/run6/pairs_eval_test.jsonl) taken from results/run4/pairs.jsonl in that
                 file's order, grouped by A, ≤ 10 per group (a larger group is split into chunks of 10, in order)
        set (b): the 100 adjudicated run-6B hold pairs (data/run6/hold_labels_adjudicated.jsonl), each A with the first 10
                 of its run-6B candidates (results/run6/candidates_k20.jsonl, retrieval order); the adjudicated B replaces
                 the 10th candidate when it is not among them (§R9.6)
        → results/run9/groups_a.jsonl, groups_b.jsonl (one row per request), results/run9/pairs_b.jsonl (the same set-(b)
          candidates as per-candidate rows, run-4 renderer, qset pair), results/run9/groups_summary.json (group sizes)
  mergeone predict --qset pair_shared --dataset results/run9/groups_<a|b>.jsonl …
        one System One request per group (state: A once, then B1…Bk; questions same_change_1…same_change_k); the k answers
        become k rows shaped like the per-candidate `pair` rows (same pr_number = sha1("repo#A~B"), same `labels`) with
        qset "pair_shared", mode "shared", group_id, group_size, slot (1-based), and probs.same_change =
        P(same) + 0.5·P(subset) (run 4's rule). Per-candidate input tokens = the request's tokens / k.
  python -m mergeone.eval.run9_pairs score --i-have-frozen-the-protocol
        shared vs per-candidate (both vendor BF16, same day): same_change AUC on set (a) (label `positive`), duplicate-vs-rest
        AUC on set (b) (adjudicated `duplicate` vs `pointer`/`unrelated`), argmax agreement and mean |Δp| (reported, no bar),
        candidates per second on set (b) from the timed passes' throughput.json → results/run9/pairs_metrics.json.
        The full report (all of R9-H2…H5) is mergeone.eval.run9_report, which uses the functions below.

Pair state is the run-4 full renderer in both arms (A ≤ 6,000 characters, B ≤ 3,000). The shared state is built from the
same A and B text the per-candidate rows carry, so the two arms see the same characters.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .metrics import bootstrap_ci, roc_auc

PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "jev_pair_shared.json"
RUN_DIR = Path("results/run9")
RUN4_PAIRS = Path("results/run4/pairs.jsonl")
PAIR_KEYS = Path("results/run6/pairs_eval_test.jsonl")
ADJUDICATED = Path("data/run6/hold_labels_adjudicated.jsonl")
CANDIDATES_K20 = Path("results/run6/candidates_k20.jsonl")
DATASET = Path("data/run3/dataset.jsonl")
MEM_DIR = Path("data/run4/memory")
GROUPS = {"a": RUN_DIR / "groups_a.jsonl", "b": RUN_DIR / "groups_b.jsonl"}
PAIRS_B = RUN_DIR / "pairs_b.jsonl"
GROUPS_SUMMARY = RUN_DIR / "groups_summary.json"
PAIRS_METRICS = RUN_DIR / "pairs_metrics.json"
K = 10
LOCAL = "pplx-flash-local"
# step directories written by scripts/run9_gpu.sh (docs/run9-tooling.md)
ARM_DIRS = {("per_candidate", "a"): RUN_DIR / "bf16" / "pairs_a", ("per_candidate", "b"): RUN_DIR / "bf16" / "pairs_b",
            ("shared", "a"): RUN_DIR / "shared" / "pairs_a", ("shared", "b"): RUN_DIR / "shared" / "pairs_b"}
QSET = {"per_candidate": "pair", "shared": "pair_shared"}
H4_SPEED, H4_TOL = 2.5, 0.02
DUP_POS, DUP_NEG = ("duplicate",), ("pointer", "unrelated")
A_HEADER, B_HEADER, PAIR_FOOTER = "=== PULL REQUEST A ===\n", "\n\n=== PULL REQUEST B ===\n", "\n\nHow does A relate to B?"


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).open() if l.strip()]


def _write(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(path)


def pair_id(repo: str, a: int, b: int) -> int:
    """run 4 / run 6B pair id: sha1("repo#A~B")[:12] as an int."""
    return int(hashlib.sha1(f"{repo}#{a}~{b}".encode()).hexdigest()[:12], 16)


def group_id(repo: str, a: int, set_name: str, chunk: int) -> int:
    return int(hashlib.sha1(f"{repo}#{a}~shared-{set_name}-{chunk}".encode()).hexdigest()[:12], 16)


def load_spec(path: Path = PROMPT) -> dict:
    spec = json.loads(Path(path).read_text())
    for k in ("state", "question_name", "question"):
        if k not in spec:
            raise ValueError(f"{path}: missing {k!r}")
    return spec


# ----------------------------------------------------------------- the shared request
def split_pair_state(state: str) -> tuple[str, str]:
    """run4.pair_state(A, B) → (A[:6000], B[:3000]). The B header is the last one in the string (B is ≤ 3,000 characters and
    never holds the header; A could only in theory, and taking the last occurrence keeps A whole in that case)."""
    if not state.startswith(A_HEADER):
        raise ValueError("not a run-4 pair state (no A header)")
    i = state.rfind(B_HEADER)
    if i < 0:
        raise ValueError("not a run-4 pair state (no B header)")
    a = state[len(A_HEADER):i]
    b = state[i + len(B_HEADER):]
    if b.endswith(PAIR_FOOTER):
        b = b[: -len(PAIR_FOOTER)]
    return a, b


def render_shared(group: dict, spec: dict) -> tuple[str, dict, list[str]]:
    """A group row → (state, questions, question names in slot order), as prompts/jev_pair_shared.json says."""
    st, cands = spec["state"], group["candidates"]
    k = len(cands)
    if not 1 <= k <= int(st.get("k_max", K)):
        raise ValueError(f"group {group.get('group_id')}: {k} candidates (allowed 1..{st.get('k_max', K)})")
    parts = [st["a_header"], group["a_state"][: int(st["a_max_chars"])]]
    for i, c in enumerate(cands, 1):
        parts += [st["candidate_header"].replace("{i}", str(i)), c["b_state"][: int(st["candidate_max_chars"])]]
    parts.append(st["footer"].replace("{k}", str(k)))
    q = spec["question"]
    names, questions = [], {}
    for i in range(1, k + 1):
        name = spec["question_name"].replace("{i}", str(i))
        body = {kk: v for kk, v in q.items() if not kk.startswith("_")}
        body["instructions"] = q["instructions"].replace("{i}", str(i)).replace("{k}", str(k))
        names.append(name)
        questions[name] = body
    return "".join(parts), questions, names


def duplicate_of(answer: dict | None) -> tuple[float, float, dict | None]:
    """(P(same) + 0.5·P(subset), confidence, probabilities) from one `same_change`-style choice answer."""
    pr = (answer or {}).get("probabilities")
    if not isinstance(pr, dict):
        return float("nan"), float("nan"), None
    return float(pr.get("same", 0.0)) + 0.5 * float(pr.get("subset", 0.0)), float(answer.get("confidence", float("nan"))), pr


def _post(model, body: dict):
    """SystemOneModel's POST with its 429 rule (retries off under MERGEONE_NO_RETRY=1). Returns (response | None, ms, error)."""
    t0 = time.perf_counter()
    try:
        r = model.client.post(model.endpoint, json=body)
        for attempt in range(model.max_retries_429):
            if r.status_code != 429:
                break
            time.sleep(min(8.0, 0.5 * 2 ** attempt))
            r = model.client.post(model.endpoint, json=body)
    except Exception as e:
        return None, (time.perf_counter() - t0) * 1000.0, f"{type(e).__name__}: {e}"
    ms = (time.perf_counter() - t0) * 1000.0
    if r.status_code != 200:
        return None, ms, f"HTTP {r.status_code}: {r.text[:300]}"
    try:
        return r.json(), ms, None
    except Exception as e:
        return None, ms, f"{type(e).__name__}: {e}"


def decide_group(model, group: dict, spec: dict) -> list[dict]:
    """One request for the group → one decision dict per candidate (Decision.to_dict shape), in slot order."""
    state, questions, names = render_shared(group, spec)
    body = {"state": state, "model": model.pinned or model.model, "questions": questions}
    d, ms, err = _post(model, body)
    k = len(names)
    version = model.pinned or model.model
    if err is None and isinstance(d, dict):
        if model.pinned is None and d.get("model"):
            model.pinned = d["model"]
        version = d.get("model", version)
    usage = (d or {}).get("usage") or {}
    it, ot = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    answers = (d or {}).get("answers") or {}
    out = []
    for name in names:
        p, conf, pr = duplicate_of(answers.get(name)) if err is None else (float("nan"), float("nan"), None)
        e = err if err is not None else (None if pr is not None else f"no answer for {name}")
        out.append({"probs": {"same_change": p}, "confidence": {"same_change": conf}, "latency_ms": ms,
                    "input_tokens": it / k, "output_tokens": ot / k,
                    "cost_usd": (it * model.in_price + ot * model.out_price) / k, "model_version": version, "error": e,
                    "raw": {"question": name, "usage_group": usage, "group_size": k, **({"choice": {"same_change": pr}} if pr else {})}})
    return out


def predict_shared(args) -> dict:
    """`mergeone predict --qset pair_shared`: groups file in, k rows per group out (cli.open_predictions keeps every other
    question set's rows in the same file). Resumable with --only-missing-from (a group is done when all its rows are
    error-free)."""
    from .. import cli
    if args.single_question or args.compact or args.words or getattr(args, "prompt_file", None):
        raise SystemExit("--qset pair_shared takes none of --single-question, --compact, --words, --prompt-file (frozen request, full state)")
    groups = cli.load_dataset(args.split, args.limit, Path(args.dataset) if args.dataset else None)
    bad = [g.get("pr_number") for g in groups if "candidates" not in g or "a_state" not in g]
    if bad:
        raise SystemExit(f"{args.dataset}: {len(bad)} rows are not group rows (build them with `python -m mergeone.eval.run9_pairs groups`)")
    if args.only_errors_from or args.only_missing_from:
        src = Path(args.only_errors_from or args.only_missing_from)
        ok = Counter()
        if src.exists():
            for r in map(json.loads, src.open()):
                if r.get("qset") == "pair_shared" and r["decision"].get("error") is None:
                    ok[r.get("group_id")] += 1
        before = len(groups)
        groups = [g for g in groups if ok[g["group_id"]] < len(g["candidates"])]
        print(f"{len(groups)} groups not yet predicted (skipping {before - len(groups)})", flush=True)
    if args.subset:
        keep = {json.loads(l)["pr_number"] for l in Path(args.subset).open()}
        groups = [g for g in groups if g["pr_number"] in keep]
    spec = load_spec()
    model = cli.build_model(args.model, "pair")
    if not hasattr(model, "client") or not hasattr(model, "endpoint"):
        raise SystemExit(f"--qset pair_shared needs a System One model (prompts/models.yaml dialect systemone); {args.model} is not")
    run_dir = cli.RESULTS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / f"predictions_{args.model}_{args.split}_all.jsonl"
    fh, kept_other = cli.open_predictions(out_path, "pair_shared", "shared", args.append)
    if kept_other:
        print(f"{out_path}: keeping {kept_other} rows of other question sets / modes", flush=True)
    n_rows = n_err = n_req_err = 0
    t0 = time.time()
    started = datetime.now(timezone.utc).isoformat()
    with fh as f, ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(lambda g: (g, decide_group(model, g, spec)), g) for g in groups]
        for i, fut in enumerate(as_completed(futs), 1):
            g, decs = fut.result()
            n_req_err += int(any(d["error"] for d in decs))
            lines = []
            for slot, (c, d) in enumerate(zip(g["candidates"], decs), 1):
                n_rows += 1
                n_err += int(d["error"] is not None)
                lines.append(json.dumps({"pr_number": c["pr_number"], "split": g["split"], "labels": c.get("labels", {}), "decision": d,
                                         "qset": "pair_shared", "mode": "shared", "group_id": g["group_id"], "group_size": len(g["candidates"]),
                                         "slot": slot, "set": g.get("set"), "repo": g.get("repo"), "a": g.get("a"), "b": c.get("b"),
                                         "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
            cli._write_locked(f, "".join(lines))
            if i % 10 == 0 or i == len(groups):
                print(f"{args.model} {args.split} pair_shared: {i}/{len(groups)} groups, {n_rows} candidates, errors={n_err} "
                      f"elapsed={time.time()-t0:.0f}s", flush=True)
    wall = time.time() - t0
    meta = {"model": args.model, "split": args.split, "variant": "all", "qset": "pair_shared", "mode": "shared", "n": n_rows,
            "n_requests": len(groups), "errors": n_err, "request_errors": n_req_err, "concurrency": args.concurrency,
            "group_sizes": dict(sorted(Counter(len(g["candidates"]) for g in groups).items())),
            "model_version": getattr(model, "pinned", None) or getattr(model, "version", None), "prompt_file": str(PROMPT.relative_to(PROMPT.parents[1])),
            "prompt_sha256": hashlib.sha256(PROMPT.read_bytes()).hexdigest(), "dataset": args.dataset,
            "started_at": started, "wall_s": wall, "finished_at": datetime.now(timezone.utc).isoformat(), "host": platform.node(),
            "python": sys.version.split()[0], "predictions_file": out_path.name, "other_rows_kept": kept_other}
    (run_dir / f"meta_{args.model}_{args.split}_all_pair_shared.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return meta


# ----------------------------------------------------------------- groups
def chunks(xs: list, n: int = K) -> list[list]:
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def build_set_a(pairs: list[dict], keys: set[int], k: int = K) -> list[dict]:
    """R4-H2 test pairs (ids in `keys`) in run-4 file order, grouped by (repo, A), chunks of k."""
    by_a: OrderedDict[tuple, list[dict]] = OrderedDict()
    for p in pairs:
        if p["pr_number"] in keys:
            by_a.setdefault((p["repo"], p["a"]), []).append(p)
    out = []
    for (repo, a), ps in by_a.items():
        a_states = set()
        cands = []
        for rank, p in enumerate(ps, 1):
            a_state, b_state = split_pair_state(p["state"])
            a_states.add(a_state)
            cands.append({"pr_number": p["pr_number"], "b": p["b"], "kind": p.get("kind"), "order": rank, "labels": p["labels"], "b_state": b_state})
        if len(a_states) != 1:
            raise ValueError(f"{repo}#{a}: the pairs carry {len(a_states)} different A texts")
        a_state = a_states.pop()
        cs = chunks(cands, k)
        for j, cc in enumerate(cs):
            gid = group_id(repo, a, "a", j)
            out.append({"pr_number": gid, "group_id": gid, "set": "a", "split": ps[0]["split"], "repo": repo, "a": a, "chunk": j,
                        "n_chunks": len(cs), "group_size": len(cc), "a_state": a_state, "candidates": cc, "labels": {}, "features": {}})
    return out


def build_set_b(adjudicated: list[dict], cand_rows: list[dict], ds: dict, mem_lookup=None, k: int = K) -> tuple[list[dict], list[dict], dict]:
    """(groups, per-candidate pair rows, summary) for the adjudicated run-6B holds. `ds` maps (repo, number) → dataset row
    (A's state); `mem_lookup(repo, number)` → (meta, body, files) for an adjudicated B outside the k = 20 list (None → error)."""
    from .run4 import _render_b, pair_state
    cands = {(r["repo"], r["number"]): r for r in cand_rows}
    groups, pair_rows = [], []
    forced, from_memory = [], []
    for adj in adjudicated:
        repo, a, b = adj["repo"], adj["a_number"], adj["b_number"]
        cr = cands.get((repo, a))
        if cr is None:
            raise ValueError(f"{repo}#{a}: not in {CANDIDATES_K20}")
        pr = ds.get((repo, a))
        if pr is None:
            raise ValueError(f"{repo}#{a}: not in {DATASET}")
        bodies = cr.get("candidate_bodies") or {}
        top = [dict(c, retrieval_rank=i) for i, c in enumerate(cr["candidates"][:k], 1)]
        entries = [(c, bodies.get(str(c["number"])) or {}) for c in top]
        if b not in {c["number"] for c in top}:
            later = next(((i, c) for i, c in enumerate(cr["candidates"], 1) if c["number"] == b), None)
            if later is not None:
                c, cb = dict(later[1], retrieval_rank=later[0]), bodies.get(str(b)) or {}
            else:
                got = mem_lookup(repo, b) if mem_lookup else None
                if got is None:
                    raise ValueError(f"{repo}#{a}: adjudicated B #{b} is neither in its k=20 list nor in the PR memory")
                meta, body, files = got
                c, cb = dict(meta, retrieval_rank=None), {"body": body, "files": files}
                from_memory.append(f"{repo}#{a}~{b}")
            forced.append({"repo": repo, "a": a, "b": b, "replaced_rank": len(entries), "b_rank": c["retrieval_rank"]})
            entries = entries[: k - 1] + [(c, cb)]
        a_state = pr["state"]
        cc = []
        for slot, (c, cb) in enumerate(entries, 1):
            b_state = _render_b(c, cb.get("body", ""), cb.get("files", []))
            pid = pair_id(repo, a, c["number"])
            is_b = c["number"] == b
            labels = {"adjudicated": adj["label"] if is_b else None, "hold_b": is_b, "retrieval_rank": c.get("retrieval_rank"),
                      "a_split": pr.get("split")}
            kind = "retrieved" if c.get("retrieval_rank") is not None and c["retrieval_rank"] <= k else "adjudicated_b_forced"
            cc.append({"pr_number": pid, "b": c["number"], "kind": kind, "order": slot, "labels": labels, "b_state": b_state[:3000]})
            pair_rows.append({"pr_number": pid, "split": "test", "repo": repo, "a": a, "b": c["number"], "kind": kind, "labels": labels,
                              "features": {}, "state": pair_state(a_state, b_state)})
        gid = group_id(repo, a, "b", 0)
        groups.append({"pr_number": gid, "group_id": gid, "set": "b", "split": "test", "repo": repo, "a": a, "chunk": 0, "n_chunks": 1,
                       "group_size": len(cc), "a_state": a_state[:6000], "candidates": cc, "labels": {"adjudicated_b": b, "adjudicated": adj["label"]},
                       "features": {}})
    summary = {"groups": len(groups), "candidates": sum(len(g["candidates"]) for g in groups), "b_forced_into_slot_10": len(forced),
               "forced": forced, "b_from_memory": from_memory,
               "labels": dict(Counter(a["label"] for a in adjudicated))}
    return groups, pair_rows, summary


def _sizes(groups: list[dict]) -> dict:
    return {str(k): v for k, v in sorted(Counter(len(g["candidates"]) for g in groups).items())}


def cmd_groups(a) -> dict:
    root = Path(a.root)
    out: dict = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "prompt_sha256": hashlib.sha256(PROMPT.read_bytes()).hexdigest()}
    missing = [str(p) for p in (RUN4_PAIRS, PAIR_KEYS) if not (root / p).exists()]
    if missing:
        out["a"] = {"status": f"not built: {', '.join(missing)} not present (results/run4/pairs.jsonl is gitignored; copy it over)"}
    else:
        keys = {r["pr_number"] for r in _rows(root / PAIR_KEYS)}
        ga = build_set_a(_rows(root / RUN4_PAIRS), keys)
        _write(root / GROUPS["a"], ga)
        n = sum(len(g["candidates"]) for g in ga)
        out["a"] = {"status": "ok", "groups": len(ga), "candidates": n, "eval_pairs": len(keys), "missing_pairs": len(keys) - n,
                    "group_sizes": _sizes(ga), "a_split_into_chunks": sum(1 for g in ga if g["chunk"] == 1),
                    "positives": sum(bool(c["labels"].get("positive")) for g in ga for c in g["candidates"])}
    missing = [str(p) for p in (ADJUDICATED, CANDIDATES_K20, DATASET) if not (root / p).exists()]
    if missing:
        out["b"] = {"status": f"not built: {', '.join(missing)} not present (candidates_k20: `python -m mergeone.eval.run6b retrieve --k 20`)"}
    else:
        ds = {(r["repo"], r["number"]): r for r in _rows(root / DATASET)}
        mems = {}

        def mem_lookup(repo, number):
            if not (root / MEM_DIR).exists():
                return None
            if not mems:
                from ..memory.retrieval import load_all
                mems.update(load_all(root / MEM_DIR))
            m = (mems.get(repo).prs.get(number) if repo in mems else None)
            if m is None:
                return None
            from .run4 import _mem_meta
            return _mem_meta(m), m.body, sorted(m.files)

        gb, pb, sb = build_set_b(_rows(root / ADJUDICATED), _rows(root / CANDIDATES_K20), ds, mem_lookup)
        _write(root / GROUPS["b"], gb)
        _write(root / PAIRS_B, pb)
        out["b"] = {"status": "ok", **sb, "group_sizes": _sizes(gb)}
    (root / GROUPS_SUMMARY).parent.mkdir(parents=True, exist_ok=True)
    (root / GROUPS_SUMMARY).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({s: {k: v for k, v in out[s].items() if k != "forced"} for s in ("a", "b")}, indent=2))
    return out


# ----------------------------------------------------------------- scoring (used by run9_report)
def pair_rows(rows: list[dict] | None, qset: str) -> dict[int, dict]:
    """{pair id: row} for error-free rows of one qset (pair: joint rows; pair_shared: shared rows); last row wins."""
    mode = "shared" if qset == "pair_shared" else "joint"
    out = {}
    for r in rows or []:
        if r.get("qset") == qset and r.get("mode", "joint") == mode and r["decision"].get("error") is None:
            p = r["decision"]["probs"].get("same_change")
            if p is not None and p == p:
                out[r["pr_number"]] = r
    return out


def _p(r: dict) -> float:
    return float(r["decision"]["probs"]["same_change"])


def auc_block(rows: dict[int, dict], label) -> dict:
    """AUC of same_change vs label(row) ∈ {0, 1, None} over rows with a label; bootstrap CI (seed 0, 500)."""
    ks = [k for k, r in rows.items() if label(r) is not None]
    y = np.array([label(rows[k]) for k in ks], dtype=int)
    x = np.array([_p(rows[k]) for k in ks], dtype=float)
    out = {"n": len(ks), "positives": int(y.sum()) if len(ks) else 0}
    if not len(ks) or not (0 < y.sum() < len(y)):
        return {**out, "auc": None, "ci95": None, "status": "insufficient (one class or empty)"}
    return {**out, "auc": roc_auc(y, x), "ci95": list(bootstrap_ci(y, x, roc_auc)), "status": "ok"}


def label_positive(keys: set[int] | None):
    return lambda r: (int(bool(r["labels"].get("positive"))) if (keys is None or r["pr_number"] in keys) else None)


def label_duplicate(r: dict):
    lab = (r.get("labels") or {}).get("adjudicated")
    return 1 if lab in DUP_POS else (0 if lab in DUP_NEG else None)


def agreement(a: dict[int, dict], b: dict[int, dict]) -> dict:
    ks = sorted(set(a) & set(b))
    if not ks:
        return {"n": 0, "agreement": None, "mean_abs_dp": None, "p99_abs_dp": None, "max_abs_dp": None}
    x = np.array([_p(a[k]) for k in ks]); y = np.array([_p(b[k]) for k in ks])
    d = np.abs(x - y)
    return {"n": len(ks), "agreement": float(((x >= 0.5) == (y >= 0.5)).mean()), "mean_abs_dp": float(d.mean()),
            "p99_abs_dp": float(np.percentile(d, 99)), "max_abs_dp": float(d.max()), "mean_dp": float((x - y).mean())}


def speed_ratio(shared: dict | None, per_cand: dict | None) -> dict:
    s, p = (shared or {}).get("items_per_s"), (per_cand or {}).get("items_per_s")
    return {"shared_candidates_per_s": s, "per_candidate_candidates_per_s": p,
            "ratio": (s / p) if (s and p) else None}


def h4_verdict(speed: dict, auc_a: dict, auc_b: dict) -> dict:
    """§R9.3 R9-H4: (a) speed ratio ≥ 2.5; (b) set-(a) AUC and set-(b) duplicate-vs-rest AUC each ≥ per-candidate − 0.02."""
    def ge(x, bar):
        return None if x is None or bar is None else x >= bar - 1e-12
    r = speed.get("ratio")
    a_s, a_p = (auc_a.get("shared") or {}).get("auc"), (auc_a.get("per_candidate") or {}).get("auc")
    b_s, b_p = (auc_b.get("shared") or {}).get("auc"), (auc_b.get("per_candidate") or {}).get("auc")
    clauses = {"speed": ge(r, H4_SPEED), "auc_set_a": ge(a_s, None if a_p is None else a_p - H4_TOL),
               "dup_vs_rest_set_b": ge(b_s, None if b_p is None else b_p - H4_TOL)}
    vals = list(clauses.values())
    p = False if any(v is False for v in vals) else (None if any(v is None for v in vals) else True)
    return {"clauses": clauses, "pass": p, "speed_ratio": r, "auc_a_delta": None if a_s is None or a_p is None else a_s - a_p,
            "dup_b_delta": None if b_s is None or b_p is None else b_s - b_p}


def score(root: Path = Path(".")) -> dict:
    root = Path(root)
    rd = lambda p: _rows(root / p) if (root / p).exists() else None
    jd = lambda p: json.loads((root / p).read_text()) if (root / p).exists() else None
    keys_rows = rd(PAIR_KEYS)
    keys = {r["pr_number"] for r in keys_rows} if keys_rows is not None else None
    arms = {}
    for (arm, s), d in ARM_DIRS.items():
        f = d / f"predictions_{LOCAL}_test_all.jsonl"
        arms[(arm, s)] = {"rows": pair_rows(rd(f), QSET[arm]), "file": str(f), "throughput": jd(d / "throughput.json")}
    auc_a = {arm: (auc_block(arms[(arm, "a")]["rows"], label_positive(keys)) if arms[(arm, "a")]["rows"] else {"status": "not run yet", "auc": None})
             for arm in ("per_candidate", "shared")}
    if keys is None:
        auc_a = {arm: {"status": f"not evaluable: {PAIR_KEYS} not present", "auc": None} for arm in auc_a}
    auc_b = {arm: (auc_block(arms[(arm, "b")]["rows"], label_duplicate) if arms[(arm, "b")]["rows"] else {"status": "not run yet", "auc": None})
             for arm in ("per_candidate", "shared")}
    speed = speed_ratio(arms[("shared", "b")]["throughput"], arms[("per_candidate", "b")]["throughput"])
    agree = {s: agreement(arms[("shared", s)]["rows"], arms[("per_candidate", s)]["rows"]) for s in ("a", "b")}
    sizes = {s: dict(sorted(Counter(r.get("group_size") for r in arms[("shared", s)]["rows"].values()).items())) for s in ("a", "b")}
    return {"auc_set_a": auc_a, "dup_vs_rest_set_b": auc_b, "speed_set_b": speed, "agreement": agree, "shared_group_sizes": sizes,
            "throughput": {f"{arm}_{s}": arms[(arm, s)]["throughput"] for (arm, s) in arms}, "R9-H4": h4_verdict(speed, auc_a, auc_b)}


def cmd_score(a) -> dict:
    if not a.i_have_frozen_the_protocol:
        raise SystemExit("refusing to score: pass --i-have-frozen-the-protocol (PROTOCOL.md §R9 frozen and tagged r9-frozen before any run-9 call)")
    rep = score(Path(a.root))
    out = Path(a.root) / PAIRS_METRICS
    out.parent.mkdir(parents=True, exist_ok=True)
    rep["computed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out.write_text(json.dumps(rep, indent=2, default=float) + "\n")
    print(json.dumps({"R9-H4": rep["R9-H4"], "speed_set_b": rep["speed_set_b"]}, indent=2, default=float))
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run9_pairs", description="run 9 R9-H4: shared-A pair groups and scoring")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("groups", aliases=["build"], help="build results/run9/groups_{a,b}.jsonl and pairs_b.jsonl (no model call)")
    g.add_argument("--root", default=".")
    g.set_defaults(fn=cmd_groups)
    s = sub.add_parser("score", help="shared vs per-candidate → results/run9/pairs_metrics.json")
    s.add_argument("--root", default=".")
    s.add_argument("--i-have-frozen-the-protocol", action="store_true")
    s.set_defaults(fn=cmd_score)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
