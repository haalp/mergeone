#!/usr/bin/env bash
# Run 10, d3-flash row, on one 48 GB pod (PROTOCOL.md §R10; docs/run10-tooling.md has the whole order incl. the Mac side).
# Derived from scripts/run9_gpu.sh: idempotent stages, results/run10 on the persistent volume, --dry-run prints every command.
# Assumes: repo cloned at the run-10 tooling commit, data/run3/dataset.jsonl (gitignored) copied over.
#
#   bash scripts/run10_gpu.sh sequence             setup serve d3 stop report (resumable: re-run after any failure)
#   bash scripts/run10_gpu.sh sequence --dry-run   print every command, run nothing
#   STAGES="serve d3" bash scripts/run10_gpu.sh [--dry-run]
#
# Stages:
#   setup   uv venv (Python 3.12) .venv; pip install -e . and transformers==5.17.0 torch torchvision pillow
#           opencv-python-headless safetensors accelerate flash-linear-attention; snapshot_download vllm-sr/d3-flash under
#           HF_HOME (/workspace/hf); the snapshot's requirements.txt; snapshot revision → results/run10/logs/snapshot.json,
#           pip freeze → results/run10/logs/pip_freeze.txt (logs/ is gitignored; the revision is also in serve_meta.json)
#   serve   <snapshot>/d3_server.py --model <snapshot> --host 127.0.0.1 --port 8000 --batch-size 8 (log: logs/server_d3.log);
#           waits for GET /health = ready; results/run10/d3/serve_meta.json from /health + /v1/models + nvidia-smi + USD_PER_HOUR;
#           then the warm pass (scripts/run10_warm.py: 12 val rows by length, v4 questions, sequential, no timeout) → d3/warm.json.
#           A server already up is kept.
#   d3      the three d3 predicts into results/run10/d3/ (python -m mergeone.eval.run10_rows run d3 --part …):
#             v4_test   TIMED, 705 PRs, concurrency D3_CONCURRENCY (8) → + server_before/after.json, throughput.json (run9_step)
#             v4_val    1,491 PRs, resumable (calibration-map input; not a bar)
#             relation2 100 adjudicated pairs, resumable
#           A finished timed pass (throughput.json present) is kept; FORCE=1 repeats it. An interrupted timed pass (v4 test rows
#           without throughput.json) is moved to d3/passes/<UTC>/ (run10_rows set-aside) and re-run whole.
#   stop    stop the server, wait until the GPU is released
#   report  python -m mergeone.eval.run10_report
# Env: USD_PER_HOUR (required by sequence), VM_TYPE ("runpod A40 <secure|community> <dc>"), PORT (8000), D3_CONCURRENCY (8),
#      PERSIST (/workspace/run10), HF_HOME (/workspace/hf), MERGEONE_HTTP_TIMEOUT (900).
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=0; MODE=stages
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    sequence) MODE=sequence ;;
    -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a"; exit 2 ;;
  esac
done
[ "${DRY_RUN:-0}" = 1 ] && DRY=1

