#!/usr/bin/env bash
# Run 6 part B on the Mac (after `git pull`, with .env present). Every stage is resumable; re-run the script after any failure.
#   retrieve  retrieval v2 at k=20 (A1 frozen + A2 re-rank from the cached data/run5/patches/) → results/run6/candidates_k20.jsonl,
#             then k=10 and k=5 derived as prefixes (asserted) → results/run6/retrieval_k.json (R6B-H1 recall@k).
#             Kept if present; FORCE_RETRIEVE=1 recomputes (deterministic).
#   pairs     results/run6/pairs.jsonl + pairs_todo_<model>.jsonl (k=20 pairs not answered in runs 4–5); prints counts and cost.
#   predict   same_change on the todo pairs: jev, pplx-flash (concurrency 2), haiku; val + test; --append --only-missing-from.
#   compact   dedupe the run-6 part-B prediction files; print error counts.
#   sample    draw the 100-PR blind hold sample (refuses until every k=20 test pair has a prediction) → data/run6/hold_sample.jsonl
# Skip stages with e.g. SKIP="predict"; run only some with STAGES="compact sample".
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
export PYTHON="$PY"
C="${CONCURRENCY:-8}"; R=results/run6
MODELS="jev pplx-flash haiku"
STAGES="${STAGES:-retrieve pairs predict compact sample}"
want() { case " $STAGES " in *" $1 "*) ;; *) return 1;; esac; case " ${SKIP:-} " in *" $1 "*) echo "--- skip stage $1"; return 1;; esac; }
keyfor() { case $1 in jev) echo TYPESAFE_API_KEY;; pplx*) echo PERPLEXITY_API_KEY;; *) echo ANTHROPIC_API_KEY;; esac; }
mkdir -p $R

if want retrieve; then
  echo "=== 1. retrieval v2, k = 20 → 10 → 5"
  [ -d data/run5/patches ] || echo "WARNING: data/run5/patches not present — A2 cannot apply; candidates will be A1-only (recorded in retrieval_k.json)"
  if [ -f $R/candidates_k20.jsonl ] && [ -z "${FORCE_RETRIEVE:-}" ]; then echo "keep $R/candidates_k20.jsonl"; else
    $PY -m mergeone.eval.run6b retrieve --k 20
  fi
  $PY -m mergeone.eval.run6b retrieve --k 10   # derived from k=20 (prefix asserted)
  $PY -m mergeone.eval.run6b retrieve --k 5
fi

if want pairs; then
  echo "=== 2. pairs at k = 20"
  $PY -m mergeone.eval.run6b pairs --k 20      # → $R/pairs.jsonl, pairs_todo_<model>.jsonl, pairs_summary.json
fi

if want predict; then
  echo "=== 3. same_change on new k=20 pairs"
  [ -f $R/pairs.jsonl ] || { echo "$R/pairs.jsonl missing (run the pairs stage)"; exit 1; }
  for model in $MODELS; do
    key=$(keyfor $model); if [ -z "${!key:-}" ]; then echo "skip $model: $key not set"; continue; fi
    c=$C; case $model in pplx*) c=2;; esac
    for sp in val test; do
      $PY -m mergeone.cli predict --model $model --split $sp --run run6 --qset pair --dataset $R/pairs.jsonl \
        --subset $R/pairs_todo_${model}.jsonl --concurrency $c --append --only-missing-from "$R/predictions_${model}_${sp}_all.jsonl"
    done
  done
fi

if want compact; then
  echo "=== 4. compact"
  for model in $MODELS; do
    for sp in val test; do
      f=$R/predictions_${model}_${sp}_all.jsonl
      [ -f $f ] || continue
      $PY -m mergeone.cli compact $f
      echo "$f errors: $(grep -c '"error": "' $f || true)"
    done
  done
fi

if want sample; then
  echo "=== 5. blind hold sample (R6B.2-B2)"
  $PY -m mergeone.eval.run6b sample   # fails loudly while any k=20 test pair prediction is missing; re-run predict, compact
  echo
  echo "Next (R6B.5): commit data/run6/hold_sample.jsonl (PR ids only) BEFORE labelling:"
  echo "  git add data/run6/hold_sample.jsonl results/run6/sample_meta.json && git commit -m 'run6 B: hold sample drawn'"
  echo "Then label (blind; keys 1/2/3; resumable):"
  echo "  $PY -m mergeone.eval.run6b label        # open http://127.0.0.1:8765"
  echo "  $PY -m mergeone.eval.run6b label --export"
  echo "Commit data/run6/hold_labels.jsonl, then: $PY -m mergeone.cli report $R --run6b"
fi
