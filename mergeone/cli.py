"""Command line: prepare -> train-gbm -> predict -> report (-> loadtest, handcheck)."""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .data.extract import read_jsonl, MergedPR
from .data.features import structural_features, secondary_labels, render_state, split_chronological, SENSITIVE_PREFIXES, BLAST_FILES, BLAST_TOPDIRS
from .data.extract import REVERT_HORIZON_DAYS, DIFF_CHAR_LIMIT
from .models.base import DECISIONS, PRIMARY, DECISIONS_V2, PRIMARY_V2, DECISIONS_V3, PRIMARY_V3, QUESTION_SETS, Decision, decide_single

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESULTS = ROOT / "results"


# ----------------------------------------------------------------- dataset

def _dataset_rows(prs: list[MergedPR]) -> list[dict]:
    rows = []
    for p in prs:
        sec = secondary_labels(p)
        rows.append({
            "pr_number": p.pr_number,
            "merge_sha": p.merge_sha,
            "merged_at": p.merged_at,
            "labels": {PRIMARY: bool(p.reverted), **sec},
            "label_complete": p.label_complete,
            "is_revert_pr": p.is_revert_pr,
            "reverted_by_pr": p.reverted_by_pr,
            "days_to_revert": p.days_to_revert,
            "features": structural_features(p),
            "state": render_state(p),
            "state_words": render_state(p, numbers_as_words=True),
        })
    return rows


def cmd_prepare(args):
    prs = read_jsonl(DATA / "raw" / "k8s_merges.jsonl")
    # Revert PRs themselves are not decision targets (a revert is a response, not a change to judge)
    prs = [p for p in prs if not p.is_revert_pr]
    splits = split_chronological(prs)
    # Drop the incomplete-horizon tail from test so every test label is observed.
    test_full = [p for p in splits["test"] if p.label_complete]
    dropped = len(splits["test"]) - len(test_full)
    splits["test"] = test_full
    out = DATA / "dataset.jsonl"
    with out.open("w") as f:
        for name, group in splits.items():
            for row in _dataset_rows(group):
                row["split"] = name
                f.write(json.dumps(row) + "\n")
    manifest = {
        "repo": "kubernetes/kubernetes",
        "branch": "master",
        "source": "git history only (merge commits on first-parent chain)",
        "n_merged_prs_total": len(prs),
        "window_start": prs[0].merged_at,
        "window_end": prs[-1].merged_at,
        "first_merge_sha": prs[0].merge_sha,
        "last_merge_sha": prs[-1].merge_sha,
        "splits": {k: {"n": len(v), "start": v[0].merged_at if v else None, "end": v[-1].merged_at if v else None,
                       "positives": sum(1 for p in v if p.reverted)} for k, v in splits.items()},
        "test_dropped_incomplete_horizon": dropped,
        "label_rules": {
            "primary": f"reverted on master within {REVERT_HORIZON_DAYS} days by a merged PR titled Revert ... referencing the PR number, merge SHA, PR title or any of the PR's commit subjects; reverts of reverts excluded; revert PRs themselves excluded from the dataset",
            "touches_sensitive_area": list(SENSITIVE_PREFIXES),
            "large_blast_radius": f"> {BLAST_FILES} files and > {BLAST_TOPDIRS} top-level areas",
        },
        "diff_char_limit": DIFF_CHAR_LIMIT,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest["splits"], indent=2))
    print("dropped from test (incomplete horizon):", dropped)


def load_dataset(split: str | None = None, limit: int | None = None, path: Path | None = None) -> list[dict]:
    rows = [json.loads(l) for l in (path or DATA / "dataset.jsonl").open()]
    if split:
        rows = [r for r in rows if r["split"] == split]
    if limit:
        rows = rows[:limit]
    return rows


# ----------------------------------------------------------------- models

def load_prices() -> dict:
    p = RESULTS / "prices.json"
    return json.loads(p.read_text()) if p.exists() else {}