py() { [ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo "${PYTHON:-python3}"; }
PY="${PYTHON:-$(py)}"
M=d3-flash-local; REPO_ID=vllm-sr/d3-flash; DS=data/run3/dataset.jsonl; R=results/run10; D=$R/d3; LOG=$R/logs
PERSIST="${PERSIST:-$([ -d /workspace ] && echo /workspace/run10 || echo "")}"
if [ -n "$PERSIST" ] && [ "$DRY" = 0 ] && [ ! -L "$R" ]; then
  mkdir -p "$PERSIST"; [ -d "$R" ] && cp -an "$R/." "$PERSIST/" && rm -rf "$R"; ln -s "$PERSIST" "$R"
  echo "results/run10 → $PERSIST (persistent volume)"
fi
PORT="${PORT:-8000}"; USD_PER_HOUR="${USD_PER_HOUR:-}"; C_D3="${D3_CONCURRENCY:-8}"
export HF_HOME="${HF_HOME:-$([ -d /workspace ] && echo /workspace/hf || echo "$HOME/.cache/huggingface")}"
export MERGEONE_D3_ENDPOINT="http://127.0.0.1:${PORT}/v1/systemone"
export MERGEONE_HTTP_TIMEOUT="${MERGEONE_HTTP_TIMEOUT:-900}"
FROZEN=--i-have-frozen-the-protocol

run() {  # print, and execute unless --dry-run
  echo "+ $*"
  [ "$DRY" = 1 ] || eval "$@"
}
note() { echo "--- $*"; }
healthy() { [ "$DRY" = 1 ] || curl -fsS "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"ready"'; }
snap_dir() { cat "$LOG/snapshot_dir" 2>/dev/null || echo '<snapshot_dir>'; }
snap() { run "$PY -m mergeone.eval.run10_rows snapshot --endpoint $MERGEONE_D3_ENDPOINT --out '$1'"; }

stage() {
  case "$1" in
  setup)
    echo "=== setup"
    run "mkdir -p '$LOG'"
    run "nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv | tee '$LOG/nvidia-smi.csv'"
    run "command -v uv >/dev/null || python3 -m pip install -q uv"
    run "[ -x .venv/bin/python3 ] || uv venv --seed --python 3.12 .venv"
    PY=.venv/bin/python3
    run "$PY -m pip install -q -e ."
    run "$PY -m pip install -q transformers==5.17.0 torch torchvision pillow opencv-python-headless safetensors accelerate flash-linear-attention"
    if [ "$DRY" = 1 ] || [ ! -s "$LOG/snapshot_dir" ]; then
      run "$PY -c \"from huggingface_hub import snapshot_download; print(snapshot_download('$REPO_ID'))\" | tail -1 > '$LOG/snapshot_dir'"
    fi
    run "$PY - '$LOG/snapshot_dir' '$LOG/snapshot.json' <<'PYEOF'
import json, sys, pathlib, datetime
d = pathlib.Path(open(sys.argv[1]).read().strip())
rec = {'repo': '$REPO_ID', 'revision': d.name, 'path': str(d), 'hf_home': '$HF_HOME',
       'files': sorted(p.name for p in d.iterdir()), 'at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
open(sys.argv[2], 'w').write(json.dumps(rec, indent=2) + '\n'); print(json.dumps({k: rec[k] for k in ('repo', 'revision', 'path')}))
PYEOF"
    run "test -f \"\$(cat '$LOG/snapshot_dir')/d3_server.py\" || { echo 'd3_server.py not in the snapshot'; exit 1; }"
    run "[ ! -f \"\$(cat '$LOG/snapshot_dir')/requirements.txt\" ] || $PY -m pip install -q -r \"\$(cat '$LOG/snapshot_dir')/requirements.txt\""
    run "$PY -c \"import transformers; v = transformers.__version__; print('transformers', v); v == '5.17.0' or print('WARNING: requirements.txt moved transformers off 5.17.0 — record it in CHANGELOG before the first call')\""
    run "$PY -c \"import fla; print('flash-linear-attention', getattr(fla, '__version__', '?'))\" || echo 'WARNING: flash-linear-attention does not import (§R10.6: the row is served with it)'"
    run "$PY -m pip freeze > '$LOG/pip_freeze.txt'"
    ;;
  serve)
    echo "=== serve d3-flash (BF16, --batch-size 8)"
    run "mkdir -p '$LOG' '$D'"
    if healthy && [ "$DRY" = 0 ]; then note "server already up on :$PORT"; return; fi
    local meta="$D/serve_meta.json"
    [ "$DRY" = 0 ] && [ -f "$meta" ] && meta="$D/serve_meta.$(date -u +%Y%m%dT%H%M%SZ).json"   # a restart keeps the first meta
    run "nohup $PY \"\$(cat '$LOG/snapshot_dir')/d3_server.py\" --model \"\$(cat '$LOG/snapshot_dir')\" --host 127.0.0.1 --port $PORT --batch-size 8 >> '$LOG/server_d3.log' 2>&1 & echo \$! > '$LOG/server.pid'"
    if [ "$DRY" = 0 ]; then
      for _ in $(seq 1 240); do
        healthy && break
        if ! kill -0 "$(cat "$LOG/server.pid")" 2>/dev/null; then echo "server died:"; tail -40 "$LOG/server_d3.log"; exit 1; fi
        sleep 5
      done
      healthy || { echo "server not ready after 20 min"; tail -40 "$LOG/server_d3.log"; exit 1; }
    fi
    run "$PY -m mergeone.eval.run10_rows serve-meta --endpoint $MERGEONE_D3_ENDPOINT --out '$meta' --batch-size 8 --revision \"\$(basename \"\$(cat '$LOG/snapshot_dir')\")\" --snapshot-dir \"\$(cat '$LOG/snapshot_dir')\" ${USD_PER_HOUR:+--usd-per-hour $USD_PER_HOUR} ${VM_TYPE:+--vm-type '$VM_TYPE'}"
    run "$PY scripts/run10_warm.py --model $M --dataset $DS --split val --n 12 --out '$D/warm.json' 2>&1 | tail -14"
    ;;
  d3)
    echo "=== d3 rows (test v4 timed at concurrency $C_D3, val v4, relation2)"
    [ "$DRY" = 1 ] || [ -f "$DS" ] || { echo "missing $DS — copy data/run3/dataset.jsonl first"; exit 1; }
    healthy || { echo "server down: run STAGES=serve"; exit 1; }
    run "mkdir -p '$D'"
    if [ -f "$D/throughput.json" ] && [ -z "${FORCE:-}" ] && [ "$DRY" = 0 ]; then
      note "keep $D/throughput.json (FORCE=1 repeats the timed pass)"
    else
      if [ "$DRY" = 1 ] || [ -f "$D/throughput.json" ] || grep -q '"qset": "v4"' "$D/predictions_${M}_test_all.jsonl" 2>/dev/null; then
        run "$PY -m mergeone.eval.run10_rows set-aside d3 --part v4_test"   # an earlier or interrupted pass is kept, not resumed
      fi
      snap "$D/server_before.json"
      run "T0=\$(date +%s.%N)"
      run "$PY -m mergeone.eval.run10_rows run d3 --part v4_test --concurrency $C_D3 $FROZEN 2>&1 | tee -a '$D/predict.log'"
      run "T1=\$(date +%s.%N)"
      snap "$D/server_after.json"
      run "$PY -m mergeone.eval.run9_step throughput --step-dir '$D' --model $M --qset v4 --split test --before '$D/server_before.json' --after '$D/server_after.json' --outer-wall-s \$(python3 -c \"print(\$T1-\$T0)\")"
    fi
    run "$PY -m mergeone.eval.run10_rows run d3 --part v4_val --concurrency $C_D3 $FROZEN 2>&1 | tee -a '$D/predict.log'"
    run "$PY -m mergeone.eval.run10_rows run d3 --part relation2 --concurrency $C_D3 $FROZEN 2>&1 | tee -a '$D/predict.log'"
    ;;
  stop)
    echo "=== stop server"
    run "if [ -f '$LOG/server.pid' ] && kill -0 \"\$(cat '$LOG/server.pid')\" 2>/dev/null; then kill \"\$(cat '$LOG/server.pid')\"; for i in \$(seq 1 60); do kill -0 \"\$(cat '$LOG/server.pid')\" 2>/dev/null || break; sleep 2; done; kill -9 \"\$(cat '$LOG/server.pid')\" 2>/dev/null || true; for i in \$(seq 1 30); do [ \"\$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)\" -lt 2000 ] && break; sleep 2; done; nvidia-smi --query-gpu=memory.used --format=csv,noheader; echo stopped; else echo 'not running'; fi" ;;
  report)   echo "=== report"; run "$PY -m mergeone.eval.run10_report" ;;
  *) echo "unknown stage $1"; exit 2 ;;
  esac
}

stages() { local s; for s in $1; do stage "$s"; done; }

if [ "$MODE" = sequence ]; then
  [ "$DRY" = 1 ] || [ -f "$DS" ] || { echo "missing $DS — copy data/run3/dataset.jsonl first"; exit 1; }
  [ "$DRY" = 1 ] || [ -n "$USD_PER_HOUR" ] || { echo "set USD_PER_HOUR=<pod \$/h> and VM_TYPE='runpod A40 <secure|community> <dc>' (H3's d3 cost)"; exit 1; }
  echo "##### run 10 pod sequence (§R10.5)$([ "$DRY" = 1 ] && echo ' — dry run')"
  stages "setup serve d3 stop report"
  echo "##### pod part done. Same UTC day on the Mac: the hosted rows (control first), then pull results/run10 and terminate the pod."
  exit 0
fi

STAGES="${STAGES:?set STAGES (setup serve d3 stop report) or use: sequence}"
stages "$STAGES"
