#!/usr/bin/env bash
# Run 5 on the Mac (after `git pull`, with .env present). Every stage is resumable; re-run the script after any failure.
#   0. data     component A2: fetch patches for the A1 top-20 (GitHub REST, ~17k calls), then retrieve → results/run5/candidates.jsonl
#   1. v3       v3 question set for jev, pplx, pplx-flash, val+test            (scripts/run5_v3_predict.sh)
#   2. assume   component B: scripts/run5_assumptions_predict.sh, if present
#   3. pairs    same_change for NEW run-5 pairs only (component-A top-5 not answered in run 4), val+test
#   4. load     pplx and pplx-flash load tests at 1×, 10×, 100× (adapter 429 retry OFF; R5-H6). Existing results are kept;
#               FORCE_LOADTEST=1 re-runs them. The 1× runs replay 600 PRs at 0.35/s: ~29 min per model.
#   5. report   compact, report results/run5 --run5, leaderboard
# Skip stages with e.g. SKIP="assume load"; run only some with STAGES="pairs report".
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
export PYTHON="$PY"
C="${CONCURRENCY:-8}"; DS=data/run3/dataset.jsonl; R=results/run5
STAGES="${STAGES:-data v3 assume pairs load report}"
want() { case " $STAGES " in *" $1 "*) ;; *) return 1;; esac; case " ${SKIP:-} " in *" $1 "*) echo "--- skip stage $1"; return 1;; esac; }
keyfor() { case $1 in jev) echo TYPESAFE_API_KEY;; pplx*) echo PERPLEXITY_API_KEY;; *) echo ANTHROPIC_API_KEY;; esac; }

if want data; then
  echo "=== 0. retrieval v2 content stage (component A2)"
  if [ -z "${GITHUB_TOKEN:-}" ]; then echo "skip: GITHUB_TOKEN not set"; else
    $PY -m mergeone.eval.run5_retrieval a1 --split all          # deterministic target list (results/run5/a1_targets.jsonl)
    $PY -m mergeone.data.patches --targets results/run5/a1_targets.jsonl   # resumable; data/run5/patches/
    $PY -m mergeone.eval.run5_retrieval retrieve                # → results/run5/candidates.jsonl, retrieval_metrics.json (R5-H1)
  fi
fi

if want v3; then
  echo "=== 1. v3 predictions"; bash scripts/run5_v3_predict.sh
fi

if want assume; then
  echo "=== 2. assumptions (component B)"
  if [ -f scripts/run5_assumptions_predict.sh ]; then bash scripts/run5_assumptions_predict.sh; else echo "scripts/run5_assumptions_predict.sh not present; skipped"; fi
fi

if want pairs; then
  echo "=== 3. same_change on new run-5 pairs"
  if [ -f $R/candidates.jsonl ]; then
    $PY -m mergeone.eval.run5 pairs   # → $R/pairs.jsonl + pairs_todo_<model>.jsonl (pairs that model did not answer in run 4)
    for model in jev pplx pplx-flash haiku; do
      key=$(keyfor $model); if [ -z "${!key:-}" ]; then echo "skip $model: $key not set"; continue; fi
      c=$C; case $model in pplx*) c=2;; esac
      for sp in val test; do
        $PY -m mergeone.cli predict --model $model --split $sp --run run5 --qset pair --dataset $R/pairs.jsonl \
          --subset $R/pairs_todo_${model}.jsonl --concurrency $c --append --only-missing-from "$R/predictions_${model}_${sp}_all.jsonl"
      done
    done
  else
    echo "$R/candidates.jsonl not present (component A pending); skipped"
  fi
fi

if want load; then
  echo "=== 4. load tests (pplx, pplx-flash; 429 retry disabled)"
  for model in pplx pplx-flash; do
    if [ -z "${PERPLEXITY_API_KEY:-}" ]; then echo "skip $model: PERPLEXITY_API_KEY not set"; continue; fi
    for x in 1 10 100; do
      f="$R/loadtest_${model}_x${x}.json"
      if [ -f "$f" ] && [ -z "${FORCE_LOADTEST:-}" ]; then echo "keep $f"; continue; fi
      MERGEONE_NO_RETRY=1 $PY -m mergeone.cli loadtest --model $model --multiplier $x --limit "${LT_LIMIT:-600}" --run run5 \
        --dataset $DS --qset v3 --no-retry || echo "loadtest $model x$x failed (exit $?)"
      sleep "${LT_GAP:-60}"   # let the per-org rate window drain between runs
    done
  done
fi

if want report; then
  echo "=== 5. compact, report, leaderboard"
  ls $R/predictions_*.jsonl >/dev/null 2>&1 && $PY -m mergeone.cli compact $R/predictions_*.jsonl
  echo "--- error counts:"; for f in $R/predictions_*.jsonl; do echo "$f $(grep -c '"error": "' $f || true)"; done
  $PY -m mergeone.cli report $R --run5 | tail -40
  $PY -m mergeone.cli leaderboard >/dev/null && echo "--- results/leaderboard.md updated"
fi