def build_model(name: str, version: str = "v1"):
    if name == "rules":
        from .models.rules import RulesModel
        return RulesModel()
    if name == "gbm":
        from .models.gbm import GBMModel
        return GBMModel(RESULTS / "gbm.pkl")
    from .models import registry
    try:
        return registry.build(name, load_prices(), version=version)
    except KeyError:
        raise SystemExit(f"unknown model {name!r}; known: {', '.join(registry.names())} (add one to prompts/models.yaml)")


def cmd_compact(args):
    """Rewrite prediction files keeping one row per (pr_number, qset, mode): the last non-error row if any, else the last row.

    Keyed by qset too because run 5 writes v3, pair and pair_assume rows into the same predictions file,
    and a pair_assume row has the same pr_number (hash of A~B) as the same_change row for that pair.
    Keyed by mode too because run 6 writes joint and single-question rows (R6A-H4) into one file; rows
    without `mode` (runs 1–5) are joint."""
    for f in args.files:
        f = Path(f); rows = {}; n = 0
        for l in f.open():
            n += 1; r = json.loads(l)
            k = (r["pr_number"], r.get("qset"), r.get("mode", "joint"))
            if k not in rows or r["decision"].get("error") is None or rows[k]["decision"].get("error") is not None:
                rows[k] = r
        f.write_text("".join(json.dumps(r) + "\n" for r in rows.values()))
        err = sum(1 for r in rows.values() if r["decision"].get("error"))
        print(f"{f}: {n} → {len(rows)} rows, {err} still errored")


def cmd_models(args):
    from .models import registry
    reg = registry.load_registry()
    print(f"{'name':14} {'dialect':10} {'model_id':28} {'$/M in':>7}  {'weights':34} key")
    for n in registry.BUILTIN:
        print(f"{n:14} {'builtin':10} {'-':28} {0:7.3f}  {'-':34} -")
    for s in reg.values():
        print(f"{s.name:14} {s.dialect:10} {s.model_id[:28]:28} {s.input_per_mtok:7.3f}  {(s.weights or '-')[:34]:34} {s.key_env}{' (optional)' if s.key_optional else ''}")


def cmd_train_gbm(args):
    from .models.gbm import GBMModel
    tr, va = load_dataset("train"), load_dataset("val")
    m = GBMModel()
    rep = m.fit([r["features"] for r in tr], {d: [int(r["labels"][d]) for r in tr] for d in DECISIONS},
                [r["features"] for r in va], {d: [int(r["labels"][d]) for r in va] for d in DECISIONS})
    m.save(RESULTS / "gbm.pkl")
    (RESULTS / "gbm_train_report.json").write_text(json.dumps(rep, indent=2))
    print(json.dumps(rep, indent=2))


