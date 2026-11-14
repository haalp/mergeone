#!/usr/bin/env bash
# R12-H1 — the focused rendering against its same-day full-state control, hosted pplx-flash, holdout-2.
# Run from the lab machine (data/run12/h1_full.jsonl and h1_focused.jsonl on disk; PERPLEXITY_API_KEY in .env).
#   scripts/run12_hosted.sh            # refuses until the protocol is frozen (tag r12-frozen must exist)
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && source .env; set +a
# the Python package (gatebench), not the Go binary of the same name: prefer the repo's venv
PY=${PY:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}
predict() { "$PY" -m mergeone.cli predict "$@"; }
git rev-parse -q --verify refs/tags/r12-frozen >/dev/null || { echo "refusing: tag r12-frozen does not exist (PROTOCOL.md R12 not frozen)"; exit 2; }
for f in data/run12/h1_full.jsonl data/run12/h1_focused.jsonl data/run12/h1_renderings.json; do
  [ -f "$f" ] || { echo "$f missing (python -m mergeone.data.prepare_holdout2 renderings)"; exit 2; }
done
[ "$(wc -l < data/run12/h1_full.jsonl)" = "$(wc -l < data/run12/h1_focused.jsonl)" ] || { echo "the two renderings do not hold the same number of PRs"; exit 2; }
: "${PERPLEXITY_API_KEY:?PERPLEXITY_API_KEY not set}"
mkdir -p results/run12/full results/run12/focused
# control first (full state), then the treatment, the same UTC day; one run directory per rendering so neither file can overwrite the other
date -u +"%Y-%m-%dT%H:%M:%SZ control start" | tee -a results/run12/run.log
predict --model pplx-flash --qset v4 --split holdout2 --dataset data/run12/h1_full.jsonl --run run12/full --concurrency "${CONC:-4}"
date -u +"%Y-%m-%dT%H:%M:%SZ focused start" | tee -a results/run12/run.log
predict --model pplx-flash --qset v4 --split holdout2 --dataset data/run12/h1_focused.jsonl --run run12/focused --concurrency "${CONC:-4}"
date -u +"%Y-%m-%dT%H:%M:%SZ done" | tee -a results/run12/run.log
