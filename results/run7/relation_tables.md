# Run 7 C5 — typed relation on the run-6B hold pairs

100 pairs · gold classes: duplicate 46, competing 7, backport 28, follow_up 7, same_area 10, unrelated 2 · gold source: notes rule 100 · threshold 0.5

## R7-H5

| model | n | related vs unrelated AUC (n unrelated) | same_change vs rest AUC (n duplicate) | accuracy [95% CI] | $/PR at k=20 |
|---|---|---|---|---|---|
| pplx-flash | 97 | 0.990 (1) | 0.728 (45) | 0.320 [0.235, 0.418] | 0.00160 |
| jev | 100 | 0.995 (2) | 0.685 (46) | 0.080 [0.041, 0.150] | 0.00217 |
| pplx-flash — run-6 pair question (baseline) | 100 | — | 0.697 | — | — |
| jev — run-6 pair question (baseline) | 100 | — | 0.673 | — | — |

R7-H5 bar: related-vs-unrelated AUC >= 0.8 for >= 1 model and same_change-vs-rest >= 0.8 → **fail**
(cost bar for reference: R6B-H3 ≤ $0.002 per PR)

## pplx-flash: accuracy per gold class (Wilson 95%)

| gold class | n | correct | accuracy [95% CI] |
|---|---|---|---|
| duplicate | 45 | 28 | 0.622 [0.476, 0.749] |
| competing | 7 | 0 | 0.000 [0.000, 0.354] |
| backport | 27 | 1 | 0.037 [0.007, 0.183] |
| follow_up | 7 | 2 | 0.286 [0.082, 0.641] |
| same_area | 10 | 0 | 0.000 [0.000, 0.278] |
| unrelated | 1 | 0 | 0.000 [0.000, 0.793] |

### pplx-flash: confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 28 | 0 | 17 | 0 | 0 | 0 |
| competing | 1 | 0 | 6 | 0 | 0 | 0 |
| backport | 26 | 0 | 1 | 0 | 0 | 0 |
| follow_up | 3 | 0 | 2 | 2 | 0 | 0 |
| same_area | 8 | 0 | 2 | 0 | 0 | 0 |
| unrelated | 0 | 0 | 1 | 0 | 0 | 0 |

## jev: accuracy per gold class (Wilson 95%)

| gold class | n | correct | accuracy [95% CI] |
|---|---|---|---|
| duplicate | 46 | 1 | 0.022 [0.004, 0.113] |
| competing | 7 | 0 | 0.000 [0.000, 0.354] |
| backport | 28 | 2 | 0.071 [0.020, 0.226] |
| follow_up | 7 | 5 | 0.714 [0.359, 0.918] |
| same_area | 10 | 0 | 0.000 [0.000, 0.278] |
| unrelated | 2 | 0 | 0.000 [0.000, 0.658] |

### jev: confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 1 | 0 | 42 | 2 | 1 | 0 |
| competing | 0 | 0 | 5 | 0 | 2 | 0 |
| backport | 26 | 0 | 2 | 0 | 0 | 0 |
| follow_up | 0 | 0 | 2 | 5 | 0 | 0 |
| same_area | 2 | 0 | 7 | 1 | 0 | 0 |
| unrelated | 1 | 0 | 0 | 1 | 0 | 0 |

## Notes

- Gold sub-classes come from the labellers' notes by a frozen keyword rule (run7_relation.GOLD_KEYWORDS); data/run7/relation_gold.jsonl overrides per pair when present.
- Class rule and precedence: run7_relation module docstring. Sub-class accuracy is reported, not a bar (n is small).
- related-vs-unrelated AUC rests on very few unrelated pairs (the sample is drawn from holds); read it with that n.
