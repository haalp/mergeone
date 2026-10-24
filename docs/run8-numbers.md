# Run 8 prep — routing, v4 and drift from existing predictions

No model call. `python scripts/run8_prep.py` → `results/run8prep/{routing,v4,drift}.json`. AUC per v3 question vs `iterated` (run5._score, labels data/run3/dataset.jsonl); CIs are paired bootstraps over PRs, seed 7, 1,000 resamples. Everything here is post-hoc on data already read once; nothing is a verdict.

## 1. Per-question routing

Route each v3 question to the model with the higher AUC; compare the routed mean with each single model on the same PRs. Chosen on test it is an upper bound (the choice has seen the labels it is scored on); chosen on val it is what run 8 could pre-register.

**Run 7 same-day controls (test) — choice made on test (upper bound)** (n 682 PRs answered by every model)

| question | jev AUC | pplx-flash AUC | routed to |
|---|---|---|---|
| is_logic_change | 0.620 | 0.618 | jev |
| weakens_tests | 0.729 | 0.719 | jev |
| message_matches_diff | 0.226 | 0.323 | pplx-flash |
| touches_concurrency | 0.604 | 0.684 | pplx-flash |
| needs_human_review | 0.789 | 0.793 | pplx-flash |
| exceeds_scope | 0.803 | 0.765 | jev |
| breaking_change | 0.725 | 0.730 | pplx-flash |
| tests_cover_change | 0.667 | 0.642 | jev |
| hard_to_revert | 0.860 | 0.759 | jev |
| follows_conventions | 0.214 | 0.252 | pplx-flash |
| **mean** | 0.624 | 0.628 | **0.646** |

| routed − single | Δ mean AUC | 95% CI (paired) |
|---|---|---|
| vs jev | +0.022 | [+0.011, +0.032] |
| vs pplx-flash | +0.018 | [+0.010, +0.026] |

**Run 7 same-day controls (test) — choice made on run 5 val (both models)** (n 682 PRs answered by every model)

| question | jev AUC | pplx-flash AUC | routed to |
|---|---|---|---|
| is_logic_change | 0.620 | 0.618 | pplx-flash |
| weakens_tests | 0.729 | 0.719 | pplx-flash |
| message_matches_diff | 0.226 | 0.323 | pplx-flash |
| touches_concurrency | 0.604 | 0.684 | jev |
| needs_human_review | 0.789 | 0.793 | pplx-flash |
| exceeds_scope | 0.803 | 0.765 | jev |
| breaking_change | 0.725 | 0.730 | pplx-flash |
| tests_cover_change | 0.667 | 0.642 | jev |
| hard_to_revert | 0.860 | 0.759 | pplx-flash |
| follows_conventions | 0.214 | 0.252 | pplx-flash |
| **mean** | 0.624 | 0.628 | **0.627** |

| routed − single | Δ mean AUC | 95% CI (paired) |
|---|---|---|
| vs jev | +0.003 | [-0.008, +0.016] |
| vs pplx-flash | -0.002 | [-0.009, +0.006] |

**Run 7 same-day controls (test) — choice made on val: jev run 5, pplx-flash run 7 round 0** (n 682 PRs answered by every model)

| question | jev AUC | pplx-flash AUC | routed to |
|---|---|---|---|
| is_logic_change | 0.620 | 0.618 | pplx-flash |
| weakens_tests | 0.729 | 0.719 | pplx-flash |
| message_matches_diff | 0.226 | 0.323 | pplx-flash |
| touches_concurrency | 0.604 | 0.684 | jev |
| needs_human_review | 0.789 | 0.793 | pplx-flash |
| exceeds_scope | 0.803 | 0.765 | jev |
| breaking_change | 0.725 | 0.730 | jev |
| tests_cover_change | 0.667 | 0.642 | jev |
| hard_to_revert | 0.860 | 0.759 | pplx-flash |
| follows_conventions | 0.214 | 0.252 | jev |
| **mean** | 0.624 | 0.628 | **0.622** |

| routed − single | Δ mean AUC | 95% CI (paired) |
|---|---|---|
| vs jev | -0.001 | [-0.010, +0.008] |
| vs pplx-flash | -0.006 | [-0.016, +0.004] |

