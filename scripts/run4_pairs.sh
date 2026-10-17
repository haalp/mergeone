#!/usr/bin/env bash
# Run 4: build labels/candidates/pairs, then ask Jev and Haiku the same_change pair question (val+test).
# Requires GITHUB data already fetched (data/run4/memory, data/run4/comments.jsonl), TYPESAFE_API_KEY, ANTHROPIC_API_KEY.
# Resumable: already-answered pairs are skipped.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
C="${CONCURRENCY:-8}"; PAIRS=results/run4/pairs.jsonl
$PY -m mergeone.cli run4-labels
$PY -m mergeone.cli run4-retrieve
$PY -m mergeone.cli run4-pairs
for model in jev pplx pplx-flash haiku; do
  case $model in jev) key=TYPESAFE_API_KEY;; pplx*) key=PERPLEXITY_API_KEY;; *) key=ANTHROPIC_API_KEY;; esac
  if [ -z "${!key:-}" ]; then echo "skip $model: $key not set"; continue; fi
  for sp in val test; do
    f="results/run4/predictions_${model}_${sp}_all.jsonl"
    $PY -m mergeone.cli predict --model $model --split $sp --run run4 --qset pair --dataset $PAIRS --concurrency $C --append --only-missing-from "$f"
  done
done
echo "--- error counts:"; for f in results/run4/predictions_*.jsonl; do echo "$f $(grep -c '"error": "' $f || true)"; done
$PY -m mergeone.cli report results/run4 --run4 | head -60
$PY -m mergeone.cli leaderboard >/dev/null && echo "--- results/leaderboard.md updated"
