# Changelog

Protocol changes after freeze are listed here with a reason. Metric thresholds in PROTOCOL.md §6 are never edited after results exist.

- 2026-10-13: protocol frozen (PROTOCOL.md).
- 2026-10-13 (before any model call): dataset window set to the most recent 15,000 merged PRs instead of the protocol's minimum of 4,000. Reason: pilot on 1,500 merges showed a positive (revert-within-30d) rate of ~0.3–0.5%, too few positives for stable PR-AUC. Thresholds unchanged.
- 2026-10-13 (before any model call): revert matching extended to match the quoted title against the PR's own commit subjects, because Kubernetes revert PRs quote the squashed commit subject rather than the PR title in some cases. Pilot: 5/7 revert PRs matched; of the 2 unmatched, one is outside the 30-day horizon by design and one uses a free-text revert title.
- 2026-10-13 (before any model call): dataset window widened again, 15,000 → 40,000 merged PRs. Reason: with 15,000 merges the chronological test split held only 12 positives (train 64 / val 27 / test 12), too few for a stable PR-AUC or R@FPR5. Split ratios (60/20/20) and thresholds unchanged. The 15,000-merge `prepare` output is discarded; no model predictions were produced on it except a sanity run of `rules`/`gbm` which is not reported.
- 2026-10-14 (after haiku predictions, before any load-test result is reported): load-test replay redefined. The first implementation compressed the historical merge stream into a fixed wall-clock window, which made 1x and 10x identical (both capped at ~2/s). New definition: open-loop replay at base_rate × multiplier, base_rate = 30,000/day (the Anthropic post's figure) = 0.347/s. So 1x/10x/100x = 30k/300k/3M decisions per day. H5 thresholds unchanged. The two earlier haiku load-test files were deleted.
- 2026-10-15: run1 complete; all five hypotheses evaluated with thresholds unchanged (H1 false for all, H2 false, H3 true, H4 false, H5 false for haiku; jev 10x/1x load tests pending).
- 2026-10-15: run2 addendum added to PROTOCOL.md *after* run1 results. It does not alter run1. Its criteria (R2-H1..H4) were written before any run2 model call.
