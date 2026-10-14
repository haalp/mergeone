# Results

## Primary decision (`will_be_reverted`), test split, variant `primary`

Test n = 7553, positives = 57, base rate = 0.0075. R@FPR5 threshold chosen on val; actual test FPR in parentheses.

| model | PR-AUC [95% CI] | ROC-AUC | R@FPR5 (FPR) | ECE | Brier | p50/p99 ms | USD / 1k | err |
|---|---|---|---|---|---|---|---|---|
| haiku | 0.010 [0.007, 0.015] | 0.610 | 0.000 (0.001) | 0.036 | 0.009 | 923 / 2685 | 0.2794 | 0.000 |

## Primary decision (`will_be_reverted`), test split, variant `all`

Test n = 7553, positives = 57, base rate = 0.0075. R@FPR5 threshold chosen on val; actual test FPR in parentheses.

| model | PR-AUC [95% CI] | ROC-AUC | R@FPR5 (FPR) | ECE | Brier | p50/p99 ms | USD / 1k | err |
|---|---|---|---|---|---|---|---|---|
| gbm | 0.011 [0.008, 0.019] | 0.605 | 0.070 (0.050) | 0.003 | 0.007 | 3 / 9 | 0.0000 | 0.000 |
| haiku | 0.010 [0.007, 0.014] | 0.599 | 0.088 (0.052) | 0.034 | 0.009 | 1085 / 2512 | 0.3085 | 0.000 |
| rules | 0.008 [0.005, 0.012] | 0.471 | 0.070 (0.051) | 0.130 | 0.028 | 0 / 0 | 0.0000 | 0.000 |

## Secondary decisions (test)

`rules` and `gbm` compute these from the same structural features that define the ground truth, so their scores are by construction and not comparable; the rows that matter are the models that must infer the rule from text.

| model | variant | sensitive acc | sensitive ECE | blast acc | blast ECE |
|---|---|---|---|---|---|
| gbm | all | 1.000 | 0.001 | 1.000 | 0.000 |
| haiku | all | 0.868 | 0.105 | 0.932 | 0.088 |
| rules | all | 1.000 | 0.000 | 1.000 | 0.000 |

## Load tests (primary only)

| model | multiplier | eff. rate/s | p50 ms | p99 ms | error rate |
|---|---|---|---|---|---|
| haiku | 1.0x | 2.00 | 821 | 2078 | 0.000 |
| haiku | 10.0x | 2.00 | 862 | 7758 | 0.000 |

## Pre-registered hypotheses

```json
{
  "H1_useful": {
    "haiku": false
  }
}
```
