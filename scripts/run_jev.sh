#!/usr/bin/env bash
# Jev-only runs (val+test; primary, all, all --words) + Jev load tests.
# Requires TYPESAFE_API_KEY. Haiku runs live in scripts/run_api_models.sh.
set -euo pipefail
RUN="${RUN:-run1}"; C="${CONCURRENCY:-8}"
cd "$(dirname "$0")/.."
for s in val test; do
  python3 -m mergeone.cli predict --model jev --split $s --run $RUN --questions primary --concurrency $C
  python3 -m mergeone.cli predict --model jev --split $s --run $RUN --questions all     --concurrency $C
  python3 -m mergeone.cli predict --model jev --split $s --run $RUN --questions all --words --concurrency $C
done
for k in 1 10 100; do python3 -m mergeone.cli loadtest --model jev --multiplier $k --run $RUN; done
