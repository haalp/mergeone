"""Blind hand-labelling for the run2 judge audit.

    python3 -m mergeone.label            # resumes where you left off
    python3 -m mergeone.label --show 3   # re-show PR #3 of the sheet

For each PR: title, message, files, the first 4,000 chars of diff (same as the
candidates saw) plus a link to the full diff on GitHub. Five y/n questions.
Judge answers are never shown. Answers go straight into
results/run2/handcheck_v2.jsonl; quit any time with q.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "results" / "run2" / "handcheck_v2.jsonl"
DATASET = ROOT / "data" / "dataset.jsonl"

QUESTIONS = [
    ("is_logic_change", "Changes program BEHAVIOUR? (n if only docs/comments/format/rename/tests/generated/dep bump)"),
    ("weakens_tests", "WEAKENS tests? (removes tests, adds skip/Pending, loosens asserts, raises timeouts, marks flaky)"),
    ("message_matches_diff", "Title+message ACCURATELY describe the diff? (n if undisclosed changes or unfulfilled claims)"),
    ("touches_concurrency", "Touches CONCURRENCY-hazard code? (locks, goroutines, channels, caches, retries, shared state)"),
    ("needs_human_review", "Would a careful maintainer want a HUMAN to review before merge?"),
]


def load_sheet():
    return [json.loads(l) for l in SHEET.open()]


def save_sheet(rows):
    with SHEET.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def states():
    want = {r["pr_number"] for r in load_sheet()}
    out = {}
    with DATASET.open() as f:
        for line in f:
            if '"split": "test"' not in line:
                continue
            d = json.loads(line)
            if d["pr_number"] in want:
                out[d["pr_number"]] = d["state"]
                if len(out) == len(want):
                    break
    return out


def ask(prompt):
    while True:
        a = input(f"  {prompt} [y/n/q]: ").strip().lower()
        if a in ("y", "n"):
            return a == "y"
        if a == "q":
            raise KeyboardInterrupt


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, help="1-based index on the sheet to (re)label")
    a = ap.parse_args(argv)
    rows = load_sheet()
    st = states()
    todo = [i for i, r in enumerate(rows) if a.show is None and any(v is None for v in r["labels_v2"].values())] if a.show is None else [a.show - 1]
    print(f"{len(rows)} PRs on the sheet, {len(todo)} to label. Answer y/n, q to quit (progress is saved after each PR).\n")
    try:
        for n, i in enumerate(todo, 1):
            r = rows[i]
            print("=" * 100)
            print(f"[{n}/{len(todo)}]  PR #{r['pr_number']}   {r['url']}")
            print("=" * 100)
            print(st.get(r["pr_number"], "(state not found in data/dataset.jsonl; open the URL)"))
            print("-" * 100)
            for q, text in QUESTIONS:
                r["labels_v2"][q] = ask(text)
            note = input("  note (optional): ").strip()
            if note:
                r["note"] = note
            save_sheet(rows)
            print()
    except KeyboardInterrupt:
        save_sheet(rows)
        print("\nsaved. run again to continue.")
        return
    done = sum(1 for r in rows if all(v is not None for v in r["labels_v2"].values()))
    print(f"done: {done}/{len(rows)} labelled. Commit results/run2/handcheck_v2.jsonl when finished.")


if __name__ == "__main__":
    main()
