# Run 8 tooling

Commands for run 8, in order. Nothing below calls a model unless marked **key**; the one wrapper that would call a model
before the protocol exists refuses without `--i-have-frozen-the-protocol`. Run everything from the repository root.

**Rule: every cross-run comparison has a same-day full-state control in the same `--run` directory.** Run 7 found
provider-side drift (pplx-flash answers changed under one version string; jev moved 1.13.0 → 1.14.0), so a run-8 number is
never compared only with a number from an earlier run. Each treatment below is paired with a control call made on the
same day, by the same model, into the same directory; the earlier run's number is shown beside it, never instead of it.

## Prediction files: one per (model, split, variant), every question set kept

`mergeone predict` writes `results/<run>/predictions_<model>_<split>_<variant>.jsonl`, `<variant>` = `all` / `primary`
plus `_words` and `_compact`. The question set is not in the name (run 5's design; every reader filters on `qset` and
`mode`), so a v3/v4 run and a pair/relation run into one directory share a file. Since run 8 that cannot lose rows:
without `--append`, only the rows of the **same** question set and mode are replaced; rows of every other question set or
mode are kept (the run prints "keeping N rows of other question sets / modes", and the meta file records
`other_rows_kept`). With `--append`, nothing is replaced. The file is locked (flock) while it is rewritten and for each
row written, so two question sets running into one file at the same time are safe. Run 7's loss — the v3 `--compact`
rows overwritten by a later pair `--compact` run in `predictions_<model>_test_all_compact.jsonl` — cannot recur.
Filenames are unchanged, so every existing reader (run 4–7 reports, leaderboard) keeps working.

Meta files do carry the question set for every set but v1 / v2 / pair: `meta_<model>_<split>_<variant>[_<qset>][_single].json`
(v3 → `_v3`, v4 → `_v4`, relation → `_relation`, relation2 → `_relation2`), so a v3/v4 run and a pair run never share a
meta file either. Treatment (`_compact`) and full-state control (no suffix) are different variants, so they are different
files in any case.

Resume an interrupted call with `--append --only-missing-from <its predictions file>` (only rows of the same question set
and mode count as done).

## 0. Freeze

Manual: commit the run-8 protocol with `prompts/jev_v4.json`, `prompts/haiku_v4.md`, `prompts/jev_relation2.json`,
`prompts/haiku_relation2.md` and the relation2 class rule (`mergeone/eval/run8_relation.py` docstring: HIGH 0.5, LOW 0.25,
rule order). `prompts/jev_v4.json` is `prompts/jev_v3.json` with `follows_conventions` and `message_matches_diff` removed
and otherwise byte-identical (`tests/test_run8.py` checks it); `haiku_v4.md` is `haiku_v3.md` without those two items,
renumbered 1–8.

## 1. No-key preparation

```
python -m pytest -q
python -m mergeone.eval.run8_relation pairs        # → results/run8/relation_pairs.jsonl (run7_relation.build_pairs, same 100 pairs)
python -m mergeone.eval.run8_relation report       # baseline row only until relation2 predictions exist
```

The pair component needs `results/run4/pairs.jsonl` (gitignored): rebuild it with
`mergeone run4-retrieve && mergeone run4-pairs` (deterministic from `data/run4/memory/` and the committed
`data/run4/labels.jsonl`; results/run4/README.md) if it is not on disk.
`data/run3/dataset.jsonl` and `data/run4/memory/` must be on disk for the compact pair state (A from its full dataset state)
and for relation2's metadata (base branch, author, created_at).

## 2. v4 question set — `results/run8/v4/` — **key**

Treatment v4 and the same-day v3 control, both full state, same directory, same day (the two share
`predictions_<model>_test_all.jsonl`; rows are kept apart by `qset`):

```
mergeone predict --model jev        --qset v4 --split test --dataset data/run3/dataset.jsonl --run run8/v4
mergeone predict --model jev        --qset v3 --split test --dataset data/run3/dataset.jsonl --run run8/v4   # control
mergeone predict --model pplx-flash --qset v4 --split test --dataset data/run3/dataset.jsonl --run run8/v4
mergeone predict --model pplx-flash --qset v3 --split test --dataset data/run3/dataset.jsonl --run run8/v4   # control
```

Primary: `PRIMARY_V4 = needs_human_review`. Scoring helpers: `run7_report.latest(rows, "v4")` and
`run7_report.compare_v4(v4_rows, v3_control_rows, y, run5_metrics)` — per question and the mean over the eight v4
questions (v3 control rows answer those eight too, so the paired bootstrap pairs on them); `compare_v3` scores the
control on all ten.

## 3. Pair compact — `results/run8/pair/` — **key**

`--qset pair --compact` works end-to-end on run-4-format pair rows (checked on the 100 real `relation_pairs.jsonl` rows:
render source `pair:dataset_state`). Treatment and same-day full-state control:

```
mergeone predict --model <m> --qset pair --split test --dataset results/run4/pairs.jsonl --compact --run run8/pair   # → predictions_<m>_test_all_compact.jsonl
mergeone predict --model <m> --qset pair --split test --dataset results/run4/pairs.jsonl           --run run8/pair   # control → predictions_<m>_test_all.jsonl
```

for `<m>` in `jev`, `pplx-flash`. A v3 `--compact` run into the same directory (as run 7's C1 did) now keeps both
question sets in `predictions_<m>_test_all_compact.jsonl`.

## 4. relation2 — `results/run8/relation/` — **key**

```
python -m mergeone.eval.run8_relation predict --model pplx-flash --model jev --i-have-frozen-the-protocol
mergeone predict --model pplx-flash --qset relation --split test --dataset results/run8/relation_pairs.jsonl --run run8/relation   # control
mergeone predict --model jev        --qset relation --split test --dataset results/run8/relation_pairs.jsonl --run run8/relation   # control
python -m mergeone.eval.run8_relation report       # → results/run8/relation_metrics.json, results/run8/relation_tables.md
```

`predict` builds the pairs and runs `mergeone predict --qset relation2 --run run8/relation` per model (`--compact` for the
compact pair state). The control is run 7's four-question `relation` prompt asked again today into the same file.
`report` scores: relation2 (three probabilities + metadata `same_base`, `same_author`, `a_newer` → class by the frozen
rule); the same-day control and run 7's stored relation answers, both re-scored through the same rule with run 7's
`same_branch` answer dropped (the baseline row — pre-registered comparison point; run 7's answers give 0.722 pplx-flash,
0.740 jev); run 7's own post-hoc metadata-branch accuracy for reference. Per gold class accuracy with Wilson 95%
intervals, confusion, same_change-vs-rest AUC (gold duplicate; secondary: duplicate or backport), related-vs-unrelated
AUC, and metadata per gold class.

## What needs a key

| step | needs |
|---|---|
| v4 treatment + v3 control | `TYPESAFE_API_KEY` (jev), `PERPLEXITY_API_KEY` (pplx-flash) |
| pair compact + full control | the same two |
| relation2 predict + relation control | the same two (+ `ANTHROPIC_API_KEY` for `--model haiku`) |
| `pytest`, `pairs`, `report`, `run4-retrieve` / `run4-pairs` rebuild | nothing (the PR memory itself is rebuilt from GitHub with `GITHUB_TOKEN` if absent) |
