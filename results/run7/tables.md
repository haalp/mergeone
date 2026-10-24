# Run 7 — report tables

computed 2026-10-24T17:30:38+00:00 · `python -m mergeone.eval.run7_report`

## Verdicts

| hypothesis | result | bar |
|---|---|---|
| R7-H1 | fail | compact − full mean v3 AUC ≥ −0.02 for jev and pplx-flash, and pair AUC ≥ −0.02 |
| R7-H2 | fail | tuned − untuned mean v3 AUC (vs iterated, test) ≥ +0.02 and test iteration eval drop ≤ 0.01 |
| R7-H3 | fail | evidence-only test AUC on iteration >= 0.76 or stacked gain >= +0.02 |
| R7-H4 | deferred to run 8 | deferred to run 8 (§R7.6) |
| R7-H5 | fail | related-vs-unrelated AUC >= 0.8 for >= 1 model and same_change-vs-rest >= 0.8 |

Verdicts are computed from the pre-registered inputs only. The pre-registered C1/C2 baselines (run 4 / run 5) were taken weeks earlier and run 7 found provider-side drift (pplx-flash answers differ under the same version string pplx-decider-flash-v1.2; jev 1.13.0 → 1.14.0); same-day controls made after these verdicts are read in the post-hoc sections at the end, which do not change this table.

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

## Post-hoc (not pre-registered): same-day controls (drift)

> NOT pre-registered. The pre-registered C1/C2 comparisons are against run-4/run-5 numbers taken weeks earlier; run 7 found provider-side drift (pplx-flash answers differ under the same version string pplx-decider-flash-v1.2; jev moved 1.13.0 → 1.14.0). Same-day full-state, untuned control calls (results/run7/control/) were made AFTER the pre-registered verdicts. Nothing here changes a verdict row.

Control = full state, untuned prompts/jev_v3.json, called today (results/run7/control/; v3 rows on the run-3 test split, pair rows on results/run4/pairs.jsonl test, scored on the R4-H2 eval pair ids). AUC and paired bootstrap as in C1 (seed 7, 1000 resamples).

### jev — version: control jev-1.14.0 · run 5 jev-1.13.0 · run 4 pair jev-1.13.0

**1. Drift — control (today) vs run 5 (v3) / run 4 (pair)** (labels: data/run3/dataset.jsonl; mean P on the PRs answered in both)

| question | n | control AUC | run 5 AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired | mean P control | mean P run 5 |
|---|---|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.608 | 0.604 | +0.003 | +0.003 | [-0.020, 0.028] | 705 | 0.719 | 0.728 |
| weakens_tests | 705 | 0.729 | 0.771 | -0.042 | -0.042 | [-0.070, -0.018] | 705 | 0.097 | 0.092 |
| message_matches_diff | 705 | 0.225 | 0.233 | -0.008 | -0.008 | [-0.026, 0.009] | 705 | 0.701 | 0.730 |
| touches_concurrency | 705 | 0.601 | 0.605 | -0.005 | -0.005 | [-0.024, 0.014] | 705 | 0.100 | 0.105 |
| needs_human_review | 705 | 0.776 | 0.777 | -0.001 | -0.001 | [-0.013, 0.011] | 705 | 0.661 | 0.691 |
| exceeds_scope | 705 | 0.798 | 0.795 | +0.003 | +0.003 | [-0.009, 0.014] | 705 | 0.206 | 0.205 |
| breaking_change | 705 | 0.714 | 0.708 | +0.007 | +0.007 | [-0.003, 0.016] | 705 | 0.280 | 0.261 |
| tests_cover_change | 705 | 0.646 | 0.756 | -0.111 | -0.111 | [-0.149, -0.075] | 705 | 0.664 | 0.648 |
| hard_to_revert | 705 | 0.854 | 0.850 | +0.003 | +0.003 | [-0.007, 0.014] | 705 | 0.164 | 0.171 |
| follows_conventions | 705 | 0.224 | 0.235 | -0.010 | -0.010 | [-0.030, 0.009] | 705 | 0.784 | 0.794 |
| **mean** | | 0.617 | 0.633 | **-0.016** | -0.016 | [-0.023, -0.009] | 705 | | |

