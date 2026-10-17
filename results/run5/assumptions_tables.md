# Run 5 B — declared assumptions (R5-H4, R5-H4b)

Test repositories have been seen in aggregate in runs 3–4 (PROTOCOL R5 status). The floor is depth-2 directory Jaccard (no model, no assumptions).

## Relation labels (regex over non-bot comments)

| label | n |
|---|---|
| comments_not_fetched | 1254 |
| not_marked | 884 |
| relation_unknown | 52 |
| related_to | 4 |
| related_to_outside_memory | 2 |

| split/relation | n |
|---|---|
| val/conflicts | 2 |
| val/depends | 2 |

## Assumption extraction (Haiku 5.5, prompts/assumptions_v1.md, title+body only)

| set | PRs | calls | empty body | ≥1 assumption | mean count | errors | cost USD | p50 ms |
|---|---|---|---|---|---|---|---|---|
| dataset | 2196 | 2194 | 2 | 2185 | 6.32 | 0 | 0.8220 | 2585 |
| memory_partners | 44 | 44 | 0 | 44 | 6.84 | 0 | 0.0174 | 2652 |

## Pairs

candidates: run-4 retrieval recomputed in-process (fallback: results/run5/candidates.jsonl not present)

| split/kind | n |
|---|---|
| val/labelled | 4 |
| val/random | 20 |
| val/retrieved | 20 |

| positives split/relation/shares_file | n |
|---|---|
| val/conflicts/False | 2 |
| val/depends/False | 1 |
| val/depends/True | 1 |

## R5-H4 — ROC-AUC of conflict_of (positives = labelled relations; negatives = other top-5 + random same-repo ±90 d)

| model | split | pairs (coverage) | positives | ROC-AUC [95% CI] | PR-AUC |
|---|---|---|---|---|---|
| haiku | val | 44 (1.00) | 4 | 0.787 [0.389, 1.000] | 0.518 |
| floor_dir_jaccard | val | 44 (1.00) | 4 | 0.528 [0.218, 0.802] | 0.102 |

## R5-H4b — no-shared-file relations caught at conflict_of ≥ 0.7 vs floor at the same random-pair FPR

| model | split | n | caught | FPR random | floor thr | floor FPR | floor caught | Δ pp |
|---|---|---|---|---|---|---|---|---|
| haiku | val | 3 | 0.000 | 0.000 | inf | 0.000 | 0.000 | 0.0 |

## Pre-registered criteria (test)

| hypothesis | result | bar | pass |
|---|---|---|---|
| R5-H4[haiku] | {"roc_auc": null, "ci95": null, "n": 0, "positives": 0, "coverage": null} | >= 0.75 | not evaluable |
| R5-H4[floor_dir_jaccard] | {"roc_auc": null, "ci95": null, "n": 0, "positives": 0, "coverage": null} | >= 0.75 | not evaluable |
