#!/usr/bin/env bash
# 20-example smoke test of both APIs before spending money. Writes to results/smoke/.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m mergeone.cli predict --model jev   --split val --run smoke --limit 20 --questions all
python3 -m mergeone.cli predict --model haiku --split val --run smoke --limit 20 --questions all
python3 - <<'PY'
import json, glob
for f in sorted(glob.glob("results/smoke/predictions_*.jsonl")):
    rows=[json.loads(l) for l in open(f)]
    ok=[r for r in rows if r["decision"]["error"] is None]
    print(f, "ok", len(ok), "/", len(rows))
    for r in ok[:3]:
        d=r["decision"]; print("   ", r["pr_number"], {k: round(v,3) for k,v in d["probs"].items()}, f"{d['latency_ms']:.0f}ms", d["input_tokens"], "tok", d["model_version"])
    for r in rows:
        if r["decision"]["error"]: print("   ERR", r["decision"]["error"][:160]); break
PY
