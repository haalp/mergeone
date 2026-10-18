#!/usr/bin/env bash
# Run 6 part A on the GPU VM (PROTOCOL.md "Run 6 addendum, part A"; docs/run6-gpu.md has the VM steps).
# Assumes: repo cloned, .venv created, data/run3/dataset.jsonl + manifest.json copied over, HF_TOKEN in env or .env.
# Every stage is resumable; re-run the script after any failure. Order follows R6A.5.
#   setup    pip install -e . + serve/requirements.txt, download the checkpoint (revision pinned on first download),
#            pip install "<ckpt>/source[fast]", nvidia-smi snapshot
#   serve    start serve/systemone_server.py in the background (logs: results/run6/logs/), wait for /healthz;
#            the server writes results/run6/serve_meta.json after warmup
#   h1       joint v3 on test (equivalence set)         → results/run6/predictions_pplx-flash-local_test_all.jsonl
#   val      joint v3 on val (H3 calibration refit)     → results/run6/predictions_pplx-flash-local_val_all.jsonl
#   single   one question per request, v3, test (H4)    → same test file, mode=single
#   pairs    same_change on the run-4 R4-H2 test pairs, if results/run4/pairs.jsonl was copied (gitignored; skipped if absent)
#   load     open-loop load tests ×1 ×10 ×30 ×100 ×300 (429 retry off, 512 workers, 60 s gaps). Existing results are kept;
#            FORCE_LOADTEST=1 re-runs them and moves the previous pass to results/run6/loadtest_passes/<UTC time>/ (R6A.4)
#   report   compact, report results/run6 --run6, leaderboard
#   stop     stop the server
# Skip stages with SKIP="pairs single"; run only some with STAGES="load report".
# Env: USD_PER_HOUR (VM on-demand $/h, default 0.8048 = g6.xlarge us-east-1 — set it for any other VM), VM_TYPE (default: EC2
#      metadata), PORT (8080), CONCURRENCY (16, predict), REVISION (first download only; default main), LT_LIMIT (600), LT_GAP (60).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
export PYTHON="$PY"
M=pplx-flash-local; DS=data/run3/dataset.jsonl; R=results/run6; LOG=$R/logs
PORT="${PORT:-8080}"; C="${CONCURRENCY:-16}"; USD_PER_HOUR="${USD_PER_HOUR:-0.8048}"
export MERGEONE_LOCAL_ENDPOINT="http://127.0.0.1:${PORT}/v1/systemone"
STAGES="${STAGES:-setup serve h1 val single pairs load report stop}"
mkdir -p "$LOG"
want() { case " $STAGES " in *" $1 "*) ;; *) return 1;; esac; case " ${SKIP:-} " in *" $1 "*) echo "--- skip stage $1"; return 1;; esac; }
healthy() { curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; }
ckpt() { cat "$LOG/checkpoint_dir" 2>/dev/null || true; }
[ -f "$DS" ] || { echo "missing $DS — copy data/run3/dataset.jsonl (and manifest.json) to the VM first (docs/run6-gpu.md)"; exit 1; }

if want setup; then
  echo "=== setup"
  [ -n "${HF_TOKEN:-}" ] || { echo "HF_TOKEN not set (the checkpoint is gated; accept the licence on huggingface.co first)"; exit 1; }
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv | tee "$LOG/nvidia-smi.csv"
  $PY -m pip install -q -e .
  $PY -m pip install -q -r serve/requirements.txt
  if [ -z "$(ckpt)" ]; then
    $PY serve/systemone_server.py --download-only --revision "${REVISION:-main}" | tail -1 > "$LOG/checkpoint_dir"
  fi
  CK="$(ckpt)"; echo "checkpoint: $CK (revision $(basename "$CK"))"
  [ -d "$CK/source" ] || { echo "$CK/source not found — unexpected checkpoint layout"; exit 1; }
  $PY -m pip install -q "$CK/source[fast]"   # vendor package; pins torch / transformers
  $PY -m pip freeze > "$LOG/pip_freeze.txt"
  $PY -c "import torch; print('torch', torch.__version__, 'cuda', torch.version.cuda, torch.cuda.get_device_name(0))"
fi

