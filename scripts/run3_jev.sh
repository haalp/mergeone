#!/usr/bin/env bash
# Run 3, Jev side (runs on a machine that can reach api.typesafe.ai). Requires TYPESAFE_API_KEY.
# Resumable: errored rows are retried with --append --only-errors-from.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
: "${TYPESAFE_API_KEY:?set TYPESAFE_API_KEY (env or .env)}"
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
C="${CONCURRENCY:-8}"; DS=data/run3/dataset.jsonl
[ -f "$DS" ] || $PY -m mergeone.cli prepare-run3
for sp in val test; do
  f="results/run3/predictions_jev_${sp}_all.jsonl"
  if [ -f "$f" ]; then
    grep -q '"error": "' "$f" && $PY -m mergeone.cli predict --model jev --split $sp --run run3 --qset v2 --dataset $DS --concurrency $C --append --only-errors-from "$f" || true
  else
    $PY -m mergeone.cli predict --model jev --split $sp --run run3 --qset v2 --dataset $DS --concurrency $C
  fi
done
# R3-H5: 100x the historical rate of the test set, open loop, through the model
$PY -m mergeone.cli loadtest --model jev --multiplier 100 --limit 600 --run run3 --dataset $DS --qset v2
echo "--- error counts:"; for f in results/run3/predictions_jev_*.jsonl; do echo "$f $(grep -c '"error": "' $f || true)"; done