*Reading: against the better single model (pplx-flash, mean 0.628), routing chosen on test gains +0.018 [+0.010, +0.026]; chosen on run 5 val -0.002 [-0.009, +0.006] (4 of 10 choices differ from the test choice: is_logic_change, weakens_tests, touches_concurrency, hard_to_revert); chosen on val with run 7 r0 for pplx-flash -0.006 [-0.016, +0.004] (6 differ). The pre-registrable version gains nothing on the same-day controls: on most questions the two models are within 0.01–0.02 and val does not pick the winner.*

Val AUC used for the choice (run 5 val, n 1,491 per model; run 7 r0 pplx-flash n 1491):

| question | run5:jev | run5:pplx-flash | run5:haiku | run5:pplx | run7r0:pplx-flash |
|---|---|---|---|---|---|
| is_logic_change | 0.602 | 0.624 | 0.606 | 0.562 | 0.627 |
| weakens_tests | 0.626 | 0.652 | 0.597 | 0.641 | 0.642 |
| message_matches_diff | 0.368 | 0.525 | 0.392 | 0.376 | 0.423 |
| touches_concurrency | 0.625 | 0.624 | 0.643 | 0.647 | 0.606 |
| needs_human_review | 0.671 | 0.694 | 0.666 | 0.647 | 0.713 |
| exceeds_scope | 0.658 | 0.639 | 0.663 | 0.681 | 0.637 |
| breaking_change | 0.562 | 0.566 | 0.611 | 0.591 | 0.535 |
| tests_cover_change | 0.605 | 0.567 | 0.532 | 0.530 | 0.553 |
| hard_to_revert | 0.558 | 0.657 | 0.644 | 0.583 | 0.629 |
| follows_conventions | 0.432 | 0.660 | 0.369 | 0.532 | 0.430 |

### Reference: run 5 test (2026-10-18, jev-1.13.0)

**Run 5 test, jev + pplx-flash — choice on test** (n 705 PRs answered by every model)

| question | jev AUC | pplx-flash AUC | routed to |
|---|---|---|---|
| is_logic_change | 0.604 | 0.655 | pplx-flash |
| weakens_tests | 0.771 | 0.696 | jev |
| message_matches_diff | 0.233 | 0.504 | pplx-flash |
| touches_concurrency | 0.605 | 0.644 | pplx-flash |
| needs_human_review | 0.777 | 0.767 | jev |
| exceeds_scope | 0.795 | 0.735 | jev |
| breaking_change | 0.708 | 0.682 | jev |
| tests_cover_change | 0.756 | 0.708 | jev |
| hard_to_revert | 0.850 | 0.770 | jev |
| follows_conventions | 0.235 | 0.641 | pplx-flash |
| **mean** | 0.633 | 0.680 | **0.710** |

| routed − single | Δ mean AUC | 95% CI (paired) |
|---|---|---|
| vs jev | +0.077 | [+0.060, +0.094] |
| vs pplx-flash | +0.030 | [+0.018, +0.044] |

**Run 5 test, jev + pplx-flash — choice on run 5 val** (n 705 PRs answered by every model)

| question | jev AUC | pplx-flash AUC | routed to |
|---|---|---|---|
| is_logic_change | 0.604 | 0.655 | pplx-flash |
| weakens_tests | 0.771 | 0.696 | pplx-flash |
| message_matches_diff | 0.233 | 0.504 | pplx-flash |
| touches_concurrency | 0.605 | 0.644 | jev |
| needs_human_review | 0.777 | 0.767 | pplx-flash |
| exceeds_scope | 0.795 | 0.735 | jev |
| breaking_change | 0.708 | 0.682 | pplx-flash |
| tests_cover_change | 0.756 | 0.708 | jev |
| hard_to_revert | 0.850 | 0.770 | pplx-flash |
| follows_conventions | 0.235 | 0.641 | pplx-flash |
| **mean** | 0.633 | 0.680 | **0.687** |

| routed − single | Δ mean AUC | 95% CI (paired) |
|---|---|---|
| vs jev | +0.054 | [+0.033, +0.074] |
| vs pplx-flash | +0.007 | [-0.001, +0.015] |

