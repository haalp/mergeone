#!/usr/bin/env bash
# §R11-H1 — the compact main call against its same-day full-state control, hosted pplx-flash, 705 test PRs.
# Run from the lab machine (data/run3/dataset.jsonl on disk; PERPLEXITY_API_KEY in .env). ≈ $0.10.
#   scripts/run11_hosted.sh            # refuses until the protocol is frozen (tag r11-frozen must exist)
#   scripts/run11_hosted.sh report     # tables only
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && source .env; set +a
RUN=run11/main
if [ "${1:-}" = "report" ]; then python -m mergeone.eval.run11_report; exit 0; fi
git rev-parse -q --verify refs/tags/r11-frozen >/dev/null || { echo "refusing: tag r11-frozen does not exist (PROTOCOL.md §R11 not frozen)"; exit 2; }
[ -f data/run3/dataset.jsonl ] || { echo "data/run3/dataset.jsonl missing (gatebench-data)"; exit 2; }
: "${PERPLEXITY_API_KEY:?PERPLEXITY_API_KEY not set}"
mkdir -p results/$RUN
date -u +"%Y-%m-%dT%H:%M:%SZ control start" | tee -a results/$RUN/run.log
# control first (full state), then the treatment, the same UTC day
mergeone predict --model pplx-flash --qset v4 --split test --dataset data/run3/dataset.jsonl --run $RUN --concurrency "${CONC:-4}"
date -u +"%Y-%m-%dT%H:%M:%SZ compact start" | tee -a results/$RUN/run.log
mergeone predict --model pplx-flash --qset v4 --split test --dataset data/run3/dataset.jsonl --run $RUN --compact --concurrency "${CONC:-4}"
date -u +"%Y-%m-%dT%H:%M:%SZ done" | tee -a results/$RUN/run.log
python -m mergeone.eval.run11_report
