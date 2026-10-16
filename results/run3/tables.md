# Run 3 — agent-authored PRs, outcome labels

val 1491 PRs · test 705 PRs across 8 test repos · test base rates: rejected 0.468, bad outcome 0.468

## Primary: calibrated `needs_human_review` → rejected (closed-unmerged ∨ change-requested), test repos

| model | n | ROC-AUC [95% CI] | PR-AUC | R@FPR5 | ECE | p50 ms | $/1k |
|---|---|---|---|---|---|---|---|
| haiku | 705 | 0.551 [0.510, 0.594] | 0.506 | 0.018 | 0.119 | 1308 | 0.369 |
| jev | 705 | 0.550 [0.510, 0.589] | 0.505 | 0.045 | 0.110 | 260 | 0.090 |
| rules | 705 | 0.528 [0.489, 0.566] | 0.486 | 0.000 | 0.120 | 0 | 0.000 |

## Default policy verdicts on test (bad outcome = closed ∨ change-requested ∨ reverted)

| model | pass % | pass bad-rate | hold+block % | hold+block bad-rate | base |
|---|---|---|---|---|---|
| haiku | 59.0 | 0.445 | 41.0 | 0.502 | 0.468 |
| jev | 35.3 | 0.410 | 64.7 | 0.500 | 0.468 |
| rules | 68.2 | 0.466 | 31.8 | 0.473 | 0.468 |

## Every question vs every outcome (ROC-AUC, test)

### haiku

| question | rejected | closed_unmerged | change_requested | discussed | iterated | reverted_within_30d | merged_untouched |
|---|---|---|---|---|---|---|---|
| is_logic_change | 0.485 | 0.472 | 0.564 | 0.582 | 0.629 | — | 0.552 |
| weakens_tests | 0.553 | 0.550 | 0.523 | 0.529 | 0.805 | — | 0.546 |
| message_matches_diff | 0.400 | 0.389 | 0.545 | 0.497 | 0.290 | — | 0.482 |
| touches_concurrency | 0.488 | 0.481 | 0.540 | 0.507 | 0.647 | — | 0.628 |
| needs_human_review | 0.551 | 0.557 | 0.456 | 0.483 | 0.763 | — | 0.650 |

### jev

| question | rejected | closed_unmerged | change_requested | discussed | iterated | reverted_within_30d | merged_untouched |
|---|---|---|---|---|---|---|---|
| is_logic_change | 0.480 | 0.459 | 0.609 | 0.552 | 0.610 | — | 0.527 |
| weakens_tests | 0.533 | 0.521 | 0.528 | 0.543 | 0.745 | — | 0.504 |
| message_matches_diff | 0.411 | 0.415 | 0.466 | 0.467 | 0.256 | — | 0.460 |
| touches_concurrency | 0.472 | 0.460 | 0.590 | 0.497 | 0.589 | — | 0.633 |
| needs_human_review | 0.550 | 0.535 | 0.597 | 0.529 | 0.697 | — | 0.574 |

### rules

| question | rejected | closed_unmerged | change_requested | discussed | iterated | reverted_within_30d | merged_untouched |
|---|---|---|---|---|---|---|---|
| is_logic_change | 0.489 | 0.477 | 0.566 | 0.527 | 0.730 | — | 0.663 |
| weakens_tests | 0.499 | 0.493 | 0.519 | 0.516 | 0.491 | — | 0.486 |
| message_matches_diff | 0.421 | 0.439 | 0.373 | 0.470 | 0.294 | — | 0.431 |
| touches_concurrency | 0.473 | 0.444 | 0.682 | 0.523 | 0.403 | — | 0.449 |
| needs_human_review | 0.528 | 0.526 | 0.518 | 0.488 | 0.721 | — | 0.622 |

## By agent (test)

| model | agent | n | rejected rate | ROC-AUC | pass % | pass bad-rate |
|---|---|---|---|---|---|---|
| haiku | claude-app | 153 | 0.425 | 0.596 | 68.0 | 0.385 |
| haiku | claude-code | 120 | 0.500 | 0.465 | 17.5 | 0.524 |
| haiku | copilot | 265 | 0.479 | 0.605 | 66.4 | 0.409 |
| haiku | cursor | 114 | 0.658 | 0.552 | 78.1 | 0.674 |
| haiku | devin | 53 | 0.057 | 0.413 | 49.1 | 0.077 |
| jev | claude-app | 153 | 0.425 | 0.602 | 48.4 | 0.338 |
| jev | claude-code | 120 | 0.500 | 0.587 | 10.8 | 0.308 |
| jev | copilot | 265 | 0.479 | 0.552 | 41.5 | 0.436 |
| jev | cursor | 114 | 0.658 | 0.521 | 32.5 | 0.622 |
| jev | devin | 53 | 0.057 | 0.427 | 28.3 | 0.133 |
| rules | claude-app | 153 | 0.425 | 0.555 | 59.5 | 0.352 |
| rules | claude-code | 120 | 0.500 | 0.652 | 61.7 | 0.432 |
| rules | copilot | 265 | 0.479 | 0.562 | 81.5 | 0.514 |
| rules | cursor | 114 | 0.658 | 0.566 | 64.9 | 0.649 |
| rules | devin | 53 | 0.057 | 0.440 | 49.1 | 0.038 |

## By repo (test)

