#!/usr/bin/env bash
# run3 data collection: discover agent-heavy repos, then fetch their agent PRs.
# Resumable: re-running continues from the checkpoints in data/run3/.
# Usage:  GITHUB_TOKEN=ghp_... scripts/run3_collect.sh          (or put GITHUB_TOKEN=... in .env)
set -u
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
: "${GITHUB_TOKEN:?set GITHUB_TOKEN (env or .env)}"
PER_REPO="${PER_REPO:-120}"
SEED="${SEED:-microsoft/vscode dotnet/runtime dotnet/aspire microsoft/semantic-kernel Azure/azure-sdk-for-net microsoft/typespec microsoft/kiota openai/openai-agents-python}"
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
KEEPAWAKE=""; command -v caffeinate >/dev/null && KEEPAWAKE="caffeinate -i"
mkdir -p data/run3
log=data/run3/collect.log
echo "=== $(date -u +%FT%TZ) start (per_repo=$PER_REPO)" | tee -a "$log"

# a crash (network blip, laptop sleep) just restarts from the checkpoint, up to 20 times
for attempt in $(seq 1 20); do
  $KEEPAWAKE $PY -m mergeone.data.agent_prs discover --min-stars 500 --min-prs 20 --pages 3 --seed $SEED 2>&1 | tee -a "$log"
  [ -s data/run3/repos.json ] && [ "$(python3 -c 'import json;print(len(json.load(open("data/run3/repos.json"))))')" -gt 2 ] && break
  echo "discover did not finish (attempt $attempt); retrying in 30s" | tee -a "$log"; sleep 30
done
python3 -c '
import json;r=json.load(open("data/run3/repos.json"));print(len(r),"repos")
for x in r: print(f"{x[\"agent_prs_seen\"]:6d} *{x[\"stars\"]:<7d} {x[\"repo\"]:45s} {x[\"by_agent\"]}")' | tee -a "$log"

for attempt in $(seq 1 20); do
  $KEEPAWAKE $PY -m mergeone.data.agent_prs fetch --per-repo "$PER_REPO" 2>&1 | tee -a "$log" && break
  echo "fetch crashed (attempt $attempt); resuming in 30s" | tee -a "$log"; sleep 30
done
echo "=== $(date -u +%FT%TZ) done: $(wc -l < data/run3/agent_prs.jsonl) PRs in data/run3/agent_prs.jsonl" | tee -a "$log"
