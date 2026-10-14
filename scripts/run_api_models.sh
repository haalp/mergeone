#!/usr/bin/env bash
# Run the API-backed systems on val+test. Requires ANTHROPIC_API_KEY and TYPESAFE_API_KEY in the environment.
# Expected volume (val 7,933 + test 7,553 = 15,486 PRs):
#   jev   : 3 variants (primary, all, all --words)  -> ~46k calls, ~$5 at list price
#   haiku : 2 variants (primary, all)               -> ~31k calls, ~$8 at list price
# Resumable: re-running with --only-errors-from retries failed rows only.
set -euo pipefail
RUN="${RUN:-run1}"
C="${CONCURRENCY:-8}"
cd "$(dirname "$0")/.."
for s in val test; do
  python3 -m mergeone.cli predict --model jev   --split $s --run $RUN --questions primary --concurrency $C
  python3 -m mergeone.cli predict --model jev   --split $s --run $RUN --questions all     --concurrency $C
  python3 -m mergeone.cli predict --model jev   --split $s --run $RUN --questions all --words --concurrency $C
  python3 -m mergeone.cli predict --model haiku --split $s --run $RUN --questions primary --concurrency $C
  python3 -m mergeone.cli predict --model haiku --split $s --run $RUN --questions all     --concurrency $C
done
for k in 1 10 100; do
  python3 -m mergeone.cli loadtest --model jev   --multiplier $k --limit 600 --run $RUN
  python3 -m mergeone.cli loadtest --model haiku --multiplier $k --limit 600 --run $RUN
done
python3 -m mergeone.cli report results/$RUN