| model | repo | n | rejected rate | ROC-AUC | pass % | pass bad-rate |
|---|---|---|---|---|---|---|
| haiku | Azure/azure-sdk-for-net | 120 | 0.508 | 0.643 | 60.8 | 0.397 |
| haiku | Comfy-Org/ComfyUI_frontend | 120 | 0.525 | 0.557 | 65.0 | 0.487 |
| haiku | VocaHQ/vocalinux | 53 | 0.057 | 0.413 | 49.1 | 0.077 |
| haiku | compdemocracy/polis | 120 | 0.500 | 0.465 | 17.5 | 0.524 |
| haiku | mattermost/mattermost | 114 | 0.658 | 0.552 | 78.1 | 0.674 |
| haiku | microsoft/semantic-kernel | 25 | 0.240 | 0.636 | 64.0 | 0.188 |
| haiku | microsoft/vscode | 120 | 0.500 | 0.555 | 72.5 | 0.460 |
| haiku | mozilla-ai/otari | 33 | 0.061 | 0.516 | 78.8 | 0.077 |
| jev | Azure/azure-sdk-for-net | 120 | 0.508 | 0.619 | 41.7 | 0.420 |
| jev | Comfy-Org/ComfyUI_frontend | 120 | 0.525 | 0.508 | 38.3 | 0.500 |
| jev | VocaHQ/vocalinux | 53 | 0.057 | 0.427 | 28.3 | 0.133 |
| jev | compdemocracy/polis | 120 | 0.500 | 0.587 | 10.8 | 0.308 |
| jev | mattermost/mattermost | 114 | 0.658 | 0.521 | 32.5 | 0.622 |
| jev | microsoft/semantic-kernel | 25 | 0.240 | 0.553 | 48.0 | 0.250 |
| jev | microsoft/vscode | 120 | 0.500 | 0.470 | 40.0 | 0.500 |
| jev | mozilla-ai/otari | 33 | 0.061 | 0.387 | 84.8 | 0.071 |
| rules | Azure/azure-sdk-for-net | 120 | 0.508 | 0.557 | 77.5 | 0.527 |
| rules | Comfy-Org/ComfyUI_frontend | 120 | 0.525 | 0.425 | 50.0 | 0.500 |
| rules | VocaHQ/vocalinux | 53 | 0.057 | 0.440 | 49.1 | 0.038 |
| rules | compdemocracy/polis | 120 | 0.500 | 0.652 | 61.7 | 0.432 |
| rules | mattermost/mattermost | 114 | 0.658 | 0.566 | 64.9 | 0.649 |
| rules | microsoft/semantic-kernel | 25 | 0.240 | 0.627 | 60.0 | 0.267 |
| rules | microsoft/vscode | 120 | 0.500 | 0.612 | 90.0 | 0.537 |
| rules | mozilla-ai/otari | 33 | 0.061 | 0.355 | 93.9 | 0.065 |

## Pre-registered criteria

| hypothesis | result | bar | pass |
|---|---|---|---|
| R3-H1[haiku] | {"roc_auc": 0.551, "not_transferring": true} | >= 0.70 (fail < 0.60) | ❌ |
| R3-H2[haiku] | {"pass_bad_rate": 0.445, "base": 0.468} | <= 0.5 x base | ❌ |
| R3-H3[haiku] | {"holdblock_bad_rate": 0.502, "base": 0.468} | >= 1.5 x base | ❌ |
| R3-H4[haiku] | {"n_non_default_base": 80, "n_stacked_linked": 80, "n_changed_by_rule": 0, "frac_changed": 0.0} | >= 5% of linked stacked PRs change verdict (reported, no correctness threshold) | ❌ |
| R3-H1[jev] | {"roc_auc": 0.55, "not_transferring": true} | >= 0.70 (fail < 0.60) | ❌ |
| R3-H2[jev] | {"pass_bad_rate": 0.41, "base": 0.468} | <= 0.5 x base | ❌ |
| R3-H3[jev] | {"holdblock_bad_rate": 0.5, "base": 0.468} | >= 1.5 x base | ❌ |
| R3-H4[jev] | {"n_non_default_base": 80, "n_stacked_linked": 80, "n_changed_by_rule": 0, "frac_changed": 0.0} | >= 5% of linked stacked PRs change verdict (reported, no correctness threshold) | ❌ |
| R3-H1[rules] | {"roc_auc": 0.528, "not_transferring": true} | >= 0.70 (fail < 0.60) | ❌ |
| R3-H2[rules] | {"pass_bad_rate": 0.466, "base": 0.468} | <= 0.5 x base | ❌ |
| R3-H3[rules] | {"holdblock_bad_rate": 0.473, "base": 0.468} | >= 1.5 x base | ❌ |
| R3-H4[rules] | {"n_non_default_base": 80, "n_stacked_linked": 80, "n_changed_by_rule": 0, "frac_changed": 0.0} | >= 5% of linked stacked PRs change verdict (reported, no correctness threshold) | ❌ |
| R3-H5[haiku x100] | {"p99_ms": 2880.291, "model_p99_ms": 2797.159, "error_rate": 0.003} | p99 <= model p99 + 200 ms and 0 errors at 100x | ❌ |
| R3-H5[jev x100] | {"p99_ms": 416.839, "model_p99_ms": 390.181, "error_rate": 0.0} | p99 <= model p99 + 200 ms and 0 errors at 100x | ✅ |

## Close reasons (sample, Claude Fable 5.1, reporting only)

| reason | n |
|---|---|
| superseded_or_duplicate | 72 |
| scope_or_direction | 62 |
| quality_or_bugs | 37 |
| abandoned_or_stale | 20 |
| other | 9 |