def cmd_predict(args):
    pmode = "single" if getattr(args, "single_question", False) else "joint"
    rows = load_dataset(args.split, args.limit, Path(args.dataset) if args.dataset else None)
    if args.only_errors_from or args.only_missing_from:
        src = Path(args.only_errors_from or args.only_missing_from)
        # a row counts as done only for the same question set (run 5 mixes v3 / pair / pair_assume rows in one file)
        # and the same mode (run 6 mixes joint and single-question rows; rows without `mode` are joint)
        done = {r["pr_number"] for r in map(json.loads, src.open()) if r["decision"].get("error") is None and r.get("qset", args.qset) == args.qset
                and r.get("mode", "joint") == pmode} if src.exists() else set()
        rows = [r for r in rows if r["pr_number"] not in done]
        print(f"{len(rows)} rows not yet predicted (skipping {len(done)})", flush=True)
    if args.subset:
        keep = {json.loads(l)["pr_number"] for l in Path(args.subset).open()}
        rows = [r for r in rows if r["pr_number"] in keep]
    model = build_model(args.model, args.qset)
    if args.qset == "pair":
        questions = QUESTION_SETS["pair"]
    elif args.qset == "v2":
        questions = DECISIONS_V2 if args.questions == "all" else (PRIMARY_V2,)
    elif args.qset == "v3":
        questions = DECISIONS_V3 if args.questions == "all" else (PRIMARY_V3,)
    else:
        questions = DECISIONS if args.questions == "all" else (PRIMARY,)
    state_key = "state_words" if args.words else "state"
    run_dir = RESULTS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    variant = f"{args.questions}{'_words' if args.words else ''}"
    out_path = run_dir / f"predictions_{args.model}_{args.split}_{variant}.jsonl"
    mode = "a" if args.append else "w"
    t0 = time.time()
    n_err = 0

    def work(r):
        if pmode == "single":  # run 6 R6A-H4: one request per question, merged into one Decision
            return r, decide_single(model, r[state_key], r["features"], tuple(questions))
        return r, model.decide(r[state_key], r["features"], questions)

    with out_path.open(mode) as f, ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for i, fut in enumerate(as_completed(futs), 1):
            r, d = fut.result()
            if d.error:
                n_err += 1
            f.write(json.dumps({"pr_number": r["pr_number"], "split": r["split"], "labels": r["labels"], "decision": d.to_dict(),
                                "qset": args.qset, "mode": pmode, "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
            if i % 50 == 0 or i == len(rows):
                print(f"{args.model} {args.split} {variant}: {i}/{len(rows)} errors={n_err} elapsed={time.time()-t0:.0f}s", flush=True)
    meta = {"model": args.model, "split": args.split, "variant": variant, "n": len(rows), "errors": n_err,
            "concurrency": args.concurrency, "model_version": getattr(model, "pinned", None) or getattr(model, "version", None),
            "finished_at": datetime.now(timezone.utc).isoformat(), "host": platform.node(), "python": sys.version.split()[0]}
    meta["qset"] = args.qset
    meta["mode"] = pmode
    qsfx = "" if args.qset in ("v1", "v2", "pair") else f"_{args.qset}"  # run 5: v3/pair_assume share the predictions file with pair
    qsfx += "_single" if pmode == "single" else ""
    (run_dir / f"meta_{args.model}_{args.split}_{variant}{qsfx}.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))


def cmd_judge(args):
    """Label a stratified sample with the LLM judge (PROTOCOL R2.3). Writes results/<run>/judge_<split>.jsonl."""
    import anthropic
    rows = load_dataset(args.split)
    rng = random.Random(args.seed)
    # stratify: half with any 'test' path touched / half without, so weakens_tests has support
    with_t = [r for r in rows if r["features"]["test_ratio"] > 0]
    without = [r for r in rows if r["features"]["test_ratio"] == 0]
    k = args.n // 2
    sample = rng.sample(with_t, min(k, len(with_t))) + rng.sample(without, min(args.n - k, len(without)))
    rng.shuffle(sample)
    raw = {p.pr_number if hasattr(p, "pr_number") else p["pr_number"]: p for p in __import__("mergeone.data.extract", fromlist=["x"]).read_jsonl(DATA / "raw" / "k8s_merges.jsonl")}
    client = anthropic.Anthropic()
    prompt = (ROOT / "prompts" / "judge_v1.md").read_text()
    schema = {"type": "object", "properties": {q: {"type": "object", "properties": {"answer": {"type": "boolean"}, "evidence": {"type": "string"}},
              "required": ["answer", "evidence"], "additionalProperties": False} for q in DECISIONS_V2}, "required": list(DECISIONS_V2), "additionalProperties": False}
    run_dir = RESULTS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / f"judge_{args.split}{'_repeat' if args.repeat else ''}.jsonl"
    done = set()
    if out.exists() and not args.repeat:
        done = {json.loads(l)["pr_number"] for l in out.open()}
    from .data.features import render_state

    def work(r):
        pr = raw[r["pr_number"]]
        # judge sees up to 40k chars of diff: re-render with a long diff from git if needed
        state = render_state(pr, max_files_listed=200)
        if pr.diff_truncated:
            try:
                import subprocess
                full = subprocess.run(["git", "-C", args.repo, "diff", f"{pr.merge_sha}^1", pr.merge_sha, "--no-color"], capture_output=True, text=True, errors="replace").stdout
                state = state.split("DIFF (first")[0] + f"DIFF (first {min(len(full), 40000)} chars{', truncated' if len(full) > 40000 else ''}):\n" + full[:40000]
            except Exception:
                pass
        t0 = time.perf_counter()
        # temperature/top_p are rejected by the 5.5 models; self-agreement is measured by re-running at the same settings
        resp = client.messages.create(model=args.judge_model, max_tokens=2048, system=prompt,
                                      output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                                      messages=[{"role": "user", "content": state}])
        text = next(b.text for b in resp.content if b.type == "text")
        j = json.loads(text)
        return {"pr_number": r["pr_number"], "split": r["split"], "labels_v2": {q: bool(j[q]["answer"]) for q in DECISIONS_V2},
                "evidence": {q: j[q]["evidence"] for q in DECISIONS_V2}, "judge_model": resp.model, "repeat": bool(args.repeat),
                "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens, "latency_ms": (time.perf_counter() - t0) * 1000,
                "ts": datetime.now(timezone.utc).isoformat()}

    if args.repeat:
        keep = {json.loads(l)["pr_number"] for l in (run_dir / "_repeat_subset.jsonl").open()}
        sample = [r for r in rows if r["pr_number"] in keep]
    todo = [r for r in sample if r["pr_number"] not in done]
    with out.open("a") as f, ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for i, fut in enumerate(as_completed([ex.submit(work, r) for r in todo]), 1):
            try:
                f.write(json.dumps(fut.result()) + "\n")
            except Exception as e:
                print("judge error:", type(e).__name__, str(e)[:200], flush=True)
            if i % 25 == 0 or i == len(todo):
                print(f"judge {args.split}: {i}/{len(todo)}", flush=True)
    print("wrote", out)


def cmd_handcheck_v2(args):
    """Blind hand-label sheet: 60 judged test PRs, judge answers hidden."""
    rows = [json.loads(l) for l in (RESULTS / args.run / "judge_test.jsonl").open()]
    rng = random.Random(42)
    sample = rng.sample(rows, min(args.n, len(rows)))
    out = RESULTS / args.run / "handcheck_v2.jsonl"
    with out.open("w") as f:
        for r in sample:
            f.write(json.dumps({"pr_number": r["pr_number"], "url": f"https://github.com/kubernetes/kubernetes/pull/{r['pr_number']}/files",
                                "labels_v2": {q: None for q in DECISIONS_V2}, "note": ""}) + "\n")
    print(f"wrote {len(sample)} rows to {out}; fill labels_v2 with true/false WITHOUT looking at judge_test.jsonl")


def cmd_handcheck(args):
    rows = load_dataset()
    rng = random.Random(42)
    pos = [r for r in rows if r["labels"][PRIMARY]]
    neg = [r for r in rows if not r["labels"][PRIMARY] and r["label_complete"]]
    sample = rng.sample(pos, min(args.n, len(pos))) + rng.sample(neg, min(args.n, len(neg)))
    rng.shuffle(sample)
    out = RESULTS / "handcheck_sample.jsonl"
    with out.open("w") as f:
        for r in sample:
            f.write(json.dumps({"pr_number": r["pr_number"], "merge_sha": r["merge_sha"], "label": r["labels"][PRIMARY],
                                "reverted_by_pr": r["reverted_by_pr"], "days_to_revert": r["days_to_revert"],
                                "url": f"https://github.com/kubernetes/kubernetes/pull/{r['pr_number']}",
                                "revert_url": f"https://github.com/kubernetes/kubernetes/pull/{r['reverted_by_pr']}" if r["reverted_by_pr"] else None,
                                "verdict": None, "note": ""}) + "\n")
    print(f"wrote {len(sample)} rows to {out}; fill `verdict` with 'correct'/'wrong'/'unclear'")


def cmd_loadtest(args):
    """Open-loop replay of the test-split merge stream at a fixed decisions-per-second rate.

    Rate = base_rate * multiplier. base_rate defaults to the Anthropic post's
    30,000 merges/day = 0.347/s, so multiplier 1 is "Anthropic scale", 10 is
    300k/day, 100 is 3M/day. Requests are fired on schedule regardless of
    whether earlier ones finished (open loop), so queueing shows up in p99.
    """
    rows = load_dataset("test", args.limit, Path(args.dataset) if args.dataset else None)
    if args.no_retry:
        os.environ["MERGEONE_NO_RETRY"] = "1"
    # the HTTP pool must not be the bottleneck: one connection per worker thread (never below httpx's default 100)
    max_conn = max(100, args.concurrency)
    os.environ["MERGEONE_HTTP_MAX_CONNECTIONS"] = str(max_conn)
    model = build_model(args.model, args.qset)
    if args.no_retry and hasattr(model, "max_retries_429"):
        model.max_retries_429 = 0  # R5-H6: 429s count as errors
    questions = {"v2": DECISIONS_V2, "v3": DECISIONS_V3}.get(args.qset, (PRIMARY,))
    rate = args.base_rate * args.multiplier
    offsets = [i / rate for i in range(len(rows))]
    print(f"replaying {len(rows)} decisions at {rate:.2f}/s (base {args.base_rate:.3f}/s x {args.multiplier:g}), ~{offsets[-1]:.0f}s", flush=True)
    results = []
    t0 = time.perf_counter()

    def work(i):
        r = rows[i]
        wait = offsets[i] - (time.perf_counter() - t0)
        if wait > 0:
            time.sleep(wait)
        started = time.perf_counter() - t0
        d = model.decide(r["state"], r["features"], questions)
        return {"i": i, "scheduled_s": offsets[i], "started_s": started, "lag_s": started - offsets[i],
                "latency_ms": d.latency_ms, "error": d.error}

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for fut in as_completed([ex.submit(work, i) for i in range(len(rows))]):
            results.append(fut.result())
    lat = sorted(r["latency_ms"] for r in results if r["error"] is None)
    lag = sorted(r["lag_s"] for r in results)
    errs = sum(1 for r in results if r["error"])
    summary = {"model": args.model, "multiplier": args.multiplier, "base_rate_per_s": args.base_rate, "rate_per_s": rate, "n": len(rows),
               "p50_ms": lat[len(lat)//2] if lat else None, "p99_ms": lat[int(len(lat)*0.99)] if lat else None,
               "p99_schedule_lag_s": lag[int(len(lag)*0.99)], "error_rate": errs / len(rows), "concurrency": args.concurrency, "http_max_connections": max_conn,
               "qset": args.qset, "retry_429": getattr(model, "max_retries_429", None), "n_429": sum(1 for r in results if r["error"] and "429" in r["error"]),
               "wall_s": time.perf_counter() - t0, "finished_at": datetime.now(timezone.utc).isoformat()}
    run_dir = RESULTS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"loadtest_{args.model}_x{args.multiplier:g}.json").write_text(json.dumps({"summary": summary, "samples": results}, indent=1))
    print(json.dumps(summary, indent=2))


def cmd_prepare_run3(args):
    from .data.prepare_run3 import prepare
    m = prepare(Path(args.src), Path(args.repos), Path(args.out), Path(args.out).with_name("manifest.json"), args.per_repo_cap)
    for s_, v in m["splits"].items():
        print(s_, v["n"], "PRs,", len(v["repos"]), "repos,", v["agents"], "\n   ", v["label_counts"])


def cmd_close_reasons(args):
    """Classify why closed-unmerged agent PRs were closed (PROTOCOL R3.4: Claude Fable 5.1, reporting only).

    Never used as a label or as a candidate; the output only annotates the results tables."""
    import anthropic
    rows = [r for r in load_dataset("test", None, Path(args.dataset)) if r["labels"]["closed_unmerged"]]
    rng = random.Random(args.seed)
    rng.shuffle(rows)
    rows = rows[: args.n]
    reasons = ["quality_or_bugs", "superseded_or_duplicate", "scope_or_direction", "abandoned_or_stale", "ci_or_conflicts", "other"]
    schema = {"type": "object", "properties": {"reason": {"type": "string", "enum": reasons}, "evidence": {"type": "string"}},
              "required": ["reason", "evidence"], "additionalProperties": False}
    system = ("You classify why a pull request authored by a coding agent was closed without being merged. "
              "Use only the title, body and diff summary given. Pick exactly one reason: quality_or_bugs (the change was wrong, "
              "incomplete or low quality), superseded_or_duplicate (another PR did it), scope_or_direction (maintainers did not want "
              "this change), abandoned_or_stale (no follow-up), ci_or_conflicts (could not land mechanically), other.")
    client = anthropic.Anthropic()
    run_dir = RESULTS / args.run
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "close_reasons.jsonl"
    done = {json.loads(l)["pr_number"] for l in out.open()} if out.exists() else set()
    with out.open("a") as f:
        for i, r in enumerate(rows, 1):
            if r["pr_number"] in done:
                continue
            resp = client.messages.create(model=args.model, max_tokens=400, system=system,
                                          output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                                          messages=[{"role": "user", "content": r["state"][:12000]}])
            j = json.loads(next(b.text for b in resp.content if b.type == "text"))
            f.write(json.dumps({"pr_number": r["pr_number"], "repo": r["repo"], "number": r["number"], "agent": r["agent"], **j,
                                "classifier": args.model, "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
            f.flush()
            if i % 25 == 0:
                print(f"{i}/{len(rows)}", flush=True)
    print("wrote", out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mergeone")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").set_defaults(fn=cmd_prepare)
    p3 = sub.add_parser("prepare-run3", help="agent PRs (data/run3/agent_prs.jsonl) → data/run3/dataset.jsonl, repo-level split")
    p3.add_argument("--src", default="data/run3/agent_prs.jsonl")
    p3.add_argument("--repos", default="data/run3/repos.json")
    p3.add_argument("--out", default="data/run3/dataset.jsonl")
    p3.add_argument("--per-repo-cap", type=int)
    p3.set_defaults(fn=cmd_prepare_run3)
    for name, fn in (("run4-labels", "cmd_labels"), ("run4-retrieve", "cmd_retrieve"), ("run4-pairs", "cmd_pairs")):
        sub.add_parser(name, help=f"run 4: {name.split('-')[1]} (see mergeone/eval/run4.py)").set_defaults(fn=lambda a, fn=fn: getattr(__import__("mergeone.eval.run4", fromlist=["x"]), fn)(a))
    cr = sub.add_parser("close-reasons", help="classify close reasons on a test sample (reporting only)")
    cr.add_argument("--n", type=int, default=200)
    cr.add_argument("--seed", type=int, default=7)
    cr.add_argument("--run", default="run3")
    cr.add_argument("--dataset", default="data/run3/dataset.jsonl")
    cr.add_argument("--model", default="claude-fable-5-1")
    cr.set_defaults(fn=cmd_close_reasons)
    sub.add_parser("train-gbm").set_defaults(fn=cmd_train_gbm)
    p = sub.add_parser("predict")
    p.add_argument("--model", required=True, help="rules | gbm | any key in prompts/models.yaml (jev, pplx, pplx-flash, haiku, clef, local, …)")
    p.add_argument("--split", required=True, choices=["train", "val", "test"])
    p.add_argument("--run", default="run1")
    p.add_argument("--limit", type=int)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--questions", choices=["primary", "all"], default="all")
    p.add_argument("--words", action="store_true", help="numbers-as-words ablation state")
    p.add_argument("--append", action="store_true")
    p.add_argument("--only-errors-from")
    p.add_argument("--only-missing-from", help="predict only rows absent (or errored) in this predictions file; use with --append after the dataset grew")
    p.add_argument("--qset", choices=["v1", "v2", "v3", "pair", "pair_assume"], default="v1", help="question set: v1 = run1 (revert), v2 = run2 (semantic triage), v3 = run5 (v2 + 5), pair = run4 same_change, pair_assume = run5 assumption_conflict")
    p.add_argument("--subset", help="jsonl of {pr_number} rows to restrict to (e.g. the judged sample)")
    p.add_argument("--dataset", help="dataset path (default data/dataset.jsonl; run3: data/run3/dataset.jsonl)")
    p.add_argument("--single-question", action="store_true", help="run 6 R6A-H4: one request per question, answers merged into one row (mode=single); default joint")
    p.set_defaults(fn=cmd_predict)
    j = sub.add_parser("judge")
    j.add_argument("--split", required=True, choices=["val", "test"])
    j.add_argument("--n", type=int, required=True)
    j.add_argument("--run", default="run2")
    j.add_argument("--judge-model", default="claude-sonnet-5-5")
    j.add_argument("--repeat", action="store_true", help="re-judge the same sample into judge_<split>_repeat.jsonl (reliability)")
    j.add_argument("--seed", type=int, default=11)
    j.add_argument("--concurrency", type=int, default=6)
    j.add_argument("--repo", default="/home/claude/kubernetes/kubernetes")
    j.set_defaults(fn=cmd_judge)
    h2 = sub.add_parser("handcheck-v2")
    h2.add_argument("--n", type=int, default=60)
    h2.add_argument("--run", default="run2")
    h2.set_defaults(fn=cmd_handcheck_v2)
    h = sub.add_parser("handcheck")
    h.add_argument("--n", type=int, default=50)
    h.set_defaults(fn=cmd_handcheck)
    l = sub.add_parser("loadtest")
    l.add_argument("--model", required=True)
    l.add_argument("--multiplier", type=float, required=True)
    l.add_argument("--limit", type=int, default=600)
    l.add_argument("--base-rate", type=float, default=30000 / 86400, help="decisions/s at multiplier 1 (default: 30k/day)")
    l.add_argument("--concurrency", type=int, default=64, help="worker threads (= max in-flight requests incl. those sleeping until their slot); run 6 uses 512 so 300× (104/s) is not throttled")
    l.add_argument("--run", default="run1")
    l.add_argument("--dataset")
    l.add_argument("--qset", choices=["v1", "v2", "v3"], default="v1")
    l.add_argument("--no-retry", action="store_true", help="disable the System One adapter's 429 retry (also MERGEONE_NO_RETRY=1); run 5 R5-H6")
    l.set_defaults(fn=cmd_loadtest)
    r = sub.add_parser("report")
    r.add_argument("run_dir")
    r.add_argument("--v2", action="store_true", help="run2 report (judge labels, calibration)")
    r.add_argument("--run3", action="store_true", help="run3 report (agent PRs, outcome labels)")
    r.add_argument("--run4", action="store_true", help="run4 report (PR memory, duplicate detection)")
    r.add_argument("--run5", action="store_true", help="run5 report, component C (v3 questions → review iteration, R5-H3/H5/H6)")
    r.add_argument("--run6", action="store_true", help="run6 part A report (self-hosted pplx-flash: R6A-H1..H4, cost row)")
    r.set_defaults(fn=lambda a: __import__("mergeone.eval.run6" if a.run6 else "mergeone.eval.run5" if a.run5 else "mergeone.eval.run4" if a.run4 else "mergeone.eval.report_run3" if a.run3 else ("mergeone.eval.report_v2" if a.v2 else "mergeone.eval.report"), fromlist=["x"]).run(Path(a.run_dir)))
    cp = sub.add_parser("compact", help="dedupe prediction files after --append re-runs (keep last non-error row per PR)")
    cp.add_argument("files", nargs="+"); cp.set_defaults(fn=cmd_compact)
    sub.add_parser("models", help="list the decision-model registry (prompts/models.yaml)").set_defaults(fn=cmd_models)
    lb = sub.add_parser("leaderboard", help="rank every model that has been run under PROTOCOL.md on gateway-relevant metrics → results/leaderboard.md")
    lb.set_defaults(fn=lambda a: __import__("mergeone.eval.leaderboard", fromlist=["x"]).run())
    sub.add_parser("serve", help="run the gateway server; all further args go to mergeone.server.app")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        return __import__("mergeone.server.app", fromlist=["x"]).main(argv[1:])
    sub.add_parser("mcp", help="run as an MCP server (stdio); all further args go to mergeone.server.mcp_server")
    if argv and argv[0] == "mcp":
        return __import__("mergeone.server.mcp_server", fromlist=["x"]).main(argv[1:])
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
