# Run 8 — report tables

computed 2026-10-25T00:57:51+00:00 · `python -m mergeone.eval.run8_report`

## Verdicts

| hypothesis | result | bar |
|---|---|---|
| R8-H1 | pass | compact − same-day control ≥ −0.02 mean v4 AUC and pair AUC, for jev and pplx-flash |
| R8-H2 | fail | accuracy ≥ 0.80 for ≥ 1 model and (that model) competing + same_area ≥ 0.40 and same_change AUC ≥ run 7 − 0.02 (0.728 / 0.685) |
| R8-H3 | reported (no bar) | no bar; reported |
| R8-H4 | deferred | R6A-H3 and R6A-H4 vs the hosted pplx-flash same-day control (only if C4's candidate is recorded) |
| R8-H5 | pass | fast signal flags exactly follows_conventions + message_matches_diff for pplx-flash run 5 → 7, nothing on jev / run 6A; no flag with |AUC Δ| < 0.03; slow signal flags jev tests_cover_change |

Every comparison is against a same-day full-state control in the same results/run8 directory; earlier runs' numbers are shown beside it, never instead of it. Paired bootstrap: seed 7, 1000 resamples.

## R8-H1 — compact vs same-day full-state control (test)

### jev

v4 (labels: data/run3/dataset.jsonl; version compact jev-1.14.0 · control jev-1.14.0)

| question | n | compact AUC | control AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.617 | 0.625 | -0.008 | -0.008 | [-0.038, 0.020] | 705 |
| weakens_tests | 705 | 0.758 | 0.742 | +0.016 | +0.016 | [-0.012, 0.041] | 705 |
| touches_concurrency | 705 | 0.625 | 0.595 | +0.029 | +0.029 | [0.005, 0.055] | 705 |
| needs_human_review (primary) | 705 | 0.815 | 0.777 | +0.038 | +0.038 | [0.019, 0.057] | 705 |
| exceeds_scope | 705 | 0.803 | 0.798 | +0.005 | +0.005 | [-0.021, 0.035] | 705 |
| breaking_change | 705 | 0.730 | 0.712 | +0.018 | +0.018 | [-0.002, 0.039] | 705 |
| tests_cover_change | 705 | 0.626 | 0.661 | -0.034 | -0.034 | [-0.076, 0.005] | 705 |
| hard_to_revert | 705 | 0.861 | 0.848 | +0.013 | +0.013 | [0.001, 0.026] | 705 |
| **mean (8)** | | 0.729 | 0.720 | **+0.010** | +0.010 | [-0.002, 0.021] | 705 |

Δ = each side's AUC on all its answered PRs (the verdict computation); Δ same PRs and the CI use the PRs answered in both.

v4 input tokens per request, median / p90: compact 1655 / 1961 · control 2424 / 2871 (same PRs)

pair (same_change vs labels.positive, 187 of 187 eval pairs; version compact jev-1.14.0 · control jev-1.14.0): compact 0.820 vs control 0.838 → **Δ -0.018** (same pairs -0.018 [-0.035, -0.004], n paired 187); run 4 full state 0.835 (reference, not the comparison)

pair input tokens per request, median / p90: compact 1845 / 2097 · control 2792 / 3097 (same pairs)

### pplx-flash

v4 (labels: data/run3/dataset.jsonl; version compact pplx-decider-flash-v1.2 · control pplx-decider-flash-v1.2)

| question | n | compact AUC | control AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.595 | 0.611 | -0.016 | -0.016 | [-0.050, 0.018] | 705 |
| weakens_tests | 705 | 0.734 | 0.724 | +0.010 | +0.010 | [-0.023, 0.046] | 705 |
| touches_concurrency | 705 | 0.690 | 0.677 | +0.013 | +0.013 | [-0.013, 0.040] | 705 |
| needs_human_review (primary) | 705 | 0.759 | 0.768 | -0.009 | -0.009 | [-0.047, 0.025] | 705 |
| exceeds_scope | 705 | 0.770 | 0.753 | +0.017 | +0.017 | [-0.013, 0.050] | 705 |
| breaking_change | 705 | 0.714 | 0.717 | -0.003 | -0.003 | [-0.024, 0.019] | 705 |
| tests_cover_change | 705 | 0.595 | 0.626 | -0.031 | -0.031 | [-0.088, 0.018] | 705 |
| hard_to_revert | 705 | 0.760 | 0.747 | +0.013 | +0.013 | [-0.023, 0.050] | 705 |
| **mean (8)** | | 0.702 | 0.703 | **-0.001** | -0.001 | [-0.019, 0.018] | 705 |

Δ = each side's AUC on all its answered PRs (the verdict computation); Δ same PRs and the CI use the PRs answered in both.

v4 input tokens per request, median / p90: compact 7911 / 10365 · control 13647 / 17428 (same PRs)

pair (same_change vs labels.positive, 187 of 187 eval pairs; version compact pplx-decider-flash-v1.2 · control pplx-decider-flash-v1.2): compact 0.825 vs control 0.840 → **Δ -0.015** (same pairs -0.015 [-0.036, 0.002], n paired 187); run 4 full state 0.842 (reference, not the comparison)

pair input tokens per request, median / p90: compact 1564 / 1774 · control 2468 / 2806 (same pairs)

R8-H1 per model: jev: v4 mean Δ +0.010, pair Δ -0.018 → pass; pplx-flash: v4 mean Δ -0.001, pair Δ -0.015 → pass

**R8-H1: pass** — bar: compact − same-day control ≥ −0.02 mean v4 AUC and pair AUC, for jev and pplx-flash

## R8-H2 — relation v2 (100 adjudicated 6B pairs)

100 pairs · gold: backport 28, same_area 10, follow_up 7, duplicate 46, competing 7, unrelated 2 · relation_metrics computed 2026-10-25T00:40:04+00:00

Rule: same_change & !same_base → backport; same_change & same_base → duplicate; builds_on → follow_up; same_goal → competing; any P ≥ low → same_area; else unrelated (same_base unknown → True).

| row | n | class accuracy [95% CI] | competing + same_area | same_change vs rest AUC (n dup) | same_change+backport vs rest AUC | related vs unrelated AUC (n unrel) |
|---|---|---|---|---|---|---|
| jev — relation2 | 100 | 0.740 [0.646, 0.816] | 3/17 = 0.176 | 0.728 (46) | 0.893 | 0.816 (2) |
| pplx-flash — relation2 | 100 | 0.570 [0.472, 0.663] | 1/17 = 0.059 | 0.689 (46) | 0.838 | 0.898 (2) |
| jev — same-day control (run-7 question, today, this rule) | 99 | 0.747 [0.654, 0.823] | 2/16 = 0.125 | 0.679 (46) | 0.868 | 0.992 (2) |
| pplx-flash — same-day control (run-7 question, today, this rule) | 100 | 0.720 [0.625, 0.799] | 0/17 = 0.000 | 0.724 (46) | 0.857 | 0.607 (2) |
| pplx-flash — baseline (run-7 answers, this rule) | 97 | 0.722 [0.625, 0.801] | 0/17 = 0.000 | 0.728 (45) | 0.868 | 0.990 (1) |
| pplx-flash — run-7 post-hoc (run-7 rule, metadata branch; reference) | 97 | 0.722 | | | | |
| jev — baseline (run-7 answers, this rule) | 100 | 0.740 [0.646, 0.816] | 2/17 = 0.118 | 0.685 (46) | 0.883 | 0.995 (2) |
| jev — run-7 post-hoc (run-7 rule, metadata branch; reference) | 100 | 0.720 | | | | |

R8-H2 per model: jev: accuracy 0.740 (fail), competing+same_area 0.176 (fail), same_change AUC 0.728 vs run 7 0.685 − 0.02 (pass) → fail; pplx-flash: accuracy 0.570 (fail), competing+same_area 0.059 (fail), same_change AUC 0.689 vs run 7 0.728 − 0.02 (fail) → fail

**R8-H2: fail** — bar: accuracy ≥ 0.80 for ≥ 1 model and (that model) competing + same_area ≥ 0.40 and same_change AUC ≥ run 7 − 0.02 (0.728 / 0.685)

Per-class accuracy and confusion: results/run8/relation_tables.md.

## R8-H3 — v4 question set (no bar)

### jev

labels: data/run3/dataset.jsonl · versions: v4 jev-1.14.0 · control jev-1.14.0 · run 5 jev-1.13.0

**Same-day v3 control vs run 5, on the eight v4 questions** (drift-free movement of each surviving question)

| question | n | control AUC | run 5 AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.620 | 0.604 | +0.015 | +0.015 | [-0.011, 0.043] | 705 |
| weakens_tests | 705 | 0.733 | 0.771 | -0.039 | -0.039 | [-0.063, -0.017] | 705 |
| touches_concurrency | 705 | 0.601 | 0.605 | -0.004 | -0.004 | [-0.023, 0.015] | 705 |
| needs_human_review (primary) | 705 | 0.779 | 0.777 | +0.002 | +0.002 | [-0.010, 0.015] | 705 |
| exceeds_scope | 705 | 0.799 | 0.795 | +0.004 | +0.004 | [-0.008, 0.016] | 705 |
| breaking_change | 705 | 0.712 | 0.708 | +0.004 | +0.004 | [-0.005, 0.013] | 705 |
| tests_cover_change | 705 | 0.659 | 0.756 | -0.098 | -0.098 | [-0.133, -0.067] | 705 |
| hard_to_revert | 705 | 0.848 | 0.850 | -0.002 | -0.002 | [-0.012, 0.008] | 705 |
| **mean (8)** | | 0.719 | 0.733 | **-0.015** | -0.015 | [-0.021, -0.008] | 705 |

Δ = each side's AUC on all its answered PRs (the verdict computation); Δ same PRs and the CI use the PRs answered in both.

**v4 treatment vs same-day v3 control, same eight questions**

| question | n | v4 AUC | v3 control AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.625 | 0.620 | +0.005 | +0.005 | [-0.013, 0.025] | 705 |
| weakens_tests | 705 | 0.742 | 0.733 | +0.009 | +0.009 | [-0.004, 0.025] | 705 |
| touches_concurrency | 705 | 0.595 | 0.601 | -0.006 | -0.006 | [-0.021, 0.008] | 705 |
| needs_human_review (primary) | 705 | 0.777 | 0.779 | -0.002 | -0.002 | [-0.008, 0.004] | 705 |
| exceeds_scope | 705 | 0.798 | 0.799 | -0.001 | -0.001 | [-0.006, 0.005] | 705 |
| breaking_change | 705 | 0.712 | 0.712 | -0.000 | -0.000 | [-0.004, 0.004] | 705 |
| tests_cover_change | 705 | 0.661 | 0.659 | +0.002 | +0.002 | [-0.021, 0.023] | 705 |
| hard_to_revert | 705 | 0.848 | 0.848 | +0.000 | +0.000 | [-0.005, 0.006] | 705 |
| **mean (8)** | | 0.720 | 0.719 | **+0.001** | +0.001 | [-0.004, 0.006] | 705 |

Δ = each side's AUC on all its answered PRs (the verdict computation); Δ same PRs and the CI use the PRs answered in both.

input tokens per request, median / p90: v4 2424 / 2871 · v3 control 2529 / 2976 (same PRs)

### pplx-flash

labels: data/run3/dataset.jsonl · versions: v4 pplx-decider-flash-v1.2 · control pplx-decider-flash-v1.2 · run 5 pplx-decider-flash-v1.2

**Same-day v3 control vs run 5, on the eight v4 questions** (drift-free movement of each surviving question)

| question | n | control AUC | run 5 AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.611 | 0.655 | -0.044 | -0.044 | [-0.066, -0.023] | 705 |
| weakens_tests | 705 | 0.724 | 0.696 | +0.028 | +0.028 | [-0.014, 0.072] | 705 |
| touches_concurrency | 705 | 0.677 | 0.644 | +0.033 | +0.033 | [-0.008, 0.075] | 705 |
| needs_human_review (primary) | 705 | 0.768 | 0.767 | +0.001 | +0.001 | [-0.026, 0.025] | 705 |
| exceeds_scope | 705 | 0.753 | 0.735 | +0.018 | +0.018 | [-0.021, 0.058] | 705 |
| breaking_change | 705 | 0.717 | 0.682 | +0.035 | +0.035 | [0.001, 0.066] | 705 |
| tests_cover_change | 705 | 0.626 | 0.708 | -0.083 | -0.083 | [-0.122, -0.044] | 705 |
| hard_to_revert | 705 | 0.747 | 0.770 | -0.023 | -0.023 | [-0.073, 0.023] | 705 |
| **mean (8)** | | 0.703 | 0.707 | **-0.004** | -0.004 | [-0.020, 0.012] | 705 |

Δ = each side's AUC on all its answered PRs (the verdict computation); Δ same PRs and the CI use the PRs answered in both.

**v4 treatment vs same-day v3 control, same eight questions**

| question | n | v4 AUC | v3 control AUC | Δ | Δ same PRs | 95% CI (paired, same PRs) | n paired |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.611 | 0.611 | +0.000 | +0.000 | [0.000, 0.000] | 705 |
| weakens_tests | 705 | 0.724 | 0.724 | +0.000 | +0.000 | [0.000, 0.000] | 705 |
| touches_concurrency | 705 | 0.677 | 0.677 | +0.000 | +0.000 | [0.000, 0.000] | 705 |
| needs_human_review (primary) | 705 | 0.768 | 0.768 | +0.000 | +0.000 | [0.000, 0.000] | 705 |
| exceeds_scope | 705 | 0.753 | 0.753 | +0.000 | +0.000 | [0.000, 0.000] | 705 |
| breaking_change | 705 | 0.717 | 0.717 | +0.000 | +0.000 | [0.000, 0.000] | 705 |
| tests_cover_change | 705 | 0.626 | 0.626 | +0.000 | +0.000 | [0.000, 0.000] | 705 |
| hard_to_revert | 705 | 0.747 | 0.747 | +0.000 | +0.000 | [0.000, 0.000] | 705 |
| **mean (8)** | | 0.703 | 0.703 | **+0.000** | +0.000 | [0.000, 0.000] | 705 |

Δ = each side's AUC on all its answered PRs (the verdict computation); Δ same PRs and the CI use the PRs answered in both.

input tokens per request, median / p90: v4 13647 / 17428 · v3 control 17050 / 21776 (same PRs)

mean v4 AUC (eight questions): jev 0.720; pplx-flash 0.703

**R8-H3: reported (no bar)**

## R8-H4 — registry row (self-hosted)

**deferred (results/run8/c4/ not present)**

## R8-H5 — drift replay

fast signal source: Go replay (results/run8/drift_replay.json, `mergeone drift-replay --json`)

| comparison | kind | fast signal flags | slow (reference: AUC Δ < −0.05) |
|---|---|---|---|
| pplx-flash_val_run5_to_run7r0 | pplx-flash | follows_conventions, message_matches_diff | — |
| pplx-flash_test_run5_to_run7control | pplx-flash | follows_conventions, message_matches_diff | — |
| jev_test_run5_to_run7control | jev | nothing | — |
| pplx-flash_test_run5hosted_to_run6Alocal | run6a | nothing | — |

slow signal: **ok** — pplx-flash_val_run5_to_run7r0: follows_conventions, message_matches_diff; pplx-flash_test_run5_to_run7control: follows_conventions, message_matches_diff, tests_cover_change; jev_test_run5_to_run7control: tests_cover_change; pplx-flash_test_run5hosted_to_run6Alocal: follows_conventions, message_matches_diff, tests_cover_change
(the right-hand column is drift.json AUC delta < −0.05 (reference only; not the slow signal of the verdict))

false positives (flagged with |AUC Δ| < 0.03 on identical rows): none

clauses: pplx_exact pass; jev_nothing pass; run6a_nothing pass; no_false_positive pass; slow_jev_tests_cover_change pass

**R8-H5: pass** — bar: fast signal flags exactly follows_conventions + message_matches_diff for pplx-flash run 5 → 7, nothing on jev / run 6A; no flag with |AUC Δ| < 0.03; slow signal flags jev tests_cover_change

## Inputs

| path | sha256 |
|---|---|
| data/run3/dataset.jsonl | `d1a6c6325666cdab` |
| results/run4/metrics.json | `7704b439eea0e4f3` |
| results/run5/metrics.json | `85ffe045fcffeea3` |
| results/run5/predictions_jev_test_all.jsonl | `2e0d2a0b09488bcd` |
| results/run5/predictions_pplx-flash_test_all.jsonl | `4e74373448f48cdc` |
| results/run6/pairs_eval_test.jsonl | `b5fcb0bf44f8fd1a` |
| results/run8/drift_replay.json | `d040570406dd91d0` |
| results/run8/drift_slow.json | `c1b5d0690a078c6d` |
| results/run8/pair/predictions_jev_test_all.jsonl | `d92d0ac7c1178ed1` |
| results/run8/pair/predictions_jev_test_all_compact.jsonl | `2de18f18a66ce27d` |
| results/run8/pair/predictions_pplx-flash_test_all.jsonl | `d78616914536c22c` |
| results/run8/pair/predictions_pplx-flash_test_all_compact.jsonl | `38d9fe882c10fb93` |
| results/run8/relation_metrics.json | `c52cb5c289eb2790` |
| results/run8/v4/predictions_jev_test_all.jsonl | `92775fc83bd8c2a8` |
| results/run8/v4/predictions_jev_test_all_compact.jsonl | `f57c2bc97c7aa360` |
| results/run8/v4/predictions_pplx-flash_test_all.jsonl | `5db226500127139d` |
| results/run8/v4/predictions_pplx-flash_test_all_compact.jsonl | `8529413360e67b3a` |
| results/run8prep/drift.json | `144a1e3c9b1d0ed2` |
