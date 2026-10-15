#!/usr/bin/env bash
# Run2, cloud side: judge labels (Sonnet 5.5) + Haiku 5.5 candidates. Requires ANTHROPIC_API_KEY.
set -euo pipefail
RUN="${RUN:-run2}"; cd "$(dirname "$0")/.."
python3 -m mergeone.cli judge --split val  --n 800  --run $RUN
python3 -m mergeone.cli judge --split test --n 1200 --run $RUN
# judge self-agreement on 60 test PRs at temperature 1.0
python3 - <<'PY'
import json, random
rows=[json.loads(l) for l in open("results/run2/judge_test.jsonl")]
random.Random(7).shuffle(rows)
with open("results/run2/_repeat_subset.jsonl","w") as f:
    for r in rows[:60]: f.write(json.dumps({"pr_number": r["pr_number"]})+"\n")
PY
python3 -m mergeone.cli judge --split test --n 60 --run $RUN --repeat
python3 -m mergeone.cli handcheck-v2 --n 60 --run $RUN
for s in val test; do
  python3 -m mergeone.cli predict --model haiku --split $s --run $RUN --qset v2 --questions all     --subset results/$RUN/judge_$s.jsonl
  python3 -m mergeone.cli predict --model haiku --split $s --run $RUN --qset v2 --questions primary --subset results/$RUN/judge_$s.jsonl
done
