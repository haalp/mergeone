# Run 11 — tables

Built 2026-11-08T15:44:12+00:00 by `python -m mergeone.eval.run11_report`; every number from `results/run11/`.

| | bar | verdict |
|---|---|---|
| R11-H1 | compact main call − same-day full-state control: mean v4 AUC Δ ≥ −0.02, every question Δ ≥ −0.03, median input tokens ≤ 0.75× control | fail |
| R11-H2 | primary score `retrieval`, k' ∈ {3, 5}: recall of the adjudicated duplicate ≥ 0.95 and dup-vs-rest AUC ≥ k'=10 − 0.02 (run 9's recorded answers) | fail |
| R11-H3 | conditional on ≥ 200 outcomes: refit ECE ≤ shipped − 0.02 on ≥ 5 of 8 questions, AUC unchanged | not run yet |

## R11-H1 — compact main call vs same-day full-state control (pplx-flash, test, v4)

| question | control AUC | compact AUC | Δ | paired 95 % CI |
|---|---|---|---|---|
| `is_logic_change` | 0.611 | 0.595 | -0.016 | [-0.050, 0.018] |
| `weakens_tests` | 0.744 | 0.740 | -0.004 | [-0.033, 0.029] |
| `touches_concurrency` | 0.682 | 0.687 | +0.005 | [-0.024, 0.034] |
| `needs_human_review` | 0.759 | 0.761 | +0.002 | [-0.035, 0.034] |
| `exceeds_scope` | 0.751 | 0.761 | +0.010 | [-0.021, 0.043] |
| `breaking_change` | 0.716 | 0.718 | +0.002 | [-0.019, 0.022] |
| `tests_cover_change` | 0.635 | 0.603 | -0.032 | [-0.095, 0.020] |
| `hard_to_revert` | 0.781 | 0.793 | +0.012 | [-0.013, 0.041] |
| **mean (8)** | 0.710 | 0.707 | -0.003 | [-0.020, 0.015] |

input tokens per PR (median, same PRs): control 2209, compact 1492, ratio 0.675 (bar ≤ 0.75)
clauses: mean True · every question ≥ −0.03 False (worst ('tests_cover_change', -0.03181793956190371)) · tokens True

## R11-H2 — pair-call prefilter on run 9's recorded set-(b) answers (no model call)

100 adjudicated A's ({'duplicate': 46, 'pointer': 52, 'unrelated': 2}); control k' = 10: dup-vs-rest AUC 0.972, 10.0 pair calls per A

| score | k' | recall of the duplicate | dup-vs-rest AUC | Δ vs k'=10 | pass |
|---|---|---|---|---|---|
| retrieval (primary) | 3 | 0.891 | 0.929 | -0.042 | fail |
| retrieval (primary) | 5 | 0.913 | 0.956 | -0.016 | fail |
| overlap | 3 | 0.783 | 0.922 | -0.050 | fail |
| overlap | 5 | 0.826 | 0.951 | -0.021 | fail |
| blend | 3 | 0.848 | 0.919 | -0.053 | fail |
| blend | 5 | 0.891 | 0.948 | -0.024 | fail |

ships: nothing (ten pair calls stay)

## R11-H3 — team recalibration (conditional)

not run yet (conditional: waits for ≥ 200 outcomes; results/run11/recalibration.json missing)
