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

## `weakens_tests` (test, all five questions in one request)

| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |
|---|---|---|---|---|---|
| haiku | 0.957 [0.943, 0.973] | 0.716 | 0.938 / 0.946 | 0.049 / 0.009 | 0.044 / 0.040 |

## `message_matches_diff` (test, all five questions in one request)

| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |
|---|---|---|---|---|---|
| haiku | 0.775 [0.745, 0.800] | 0.845 | 0.734 / 0.748 | 0.061 / 0.045 | 0.177 / 0.173 |

## `touches_concurrency` (test, all five questions in one request)

| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |
|---|---|---|---|---|---|
| haiku | 0.955 [0.941, 0.968] | 0.856 | 0.912 / 0.922 | 0.063 / 0.017 | 0.070 / 0.057 |

## `needs_human_review` (test, all five questions in one request)

| model | ROC-AUC [95% CI] | PR-AUC | acc raw / cal | ECE raw / cal | Brier raw / cal |
|---|---|---|---|---|---|
| haiku | 0.875 [0.854, 0.894] | 0.887 | 0.758 / 0.792 | 0.145 / 0.041 | 0.171 / 0.143 |

## Latency and cost (test)

| model | variant | p50 ms | p99 ms | USD / 1k | mean input tok | err |
|---|---|---|---|---|---|---|
| haiku | all | 1067 | 2518 | 0.3136 | 2799 | 0.000 |
| haiku | primary | 951 | 3314 | 0.2616 | 2542 | 0.000 |

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
    }
  },
  "R2_H3_multi_question_cost": {
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
    }
  }
}
```