**Run 5 test, four models — choice on test** (n 705 PRs answered by every model)

| question | jev AUC | pplx-flash AUC | haiku AUC | pplx AUC | routed to |
|---|---|---|---|---|---|
| is_logic_change | 0.604 | 0.655 | 0.665 | 0.580 | haiku |
| weakens_tests | 0.771 | 0.696 | 0.809 | 0.789 | haiku |
| message_matches_diff | 0.233 | 0.504 | 0.356 | 0.289 | pplx-flash |
| touches_concurrency | 0.605 | 0.644 | 0.642 | 0.654 | pplx |
| needs_human_review | 0.777 | 0.767 | 0.738 | 0.740 | jev |
| exceeds_scope | 0.795 | 0.735 | 0.781 | 0.758 | jev |
| breaking_change | 0.708 | 0.682 | 0.750 | 0.749 | haiku |
| tests_cover_change | 0.756 | 0.708 | 0.624 | 0.596 | jev |
| hard_to_revert | 0.850 | 0.770 | 0.808 | 0.841 | jev |
| follows_conventions | 0.235 | 0.641 | 0.307 | 0.374 | pplx-flash |
| **mean** | 0.633 | 0.680 | 0.648 | 0.637 | **0.720** |

| routed − single | Δ mean AUC | 95% CI (paired) |
|---|---|---|
| vs jev | +0.087 | [+0.069, +0.105] |
| vs pplx-flash | +0.040 | [+0.025, +0.059] |
| vs haiku | +0.072 | [+0.051, +0.091] |
| vs pplx | +0.083 | [+0.060, +0.112] |

**Run 5 test, four models — choice on run 5 val** (n 705 PRs answered by every model)

| question | jev AUC | pplx-flash AUC | haiku AUC | pplx AUC | routed to |
|---|---|---|---|---|---|
| is_logic_change | 0.604 | 0.655 | 0.665 | 0.580 | pplx-flash |
| weakens_tests | 0.771 | 0.696 | 0.809 | 0.789 | pplx-flash |
| message_matches_diff | 0.233 | 0.504 | 0.356 | 0.289 | pplx-flash |
| touches_concurrency | 0.605 | 0.644 | 0.642 | 0.654 | pplx |
| needs_human_review | 0.777 | 0.767 | 0.738 | 0.740 | pplx-flash |
| exceeds_scope | 0.795 | 0.735 | 0.781 | 0.758 | pplx |
| breaking_change | 0.708 | 0.682 | 0.750 | 0.749 | haiku |
| tests_cover_change | 0.756 | 0.708 | 0.624 | 0.596 | jev |
| hard_to_revert | 0.850 | 0.770 | 0.808 | 0.841 | pplx-flash |
| follows_conventions | 0.235 | 0.641 | 0.307 | 0.374 | pplx-flash |
| **mean** | 0.633 | 0.680 | 0.648 | 0.637 | **0.695** |

| routed − single | Δ mean AUC | 95% CI (paired) |
|---|---|---|
| vs jev | +0.062 | [+0.043, +0.081] |
| vs pplx-flash | +0.015 | [+0.005, +0.026] |
| vs haiku | +0.047 | [+0.023, +0.069] |
| vs pplx | +0.058 | [+0.036, +0.085] |

*Reading: before the drift, val-chosen routing over jev + pplx-flash was +0.007 [-0.001, +0.015] vs pplx-flash, over four models +0.015 [+0.005, +0.026] vs pplx-flash; most of the larger test-chosen gains come from picking the least-bad answer to the two questions that score below 0.5 (section 2).*

## 2. v4 = v3 minus `follows_conventions` and `message_matches_diff`

