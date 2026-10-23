"""Run 7 C2: prompt tuning on validation only, frozen before test (docs/run7-addendum-draft.md R7.2 C2, criterion R7-H2).
No model is called here; every model call goes through `mergeone.cli predict`, this module draws, records and freezes.

  python -m mergeone.eval.run7_tune draw
        tuning set: at most 400 val PRs of data/run3/dataset.jsonl, seed 7, stratified on the run-5 primary target
        (`iterated`, proportional allocation, largest remainder — run6b.allocate) → data/run7/tuning_set.jsonl
        {pr_number, repo, number, stratum, drawn_at}. Drawn once: an existing file is kept if identical (drawn_at
        aside) and the command refuses if it differs.

  per round r (0 = the untuned prompt, prompts/jev_v3.json; 1..5 = edits), the operator runs, with the round's prompt P:
        mergeone predict --model pplx-flash --qset v3 --split val --dataset data/run3/dataset.jsonl \\
            --subset data/run7/tuning_set.jsonl --prompt-file P --run run7/tune_r<r>                        (tuning)
        mergeone predict … --split val --dataset data/run3/dataset.jsonl --prompt-file P --run run7/tune_r<r>_iter
            (iteration eval; the tuning PRs are dropped when scoring)
        mergeone predict … --split val --prompt-file P --run run7/tune_r<r>_revert   (k8s data/dataset.jsonl, revert eval)
  python -m mergeone.eval.run7_tune round --round r --prompt P --tuning-preds … [--iteration-preds …] [--revert-preds …]
        records into data/run7/tuning_log.jsonl: prompt sha256, val AUC per question vs `iterated` on the tuning set,
        the primary (needs_human_review vs iterated), the eval scores, and the keep/reject decision.
  python -m mergeone.eval.run7_tune freeze
        → data/run7/frozen.json {round, prompt_sha256, …} for the prompt in force; prints the tag to create: r7-tuned.

Rules (R7.2 C2), enforced by `round`:
  * round numbers 0..5, each recorded once, in order (round r needs r − 1); round 0 is the untuned prompt and is in force;
  * a prompt whose sha256 is already in the log is refused (no re-recording, no resubmitting an earlier prompt);
  * every prediction row must be split val, qset v3, and carry prompt_sha256 equal to the prompt file's sha256
    (i.e. made with `predict --prompt-file`); tuning rows must all be tuning-set PRs; any test row is refused;
  * keep iff primary val AUC > the prompt in force's AND eval_score ≥ in force's eval_score − 0.01, where
    eval_score = mean of the available revert AUC (needs_human_review vs will_be_reverted, k8s val) and iteration AUC
    (needs_human_review vs iterated, run-3 val PRs outside the tuning set); a kept round becomes the prompt in force;
  * stop: after two consecutive rejected rounds, or after round 5, no further round is recorded;
  * nothing is recorded after data/run7/frozen.json exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

from ..models.base import DECISIONS_V3, PRIMARY_V3
from .run6b import _rows, _write, allocate

DATASET = Path("data/run3/dataset.jsonl")
DATA_DIR = Path("data/run7")
TUNING_N, TUNING_SEED = 400, 7
TARGET = "iterated"            # run-5 primary target (R5-H5)
REVERT_LABEL = "will_be_reverted"
MAX_ROUND = 5
EVAL_TOLERANCE = 0.01
TAG = "r7-tuned"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ----------------------------------------------------------------- draw
def draw_tuning(ds_rows: list[dict], n: int = TUNING_N, seed: int = TUNING_SEED) -> list[dict]:
    """Deterministic: val rows sorted by pr_number, strata by `iterated`, proportional allocation, random.Random(seed)
    samples each stratum (iterated first), then one shuffle. Rows {pr_number, repo, number, stratum}."""
    val = sorted((r for r in ds_rows if r["split"] == "val"), key=lambda r: r["pr_number"])
    pops = {"iterated": [r for r in val if r["labels"][TARGET]], "not_iterated": [r for r in val if not r["labels"][TARGET]]}
    alloc = allocate({s: len(v) for s, v in pops.items()}, n)
    rng = random.Random(seed)
    picked = []
    for s in ("iterated", "not_iterated"):
        picked += [(s, r) for r in rng.sample(pops[s], alloc[s])]
    rng.shuffle(picked)
    return [{"pr_number": r["pr_number"], "repo": r["repo"], "number": r["number"], "stratum": s} for s, r in picked]


def cmd_draw(a) -> None:
    rows = draw_tuning(_rows(DATASET), a.n, a.seed)
    out = DATA_DIR / "tuning_set.jsonl"
    if out.exists():
        old = [{k: v for k, v in r.items() if k != "drawn_at"} for r in _rows(out)]
        if old == rows:
            print(f"{out} exists and is identical to this draw; unchanged")
            return
        raise SystemExit(f"REFUSING: {out} exists and differs from this draw (the tuning set is drawn once, R7.2 C2)")
    ts = _now()
    _write(out, ({**r, "drawn_at": ts} for r in rows))
    print(json.dumps({"wrote": str(out), "n": len(rows), "seed": a.seed, "drawn_at": ts,
                      "strata": {s: sum(r["stratum"] == s for r in rows) for s in ("iterated", "not_iterated")}}, indent=2))


# ----------------------------------------------------------------- round
def read_log(path: Path | None = None) -> list[dict]:
    p = Path(path or DATA_DIR / "tuning_log.jsonl")
    return _rows(p) if p.exists() else []


def in_force(log: list[dict]) -> dict | None:
    """The last kept round (round 0 counts as kept)."""
    kept = [r for r in log if r["kept"]]
    return kept[-1] if kept else None


def stopped(log: list[dict]) -> str | None:
    if log and log[-1]["round"] >= MAX_ROUND:
        return f"round {MAX_ROUND} recorded"
    if len(log) >= 2 and not log[-1]["kept"] and not log[-2]["kept"] and log[-2]["round"] > 0:
        return "two consecutive rounds rejected"
    return None


def _check_rows(rows: list[dict], sha: str, what: str, allowed: set | None = None) -> list[dict]:
    if not rows:
        raise SystemExit(f"REFUSING: {what}: no rows")
    for r in rows:
        if r.get("split") != "val":
            raise SystemExit(f"REFUSING: {what}: row {r['pr_number']} is split {r.get('split')!r} (val only, R7.4)")
        if r.get("qset") != "v3":
            raise SystemExit(f"REFUSING: {what}: row {r['pr_number']} is qset {r.get('qset')!r}, not v3")
        if r.get("prompt_sha256") != sha:
            raise SystemExit(f"REFUSING: {what}: row {r['pr_number']} has prompt_sha256 {r.get('prompt_sha256')!r}, the prompt file is {sha} "
                             "(predict with --prompt-file <this prompt>)")
        if allowed is not None and r["pr_number"] not in allowed:
            raise SystemExit(f"REFUSING: {what}: row {r['pr_number']} is not in the tuning set")
    last = {}
    for r in rows:  # last row per PR wins (as run5._load); errored rows count as missing
        last[r["pr_number"]] = r
    return [r for r in last.values() if r["decision"].get("error") is None]


def _auc(rows: list[dict], q: str, label: str) -> float | None:
    from .run7_evidence import auc
    ok = [r for r in rows if (r["decision"]["probs"].get(q) is not None and r["decision"]["probs"][q] == r["decision"]["probs"][q])]
    return auc([int(bool(r["labels"][label])) for r in ok], [float(r["decision"]["probs"][q]) for r in ok])


def score_round(tuning: list[dict], iteration: list[dict] | None, revert: list[dict] | None, tuning_ids: set) -> dict:
    per_q = {q: _auc(tuning, q, TARGET) for q in DECISIONS_V3}
    vals = [v for v in per_q.values() if v is not None]
    it = [r for r in (iteration or []) if r["pr_number"] not in tuning_ids]
    it_auc = _auc(it, PRIMARY_V3, TARGET) if iteration is not None else None
    rv_auc = _auc(revert, PRIMARY_V3, REVERT_LABEL) if revert is not None else None
    ev = [v for v in (rv_auc, it_auc) if v is not None]
    return {"val_auc": per_q, "primary_val_auc": per_q[PRIMARY_V3], "mean_val_auc": sum(vals) / len(vals) if vals else None,
            "n_tuning": len(tuning), "n_iteration_eval": len(it) if iteration is not None else None,
            "n_revert_eval": len(revert) if revert is not None else None,
            "revert_auc": rv_auc, "iteration_auc": it_auc, "eval_score": sum(ev) / len(ev) if ev else None}


def decide(entry: dict, force: dict | None) -> tuple[bool, str]:
    if entry["round"] == 0:
        return True, "round 0: the untuned prompt, in force by definition"
    p, pf = entry["primary_val_auc"], force["primary_val_auc"]
    if p is None or pf is None or not p > pf:
        return False, f"primary val AUC {p} does not rise above the prompt in force (round {force['round']}: {pf})"
    e, ef = entry["eval_score"], force["eval_score"]
    if ef is not None and (e is None or e < ef - EVAL_TOLERANCE):
        return False, f"revert/iteration eval {e} drops more than {EVAL_TOLERANCE} below round {force['round']} ({ef})"
    return True, f"primary val AUC {p:.4f} > {pf:.4f}; eval {e} within {EVAL_TOLERANCE} of {ef}"


def record_round(rnd: int, prompt: Path, tuning_rows: list[dict], iteration_rows: list[dict] | None = None,
                 revert_rows: list[dict] | None = None, data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir or DATA_DIR)
    if (data_dir / "frozen.json").exists():
        raise SystemExit(f"REFUSING: {data_dir / 'frozen.json'} exists; the prompt is frozen (tag {TAG})")
    ts_path = data_dir / "tuning_set.jsonl"
    if not ts_path.exists():
        raise SystemExit(f"REFUSING: {ts_path} not found (run `draw` first)")
    tuning_ids = {r["pr_number"] for r in _rows(ts_path)}
    log_path = data_dir / "tuning_log.jsonl"
    log = read_log(log_path)
    sha = sha256_file(prompt)
    if any(r["prompt_sha256"] == sha for r in log):
        prev = next(r for r in log if r["prompt_sha256"] == sha)
        raise SystemExit(f"REFUSING: a round with prompt sha256 {sha} is already recorded (round {prev['round']})")
    if any(r["round"] == rnd for r in log):
        raise SystemExit(f"REFUSING: round {rnd} is already recorded")
    expected = (log[-1]["round"] + 1) if log else 0
    if rnd != expected:
        raise SystemExit(f"REFUSING: next round is {expected}, got {rnd}")
    if not 0 <= rnd <= MAX_ROUND:
        raise SystemExit(f"REFUSING: rounds are 0..{MAX_ROUND}")
    why = stopped(log)
    if why:
        raise SystemExit(f"REFUSING: tuning has stopped ({why}); run `freeze`")
    tun = _check_rows(tuning_rows, sha, "tuning predictions", tuning_ids)
    it = _check_rows(iteration_rows, sha, "iteration-eval predictions") if iteration_rows is not None else None
    rv = _check_rows(revert_rows, sha, "revert-eval predictions") if revert_rows is not None else None
    entry = {"round": rnd, "prompt_path": str(prompt), "prompt_sha256": sha, **score_round(tun, it, rv, tuning_ids), "recorded_at": _now()}
    entry["n_tuning_missing"] = len(tuning_ids) - len({r["pr_number"] for r in tun})
    force = in_force(log)
    entry["kept"], entry["decision"] = decide(entry, force)
    entry["in_force_before"] = force["round"] if force else None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def cmd_round(a) -> None:
    e = record_round(a.round, Path(a.prompt), _rows(a.tuning_preds), _rows(a.iteration_preds) if a.iteration_preds else None,
                     _rows(a.revert_preds) if a.revert_preds else None)
    print(json.dumps(e, indent=2))
    why = stopped(read_log())
    if why:
        print(f"tuning stopped ({why}); next: python -m mergeone.eval.run7_tune freeze")


# ----------------------------------------------------------------- freeze
def freeze(data_dir: Path | None = None) -> dict:
    data_dir = Path(data_dir or DATA_DIR)
    log_path = data_dir / "tuning_log.jsonl"
    log = read_log(log_path)
    force = in_force(log)
    if force is None:
        raise SystemExit("REFUSING: no round recorded (round 0 = the untuned prompt comes first)")
    out = data_dir / "frozen.json"
    fz = {"round": force["round"], "prompt_path": force["prompt_path"], "prompt_sha256": force["prompt_sha256"],
          "primary_val_auc": force["primary_val_auc"], "eval_score": force["eval_score"], "rounds_recorded": len(log),
          "stopped": stopped(log) or "frozen early by the operator", "tuning_log_sha256": sha256_file(log_path), "tag": TAG}
    if out.exists():
        old = json.loads(out.read_text())
        if {k: v for k, v in old.items() if k != "frozen_at"} == fz:
            print(f"{out} exists and matches; unchanged")
            return old
        raise SystemExit(f"REFUSING: {out} exists and differs (any edit after the {TAG} tag is a protocol break, R7-H2)")
    fz["frozen_at"] = _now()
    if Path(fz["prompt_path"]).exists() and sha256_file(Path(fz["prompt_path"])) != fz["prompt_sha256"]:
        raise SystemExit(f"REFUSING: {fz['prompt_path']} no longer has sha256 {fz['prompt_sha256']} (edited after its round)")
    out.write_text(json.dumps(fz, indent=2))
    return fz


def cmd_freeze(a) -> None:
    fz = freeze()
    print(json.dumps(fz, indent=2))
    print(f"\nnext: commit {DATA_DIR}/frozen.json, {DATA_DIR}/tuning_log.jsonl and {fz['prompt_path']}, then\n"
          f"  git tag {TAG}\nNo test-split call before this tag (R7.4).")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run7_tune")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("draw", help="draw the tuning set once → data/run7/tuning_set.jsonl")
    s.add_argument("--n", type=int, default=TUNING_N); s.add_argument("--seed", type=int, default=TUNING_SEED); s.set_defaults(fn=cmd_draw)
    s = sub.add_parser("round", help="record one tuning round from existing prediction files (no model call)")
    s.add_argument("--round", type=int, required=True); s.add_argument("--prompt", required=True)
    s.add_argument("--tuning-preds", required=True, help="predictions on the tuning set (predict --subset data/run7/tuning_set.jsonl --prompt-file …)")
    s.add_argument("--iteration-preds", help="v3 predictions on run-3 val (tuning PRs are dropped when scoring)")
    s.add_argument("--revert-preds", help="v3 predictions on the k8s val slice (data/dataset.jsonl)")
    s.set_defaults(fn=cmd_round)
    sub.add_parser("freeze", help="write data/run7/frozen.json for the prompt in force; prints the r7-tuned tag").set_defaults(fn=cmd_freeze)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
