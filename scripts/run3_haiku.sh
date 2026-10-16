#!/usr/bin/env bash
# Run 3, Claude side: Haiku 5.5 candidate on val+test, 100x load test, close-reason classification (Fable, reporting only).
# Requires ANTHROPIC_API_KEY. Resumable: already-predicted rows are skipped.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY (env or .env)}"
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
C="${CONCURRENCY:-6}"; DS=data/run3/dataset.jsonl
[ -f "$DS" ] || $PY -m mergeone.cli prepare-run3
for sp in val test; do
  f="results/run3/predictions_haiku_${sp}_all.jsonl"
  $PY -m mergeone.cli predict --model haiku --split $sp --run run3 --qset v2 --dataset $DS --concurrency $C --append --only-missing-from "$f"
done
$PY -m mergeone.cli loadtest --model haiku --multiplier 100 --limit 600 --run run3 --dataset $DS --qset v2 --concurrency 32 || true
# close reasons: 200 closed-unmerged test PRs, Claude Fable 5.1, reporting only (PROTOCOL R3.4)
$PY -m mergeone.cli close-reasons --n 200 --run run3 --dataset $DS
echo "--- error counts:"; for f in results/run3/predictions_haiku_*.jsonl; do echo "$f $(grep -c '"error": "' $f || true)"; done
