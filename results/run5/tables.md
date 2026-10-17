# Run 5 — component C: question set v3 → review iteration

val 1491 PRs · test 705 PRs · `iterated` base rate: test 0.082, val 0.031 · rejected (continuity) 0.468

## R5-H5: calibrated `needs_human_review` (v3 calibration, isotonic on val vs iterated) → iterated, test repos

| model | n | ROC-AUC [95% CI] | raw AUC | PR-AUC | R@FPR5 | ECE (cal) | stack AUC [95% CI] | stack unpruned | pruned on val | p50 ms | p99 ms | $/1k |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| haiku | 705 | 0.740 [0.684, 0.790] | 0.738 | 0.155 | 0.086 | 0.055 | 0.784 [0.737, 0.830] | 0.768 | breaking_change, tests_cover_change, hard_to_revert | 1096 | 2619 | 0.472 |
| rules | 705 | 0.728 [0.667, 0.786] | 0.744 | 0.169 | 0.155 | 0.057 | 0.731 [0.666, 0.785] | 0.731 | exceeds_scope, breaking_change, tests_cover_change, hard_to_revert, follows_conventions | 0 | 1 | 0.000 |

## Every v3 question (test ROC-AUC; val AUC is in-sample for calibration and pruning)

### haiku

| question | vs iterated (raw) | vs iterated (cal) | ECE cal | vs rejected | val vs iterated | val marginal gain (stack) |
|---|---|---|---|---|---|---|
| is_logic_change | 0.665 | 0.578 | 0.068 | 0.479 | 0.606 | -0.0030 |
| weakens_tests | 0.809 | 0.797 | 0.063 | 0.559 | 0.597 | -0.0013 |
| message_matches_diff | 0.356 | 0.477 | 0.082 | 0.397 | 0.392 | 0.0239 |
| touches_concurrency | 0.642 | 0.631 | 0.058 | 0.482 | 0.643 | 0.0014 |
| needs_human_review | 0.738 | 0.740 | 0.055 | 0.563 | 0.666 | 0.0040 |
| exceeds_scope | 0.781 | 0.773 | 0.062 | 0.518 | 0.663 | 0.0142 |
| breaking_change | 0.750 | 0.700 | 0.064 | 0.499 | 0.611 | -0.0053 (pruned) |
| tests_cover_change | 0.624 | 0.624 | 0.072 | 0.515 | 0.532 | -0.0039 (pruned) |
| hard_to_revert | 0.808 | 0.789 | 0.063 | 0.508 | 0.644 | -0.0020 (pruned) |
| follows_conventions | 0.307 | 0.476 | 0.076 | 0.396 | 0.369 | 0.0068 |

val 5-fold stack AUC: all ten 0.652, kept 0.664 (46 val positives).

### rules

| question | vs iterated (raw) | vs iterated (cal) | ECE cal | vs rejected | val vs iterated | val marginal gain (stack) |
|---|---|---|---|---|---|---|
| is_logic_change | 0.730 | 0.716 | 0.065 | 0.489 | 0.570 | -0.0428 |
| weakens_tests | 0.491 | 0.500 | 0.087 | 0.499 | 0.490 | 0.0050 |
| message_matches_diff | 0.294 | 0.417 | 0.082 | 0.421 | 0.489 | 0.0057 |
| touches_concurrency | 0.403 | 0.500 | 0.087 | 0.473 | 0.422 | 0.0277 |
| needs_human_review | 0.744 | 0.728 | 0.057 | 0.528 | 0.617 | 0.0134 |
| exceeds_scope | 0.500 | 0.500 | 0.087 | 0.500 | 0.500 | 0.0000 (pruned) |
| breaking_change | 0.500 | 0.500 | 0.087 | 0.500 | 0.500 | 0.0000 (pruned) |
| tests_cover_change | 0.500 | 0.500 | 0.087 | 0.500 | 0.500 | 0.0000 (pruned) |
| hard_to_revert | 0.500 | 0.500 | 0.087 | 0.500 | 0.500 | 0.0000 (pruned) |
| follows_conventions | 0.500 | 0.500 | 0.087 | 0.500 | 0.500 | 0.0000 (pruned) |

