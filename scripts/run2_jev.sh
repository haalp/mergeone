#!/usr/bin/env bash
# Run2, Jev side (author's machine). Requires TYPESAFE_API_KEY and results/run2/judge_{val,test}.jsonl pulled from git.
set -euo pipefail
RUN="${RUN:-run2}"; C="${CONCURRENCY:-8}"; cd "$(dirname "$0")/.."
for s in val test; do
  python3 -m mergeone.cli predict --model jev --split $s --run $RUN --qset v2 --questions all     --subset results/$RUN/judge_$s.jsonl --concurrency $C
  python3 -m mergeone.cli predict --model jev --split $s --run $RUN --qset v2 --questions primary --subset results/$RUN/judge_$s.jsonl --concurrency $C
  python3 -m mergeone.cli predict --model jev --split $s --run $RUN --qset v2 --questions all --words --subset results/$RUN/judge_$s.jsonl --concurrency $C
done
echo "--- error counts:"; for f in results/$RUN/predictions_jev_*.jsonl; do echo "$f $(grep -c '"error": "' $f || true)"; done
