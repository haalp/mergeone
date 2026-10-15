# Run 2 results: semantic typed triage

Judge-labelled test PRs: 1200, val: 800. Prevalence (test): is_logic_change=0.70, weakens_tests=0.09, message_matches_diff=0.67, touches_concurrency=0.18, needs_human_review=0.54

## Judge reliability

| check | n | is_logic_change | weakens_tests | message_matches_diff | touches_concurrency | needs_human_review |
|---|---|---|---|---|---|---|
| self_agreement | 60 | 1.00 | 0.98 | 0.92 | 0.98 | 0.95 |
| judge_vs_human | 0 | – | – | – | – | – |

## `is_logic_change` (test, all five questions in one request)

| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |
|---|---|---|---|---|---|
| haiku | 0.952 [0.940, 0.962] | 0.973 | 0.829 / 0.902 | 0.174 / 0.027 | 0.123 / 0.075 |
| jev | 0.937 [0.923, 0.950] | 0.973 | 0.823 / 0.853 | 0.145 / 0.030 | 0.128 / 0.095 |

## `weakens_tests` (test, all five questions in one request)

| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |
|---|---|---|---|---|---|
| haiku | 0.957 [0.943, 0.973] | 0.716 | 0.938 / 0.946 | 0.049 / 0.009 | 0.044 / 0.040 |
| jev | 0.955 [0.939, 0.970] | 0.752 | 0.950 / 0.935 | 0.029 / 0.031 | 0.041 / 0.047 |

## `message_matches_diff` (test, all five questions in one request)

| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |
|---|---|---|---|---|---|
| haiku | 0.775 [0.745, 0.800] | 0.845 | 0.734 / 0.748 | 0.061 / 0.045 | 0.177 / 0.173 |
| jev | 0.804 [0.777, 0.829] | 0.886 | 0.683 / 0.748 | 0.184 / 0.037 | 0.217 / 0.166 |

## `touches_concurrency` (test, all five questions in one request)

| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |
|---|---|---|---|---|---|
| haiku | 0.955 [0.941, 0.968] | 0.856 | 0.912 / 0.922 | 0.063 / 0.017 | 0.070 / 0.057 |
| jev | 0.952 [0.938, 0.965] | 0.860 | 0.912 / 0.920 | 0.054 / 0.016 | 0.067 / 0.061 |

## `needs_human_review` (test, all five questions in one request)

| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |
|---|---|---|---|---|---|
| haiku | 0.875 [0.854, 0.894] | 0.887 | 0.758 / 0.792 | 0.145 / 0.041 | 0.171 / 0.143 |
| jev | 0.871 [0.851, 0.891] | 0.891 | 0.771 / 0.784 | 0.094 / 0.047 | 0.154 / 0.148 |

## Latency and cost (test)

| model | variant | p50 ms | p99 ms | USD / 1k | mean input tok | err |
|---|---|---|---|---|---|---|
| haiku | all | 1067 | 2518 | 0.3136 | 2799 | 0.000 |
| haiku | primary | 951 | 3314 | 0.2616 | 2542 | 0.000 |
| jev | all | 347 | 745 | 0.0740 | 1762 | 0.000 |
| jev | primary | 335 | 2713 | 0.0661 | 1573 | 0.000 |
| jev | all_words | 343 | 640 | 0.0752 | 1792 | 0.000 |

## Pre-registered hypotheses (R2)

```json
{
  "R2_H1_useful": {
    "haiku": {
      "is_logic_change": true,
      "weakens_tests": true,
      "message_matches_diff": false,
      "touches_concurrency": true,
      "needs_human_review": true
    },
    "jev": {
      "is_logic_change": true,
      "weakens_tests": true,
      "message_matches_diff": true,
      "touches_concurrency": true,
      "needs_human_review": true
    }
  },
  "R2_H2_jev_vs_haiku": {
    "is_logic_change": {
      "competitive": true,
      "better": false,
      "jev_roc": 0.9373452821223389,
      "haiku_roc": 0.9522513631787236,
      "jev_ece_cal": 0.030219570631508246,
      "haiku_ece_cal": 0.027481983294483293
    },
    "weakens_tests": {
      "competitive": false,
      "better": false,
      "jev_roc": 0.9548790658882402,
      "haiku_roc": 0.9568515429524603,
      "jev_ece_cal": 0.031231714177026672,
      "haiku_ece_cal": 0.00852629315129315
    },
    "message_matches_diff": {
      "competitive": true,
      "better": true,
      "jev_roc": 0.8044141773136633,
      "haiku_roc": 0.774923068719004,
      "jev_ece_cal": 0.037440413041967865,
      "haiku_ece_cal": 0.04504754383001475
    },
    "touches_concurrency": {
      "competitive": true,
      "better": false,
      "jev_roc": 0.9520657795608224,
      "haiku_roc": 0.9553427405377175,
      "jev_ece_cal": 0.01585234289062556,
      "haiku_ece_cal": 0.01657188349407567
    },
    "needs_human_review": {
      "competitive": true,
      "better": false,
      "jev_roc": 0.8712426194310253,
      "haiku_roc": 0.8752012882447664,
      "jev_ece_cal": 0.047429837115294346,
      "haiku_ece_cal": 0.04071082124776533
    }
  },
  "R2_H3_multi_question_cost": {
    "jev": {
      "p50_ratio": 1.0343322187383124,
      "usd_ratio": 1.1201542297149674,
      "holds": true
    },
    "haiku": {
      "p50_ratio": 1.1222236336505247,
      "usd_ratio": 1.1987372570328183,
      "holds": true
    }
  },
  "R2_H4_routing": {
    "haiku": {
      "capture_at_25pct": 0.44135802469135804,
      "holds": false
    },
    "jev": {
      "capture_at_25pct": 0.4351851851851852,
      "holds": false
    }
  }
}
```
