# Run 5 B — declared assumptions (R5-H4, R5-H4b)

Test repositories have been seen in aggregate in runs 3–4 (PROTOCOL R5 status). The floor is depth-2 directory Jaccard (no model, no assumptions).

## Relation labels (regex over non-bot comments)

| label | n |
|---|---|
| not_marked | 2096 |
| relation_unknown | 88 |
| related_to | 9 |
| related_to_outside_memory | 3 |

| split/relation | n |
|---|---|
| test/conflicts | 1 |
| test/depends | 2 |
| val/conflicts | 2 |
| val/depends | 4 |

## Assumption extraction (Haiku 5.5, prompts/assumptions_v1.md, title+body only)

| set | PRs | calls | empty body | ≥1 assumption | mean count | errors | cost USD | p50 ms |
|---|---|---|---|---|---|---|---|---|
| dataset | 2196 | 2194 | 2 | 2185 | 6.32 | 0 | 0.8220 | 2585 |
| memory_partners | 102 | 102 | 0 | 102 | 6.51 | 0 | 0.0384 | 2652 |

## Pairs

candidates: results/run5/candidates.jsonl

| split/kind | n |
|---|---|
| test/labelled | 3 |
| test/random | 15 |
| test/retrieved | 15 |
| val/labelled | 6 |
| val/random | 29 |
| val/retrieved | 29 |

| positives split/relation/shares_file | n |
|---|---|
| test/conflicts/True | 1 |
| test/depends/True | 2 |
| val/conflicts/False | 2 |
| val/depends/False | 1 |
| val/depends/True | 3 |

## R5-H4 — ROC-AUC of conflict_of (positives = labelled relations; negatives = other top-5 + random same-repo ±90 d)

| model | split | pairs (coverage) | positives | ROC-AUC [95% CI] | PR-AUC |
|---|---|---|---|---|---|
| haiku | test | 33 (1.00) | 3 | 0.611 [0.226, 0.952] | 0.171 |
| haiku | val | 64 (1.00) | 6 | 0.822 [0.623, 0.975] | 0.494 |
| jev | test | 33 (1.00) | 3 | 0.856 [0.625, 1.000] | 0.528 |
| jev | val | 64 (1.00) | 6 | 0.751 [0.498, 0.962] | 0.431 |
| pplx-flash | test | 33 (1.00) | 3 | 0.744 [0.355, 1.000] | 0.476 |
| pplx-flash | val | 64 (1.00) | 6 | 0.756 [0.545, 0.937] | 0.302 |
| pplx | test | 33 (1.00) | 3 | 0.900 [0.750, 1.000] | 0.569 |
| pplx | val | 64 (1.00) | 6 | 0.741 [0.504, 0.937] | 0.274 |
| floor_dir_jaccard | test | 33 (1.00) | 3 | 0.711 [0.476, 0.917] | 0.162 |
| floor_dir_jaccard | val | 64 (1.00) | 6 | 0.563 [0.336, 0.760] | 0.109 |

## R5-H4b — no-shared-file relations caught at conflict_of ≥ 0.7 vs floor at the same random-pair FPR

| model | split | n | caught | FPR random | floor thr | floor FPR | floor caught | Δ pp |
|---|---|---|---|---|---|---|---|---|
| haiku | test | 0 | — | 0.000 | inf | 0.000 | — | — |
| haiku | val | 3 | 0.000 | 0.000 | inf | 0.000 | 0.000 | 0.0 |
| jev | test | 0 | — | 0.000 | inf | 0.000 | — | — |
| jev | val | 3 | 0.333 | 0.000 | inf | 0.000 | 0.000 | 33.3 |
| pplx-flash | test | 0 | — | 0.000 | inf | 0.000 | — | — |
| pplx-flash | val | 3 | 0.000 | 0.000 | inf | 0.000 | 0.000 | 0.0 |
| pplx | test | 0 | — | 0.000 | inf | 0.000 | — | — |
| pplx | val | 3 | 0.333 | 0.000 | inf | 0.000 | 0.000 | 33.3 |

## Pre-registered criteria (test)

| hypothesis | result | bar | pass |
|---|---|---|---|
| R5-H4[haiku] | {"roc_auc": 0.611, "ci95": [0.22580645161290325, 0.9516507056451613], "n": 33, "positives": 3, "coverage": 1.0} | >= 0.75 | ❌ |
| R5-H4b[haiku] | {"caught": NaN, "floor_caught": NaN, "delta_pp": NaN, "fpr_random": 0.0, "n": 0} | >= +15 pp over dir-Jaccard floor at same random-pair FPR | not evaluable |
| R5-H4[jev] | {"roc_auc": 0.856, "ci95": [0.625, 1.0], "n": 33, "positives": 3, "coverage": 1.0} | >= 0.75 | ✅ |
| R5-H4b[jev] | {"caught": NaN, "floor_caught": NaN, "delta_pp": NaN, "fpr_random": 0.0, "n": 0} | >= +15 pp over dir-Jaccard floor at same random-pair FPR | not evaluable |
| R5-H4[pplx-flash] | {"roc_auc": 0.744, "ci95": [0.3548387096774194, 1.0], "n": 33, "positives": 3, "coverage": 1.0} | >= 0.75 | ❌ |
| R5-H4b[pplx-flash] | {"caught": NaN, "floor_caught": NaN, "delta_pp": NaN, "fpr_random": 0.0, "n": 0} | >= +15 pp over dir-Jaccard floor at same random-pair FPR | not evaluable |
| R5-H4[pplx] | {"roc_auc": 0.9, "ci95": [0.7498611111111111, 1.0], "n": 33, "positives": 3, "coverage": 1.0} | >= 0.75 | ✅ |
| R5-H4b[pplx] | {"caught": NaN, "floor_caught": NaN, "delta_pp": NaN, "fpr_random": 0.0, "n": 0} | >= +15 pp over dir-Jaccard floor at same random-pair FPR | not evaluable |
| R5-H4[floor_dir_jaccard] | {"roc_auc": 0.711, "ci95": [0.47580645161290325, 0.916733870967742], "n": 33, "positives": 3, "coverage": 1.0} | >= 0.75 | ❌ |
