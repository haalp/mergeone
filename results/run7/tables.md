# Run 7 — report tables

computed 2026-10-24T17:23:52+00:00 · `python -m mergeone.eval.run7_report`

## Verdicts

| hypothesis | result | bar |
|---|---|---|
| R7-H1 | fail | compact − full mean v3 AUC ≥ −0.02 for jev and pplx-flash, and pair AUC ≥ −0.02 |
| R7-H2 | fail | tuned − untuned mean v3 AUC (vs iterated, test) ≥ +0.02 and test iteration eval drop ≤ 0.01 |
| R7-H3 | fail | evidence-only test AUC on iteration >= 0.76 or stacked gain >= +0.02 |
| R7-H4 | deferred to run 8 | deferred to run 8 (§R7.6) |
| R7-H5 | fail | related-vs-unrelated AUC >= 0.8 for >= 1 model and same_change-vs-rest >= 0.8 |

## C1 — compact state (test; full state = run 5 v3 / run 4 pair)

### jev — version: full jev-1.13.0 → compact jev-1.14.0 (version drift)

v3 (ok; labels: data/run3/dataset.jsonl; ΔCI: paired bootstrap, seed 7, 1000 resamples)

| question | n | compact AUC | full AUC (run 5) | Δ | 95% CI (paired) | n paired |
|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.617 | 0.604 | +0.013 | [-0.014, 0.042] | 705 |
| weakens_tests | 705 | 0.760 | 0.771 | -0.011 | [-0.041, 0.021] | 705 |
| message_matches_diff | 705 | 0.256 | 0.233 | +0.023 | [-0.012, 0.056] | 705 |
| touches_concurrency | 705 | 0.619 | 0.605 | +0.013 | [-0.009, 0.038] | 705 |
| needs_human_review | 705 | 0.815 | 0.777 | +0.038 | [0.020, 0.056] | 705 |
| exceeds_scope | 705 | 0.804 | 0.795 | +0.009 | [-0.016, 0.036] | 705 |
| breaking_change | 705 | 0.732 | 0.708 | +0.025 | [0.003, 0.046] | 705 |
| tests_cover_change | 705 | 0.632 | 0.756 | -0.124 | [-0.174, -0.076] | 705 |
| hard_to_revert | 705 | 0.859 | 0.850 | +0.009 | [-0.005, 0.024] | 705 |
| follows_conventions | 705 | 0.221 | 0.235 | -0.014 | [-0.038, 0.010] | 705 |
| **mean** | | 0.631 | 0.633 | **-0.002** | [-0.013, 0.009] | 705 |

input tokens per request, median / p90: compact 1760 / 2066 · full 2529 / 2976 (same PRs)

pair (ok): same_change AUC compact 0.822 (n 187 of 187) vs full 0.835 (run 4) → Δ -0.013 [-0.032, 0.005]; tokens median / p90: compact 1845 / 2097 · full 2792 / 3097

### pplx-flash — version: full pplx-decider-flash-v1.2 → compact pplx-decider-flash-v1.2

v3 (ok; labels: data/run3/dataset.jsonl; ΔCI: paired bootstrap, seed 7, 1000 resamples)

| question | n | compact AUC | full AUC (run 5) | Δ | 95% CI (paired) | n paired |
|---|---|---|---|---|---|---|
| is_logic_change | 703 | 0.596 | 0.655 | -0.059 | [-0.104, -0.022] | 703 |
| weakens_tests | 703 | 0.734 | 0.696 | +0.038 | [-0.009, 0.082] | 703 |
| message_matches_diff | 703 | 0.400 | 0.504 | -0.103 | [-0.184, -0.018] | 703 |
| touches_concurrency | 703 | 0.690 | 0.644 | +0.046 | [-0.009, 0.097] | 703 |
| needs_human_review | 703 | 0.759 | 0.767 | -0.009 | [-0.053, 0.031] | 703 |
| exceeds_scope | 703 | 0.770 | 0.735 | +0.035 | [-0.003, 0.077] | 703 |
| breaking_change | 703 | 0.714 | 0.682 | +0.032 | [-0.004, 0.069] | 703 |
| tests_cover_change | 703 | 0.597 | 0.708 | -0.111 | [-0.174, -0.060] | 703 |
| hard_to_revert | 703 | 0.759 | 0.770 | -0.010 | [-0.053, 0.037] | 703 |
| follows_conventions | 703 | 0.293 | 0.641 | -0.348 | [-0.446, -0.244] | 703 |
| **mean** | | 0.631 | 0.680 | **-0.049** | [-0.077, -0.024] | 703 |

input tokens per request, median / p90: compact 9880 / 12954 · full 2344 / 2817 (same PRs)

pair (ok): same_change AUC compact 0.825 (n 187 of 187) vs full 0.842 (run 4) → Δ -0.018 [-0.037, -0.001]; tokens median / p90: compact 1564 / 1774 · full 2468 / 2806

R7-H1 per model: jev: v3 mean Δ -0.002, pair Δ -0.013 → pass; pplx-flash: v3 mean Δ -0.049, pair Δ -0.018 → fail

**R7-H1: fail** — bar: compact − full mean v3 AUC ≥ −0.02 for jev and pplx-flash, and pair AUC ≥ −0.02

## C2 — prompt tuning (pplx-flash; rounds on val, one test call)

| round | prompt sha256 | primary val AUC (nhr→iterated) | mean v3 val AUC | revert AUC | iteration AUC | eval score | decision |
|---|---|---|---|---|---|---|---|
| 0 | `10dab860ef3d` | 0.695 | 0.532 | 0.675 | 0.719 | 0.697 | keep |
| 1 | `3a3047fe0b70` | 0.581 | 0.521 | 0.621 | 0.628 | 0.624 | reject |
| 2 | `ada9b68ec852` | 0.698 | 0.533 | 0.677 | 0.702 | 0.689 | keep |