Δ = each side's AUC on all its answered PRs (as in the verdict computation); Δ same PRs and the CI use the PRs answered in both.

pair (same_change, 187 of 187 eval pairs): control 0.837 vs run 4 0.835 → Δ +0.002 (same pairs +0.002 [-0.011, 0.013], n paired 187); tokens median / p90 on the same pairs: control 2792 / 3097 · run 4 2792 / 3097

**2. Drift-adjusted C1 — compact vs control (same day)**

| question | n | compact AUC | control AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.617 | 0.608 | +0.010 | +0.010 | [-0.016, 0.032] | 705 |
| weakens_tests | 705 | 0.760 | 0.729 | +0.031 | +0.031 | [0.007, 0.058] | 705 |
| message_matches_diff | 705 | 0.256 | 0.225 | +0.031 | +0.031 | [0.003, 0.059] | 705 |
| touches_concurrency | 705 | 0.619 | 0.601 | +0.018 | +0.018 | [-0.002, 0.040] | 705 |
| needs_human_review | 705 | 0.815 | 0.776 | +0.039 | +0.039 | [0.018, 0.059] | 705 |
| exceeds_scope | 705 | 0.804 | 0.798 | +0.006 | +0.006 | [-0.019, 0.036] | 705 |
| breaking_change | 705 | 0.732 | 0.714 | +0.018 | +0.018 | [-0.001, 0.036] | 705 |
| tests_cover_change | 705 | 0.632 | 0.646 | -0.014 | -0.014 | [-0.055, 0.029] | 705 |
| hard_to_revert | 705 | 0.859 | 0.854 | +0.005 | +0.005 | [-0.007, 0.016] | 705 |
| follows_conventions | 705 | 0.221 | 0.224 | -0.004 | -0.004 | [-0.025, 0.019] | 705 |
| **mean** | | 0.631 | 0.617 | **+0.014** | +0.014 | [0.004, 0.024] | 705 |

Δ = each side's AUC on all its answered PRs (as in the verdict computation); Δ same PRs and the CI use the PRs answered in both.

v3 input tokens per request, median / p90: compact 1760 / 2066 · control 2529 / 2976 (same PRs)

pair (same_change, 187 of 187 eval pairs): compact 0.822 vs control 0.837 → Δ -0.015 (same pairs -0.015 [-0.030, -0.001], n paired 187); tokens median / p90 on the same pairs: compact 1845 / 2097 · control 2792 / 3097

*post-hoc reading: the R7-H1 bar (−0.02) would be met on this comparison (Δ as in the verdict computation: each side on all its answered PRs; the same on the PRs answered in both; verdict row unchanged)* (mean Δ +0.014 / same PRs +0.014; pair Δ -0.015 / same pairs -0.015).

### pplx-flash — version: control pplx-decider-flash-v1.2 · run 5 pplx-decider-flash-v1.2 · run 4 pair pplx-decider-flash-v1.2

**1. Drift — control (today) vs run 5 (v3) / run 4 (pair)** (labels: data/run3/dataset.jsonl; mean P on the PRs answered in both)

| question | n | control AUC | run 5 AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired | mean P control | mean P run 5 |
|---|---|---|---|---|---|---|---|---|---|
| is_logic_change | 682 | 0.618 | 0.655 | -0.037 | -0.042 | [-0.065, -0.021] | 682 | 0.683 | 0.606 |
| weakens_tests | 682 | 0.719 | 0.696 | +0.023 | +0.033 | [-0.010, 0.080] | 682 | 0.064 | 0.214 |
| message_matches_diff | 682 | 0.323 | 0.504 | -0.181 | -0.177 | [-0.269, -0.080] | 682 | 0.921 | 0.669 |
| touches_concurrency | 682 | 0.684 | 0.644 | +0.040 | +0.032 | [-0.013, 0.077] | 682 | 0.037 | 0.095 |
| needs_human_review | 682 | 0.793 | 0.767 | +0.025 | +0.012 | [-0.012, 0.035] | 682 | 0.599 | 0.523 |
| exceeds_scope | 682 | 0.765 | 0.735 | +0.030 | +0.027 | [-0.017, 0.070] | 682 | 0.168 | 0.344 |
| breaking_change | 682 | 0.730 | 0.682 | +0.048 | +0.040 | [0.006, 0.074] | 682 | 0.201 | 0.287 |
| tests_cover_change | 682 | 0.642 | 0.708 | -0.067 | -0.083 | [-0.123, -0.045] | 682 | 0.622 | 0.526 |
| hard_to_revert | 682 | 0.759 | 0.770 | -0.011 | -0.019 | [-0.065, 0.021] | 682 | 0.147 | 0.170 |
| follows_conventions | 682 | 0.252 | 0.641 | -0.389 | -0.395 | [-0.489, -0.304] | 682 | 0.819 | 0.258 |
| **mean** | | 0.628 | 0.680 | **-0.052** | -0.057 | [-0.079, -0.032] | 682 | | |

