#!/usr/bin/env bash
# Run 5 component B (PROTOCOL R5.1-B, R5-H4/H4b): assumption_conflict pair question.
# Steps that need GitHub GraphQL (merged-PR comments) and the System One endpoints (Jev, Perplexity) run here;
# everything is resumable.
#   1. merged-PR comments → data/run5/comments_merged.jsonl   (GITHUB_TOKEN; skipped when complete)
#   2. relation labels     → data/run5/relation_labels.jsonl
#   3. assumptions         → data/run5/assumptions.jsonl       (ANTHROPIC_API_KEY; only PRs not yet extracted,
#                            incl. memory PRs that became side B of a new pair)
#   4. pairs               → results/run5/pairs_assumptions.jsonl
#   5. predictions         → results/run5_assume/predictions_<model>_<split>_all.jsonl (kept apart from run-5 same_change files)
#   6. report              → results/run5/assumptions_metrics.json, assumptions_tables.md
# Requires TYPESAFE_API_KEY (jev), PERPLEXITY_API_KEY (pplx, pplx-flash); haiku runs when ANTHROPIC_API_KEY is set.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
C="${CONCURRENCY:-8}"; PAIRS=results/run5/pairs_assumptions.jsonl; RUN=run5_assume
if [ -n "${GITHUB_TOKEN:-}" ]; then $PY -m mergeone.data.pr_memory comments --merged; else echo "skip merged comments: GITHUB_TOKEN not set"; fi
$PY -m mergeone.eval.run5_assumptions labels
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then $PY -m mergeone.eval.run5_assumptions extract --concurrency 8; else echo "skip extract: ANTHROPIC_API_KEY not set (pairs render missing sides as 'none')"; fi
$PY -m mergeone.eval.run5_assumptions pairs
for model in jev pplx pplx-flash haiku; do
  case $model in jev) key=TYPESAFE_API_KEY;; pplx*) key=PERPLEXITY_API_KEY;; *) key=ANTHROPIC_API_KEY;; esac
  if [ -z "${!key:-}" ]; then echo "skip $model: $key not set"; continue; fi
  for sp in val test; do
    f="results/$RUN/predictions_${model}_${sp}_all.jsonl"
    c=$C; case $model in pplx*) c=2;; esac   # Perplexity: 10 rps per org
    $PY -m mergeone.cli predict --model $model --split $sp --run $RUN --qset pair_assume --dataset $PAIRS --concurrency $c --append --only-missing-from "$f"
  done
done
$PY -m mergeone.cli compact results/$RUN/predictions_*.jsonl
echo "--- error counts:"; for f in results/$RUN/predictions_*.jsonl; do echo "$f $(grep -c '"error": "' $f || true)"; done
$PY -m mergeone.eval.run5_assumptions report | tail -30
