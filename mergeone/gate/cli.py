"""`gate` command line.

  gate decide --policy .mergeone.yaml --state state.txt [--paths a.go b.go]   # one decision, JSON out
  gate pr --policy .mergeone.yaml --repo . --base main --head HEAD             # build state from git, decide
  gate replay --policy .mergeone.yaml --dataset data/dataset.jsonl --n 200     # dry-run a policy on history
  gate fit-calibration --run results/run2 --model jev --qset v2                 # write calibration_<model>_<qset>.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .engine import Gate


def _state_from_git(repo: str, base: str, head: str) -> tuple[str, list[str]]:
    from ..data.extract import FileStat, MergedPR
    from ..data.features import render_state
    from datetime import datetime, timezone

    def git(*a):
        return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, errors="replace", check=True).stdout

    title = git("log", "-1", "--format=%s", head).strip()
    body = git("log", "-1", "--format=%b", head)
    files = []
    for line in git("diff", "--numstat", f"{base}...{head}").splitlines():
        ins, dels, path = line.split("\t")
        files.append(FileStat(path, int(ins) if ins != "-" else 0, int(dels) if dels != "-" else 0))
    diff = git("diff", f"{base}...{head}", "--no-color")
    pr = MergedPR(0, head, datetime.now(timezone.utc).isoformat(), title, body, "", files, diff[:4000], len(diff) > 4000, False)
    return render_state(pr), [f.path for f in files]


def cmd_decide(a):
    gate = Gate.from_policy(a.policy, a.log)
    state = Path(a.state).read_text()
    res = gate.decide(state, a.paths or [], context={"source": "cli"})
    print(json.dumps(res.to_dict(), indent=2))
    sys.exit(0 if res.verdict == "pass" else (2 if res.verdict == "block" else 1))


def cmd_pr(a):
    gate = Gate.from_policy(a.policy, a.log)
    state, paths = _state_from_git(a.repo, a.base, a.head)
    res = gate.decide(state, paths, context={"source": "git", "repo": a.repo, "base": a.base, "head": a.head})
    print(json.dumps(res.to_dict(), indent=2))
    sys.exit(0 if res.verdict == "pass" else (2 if res.verdict == "block" else 1))


def cmd_replay(a):
    """Dry-run a policy on dataset rows using saved predictions (no API calls)."""
    from collections import Counter
    preds = {}
    for l in Path(a.predictions).open():
        r = json.loads(l)
        preds[r["pr_number"]] = r["decision"]["probs"]
    policy = __import__("yaml").safe_load(Path(a.policy).read_text())
    from .engine import Calibrator, Gate
    g = Gate(policy, model=None, calibrator=Calibrator.load(a.calibration), log_path=None)
    c = Counter()
    for pr, probs in list(preds.items())[: a.n or None]:
        rule, verdict, route, _ = g._match(g.cal(probs))
        c[(verdict, route, rule)] += 1
    tot = sum(c.values())
    print(f"{tot} PRs")
    for (v, r, rule), n in c.most_common():
        print(f"  {n:6d}  {n/tot:5.1%}  {v:5s}  {r:22s}  {rule}")


def cmd_fit_calibration(a):
    """Fit isotonic maps on the run's val predictions vs judge labels; save as piecewise-linear tables."""
    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from ..models.base import DECISIONS_V2
    run = Path(a.run)
    labels = {json.loads(l)["pr_number"]: json.loads(l)["labels_v2"] for l in (run / "judge_val.jsonl").open()}
    preds = {}
    for l in (run / f"predictions_{a.model}_val_all.jsonl").open():
        r = json.loads(l)
        if r.get("qset") == a.qset and r["decision"]["error"] is None:
            preds[r["pr_number"]] = r["decision"]["probs"]
    keys = sorted(set(labels) & set(preds))
    maps = {}
    for q in DECISIONS_V2:
        y = np.array([int(labels[k][q]) for k in keys]); p = np.array([preds[k][q] for k in keys])
        if y.sum() == 0 or y.sum() == len(y):
            continue
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(p, y)
        xs = np.linspace(0, 1, 101)
        maps[q] = {"x": xs.tolist(), "y": iso.predict(xs).tolist(), "n": int(len(y)), "positives": int(y.sum())}
    out = Path(__file__).with_name(f"calibration_{a.model}_{a.qset}.json")
    out.write_text(json.dumps(maps, indent=1))
    print("wrote", out, "questions:", list(maps))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide"); d.add_argument("--policy", required=True); d.add_argument("--state", required=True); d.add_argument("--paths", nargs="*"); d.add_argument("--log", default=".mergeone/gatelog.jsonl"); d.set_defaults(fn=cmd_decide)
    p = sub.add_parser("pr"); p.add_argument("--policy", required=True); p.add_argument("--repo", default="."); p.add_argument("--base", default="main"); p.add_argument("--head", default="HEAD"); p.add_argument("--log", default=".mergeone/gatelog.jsonl"); p.set_defaults(fn=cmd_pr)
    r = sub.add_parser("replay"); r.add_argument("--policy", required=True); r.add_argument("--predictions", required=True); r.add_argument("--calibration"); r.add_argument("--n", type=int); r.set_defaults(fn=cmd_replay)
    f = sub.add_parser("fit-calibration"); f.add_argument("--run", required=True); f.add_argument("--model", default="jev"); f.add_argument("--qset", default="v2"); f.set_defaults(fn=cmd_fit_calibration)
    a = ap.parse_args(argv); a.fn(a)


if __name__ == "__main__":
    main()