Δ = each side's AUC on all its answered PRs (as in the verdict computation); Δ same PRs and the CI use the PRs answered in both.

pair (same_change, 187 of 187 eval pairs): control 0.840 vs run 4 0.842 → Δ -0.002 (same pairs -0.002 [-0.009, 0.004], n paired 187); tokens median / p90 on the same pairs: control 2468 / 2806 · run 4 2468 / 2806

**2. Drift-adjusted C1 — compact vs control (same day)**

| question | n | compact AUC | control AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |
|---|---|---|---|---|---|---|---|
| is_logic_change | 703 | 0.596 | 0.618 | -0.022 | -0.014 | [-0.050, 0.021] | 680 |
| weakens_tests | 703 | 0.734 | 0.719 | +0.015 | +0.011 | [-0.022, 0.047] | 680 |
| message_matches_diff | 703 | 0.400 | 0.323 | +0.078 | +0.070 | [0.017, 0.121] | 680 |
| touches_concurrency | 703 | 0.690 | 0.684 | +0.006 | +0.015 | [-0.014, 0.040] | 680 |
| needs_human_review | 703 | 0.759 | 0.793 | -0.034 | -0.010 | [-0.050, 0.025] | 680 |
| exceeds_scope | 703 | 0.770 | 0.765 | +0.005 | +0.017 | [-0.013, 0.048] | 680 |
| breaking_change | 703 | 0.714 | 0.730 | -0.016 | -0.007 | [-0.030, 0.014] | 680 |
| tests_cover_change | 703 | 0.597 | 0.642 | -0.044 | -0.036 | [-0.094, 0.016] | 680 |
| hard_to_revert | 703 | 0.759 | 0.759 | +0.000 | +0.006 | [-0.032, 0.046] | 680 |
| follows_conventions | 703 | 0.293 | 0.252 | +0.041 | +0.018 | [-0.021, 0.056] | 680 |
| **mean** | | 0.631 | 0.628 | **+0.003** | +0.007 | [-0.011, 0.023] | 680 |

Δ = each side's AUC on all its answered PRs (as in the verdict computation); Δ same PRs and the CI use the PRs answered in both.

v3 input tokens per request, median / p90: compact 9870 / 12993 · control 16980 / 21794 (same PRs)

pair (same_change, 187 of 187 eval pairs): compact 0.825 vs control 0.840 → Δ -0.015 (same pairs -0.015 [-0.036, 0.002], n paired 187); tokens median / p90 on the same pairs: compact 1564 / 1774 · control 2468 / 2806

*post-hoc reading: the R7-H1 bar (−0.02) would be met on this comparison (Δ as in the verdict computation: each side on all its answered PRs; the same on the PRs answered in both; verdict row unchanged)* (mean Δ +0.003 / same PRs +0.007; pair Δ -0.015 / same pairs -0.015).

**3. Drift-adjusted C2 — tuned (results/run7/c2) vs control untuned (same day)**