val 5-fold stack AUC: all ten 0.588, kept 0.588 (46 val positives).

## R5-H3: verdict level with component-A top-5 (run-4 pair predictions reused where identical)

**A pending** — results/run5/candidates.jsonl not found (component A); R5-H3 skipped

## R5-H6: load tests (pplx, pplx-flash; adapter 429 retry disabled)

| model | ×1 p99 | ×10 p99 | ×100 p99 | ×100 errors | ×100 429s | in-run p99 | status |
|---|---|---|---|---|---|---|---|
| pplx | — | — | — | — | — | — | pending |
| pplx-flash | — | — | — | — | — | — | pending |

## Pre-registered criteria

| hypothesis | result | bar | pass |
|---|---|---|---|
| R5-H5[haiku] | {"roc_auc": 0.74, "ci95": [0.684, 0.79], "system_one": false, "not_replicating": false} | >= 0.70 calibrated needs_human_review → iterated (System One counts; < 0.60 not replicating) | ✅ |
| R5-H5-stack[haiku] | {"roc_auc": 0.784, "ci95": [0.737, 0.83], "pruned_on_val": ["breaking_change", "tests_cover_change", "hard_to_revert"]} | >= 0.75 (stack fit on val) | ✅ |
| R5-H5[rules] | {"roc_auc": 0.728, "ci95": [0.667, 0.786], "system_one": false, "not_replicating": false} | >= 0.70 calibrated needs_human_review → iterated (System One counts; < 0.60 not replicating) | ✅ |
| R5-H5-stack[rules] | {"roc_auc": 0.731, "ci95": [0.666, 0.785], "pruned_on_val": ["exceeds_scope", "breaking_change", "tests_cover_change", "hard_to_revert", "follows_conventions"]} | >= 0.75 (stack fit on val) | ❌ |
| R5-H5 | {"status": "System One v3 predictions pending (jev/pplx/pplx-flash run on the Mac)"} | nhr >= 0.70 for >= 1 System One model and stack >= 0.75 | pending |
| R5-H3 | {"status": "A pending"} | pass bad-rate <= 0.5 x base; closed coverage >= +15 pp | pending |
| R5-H6[pplx] | {"status": "pending"} | 100×: p99 <= in-run p99 + 200 ms, 0 errors (429 = error, retry off) | pending |
| R5-H6[pplx-flash] | {"status": "pending"} | 100×: p99 <= in-run p99 + 200 ms, 0 errors (429 = error, retry off) | pending |

## Notes

- R5-H5: per question, isotonic (sklearn, out_of_bounds=clip) fitted on val-repo raw answers vs `iterated`, applied to test. R5-H3 verdicts: run-3 rule unchanged — default gate policy (always_human_paths cleared), shipped calibration_<model>_v2.json maps for the four non-primary questions where they exist (jev, haiku), and needs_human_review refit by isotonic on val repos vs `rejected` (report_run3._calibrate_primary). jev/haiku/rules use their run-3 v2 predictions (as run 4). pplx/pplx-flash have no shipped maps, so their four non-primary v2 answers are used uncalibrated; their needs_human_review is refit vs `rejected` on their run-5 v3 val answers; the five new v3 questions are not in the policy and do not affect verdicts.
- Stack: features = the raw answers to the ten v3 questions (rows with all ten present), StandardScaler + L2 logistic regression (C=1, lbfgs) fitted on val repos vs `iterated`. Pruning (val only): marginal gain of q = pooled out-of-fold AUC of the 10-question stack minus that of the stack without q, 5-fold stratified CV within val (seed 5). Only the five questions new in v3 are prunable; gains are reported for all ten. Pruned questions are dropped for the stack only.
- R5-H6 applies the R3-H5 computation: ×100 p99 vs the model's in-run p99 (v3 rows, val+test) + 200 ms, and 0 errors. The in-run pplx calls ran at concurrency 2 with the adapter's 429 retry on (as run 4), so `pass_vs_x1` — the same bar against the retry-off ×1 load-test p99 — is reported alongside. A load test whose summary does not show retry_429 = 0 is invalid for R5-H6.