| set | model | n | mean v3 AUC (10) | mean v4 AUC (8) | Δ | 95% CI (paired) | follows_conventions | message_matches_diff |
|---|---|---|---|---|---|---|---|---|
| run7 control test | jev | 705 | 0.617 | 0.716 | +0.098 | [+0.084, +0.110] | 0.224 | 0.225 |
| run7 control test | pplx-flash | 682 | 0.628 | 0.714 | +0.085 | [+0.071, +0.099] | 0.252 | 0.323 |
| run5 test | jev | 705 | 0.633 | 0.733 | +0.100 | [+0.086, +0.113] | 0.235 | 0.233 |
| run5 test | pplx-flash | 705 | 0.680 | 0.707 | +0.027 | [+0.016, +0.038] | 0.641 | 0.504 |
| run5 test | haiku | 705 | 0.648 | 0.727 | +0.079 | [+0.063, +0.094] | 0.307 | 0.356 |
| run5 test | pplx | 705 | 0.637 | 0.713 | +0.076 | [+0.063, +0.089] | 0.374 | 0.289 |
| run5 test | rules | 705 | 0.516 | 0.546 | +0.030 | [+0.023, +0.037] | 0.500 | 0.294 |

*Reading: v4 is higher for every model, mechanically — the two dropped questions score below the other eight (below 0.5 everywhere except pplx-flash in run 5), so no question answers better. What v4 does change: run 5 → run 7 control, pplx-flash mean moves -0.052 on v3 but +0.007 on v4 (the drift sits in the two dropped questions); jev moves -0.016 on v3, -0.018 on v4 (tests_cover_change).*

Policy surface naming either question (`docs/POLICY.md`, `mergeone/gate/policy.yaml`, `gate/internal/initcmd`, any `*.mergeone.yaml`):

| path:line | question | kind | text |
|---|---|---|---|
| `docs/POLICY.md:12` | follows_conventions | comment/doc | `| `question_set` | `v2` (default, five questions) or `v3` (ten; `hard_to_revert`, `exceeds_scope` recommended, `follows_conventions` not — run 5) |` |
| `mergeone/gate/policy.yaml:52` | message_matches_diff | rule condition | `when: { message_matches_diff: "<= 0.4", is_logic_change: ">= 0.5" }` |
| `gate/internal/initcmd/templates/policy.yaml.tmpl:5` | message_matches_diff | comment/doc | `#      with a probability: is_logic_change, weakens_tests, message_matches_diff, touches_concurrency,` |
| `gate/internal/initcmd/templates/policy.yaml.tmpl:79` | message_matches_diff | rule condition | `when: { message_matches_diff: "<= 0.4", is_logic_change: ">= 0.5" }` |

*Reading: 2 rule condition(s) use a dropped question — the `message-mismatch` hold (gate/internal/initcmd/templates/policy.yaml.tmpl, mergeone/gate/policy.yaml) reads `message_matches_diff` (a v2 question, in the default v2 set). Both engines treat an unanswered question as a non-match, so under a v4 question set that rule would silently never fire: v4 needs the rule dropped or the question kept. `follows_conventions` is in no rule (POLICY.md already recommends against it).*

## 3. Drift metric on existing data

shift(q) = |mean p_q(now) − mean p_q(then)|, auc_delta(q) = AUC(now) − AUC(then), on the PRs both answered. Trip = shift strictly above the threshold.

**pplx-flash val: run 5 (2026-10-18) → run 7 r0 (2026-10-23)**

| question | n | mean p then | mean p now | shift | AUC then | AUC now | auc_delta | 95% CI |
|---|---|---|---|---|---|---|---|---|
| is_logic_change | 1491 | 0.553 | 0.641 | 0.088 | 0.624 | 0.627 | +0.003 | [-0.020, +0.026] |
| weakens_tests | 1491 | 0.178 | 0.064 | **0.114** | 0.652 | 0.642 | -0.010 | [-0.066, +0.045] |
| message_matches_diff | 1491 | 0.672 | 0.929 | **0.257** | 0.525 | 0.423 | -0.102 | [-0.217, +0.022] |
| touches_concurrency | 1491 | 0.096 | 0.042 | 0.054 | 0.624 | 0.606 | -0.018 | [-0.070, +0.030] |
| needs_human_review | 1491 | 0.490 | 0.567 | 0.077 | 0.694 | 0.713 | +0.020 | [-0.012, +0.049] |
| exceeds_scope | 1491 | 0.302 | 0.144 | **0.158** | 0.639 | 0.637 | -0.003 | [-0.045, +0.044] |
| breaking_change | 1491 | 0.259 | 0.170 | 0.089 | 0.566 | 0.535 | -0.031 | [-0.080, +0.006] |
| tests_cover_change | 1491 | 0.443 | 0.493 | 0.049 | 0.567 | 0.553 | -0.013 | [-0.046, +0.021] |
| hard_to_revert | 1491 | 0.170 | 0.136 | 0.035 | 0.657 | 0.629 | -0.028 | [-0.079, +0.019] |
| follows_conventions | 1491 | 0.264 | 0.823 | **0.559** | 0.660 | 0.430 | -0.231 | [-0.337, -0.125] |

