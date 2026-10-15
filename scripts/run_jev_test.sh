#!/usr/bin/env bash
# Second half of the Jev run: test split (3 variants), retry val errors, load tests.
# Use after a val run that completed. Requires TYPESAFE_API_KEY.
set -euo pipefail
RUN="${RUN:-run1}"; C="${CONCURRENCY:-8}"
cd "$(dirname "$0")/.."
# retry val rows that errored (network blips); report keeps the latest row per PR
for v in "primary" "all" "all --words"; do
  name=$(echo "$v" | sed 's/ --words/_words/')
  f="results/$RUN/predictions_jev_val_${name}.jsonl"
  if [ -f "$f" ] && grep -q '"error": "' "$f"; then
    python3 -m mergeone.cli predict --model jev --split val --run $RUN --questions $v --concurrency $C --append --only-errors-from "$f"
  fi
done
python3 -m mergeone.cli predict --model jev --split test --run $RUN --questions primary --concurrency $C
python3 -m mergeone.cli predict --model jev --split test --run $RUN --questions all     --concurrency $C
python3 -m mergeone.cli predict --model jev --split test --run $RUN --questions all --words --concurrency $C
for k in 100 10 1; do python3 -m mergeone.cli loadtest --model jev --multiplier $k --limit 600 --run $RUN; done
echo "--- error counts:"
for f in results/$RUN/predictions_jev_*.jsonl; do echo "$f $(grep -c '"error": "' $f || true)"; done
