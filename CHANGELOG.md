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