| question | n | tuned AUC | control AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |
|---|---|---|---|---|---|---|---|
| is_logic_change | 697 | 0.612 | 0.618 | -0.007 | +0.000 | [0.000, 0.000] | 677 |
| weakens_tests | 697 | 0.723 | 0.719 | +0.004 | +0.000 | [0.000, 0.000] | 677 |
| message_matches_diff | 697 | 0.331 | 0.323 | +0.008 | +0.000 | [0.000, 0.000] | 677 |
| touches_concurrency | 697 | 0.677 | 0.684 | -0.007 | +0.000 | [0.000, 0.000] | 677 |
| needs_human_review | 697 | 0.755 | 0.793 | -0.037 | -0.016 | [-0.034, 0.002] | 677 |
| exceeds_scope | 697 | 0.754 | 0.765 | -0.011 | +0.000 | [0.000, 0.000] | 677 |
| breaking_change | 697 | 0.718 | 0.730 | -0.013 | +0.000 | [0.000, 0.000] | 677 |
| tests_cover_change | 697 | 0.626 | 0.642 | -0.016 | +0.000 | [0.000, 0.000] | 677 |
| hard_to_revert | 697 | 0.746 | 0.759 | -0.013 | +0.000 | [0.000, 0.000] | 677 |
| follows_conventions | 697 | 0.268 | 0.252 | +0.017 | +0.000 | [0.000, 0.000] | 677 |
| **mean** | | 0.621 | 0.628 | **-0.007** | -0.002 | [-0.003, 0.000] | 677 |

Δ = each side's AUC on all its answered PRs (as in the verdict computation); Δ same PRs and the CI use the PRs answered in both.

iteration eval (needs_human_review → iterated): tuned 0.755 vs control 0.793 (Δ -0.037; same PRs -0.016 [-0.034, 0.002])

*post-hoc reading: the R7-H2 bar (+0.02 mean, iteration eval drop ≤ 0.01) would not be met on this comparison (Δ as in the verdict computation: each side on all its answered PRs; the same on the PRs answered in both; verdict row unchanged)* (mean Δ -0.007 / same PRs -0.002; iteration eval Δ -0.037 / same PRs -0.016).

## Post-hoc (not pre-registered): C5 class rule with metadata branch

> NOT pre-registered; the R7-H5 verdict row is unchanged. The frozen rule (run7_relation.relation_class) splits same_change into backport vs duplicate on the model's same_branch answer; here P(same_branch) is replaced by the metadata fact (base branch of A == base branch of B → 1, else 0), thresholds and precedence unchanged, scored with run7_relation.score_model.

base branches: A: data/run3/dataset.jsonl `base` (repo + number); B: data/run3/dataset.jsonl when present, else data/run4/memory/<repo>.jsonl raw `base` (A+B lookups: dataset 119, memory 62, missing 0); equal base on 65 of 100 pairs (0.650); by gold class (equal / n): duplicate 43/46, competing 6/7, backport 1/28, follow_up 7/7, same_area 7/10, unrelated 1/2

| model | n | accuracy frozen rule [95% CI] | accuracy metadata branch [95% CI] | same_change vs rest AUC (unchanged) | model same_branch agrees with metadata |
|---|---|---|---|---|---|
| jev | 100 | 0.080 [0.041, 0.150] | 0.720 [0.625, 0.799] | 0.685 / 0.685 | 7 of 100 |
| pplx-flash | 97 | 0.320 [0.235, 0.418] | 0.722 [0.625, 0.801] | 0.728 / 0.728 | 39 of 97 |

### jev: per gold class, frozen rule → metadata branch

| gold class | n | correct (frozen) | correct (metadata) | accuracy frozen → metadata |
|---|---|---|---|---|
| duplicate | 46 | 1 | 40 | 0.022 → 0.870 |
| competing | 7 | 0 | 2 | 0.000 → 0.286 |
| backport | 28 | 2 | 27 | 0.071 → 0.964 |
| follow_up | 7 | 5 | 3 | 0.714 → 0.429 |
| same_area | 10 | 0 | 0 | 0.000 → 0.000 |
| unrelated | 2 | 0 | 0 | 0.000 → 0.000 |

