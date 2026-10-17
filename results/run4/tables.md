# Run 4 — PR memory and duplicate detection

## Human labels on closed-unmerged PRs (regex over maintainer comments)

| label | n |
|---|---|
| not_marked | 757 |
| superseded_unknown | 113 |
| superseded_by | 51 |
| superseded_by_outside_memory | 21 |

## Retrieval (top-5, deterministic)

| split | gold pairs | recall@5 |
|---|---|---|
| test | 29 | 0.552 |
| val | 22 | 0.727 |

retrieval latency p50 5.93 ms · p99 197.63 ms over 2196 PRs

## Pair question `same_change` → duplicate_of

| model | split | pairs | positives | ROC-AUC [95% CI] | PR-AUC |
|---|---|---|---|---|---|
| haiku | test | 187 | 29 | 0.765 [0.679, 0.841] | 0.421 |
| haiku | val | 133 | 22 | 0.914 [0.863, 0.961] | 0.543 |
| jev | test | 187 | 29 | 0.835 [0.761, 0.901] | 0.522 |
| jev | val | 133 | 22 | 0.904 [0.848, 0.953] | 0.570 |
| pplx-flash | test | 187 | 29 | 0.842 [0.764, 0.906] | 0.482 |
| pplx-flash | val | 133 | 22 | 0.910 [0.852, 0.957] | 0.590 |
| pplx | test | 187 | 29 | 0.869 [0.795, 0.924] | 0.562 |
| pplx | val | 133 | 22 | 0.920 [0.865, 0.964] | 0.608 |
| rules | test | 187 | 29 | 0.579 [0.451, 0.699] | 0.341 |
| rules | val | 133 | 22 | 0.610 [0.439, 0.749] | 0.282 |

## Verdict level on run-3 test PRs (default policy, then + dedup rule duplicate_of ≥ 0.7 → hold)

| model | variant | pass % | pass bad-rate | closed PRs not passed | dedup holds |
|---|---|---|---|---|---|
| haiku | without_dedup | 59.0 | 0.445 | 44.9% | 0 |
| haiku | with_dedup | 50.9 | 0.409 | 55.7% | 112 |
| jev | without_dedup | 35.3 | 0.410 | 67.5% | 0 |
| jev | with_dedup | 30.8 | 0.355 | 75.7% | 136 |
| rules | without_dedup | 68.2 | 0.466 | 31.1% | 0 |
| rules | with_dedup | 50.4 | 0.473 | 49.2% | 183 |

## Pre-registered criteria

| hypothesis | result | bar | pass |
|---|---|---|---|
| R4-H1 | {"recall_at_5": 0.552, "n_gold": 29, "not_working": false} | >= 0.60 (not working < 0.40) | ❌ |
| R4-H3[haiku] | {"pass_bad_rate": 0.409, "base": 0.468, "run3_pass_bad_rate": 0.445} | <= 0.5 x base | ❌ |
| R4-H4[haiku] | {"closed_nonpass_share_run3": 0.449, "with_dedup": 0.557, "delta_pp": 10.82} | >= +15 pp | ❌ |
| R4-H2[haiku] | {"roc_auc": 0.765, "n": 187} | >= 0.80 | ❌ |
| R4-H3[jev] | {"pass_bad_rate": 0.355, "base": 0.468, "run3_pass_bad_rate": 0.41} | <= 0.5 x base | ❌ |
| R4-H4[jev] | {"closed_nonpass_share_run3": 0.675, "with_dedup": 0.757, "delta_pp": 8.197} | >= +15 pp | ❌ |
| R4-H2[jev] | {"roc_auc": 0.835, "n": 187} | >= 0.80 | ✅ |
| R4-H2[pplx-flash] | {"roc_auc": 0.842, "n": 187} | >= 0.80 | ✅ |
| R4-H2[pplx] | {"roc_auc": 0.869, "n": 187} | >= 0.80 | ✅ |
| R4-H3[rules] | {"pass_bad_rate": 0.473, "base": 0.468, "run3_pass_bad_rate": 0.466} | <= 0.5 x base | ❌ |
| R4-H4[rules] | {"closed_nonpass_share_run3": 0.311, "with_dedup": 0.492, "delta_pp": 18.033} | >= +15 pp | ✅ |
| R4-H5 | {"p99_ms": 197.635} | retrieval p99 <= 20 ms; <= 1+k model calls per PR | ❌ |
