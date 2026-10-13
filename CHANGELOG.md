# Changelog

Protocol changes after freeze are listed here with a reason. Metric thresholds in PROTOCOL.md §6 are never edited after results exist.

- 2026-10-13: protocol frozen (PROTOCOL.md).
- 2026-10-13 (before any model call): dataset window set to the most recent 15,000 merged PRs instead of the protocol's minimum of 4,000. Reason: pilot on 1,500 merges showed a positive (revert-within-30d) rate of ~0.3–0.5%, too few positives for stable PR-AUC. Thresholds unchanged.
- 2026-10-13 (before any model call): revert matching extended to match the quoted title against the PR's own commit subjects, because Kubernetes revert PRs quote the squashed commit subject rather than the PR title in some cases. Pilot: 5/7 revert PRs matched; of the 2 unmatched, one is outside the 30-day horizon by design and one uses a free-text revert title.
- 2026-10-13 (before any model call): dataset window widened again, 15,000 → 40,000 merged PRs. Reason: with 15,000 merges the chronological test split held only 12 positives (train 64 / val 27 / test 12), too few for a stable PR-AUC or R@FPR5. Split ratios (60/20/20) and thresholds unchanged. The 15,000-merge `prepare` output is discarded; no model predictions were produced on it except a sanity run of `rules`/`gbm` which is not reported.