jev: confusion with metadata branch (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 40 | 3 | 3 | 0 | 0 | 0 |
| competing | 4 | 2 | 1 | 0 | 0 | 0 |
| backport | 1 | 0 | 27 | 0 | 0 | 0 |
| follow_up | 2 | 2 | 0 | 3 | 0 | 0 |
| same_area | 6 | 1 | 3 | 0 | 0 | 0 |
| unrelated | 0 | 0 | 1 | 1 | 0 | 0 |

### pplx-flash: per gold class, frozen rule → metadata branch

| gold class | n | correct (frozen) | correct (metadata) | accuracy frozen → metadata |
|---|---|---|---|---|
| duplicate | 45 | 28 | 42 | 0.622 → 0.933 |
| competing | 7 | 0 | 0 | 0.000 → 0.000 |
| backport | 27 | 1 | 26 | 0.037 → 0.963 |
| follow_up | 7 | 2 | 2 | 0.286 → 0.286 |
| same_area | 10 | 0 | 0 | 0.000 → 0.000 |
| unrelated | 1 | 0 | 0 | 0.000 → 0.000 |

pplx-flash: confusion with metadata branch (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 42 | 0 | 3 | 0 | 0 | 0 |
| competing | 6 | 0 | 1 | 0 | 0 | 0 |
| backport | 1 | 0 | 26 | 0 | 0 | 0 |
| follow_up | 5 | 0 | 0 | 2 | 0 | 0 |
| same_area | 7 | 0 | 3 | 0 | 0 | 0 |
| unrelated | 1 | 0 | 0 | 0 | 0 | 0 |

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

### Post-hoc inputs

| path | sha256 |
|---|---|
| data/run3/dataset.jsonl | `d1a6c6325666cdab` |
| data/run4/memory/Azure__azure-sdk-for-net.jsonl | `ab0a7072fffde828` |
| data/run4/memory/Comfy-Org__ComfyUI_frontend.jsonl | `b88bf2e4e74f5d48` |
| data/run4/memory/VocaHQ__vocalinux.jsonl | `5f0ab486c4ba6a5a` |
| data/run4/memory/compdemocracy__polis.jsonl | `f1c56bdf7e25c487` |
| data/run4/memory/mattermost__mattermost.jsonl | `e211c9b1230834ff` |
| data/run4/memory/microsoft__vscode.jsonl | `ccf72293752e5e1a` |
| data/run6/hold_labels.jsonl | `bad6d0d3c916acdf` |
| data/run6/hold_labels_adjudicated.jsonl | `df4e0dbe38a2f921` |
| data/run6/hold_labels_fable.jsonl | `d45f8664232420f5` |
| results/run4/metrics.json | `7704b439eea0e4f3` |
| results/run4/predictions_jev_test_all.jsonl | `eb922f22f19a6fa4` |
| results/run4/predictions_pplx-flash_test_all.jsonl | `373ea28608d4a95c` |
| results/run5/metrics.json | `85ffe045fcffeea3` |
| results/run5/predictions_jev_test_all.jsonl | `2e0d2a0b09488bcd` |
| results/run5/predictions_pplx-flash_test_all.jsonl | `4e74373448f48cdc` |
| results/run6/pairs_eval_test.jsonl | `b5fcb0bf44f8fd1a` |
| results/run7/c1/predictions_jev_test_all_compact.jsonl | `f7ad2ef25069c495` |
| results/run7/c1/predictions_pplx-flash_test_all_compact.jsonl | `c0064da64d3c1d57` |
| results/run7/c2/predictions_pplx-flash_test_all.jsonl | `8eccd2ce3ca076d3` |
| results/run7/control/meta_jev_test_all.json | `002fcc5341513ff4` |
| results/run7/control/meta_jev_test_all_v3.json | `a56f4f7a99c63116` |
| results/run7/control/meta_pplx-flash_test_all.json | `8e7581b6206b9bca` |
| results/run7/control/meta_pplx-flash_test_all_v3.json | `d308236a5ea8a16f` |
| results/run7/control/predictions_jev_test_all.jsonl | `51f53057f0b4b5e4` |
| results/run7/control/predictions_pplx-flash_test_all.jsonl | `f301f7927b8bfd6b` |
| results/run7/relation/predictions_jev_test_all.jsonl | `3d8fc33839fa285c` |
| results/run7/relation/predictions_pplx-flash_test_all.jsonl | `60956be9925eca97` |
