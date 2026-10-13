# Results

## Primary decision (`will_be_reverted`), test split, variant `all`

Test n = 7553, positives = 57, base rate = 0.0075. R@FPR5 threshold chosen on val; actual test FPR in parentheses.

| model | PR-AUC [95% CI] | ROC-AUC | R@FPR5 (FPR) | ECE | Brier | p50/p99 ms | USD / 1k | err |
|---|---|---|---|---|---|---|---|---|
| gbm | 0.011 [0.008, 0.019] | 0.605 | 0.070 (0.050) | 0.003 | 0.007 | 3 / 9 | 0.0000 | 0.000 |
| rules | 0.008 [0.005, 0.012] | 0.471 | 0.070 (0.051) | 0.130 | 0.028 | 0 / 0 | 0.0000 | 0.000 |

## Secondary decisions (test)

`rules` and `gbm` compute these from the same structural features that define the ground truth, so their scores are by construction and not comparable; the rows that matter are the models that must infer the rule from text.

| model | variant | sensitive acc | sensitive ECE | blast acc | blast ECE |
|---|---|---|---|---|---|
| gbm | all | 1.000 | 0.001 | 1.000 | 0.000 |
| rules | all | 1.000 | 0.000 | 1.000 | 0.000 |

## Pre-registered hypotheses

```json
{
  "H1_useful": {
    "gbm": false,
    "rules": false
  },
  "H3_gbm_matches_or_beats": {}
}
```
