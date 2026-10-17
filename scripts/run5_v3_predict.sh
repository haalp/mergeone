#!/usr/bin/env bash
# Run 5 component C (PROTOCOL R5.1 C): question set v3 (ten noul questions) on val+test for the System One candidates.
# Runs where api.typesafe.ai / api.perplexity.ai are reachable. Haiku and rules were predicted in the sandbox (2026-10-18).
# Resumable: rows already answered (qset v3, no error) are skipped. MODELS="jev pplx" to restrict.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
C="${CONCURRENCY:-8}"; DS=data/run3/dataset.jsonl
for model in ${MODELS:-jev pplx pplx-flash}; do
  case $model in jev) key=TYPESAFE_API_KEY;; pplx*) key=PERPLEXITY_API_KEY;; *) key=ANTHROPIC_API_KEY;; esac
  if [ -z "${!key:-}" ]; then echo "skip $model: $key not set"; continue; fi
  c=$C; case $model in pplx*) c=2;; esac   # Perplexity: 10 rps per org
  for sp in val test; do
    f="results/run5/predictions_${model}_${sp}_all.jsonl"
    $PY -m mergeone.cli predict --model $model --split $sp --run run5 --qset v3 --dataset $DS --concurrency $c --append --only-missing-from "$f"
  done
done