**jev test: run 5 (jev-1.13.0) → run 7 control (jev-1.14.0)**

| question | n | mean p then | mean p now | shift | AUC then | AUC now | auc_delta | 95% CI |
|---|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.728 | 0.719 | 0.010 | 0.604 | 0.608 | +0.003 | [-0.020, +0.028] |
| weakens_tests | 705 | 0.092 | 0.097 | 0.006 | 0.771 | 0.729 | -0.042 | [-0.070, -0.018] |
| message_matches_diff | 705 | 0.730 | 0.701 | 0.028 | 0.233 | 0.225 | -0.008 | [-0.026, +0.009] |
| touches_concurrency | 705 | 0.105 | 0.100 | 0.005 | 0.605 | 0.601 | -0.005 | [-0.024, +0.014] |
| needs_human_review | 705 | 0.691 | 0.661 | 0.029 | 0.777 | 0.776 | -0.001 | [-0.013, +0.011] |
| exceeds_scope | 705 | 0.205 | 0.206 | 0.002 | 0.795 | 0.798 | +0.003 | [-0.009, +0.014] |
| breaking_change | 705 | 0.261 | 0.280 | 0.019 | 0.708 | 0.714 | +0.007 | [-0.003, +0.016] |
| tests_cover_change | 705 | 0.648 | 0.664 | 0.015 | 0.756 | 0.646 | -0.111 | [-0.149, -0.075] |
| hard_to_revert | 705 | 0.171 | 0.164 | 0.007 | 0.850 | 0.854 | +0.003 | [-0.007, +0.014] |
| follows_conventions | 705 | 0.794 | 0.784 | 0.010 | 0.235 | 0.224 | -0.010 | [-0.030, +0.009] |

**pplx-flash test: run 5 → run 7 control**

| question | n | mean p then | mean p now | shift | AUC then | AUC now | auc_delta | 95% CI |
|---|---|---|---|---|---|---|---|---|
| is_logic_change | 682 | 0.606 | 0.683 | 0.076 | 0.660 | 0.618 | -0.042 | [-0.065, -0.021] |
| weakens_tests | 682 | 0.214 | 0.064 | **0.150** | 0.686 | 0.719 | +0.033 | [-0.010, +0.080] |
| message_matches_diff | 682 | 0.669 | 0.921 | **0.252** | 0.499 | 0.323 | -0.177 | [-0.269, -0.080] |
| touches_concurrency | 682 | 0.095 | 0.037 | 0.058 | 0.652 | 0.684 | +0.032 | [-0.013, +0.077] |
| needs_human_review | 682 | 0.523 | 0.599 | 0.075 | 0.781 | 0.793 | +0.012 | [-0.012, +0.035] |
| exceeds_scope | 682 | 0.344 | 0.168 | **0.176** | 0.738 | 0.765 | +0.027 | [-0.017, +0.070] |
| breaking_change | 682 | 0.287 | 0.201 | 0.086 | 0.690 | 0.730 | +0.040 | [+0.006, +0.074] |
| tests_cover_change | 682 | 0.526 | 0.622 | 0.097 | 0.725 | 0.642 | -0.083 | [-0.123, -0.045] |
| hard_to_revert | 682 | 0.170 | 0.147 | 0.023 | 0.778 | 0.759 | -0.019 | [-0.065, +0.021] |
| follows_conventions | 682 | 0.258 | 0.819 | **0.561** | 0.647 | 0.252 | -0.395 | [-0.489, -0.304] |

**reference — pplx-flash test: run 5 hosted → run 6A self-hosted (same weights)**

