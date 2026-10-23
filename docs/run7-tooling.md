# Run 7 tooling

Commands for docs/run7-addendum-draft.md, in the order of R7.5. Nothing below calls a model unless marked **key**, and
the two commands that would score or call before the protocol exists refuse without `--i-have-frozen-the-protocol`.
Run everything from the repository root.

## 1. Commit the addendum as PROTOCOL.md §R7

Manual. Freeze with it: the C3 pattern lists (`mergeone/eval/run7_evidence.py`, every constant marked
"frozen before scoring (R7.4)"), the C5 prompts (`prompts/jev_relation.json`, `prompts/haiku_relation.md`), the C5 class
rule and gold-note rule (`mergeone/eval/run7_relation.py` docstring, `GOLD_KEYWORDS`), and the C2 keep rule
(`mergeone/eval/run7_tune.py` docstring).

## 2. Val-only work: C3 features, C2 tuning set and rounds

```
python -m mergeone.eval.run7_evidence features            # → results/run7/evidence_features.jsonl (no labels, patterns sha256 per row)
python -m mergeone.eval.run7_tune draw                    # → data/run7/tuning_set.jsonl (≤ 400 val PRs, seed 7, stratified on `iterated`); commit it
```

Each tuning round r (0 = untuned `prompts/jev_v3.json`, then up to 5 edits) — **key** (`PERPLEXITY_API_KEY`):

```
P=prompts/jev_v3_r<r>.json                                # round 0: prompts/jev_v3.json
mergeone predict --model pplx-flash --qset v3 --split val --dataset data/run3/dataset.jsonl \
    --subset data/run7/tuning_set.jsonl --prompt-file $P --run run7/tune_r<r>                  # tuning set
mergeone predict --model pplx-flash --qset v3 --split val --dataset data/run3/dataset.jsonl \
    --prompt-file $P --run run7/tune_r<r>_iter                                                  # iteration eval (tuning PRs dropped when scored)
mergeone predict --model pplx-flash --qset v3 --split val --prompt-file $P --run run7/tune_r<r>_revert   # k8s revert eval
python -m mergeone.eval.run7_tune round --round <r> --prompt $P \
    --tuning-preds results/run7/tune_r<r>/predictions_pplx-flash_val_all.jsonl \
    --iteration-preds results/run7/tune_r<r>_iter/predictions_pplx-flash_val_all.jsonl \
    --revert-preds results/run7/tune_r<r>_revert/predictions_pplx-flash_val_all.jsonl
```

`round` only records: prompt sha256, val AUC per v3 question vs `iterated` on the tuning set, the primary
(`needs_human_review` vs `iterated`), the revert and iteration AUCs, and keep/reject → `data/run7/tuning_log.jsonl`. It refuses
a prompt sha256 already in the log, a round out of order, rows not made with `--prompt-file` of that prompt, any non-val
row, tuning rows outside the tuning set, and any round after two consecutive rejections, after round 5 or after freezing.
Keep = primary val AUC rises over the prompt in force **and** the eval score (mean of revert and iteration AUC) drops
≤ 0.01. `--prompt-file` writes `prompt_sha256` into every prediction row and the meta file.

## 3. Freeze and tag

```
python -m mergeone.eval.run7_tune freeze                   # → data/run7/frozen.json (prompt in force); prints `git tag r7-tuned`
```

Commit `frozen.json`, `tuning_log.jsonl` and the prompt, then tag `r7-tuned`. No test-split call before the tag.

## 4. Test-split work

**C1 compact state** — **key** (`TYPESAFE_API_KEY`, `PERPLEXITY_API_KEY`):

```
mergeone predict --model jev --qset v3 --split test --dataset data/run3/dataset.jsonl --compact --run run7/c1
mergeone predict --model pplx-flash --qset v3 --split test --dataset data/run3/dataset.jsonl --compact --run run7/c1
mergeone predict --model <m> --qset pair --split test --dataset results/run6/pairs.jsonl --compact --run run7/c1   # pairs.jsonl: run6b pairs (rebuildable)
```

`--compact` writes `predictions_<model>_<split>_all_compact.jsonl` (never the full-state file) with `render: "compact"` and
`render_source` per row and in the meta. Source: `raw` (re-rendered with `render_state(pr, compact=True)` from
`data/run3/agent_prs.jsonl` / `data/raw/k8s_merges.jsonl`) when the raw file holds every row, else `state`
(`compact_state()` on the stored string — identical text, checked on all 2,196 run-3 rows); pair rows use
`pair:dataset_state` (A compacted from its full dataset state, B body 600 / 20 files) or `pair:stored`. The full-state
numbers are run 5's (v3) and run 4–6's (pair); token counts are in each row's `decision.input_tokens`.

**C2** — **key**: `mergeone predict --model pplx-flash --qset v3 --split test --dataset data/run3/dataset.jsonl --prompt-file <frozen prompt> --run run7/c2`, once.

**C3 scoring** (no key):

```
python -m mergeone.eval.run7_evidence score --target iteration --i-have-frozen-the-protocol
python -m mergeone.eval.run7_evidence score --target rejection --i-have-frozen-the-protocol
```

Fits on val, freezes weights to `results/run7/evidence_weights_<target>[_stacked_<model>].json` (reused, never refit; a
pattern edit is refused), scores test, stacks on each model's run-5 `needs_human_review` (isotonic on val, as R5-H5) or on
`--probs file.jsonl` ({pr_number, prob}) → `results/run7/evidence_metrics_<target>.json` with the R7-H3 verdict.

**C5 relation** (after the adjudication round):

```
python -m mergeone.eval.run7_relation pairs                                   # no key: → results/run7/relation_pairs.jsonl
python -m mergeone.eval.run7_relation predict --model pplx-flash --model jev --i-have-frozen-the-protocol   # key
python -m mergeone.eval.run7_relation report                                  # → results/run7/relation_metrics.json, relation_tables.md
```

`predict` builds the 100 pair rows (the pair question's text: `results/run6/pairs.jsonl` verbatim if present, else
the run-4 builder over `candidates_k20.jsonl` or the PR memory) and runs `mergeone predict --qset relation` per model into
`results/run7/relation/`. `report` maps the four binaries to a class (rule table in the module docstring), scores against
`data/run6/hold_labels_adjudicated.jsonl` with sub-classes from the labellers' notes (override:
`data/run7/relation_gold.jsonl`), and gives related-vs-unrelated and same_change-vs-rest ROC-AUC, per-class accuracy with
Wilson intervals, the confusion matrix and cost per PR at k = 20.

## 5. C4 on RunPod (GPU)

No new code: the run-6A harness (`serve/`, `docs/run6-gpu.md`) with the candidate model as a `models.yaml` row, then
`mergeone predict --qset v3/pair` and `mergeone loadtest --multiplier 1/10/30`. Needs an L4 and its weights.

## 6. Report

Not built yet: a run-7 report that collects C1 (AUC deltas vs run 5 / runs 4–6, tokens per request), C2, C3, C4 and C5
into `results/run7/` tables and the R7-H1…H5 verdicts.

## What still needs a key or a GPU

| step | needs |
|---|---|
| C2 rounds and the one test call | `PERPLEXITY_API_KEY` |
| C1 compact predictions | `TYPESAFE_API_KEY`, `PERPLEXITY_API_KEY` |
| C5 predict | the same two (+ `ANTHROPIC_API_KEY` if `--model haiku`) |
| C4 | one L4 (RunPod) |
| C3, `draw`, `round`, `freeze`, `pairs`, `report` | nothing |
