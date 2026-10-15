# Results

## Primary decision (`will_be_reverted`), test split, variant `primary`

Test n = 7553, positives = 57, base rate = 0.0075. R@FPR5 threshold chosen on val; actual test FPR in parentheses.

| model | PR-AUC [95% CI] | ROC-AUC | R@FPR5 (FPR) | ECE | Brier | p50/p99 ms | USD / 1k | err |
|---|---|---|---|---|---|---|---|---|
| haiku | 0.010 [0.007, 0.015] | 0.610 | 0.000 (0.001) | 0.036 | 0.009 | 923 / 2685 | 0.2794 | 0.000 |
| jev | 0.010 [0.008, 0.016] | 0.622 | 0.053 (0.030) | 0.086 | 0.016 | 323 / 561 | 0.0691 | 0.000 |

## Primary decision (`will_be_reverted`), test split, variant `all`

Test n = 7553, positives = 57, base rate = 0.0075. R@FPR5 threshold chosen on val; actual test FPR in parentheses.

| model | PR-AUC [95% CI] | ROC-AUC | R@FPR5 (FPR) | ECE | Brier | p50/p99 ms | USD / 1k | err |
|---|---|---|---|---|---|---|---|---|
| gbm | 0.011 [0.008, 0.019] | 0.605 | 0.070 (0.050) | 0.003 | 0.007 | 3 / 9 | 0.0000 | 0.000 |
| haiku | 0.010 [0.007, 0.014] | 0.599 | 0.088 (0.052) | 0.034 | 0.009 | 1085 / 2512 | 0.3085 | 0.000 |
| jev | 0.011 [0.008, 0.017] | 0.614 | 0.035 (0.029) | 0.086 | 0.016 | 324 / 614 | 0.0757 | 0.000 |
| rules | 0.008 [0.005, 0.012] | 0.471 | 0.070 (0.051) | 0.130 | 0.028 | 0 / 0 | 0.0000 | 0.000 |

## Primary decision (`will_be_reverted`), test split, variant `all_words`

Test n = 7553, positives = 57, base rate = 0.0075. R@FPR5 threshold chosen on val; actual test FPR in parentheses.

| model | PR-AUC [95% CI] | ROC-AUC | R@FPR5 (FPR) | ECE | Brier | p50/p99 ms | USD / 1k | err |
|---|---|---|---|---|---|---|---|---|
| jev | 0.011 [0.008, 0.016] | 0.613 | 0.035 (0.035) | 0.087 | 0.016 | 316 / 519 | 0.0769 | 0.000 |

## Secondary decisions (test)

`rules` and `gbm` compute these from the same structural features that define the ground truth, so their scores are by construction and not comparable; the rows that matter are the models that must infer the rule from text.

| model | variant | sensitive acc | sensitive ECE | blast acc | blast ECE |
|---|---|---|---|---|---|
| gbm | all | 1.000 | 0.001 | 1.000 | 0.000 |
| haiku | all | 0.868 | 0.105 | 0.932 | 0.088 |
| jev | all | 0.972 | 0.053 | 0.974 | 0.042 |
| jev | all_words | 0.984 | 0.048 | 0.983 | 0.036 |
| rules | all | 1.000 | 0.000 | 1.000 | 0.000 |

## Cascade (jev → haiku)

| variant | escalation | PR-AUC | R@FPR5 | ECE | p50/p99 ms | USD / 1k |
|---|---|---|---|---|---|---|
| jev_then_haiku_primary | 0.218 | 0.011 | 0.088 | 0.070 | 337 / 2046 | 0.1324 |
| jev_then_haiku_all | 0.215 | 0.011 | 0.088 | 0.070 | 336 / 2271 | 0.1444 |

## Load tests (primary only; open-loop replay, 1x = 30,000 decisions/day)

| model | multiplier | rate/s | p50 ms | p99 ms | p99 schedule lag s | error rate |
|---|---|---|---|---|---|---|
| haiku | 1x | 0.35 | 916 | 3355 | 0.0 | 0.000 |
| haiku | 10x | 3.47 | 983 | 7471 | 0.0 | 0.000 |
| haiku | 100x | 34.72 | 1223 | 9662 | 0.2 | 0.360 |
| jev | 100x | 34.72 | 311 | 527 | 0.0 | 0.000 |

## Pre-registered hypotheses

```json
{
  "H1_useful": {
    "gbm": false,
    "haiku": false,
    "jev": false,
    "rules": false
  },
  "H2_jev_vs_haiku": {
    "competitive": false,
    "better": false,
    "jev": {
      "pr_auc": 0.01045064025669475,
      "ece": 0.08617238183503242
    },
    "haiku": {
      "pr_auc": 0.01036125858935362,
      "ece": 0.03613531047265988
    }
  },
  "H3_gbm_matches_or_beats": {
    "jev": true,
    "haiku": true
  },
  "H4_cascade_worthwhile": false,
  "H4_detail": {
    "escalation": 0.21752945849331393,
    "cascade_pr_auc": 0.010961459254634038,
    "haiku_pr_auc": 0.01036125858935362,
    "cascade_usd_per_1k": 0.132444571163776,
    "haiku_usd_per_1k": 0.27941052561895935
  },
  "H5_holds_at_scale": {
    "haiku": false
  }
}
```