frozen: round 2, `ada9b68ec852` (prompts/jev_v3_r2.json), frozen early by the operator

test (ok; n 697; labels: data/run3/dataset.jsonl; prompt matches frozen: True; version pplx-decider-flash-v1.2 vs run 5 pplx-decider-flash-v1.2)

| question | n | tuned AUC | untuned AUC (run 5) | Δ | 95% CI (paired) |
|---|---|---|---|---|---|
| is_logic_change | 697 | 0.612 | 0.655 | -0.043 | [-0.067, -0.022] |
| weakens_tests | 697 | 0.723 | 0.696 | +0.027 | [-0.015, 0.072] |
| message_matches_diff | 697 | 0.331 | 0.504 | -0.173 | [-0.259, -0.087] |
| touches_concurrency | 697 | 0.677 | 0.644 | +0.033 | [-0.006, 0.075] |
| needs_human_review | 697 | 0.755 | 0.767 | -0.012 | [-0.040, 0.018] |
| exceeds_scope | 697 | 0.754 | 0.735 | +0.019 | [-0.021, 0.059] |
| breaking_change | 697 | 0.718 | 0.682 | +0.035 | [0.002, 0.067] |
| tests_cover_change | 697 | 0.626 | 0.708 | -0.082 | [-0.121, -0.046] |
| hard_to_revert | 697 | 0.746 | 0.770 | -0.024 | [-0.072, 0.023] |
| follows_conventions | 697 | 0.268 | 0.641 | -0.373 | [-0.469, -0.272] |
| **mean** | | 0.621 | 0.680 | **-0.059** | [-0.082, -0.038] |

test iteration eval (needs_human_review → iterated): 0.755 vs 0.767 (Δ -0.012); revert eval: unavailable: no untuned k8s test number (run 5 did not run it; R7.4 forbids re-running untuned)

**R7-H2: fail** — bar: tuned − untuned mean v3 AUC (vs iterated, test) ≥ +0.02 and test iteration eval drop ≤ 0.01

## C3 — structural evidence floor (no model)

| target | n test | evidence-only AUC | stacked AUC / nhr alone / gain |
|---|---|---|---|
| iteration | 705 | 0.484 | jev: 0.635 / 0.756 / -0.121; pplx: 0.529 / 0.686 / -0.157; pplx-flash: 0.588 / 0.758 / -0.170; haiku: 0.642 / 0.740 / -0.097 |
| rejection | 705 | 0.494 | jev: 0.543 / 0.540 / +0.003; pplx: 0.503 / 0.503 / -0.001; pplx-flash: 0.500 / 0.511 / -0.011; haiku: 0.544 / 0.554 / -0.010 |

**R7-H3: fail** — bar: evidence-only test AUC on iteration >= 0.76 or stacked gain >= +0.02

## C4 — registry row

deferred to run 8 (§R7.6); R7-H4 is not a run-7 hypothesis.

## C5 — typed relation on the run-6B pairs

100 pairs · gold: backport 28, same_area 10, follow_up 7, duplicate 46, competing 7, unrelated 2

| model | n | related vs unrelated AUC (n unrelated) | same_change vs rest AUC (n duplicate) | accuracy [95% CI] | $/PR at k=20 |
|---|---|---|---|---|---|
| pplx-flash | 97 | 0.990 (1) | 0.728 (45) | 0.320 [0.235, 0.418] | 0.00160 |
| jev | 100 | 0.995 (2) | 0.685 (46) | 0.080 [0.041, 0.150] | 0.00217 |

**R7-H5: fail** — bar: related-vs-unrelated AUC >= 0.8 for >= 1 model and same_change-vs-rest >= 0.8

Per-class accuracy and confusion: results/run7/relation_tables.md.

## Inputs

| path | sha256 |
|---|---|
| data/run3/dataset.jsonl | `d1a6c6325666cdab` |
| data/run7/frozen.json | `35d691f08665dc42` |
| data/run7/tuning_log.jsonl | `8f539ddb94e22cb9` |
| results/run4/metrics.json | `7704b439eea0e4f3` |
| results/run4/predictions_jev_test_all.jsonl | `eb922f22f19a6fa4` |
| results/run4/predictions_pplx-flash_test_all.jsonl | `373ea28608d4a95c` |
| results/run5/metrics.json | `85ffe045fcffeea3` |
| results/run5/predictions_jev_test_all.jsonl | `2e0d2a0b09488bcd` |
| results/run5/predictions_pplx-flash_test_all.jsonl | `4e74373448f48cdc` |
| results/run6/pairs_eval_test.jsonl | `b5fcb0bf44f8fd1a` |
| results/run7/c1/meta_jev_test_all_compact.json | `48a4e66b88326a7d` |
| results/run7/c1/meta_jev_test_all_compact_v3.json | `3fde67ebc999071e` |
| results/run7/c1/meta_pplx-flash_test_all_compact.json | `089db40a4468509a` |
| results/run7/c1/meta_pplx-flash_test_all_compact_v3.json | `cda2704c0409c5de` |
| results/run7/c1/predictions_jev_test_all_compact.jsonl | `f7ad2ef25069c495` |
| results/run7/c1/predictions_pplx-flash_test_all_compact.jsonl | `c0064da64d3c1d57` |
| results/run7/c2/predictions_pplx-flash_test_all.jsonl | `8eccd2ce3ca076d3` |
| results/run7/evidence_metrics_iteration.json | `d38ffb60f3f9dc75` |
| results/run7/evidence_metrics_rejection.json | `11c3285c8a708d4e` |
| results/run7/relation_metrics.json | `d2f916fb549cbe84` |