| question | n | mean p then | mean p now | shift | AUC then | AUC now | auc_delta | 95% CI |
|---|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.611 | 0.592 | 0.019 | 0.655 | 0.660 | +0.005 | [-0.005, +0.017] |
| weakens_tests | 705 | 0.216 | 0.215 | 0.001 | 0.696 | 0.706 | +0.010 | [-0.013, +0.031] |
| message_matches_diff | 705 | 0.671 | 0.641 | 0.031 | 0.504 | 0.508 | +0.005 | [-0.037, +0.045] |
| touches_concurrency | 705 | 0.094 | 0.087 | 0.007 | 0.644 | 0.647 | +0.004 | [-0.013, +0.020] |
| needs_human_review | 705 | 0.527 | 0.496 | 0.031 | 0.767 | 0.770 | +0.002 | [-0.011, +0.015] |
| exceeds_scope | 705 | 0.348 | 0.323 | 0.025 | 0.735 | 0.748 | +0.013 | [-0.002, +0.029] |
| breaking_change | 705 | 0.290 | 0.258 | 0.032 | 0.682 | 0.691 | +0.009 | [-0.005, +0.023] |
| tests_cover_change | 705 | 0.529 | 0.501 | 0.028 | 0.708 | 0.675 | -0.034 | [-0.064, -0.006] |
| hard_to_revert | 705 | 0.170 | 0.209 | 0.040 | 0.770 | 0.714 | -0.056 | [-0.077, -0.034] |
| follows_conventions | 705 | 0.257 | 0.181 | 0.077 | 0.641 | 0.644 | +0.002 | [-0.039, +0.044] |

Which questions trip:

| comparison | shift > 0.10 | shift > 0.15 | shift > 0.20 |
|---|---|---|---|
| pplx-flash val: run 5 (2026-10-18) → run 7 r0 (2026-10-23) | weakens_tests, message_matches_diff, exceeds_scope, follows_conventions | message_matches_diff, exceeds_scope, follows_conventions | message_matches_diff, follows_conventions |
| jev test: run 5 (jev-1.13.0) → run 7 control (jev-1.14.0) | none | none | none |
| pplx-flash test: run 5 → run 7 control | weakens_tests, message_matches_diff, exceeds_scope, follows_conventions | weakens_tests, message_matches_diff, exceeds_scope, follows_conventions | message_matches_diff, follows_conventions |
| reference — pplx-flash test: run 5 hosted → run 6A self-hosted (same weights) | none | none | none |

Shift vs AUC (AUC moved = paired CI on auc_delta excludes 0):

| comparison | max shift | AUC moved | AUC moved, shift ≤ 0.10 | tripped > 0.10, AUC held | tripped > 0.15, AUC held | tripped > 0.20, AUC held |
|---|---|---|---|---|---|---|
| pplx-flash val: run 5 (2026-10-18) → run 7 r0 (2026-10-23) | 0.559 | follows_conventions | none | weakens_tests, message_matches_diff, exceeds_scope | message_matches_diff, exceeds_scope | message_matches_diff |
| jev test: run 5 (jev-1.13.0) → run 7 control (jev-1.14.0) | 0.029 | weakens_tests, tests_cover_change | weakens_tests, tests_cover_change | none | none | none |
| pplx-flash test: run 5 → run 7 control | 0.561 | is_logic_change, message_matches_diff, breaking_change, tests_cover_change, follows_conventions | is_logic_change, breaking_change, tests_cover_change | weakens_tests, exceeds_scope | weakens_tests, exceeds_scope | none |
| reference — pplx-flash test: run 5 hosted → run 6A self-hosted (same weights) | 0.077 | tests_cover_change, hard_to_revert | tests_cover_change, hard_to_revert | none | none | none |

*Reading: different serving of the same weights (run 6A) shifts at most 0.077, so 0.10 has headroom over that; 0.20 catches exactly the two questions that broke, 0.15 and 0.10 also flag `exceeds_scope` (and `weakens_tests`), whose AUC held. Shift is blind to a ranking change at a constant mean: jev `tests_cover_change` lost 0.11 AUC with a 0.015 shift, so a mean-shift monitor needs the gatelog-label AUC check beside it.*
