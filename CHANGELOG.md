# Changelog

Protocol changes after freeze are listed here with a reason. Metric thresholds in PROTOCOL.md §6 are never edited after results exist.

- 2026-10-13: protocol frozen (PROTOCOL.md).
- 2026-10-13 (before any model call): dataset window set to the most recent 15,000 merged PRs instead of the protocol's minimum of 4,000. Reason: pilot on 1,500 merges showed a positive (revert-within-30d) rate of ~0.3–0.5%, too few positives for stable PR-AUC. Thresholds unchanged.
- 2026-10-13 (before any model call): revert matching extended to match the quoted title against the PR's own commit subjects, because Kubernetes revert PRs quote the squashed commit subject rather than the PR title in some cases. Pilot: 5/7 revert PRs matched; of the 2 unmatched, one is outside the 30-day horizon by design and one uses a free-text revert title.
- 2026-10-13 (before any model call): dataset window widened again, 15,000 → 40,000 merged PRs. Reason: with 15,000 merges the chronological test split held only 12 positives (train 64 / val 27 / test 12), too few for a stable PR-AUC or R@FPR5. Split ratios (60/20/20) and thresholds unchanged. The 15,000-merge `prepare` output is discarded; no model predictions were produced on it except a sanity run of `rules`/`gbm` which is not reported.
- 2026-10-14 (after haiku predictions, before any load-test result is reported): load-test replay redefined. The first implementation compressed the historical merge stream into a fixed wall-clock window, which made 1x and 10x identical (both capped at ~2/s). New definition: open-loop replay at base_rate × multiplier, base_rate = 30,000/day (the Anthropic post's figure) = 0.347/s. So 1x/10x/100x = 30k/300k/3M decisions per day. H5 thresholds unchanged. The two earlier haiku load-test files were deleted.
- 2026-10-15: run1 complete; all five hypotheses evaluated with thresholds unchanged (H1 false for all, H2 false, H3 true, H4 false, H5 false for haiku; jev 10x/1x load tests pending).
- 2026-10-15: run2 addendum added to PROTOCOL.md *after* run1 results. It does not alter run1. Its criteria (R2-H1..H4) were written before any run2 model call.
- 2026-10-15: run3 addendum (agent-authored PRs, outcome labels, repo-level splits) added to PROTOCOL.md before any run3 data was fetched. Fable 5.1 may classify close reasons on a sample for reporting only.

## 2026-10-16 — run3 tooling (before any run3 model call)

- `prepare-run3`: builds `data/run3/dataset.jsonl` from fetched agent PRs; repo-level split 70/30 stratified by dominant agent, seed 3 (R3.2).
- `report --run3` implements R3-H1..H5. Calibration note: only `needs_human_review` is refit on val repos (isotonic vs `rejected`), because run3 has outcome labels, not per-question judge labels; the other four questions keep the run2 calibration maps shipped in `mergeone/gate/`. This is a deviation from the literal "calibration refit on val repos" in R3.5 and is recorded here.
- `rules` model gets a v2 heuristic floor (`RulesModel._v2`), written before any run3 prediction, weights unfitted.
- R3-H4 stack linking uses `base` ref → another PR's `head_ref` in the same repo (GitHub REST has no stack object). `head_ref` was added to the fetcher after the first ~100 rows had been fetched; rows without it cannot be linked and are counted but not evaluated.
- `close-reasons`: Claude Fable 5.1 classifies a sample of closed-unmerged test PRs into six reasons. Reporting only (R3.4).
- Discover/fetch were made resumable (checkpoints) and robust to renamed repos (301/422) after the first collection attempt failed on `dotnet/aspire → microsoft/aspire`. `per_repo` was lowered from 150 (R3.2) to 120 to fit the API budget in one night; the balance rule (half merged / half closed where available) is unchanged.

## 2026-10-17 — run3 results

- Negative result on R3-H1/H2/H3 for every candidate (AUC 0.55 Jev, 0.55 Haiku, 0.53 rules; bar 0.70, "not transferring" line 0.60). R3-H5 passes for Jev (p99 417 ms, 0 errors at 100×), fails for Haiku (p99 2,880 ms, 0.3% errors). R3-H4 not evaluable (no linkable stacks via REST). See `results/run3/README.md`.
- Close-reason classification (Fable 5.1, 200 PRs, reporting only): 67% of closures are superseded/duplicate/direction, 19% quality.
- Exploratory, not pre-registered: the questions track review iteration (force-pushes ≥ 2) at AUC 0.70–0.81. Candidate outcome for run 4.
- Housekeeping: 825 Haiku val rows that errored with HTTP 401 (bad key on first attempt) were dropped; coverage verified 1491/1491. Jev predictions produced in two batches with identical rendering; verified.

## 2026-10-17 — decision-model registry and leaderboard (tooling; no protocol change)

- `prompts/models.yaml` replaces per-model classes: Jev, Perplexity (`pplx`, `pplx-flash`), Haiku and a set of open-weight candidates (`clef`, `clef-flash`, `open-jev-9b/27b`, `h2o-lightning-4b`, `laya`, `local`) are rows with a wire dialect (`systemone` | `decide` | `anthropic`). Python (`mergeone.models.registry`) and Go (`gate/internal/models/registry.go`) read the same file; the policy accepts any row as `model:` plus an inline `provider:` override. `JevModel` / `PplxModel` / `PplxFlashModel` remain as aliases so run 1–4 scripts are unchanged.
- `mergeone leaderboard` → `results/leaderboard.md`: reporting-only ranking of every model with frozen run artefacts (Borda over AUC, ECE, p99 at 100×, $/1k; scale pass as hard gate). It copies numbers from `results/run*/`, never re-scores. Not a pre-registered hypothesis.
- Release assets now include `jev_pair.json`, `haiku_pair.md`, `models.yaml`.

## 2026-10-17 — run4 results

- R4-H2 passes for pplx-decider (0.87), pplx-flash (0.84) and Jev (0.83); fails for Haiku (0.76). R4-H1 fails (recall@5 0.55 test, 0.73 val). R4-H3 fails for Jev (0.355, bar 0.234) and Haiku; R4-H4 fails for Jev (+8.2 pp) and Haiku (+10.8), passes for rules (+18.0) with a worse pass-group bad rate. R4-H5 fails (Python retrieval p99 198 ms). pplx candidates have no run-3 verdicts, so H3/H4 are not evaluable for them. See `results/run4/README.md`.
- Housekeeping: Perplexity's first pass hit HTTP 429 on 75% of pairs (8 concurrent calls vs 10 rps/org). Fixed by 429 backoff in the adapter and concurrency 2 for pplx; errored rows re-predicted, files compacted to one row per pair (`mergeone compact`). Prompts, questions and thresholds unchanged between passes. TypeSafe returned 402 (billing) after Jev's predictions were complete; no effect.
- `data/run4/labels.jsonl` (maintainer-comment labels) is now committed; memory/comments dumps are not (rebuildable).
- Product: `memory` stays opt-in in the default policy; `duplicate_of ≥ 0.7` remains a hold. Retrieval to be replaced before any default-on.


## 2026-10-18 — run 5, component C (question set v3, review iteration)

- 23:15Z: v3 wired before any v3 call: `QUESTION_SETS["v3"]` = v2 + {exceeds_scope, breaking_change, tests_cover_change, hard_to_revert, follows_conventions}, `PRIMARY_V3 = needs_human_review`; Haiku `SCHEMA_V3` + `prompts/haiku_v3.md`; System One dialect reads `prompts/jev_v3.json`; `rules` v3 floor = v2 heuristics + 0.5 for the five new questions. `predict --qset v3`, `loadtest --qset v3 --no-retry` (or `MERGEONE_NO_RETRY=1`: adapter 429 retry off, R5-H6). `--only-missing-from` and `compact` are now keyed by (pr_number, qset), because run 5 writes v3, pair and pair_assume rows into the same `predictions_<model>_<split>_all.jsonl` and a pair_assume row shares its pr_number with the same_change row of the same pair. Run-1..4 files have one qset per file, so their behaviour is unchanged. Meta files for v3/pair_assume get a `_<qset>` suffix.

## 2026-10-17/18 — run 5, component A (retrieval v2)

- 2026-10-17 23:18Z: A1 weights fit on the 22 val gold pairs only (826-point grid, R5.1-A1) and frozen in `mergeone/memory/retrieval_v2.py`: issue 0.5, dir 0.1, file 0.2, text 0.2, author 0.0. Val (tuned): recall@20 0.909, recall@5 0.864; 4 grid points tied on (recall@20, recall@5), broken by val MRR@20 (0.582), a tie-break the protocol does not specify. Grid in `results/run5/a1_weight_grid.json`. No test PR scored before this commit. Same-author compares logins lower-cased with `[bot]` stripped (dataset has `claude[bot]`, memory `claude`). `IDENT_STOP` (A2 keyword stop-list) committed in the same file before any patch fetch.