if want serve; then
  echo "=== serve"
  if healthy; then echo "server already up on :$PORT"; else
    CK="$(ckpt)"; [ -n "$CK" ] || { echo "no checkpoint recorded; run STAGES=setup first"; exit 1; }
    nohup $PY serve/systemone_server.py --checkpoint-dir "$CK" --port "$PORT" --fast --batch-max 32 --batch-ms 20 \
      --usd-per-hour "$USD_PER_HOUR" ${VM_TYPE:+--vm-type "$VM_TYPE"} --meta-out "$R/serve_meta.json" \
      >> "$LOG/server.log" 2>&1 &
    echo $! > "$LOG/server.pid"
    for i in $(seq 1 180); do healthy && break; kill -0 "$(cat "$LOG/server.pid")" 2>/dev/null || { echo "server died:"; tail -30 "$LOG/server.log"; exit 1; }; sleep 5; done
    healthy || { echo "server not healthy after 15 min"; tail -30 "$LOG/server.log"; exit 1; }
  fi
  curl -fsS "http://127.0.0.1:${PORT}/healthz"; echo
  cat "$R/serve_meta.json"
fi

predict() {  # predict <split> [extra args...]; always append + resume
  local sp=$1; shift
  $PY -m mergeone.cli predict --model $M --split "$sp" --run run6 --dataset "$DS" --qset v3 --concurrency "$C" \
    --append --only-missing-from "$R/predictions_${M}_${sp}_all.jsonl" "$@"
}

if want h1; then echo "=== H1: joint v3, test"; healthy || { echo "server down"; exit 1; }; predict test; fi
if want val; then echo "=== H3: joint v3, val (calibration refit)"; healthy || { echo "server down"; exit 1; }; predict val; fi
if want single; then echo "=== H4: single question per request, v3, test"; healthy || { echo "server down"; exit 1; }; predict test --single-question; fi

if want pairs; then
  echo "=== H3 pair task: same_change on run-4 test pairs"
  if [ -f results/run4/pairs.jsonl ]; then
    healthy || { echo "server down"; exit 1; }
    $PY -m mergeone.eval.run6 pair-subset --pairs results/run4/pairs.jsonl --out "$R/pairs_eval_test.jsonl"
    $PY -m mergeone.cli predict --model $M --split test --run run6 --dataset results/run4/pairs.jsonl --qset pair \
      --subset "$R/pairs_eval_test.jsonl" --concurrency "$C" --append --only-missing-from "$R/predictions_${M}_test_all.jsonl"
  else
    echo "results/run4/pairs.jsonl not present (gitignored; copy it from the run-4 machine to evaluate the pair task); skipped"
  fi
fi

if want load; then
  echo "=== H2: load tests (open loop, v3, 429 retry off, 512 workers)"
  healthy || { echo "server down"; exit 1; }
  for x in 1 10 30 100 300; do
    f="$R/loadtest_${M}_x${x}.json"
    if [ -f "$f" ]; then
      if [ -z "${FORCE_LOADTEST:-}" ]; then echo "keep $f"; continue; fi
      d="$R/loadtest_passes/$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$d"; mv "$f" "$d/"; echo "previous pass → $d"
    fi
    MERGEONE_NO_RETRY=1 $PY -m mergeone.cli loadtest --model $M --multiplier $x --limit "${LT_LIMIT:-600}" --run run6 \
      --dataset "$DS" --qset v3 --no-retry --concurrency 512 || echo "loadtest x$x failed (exit $?)"
    curl -fsS "http://127.0.0.1:${PORT}/healthz" > "$LOG/healthz_after_x${x}.json" || true
    sleep "${LT_GAP:-60}"
  done
fi

if want report; then
  echo "=== report"
  ls $R/predictions_*.jsonl >/dev/null 2>&1 && $PY -m mergeone.cli compact $R/predictions_*.jsonl
  echo "--- error counts:"; for f in $R/predictions_*.jsonl; do echo "$f $(grep -c '"error": "' "$f" || true)"; done
  $PY -m mergeone.cli report $R --run6 | tail -30
  $PY -m mergeone.cli leaderboard >/dev/null && echo "--- results/leaderboard.md updated"
fi

if want stop; then
  echo "=== stop server"
  if [ -f "$LOG/server.pid" ] && kill -0 "$(cat "$LOG/server.pid")" 2>/dev/null; then kill "$(cat "$LOG/server.pid")"; echo stopped; else echo "not running"; fi
fi
echo "done. Commit results/run6/ (not logs/) from the VM or copy it back, then terminate the VM (R6A.5 step 7)."
