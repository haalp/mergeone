#!/usr/bin/env bash
# Run 9 on one L4 pod (PROTOCOL.md §R9; docs/run9-tooling.md has the order and the pod steps; docs/run6-gpu.md §1b the pod).
# Derived from scripts/run6_gpu.sh: same setup / serve / stop; PASS → ARM, one directory per row under results/run9/.
# Assumes: repo cloned at the run-9 tooling commit, .venv, data/run3/dataset.jsonl + manifest.json, results/run4/pairs.jsonl,
# results/run6/candidates_k20.jsonl copied over (the last two are gitignored).
#
#   bash scripts/run9_gpu.sh sequence             the whole pod-side order of §R9.5, resumable (re-run after any failure)
#   bash scripts/run9_gpu.sh sequence --dry-run   print every command of the sequence, run nothing
#   ARM=<arm> STAGES="…" bash scripts/run9_gpu.sh [--dry-run]    single stages (docs/run9-tooling.md §2–8)
#
# Arms (server rows) and their vendor FastOptions (fixed here; FAST_OPTIONS may not override them):
#   bf16     control: no --fast-options (FastOptions() defaults)          results/run9/bf16/
#   fp8      R9-H3:   --fast-options '{"fp8": true}'                       results/run9/fp8/
#   buckets  R9-H2 fallback, only if H3 fails: '{"buckets": [2560, 3072, 4096, 8192, 16384, 32768]}'   results/run9/buckets/
#   shared   R9-H4 shared-A arm: BF16, restarted, no options               results/run9/shared/
#   h5       R9-H5 local single-question: BF16, restarted, no options      results/run9/h5/
# Every server: --fast --batch-max 32 --batch-ms 20 --batch-tokens 131072 (§R9.2, §R9.6).
#
# Stages (each writes into results/run9/<arm>/<step>/ with server_before.json / server_after.json = GET /meta + /healthz):
#   setup     pip install, checkpoint at the run-6A revision (REVISION, default 6d771ad7…), vendor package from <ckpt>/source,
#             vendor API record → results/run9/logs/vendor_api.json (how FastOptions reaches the decider; settle before any call)
#   groups    python -m mergeone.eval.run9_pairs groups (no model call)
#   serve     start the arm's server (logs: results/run9/logs/server_<arm>.log); serve_meta.json under results/run9/<arm>/.
#             If the fp8 / buckets server fails to load or warm up → results/run9/<arm>/unavailable.json and exit 3.
#             If a server is already up, its /meta options must be this arm's (else: run the stop stage first).
#   equiv     TIMED joint v3 on the 705 test PRs, concurrency 32 → <arm>/equiv/ + throughput.json
#   val       joint v3 on val, concurrency 16, untimed (FP8 isotonic refit) → <arm>/val/
#   pairs_a   per-candidate same_change on the 187 R4-H2 test pairs, concurrency 16, untimed → <arm>/pairs_a/
#   pairs_b   TIMED per-candidate same_change on set (b), 1,000 candidates, concurrency 32 → <arm>/pairs_b/ + throughput.json
#   shared_a  shared-A request on set (a) groups, concurrency 16, untimed → <arm>/pairs_a/   (ARM=shared)
#   shared_b  TIMED shared-A request on set (b), 100 requests = 1,000 candidates, concurrency 32 → <arm>/pairs_b/ + throughput.json
#   load      open loop ×1 ×10 ×30, 600 v3 decisions per step, 512 workers, no retry, 60 s gaps → <arm>/load/
#   single    local --single-question v3 on test (7,050 requests), concurrency 16 → results/run9/h5/single/   (ARM=h5)
#   hosted    hosted pplx-flash v3 on test (key: PERPLEXITY_API_KEY; same UTC day as single) → results/run9/h5/hosted/
#   report    python -m mergeone.eval.run9_report
#   stop      stop the server
# Idempotent: a timed step whose throughput.json exists, and a load step whose json exists, are kept and skipped; FORCE=1 moves
# the earlier pass to <step>/passes/<UTC>/ and runs it again (§R9.4: a repeated pass is reported, not substituted). A timed
# step that was interrupted (predictions without throughput.json) is moved aside the same way and re-run whole, since a resumed
# pass is not one pass. Untimed steps resume (--append --only-missing-from).
# Env: USD_PER_HOUR, VM_TYPE ("runpod L4 <secure|community> <dc>"), PORT (8080), REVISION, LT_GAP (60), LT_LIMIT (600).
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=0; MODE=stages
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    sequence) MODE=sequence ;;
    -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a"; exit 2 ;;
  esac
done
[ "${DRY_RUN:-0}" = 1 ] && DRY=1

[ -f .env ] && [ "$DRY" = 0 ] && set -a && . ./.env && set +a
PY="${PYTHON:-$([ -x .venv/bin/python3 ] && echo .venv/bin/python3 || echo python3)}"
M=pplx-flash-local; DS=data/run3/dataset.jsonl; R=results/run9; LOG=$R/logs
PORT="${PORT:-8080}"; USD_PER_HOUR="${USD_PER_HOUR:-}"
REVISION="${REVISION:-6d771ad7b5ecff5e93b8fc7b90d4c5ccc341954d}"   # run 6A's (results/run6/serve_meta.json), §R9.2
BUCKETS_JSON='{"buckets": [2560, 3072, 4096, 8192, 16384, 32768]}'
export MERGEONE_LOCAL_ENDPOINT="http://127.0.0.1:${PORT}/v1/systemone"
C_TIMED=32; C_UNTIMED=16; LT_MULTS="1 10 30"

run() {  # print, and execute unless --dry-run
  echo "+ $*"
  [ "$DRY" = 1 ] || eval "$@"
}
note() { echo "--- $*"; }
healthy() { [ "$DRY" = 1 ] || curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; }
ckpt() { cat "$LOG/checkpoint_dir" 2>/dev/null || echo '<checkpoint_dir>'; }
utc() { date -u +%Y%m%dT%H%M%SZ; }

arm_options() {  # the arm's --fast-options value ('' = none)
  case "$1" in
    bf16|shared|h5) echo "" ;;
    fp8) echo '{"fp8": true}' ;;
    buckets) echo "$BUCKETS_JSON" ;;
    *) echo "unknown ARM=$1 (bf16 fp8 buckets shared h5)" >&2; exit 2 ;;
  esac
}

check_arm() {
  ARM="${ARM:?set ARM=bf16|fp8|buckets|shared|h5}"
  OPTS="$(arm_options "$ARM")"
  if [ -n "${FAST_OPTIONS:-}" ] && [ "$FAST_OPTIONS" != "$OPTS" ]; then
    echo "FAST_OPTIONS='$FAST_OPTIONS' differs from the frozen options of ARM=$ARM ('$OPTS'); unset it"; exit 2
  fi
}

snap() { run "$PY -m mergeone.eval.run9_step snapshot --port $PORT --out $1"; }

move_aside() {  # move_aside <dir>: keep an earlier (or interrupted) pass under <dir>/passes/<UTC>/
  local d=$1 p; p="$d/passes/$(utc)"
  run "mkdir -p '$p' && find '$d' -maxdepth 1 -type f -exec mv {} '$p/' \\;"
  note "earlier pass kept in $p"
}

timed() {  # timed <step> <qset> <predict args…>: one closed-loop pass at concurrency 32 + throughput.json
  local step=$1 qset=$2; shift 2
  local d="$R/$ARM/$step"
  if [ -f "$d/throughput.json" ] && [ -z "${FORCE:-}" ]; then note "keep $d/throughput.json (FORCE=1 repeats the pass)"; return; fi
  if [ "$DRY" = 0 ] && [ -n "$(ls "$d"/predictions_*.jsonl 2>/dev/null)" ]; then move_aside "$d"; fi
  healthy || { echo "server down"; exit 1; }
  run "mkdir -p '$d'"
  snap "$d/server_before.json"
  run "T0=\$(date +%s.%N)"
  run "$PY -m mergeone.cli predict --model $M --split test --run run9/$ARM/$step --qset $qset --concurrency $C_TIMED $* 2>&1 | tee '$d/predict.log'"
  run "T1=\$(date +%s.%N)"
  snap "$d/server_after.json"
  run "$PY -m mergeone.eval.run9_step throughput --step-dir '$d' --qset $qset --split test --before '$d/server_before.json' --after '$d/server_after.json' --outer-wall-s \$(python3 -c \"print(\$T1-\$T0)\")"
}

untimed() {  # untimed <step> <split> <qset> <predict args…>: resumable, concurrency 16
  local step=$1 sp=$2 qset=$3; shift 3
  local d="$R/$ARM/$step"
  healthy || { echo "server down"; exit 1; }
  run "mkdir -p '$d'"
  [ -f "$d/server_before.json" ] || snap "$d/server_before.json"
  run "$PY -m mergeone.cli predict --model $M --split $sp --run run9/$ARM/$step --qset $qset --concurrency $C_UNTIMED --append --only-missing-from '$d/predictions_${M}_${sp}_all.jsonl' $* 2>&1 | tee -a '$d/predict.log'"
}

stage() {  # stage <name>: run one stage for the current ARM
  case "$1" in
  setup)
    echo "=== setup"
    run "mkdir -p '$LOG'"
    run "nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv | tee '$LOG/nvidia-smi.csv'"
    run "$PY -m pip install -q -e ."
    run "$PY -m pip install -q -r serve/requirements.txt"
    if [ "$DRY" = 1 ] || [ ! -s "$LOG/checkpoint_dir" ]; then
      run "$PY serve/systemone_server.py --download-only --revision '$REVISION' | tail -1 > '$LOG/checkpoint_dir'"
    fi
    run "test \"\$(basename \"\$(cat '$LOG/checkpoint_dir')\")\" = '$REVISION' || echo 'WARNING: resolved revision differs from $REVISION — record it in CHANGELOG before the first call (§R9.2)'"
    run "$PY -m pip install -q \"\$(cat '$LOG/checkpoint_dir')/source[fast]\""
    run "$PY -m pip freeze > '$LOG/pip_freeze.txt'"
    # how FastOptions reaches the decider, read from the installed package (docs/run9-tooling.md §1 unknowns)
    run "$PY - <<'PYEOF' | tee '$LOG/vendor_api.json'
import dataclasses, hashlib, inspect, json, pathlib, pplx_decider as p, torch
src = pathlib.Path(p.__file__).parent
fo = getattr(p, 'FastOptions', None)
print(json.dumps({'version': getattr(p, '__version__', None),
  'from_pretrained': str(inspect.signature(p.Decider.from_pretrained)),
  'fast_options_fields': {f.name: repr(f.default) for f in dataclasses.fields(fo)} if fo and dataclasses.is_dataclass(fo) else None,
  'sha256': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(src.glob('*.py'))},
  'torch': torch.__version__, 'cuda': torch.version.cuda, 'gpu': torch.cuda.get_device_name(0),
  'compute_capability': list(torch.cuda.get_device_capability(0)), 'has_scaled_mm': hasattr(torch, '_scaled_mm')}, indent=2))
PYEOF"
    ;;
  groups)
    echo "=== groups (no model call)"
    run "$PY -m mergeone.eval.run9_pairs groups"
    ;;
  serve)
    check_arm
    echo "=== serve ARM=$ARM ${OPTS:+--fast-options $OPTS}"
    run "mkdir -p '$LOG' '$R/$ARM'"
    if healthy && [ "$DRY" = 0 ]; then
      local got; got="$(curl -fsS "http://127.0.0.1:${PORT}/meta" | $PY -c 'import json,sys; print(json.load(sys.stdin).get("fast_options_flag") or "")')"
      if [ "$got" != "$OPTS" ]; then echo "a server is up with fast options '$got', ARM=$ARM needs '$OPTS': run STAGES=stop first"; exit 2; fi
      note "server already up on :$PORT with ARM=$ARM's options"; return
    fi
    local meta="$R/$ARM/serve_meta.json"
    [ "$DRY" = 0 ] && [ -f "$meta" ] && meta="$R/$ARM/serve_meta.$(utc).json"   # a restart keeps the first meta
    run "nohup $PY serve/systemone_server.py --checkpoint-dir \"\$(cat '$LOG/checkpoint_dir')\" --port $PORT --fast --batch-max 32 --batch-ms 20 --batch-tokens 131072 ${OPTS:+--fast-options '$OPTS'} ${USD_PER_HOUR:+--usd-per-hour $USD_PER_HOUR} ${VM_TYPE:+--vm-type '$VM_TYPE'} --meta-out '$meta' >> '$LOG/server_$ARM.log' 2>&1 & echo \$! > '$LOG/server.pid'"
    [ "$DRY" = 1 ] && return
    for _ in $(seq 1 240); do
      healthy && break
      if ! kill -0 "$(cat "$LOG/server.pid")" 2>/dev/null; then
        echo "server died:"; tail -40 "$LOG/server_$ARM.log"
        if [ "$ARM" = fp8 ] || [ "$ARM" = buckets ]; then
          $PY - "$R/$ARM/unavailable.json" "$LOG/server_$ARM.log" "$OPTS" <<'PYEOF'
import json, sys, datetime
out, log, opts = sys.argv[1:4]
tail = open(log, errors="replace").read().splitlines()[-60:]
json.dump({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "fast_options": opts,
           "reason": "server failed to load or warm up with these options", "log_tail": tail}, open(out, "w"), indent=2)
PYEOF
          echo "→ $R/$ARM/unavailable.json (reported 'not run (unavailable on the pod)'; no substitute row, §R9.6)"; exit 3
        fi
        exit 1
      fi
      sleep 5
    done
    healthy || { echo "server not healthy after 20 min"; tail -40 "$LOG/server_$ARM.log"; exit 1; }
    curl -fsS "http://127.0.0.1:${PORT}/meta"; echo
    ;;
  equiv)    check_arm; echo "=== equivalence set, timed (ARM=$ARM)"; timed equiv v3 --dataset "$DS" ;;
  val)      check_arm; echo "=== val, untimed (ARM=$ARM)"; untimed val val v3 --dataset "$DS" ;;
  pairs_a)
    check_arm; echo "=== per-candidate pairs, set (a), untimed (ARM=$ARM)"
    [ "$DRY" = 1 ] || [ -f results/run4/pairs.jsonl ] || { echo "results/run4/pairs.jsonl missing (gitignored; copy it over)"; exit 1; }
    untimed pairs_a test pair --dataset results/run4/pairs.jsonl --subset results/run6/pairs_eval_test.jsonl ;;
  pairs_b)
    check_arm; echo "=== per-candidate pairs, set (b), timed (ARM=$ARM)"
    [ "$DRY" = 1 ] || [ -f "$R/pairs_b.jsonl" ] || { echo "$R/pairs_b.jsonl missing: run STAGES=groups"; exit 1; }
    timed pairs_b pair --dataset "$R/pairs_b.jsonl" ;;
  shared_a)
    check_arm; [ "$ARM" = shared ] || { echo "shared_a runs with ARM=shared"; exit 2; }
    echo "=== shared-A request, set (a), untimed"; untimed pairs_a test pair_shared --dataset "$R/groups_a.jsonl" ;;
  shared_b)
    check_arm; [ "$ARM" = shared ] || { echo "shared_b runs with ARM=shared"; exit 2; }
    echo "=== shared-A request, set (b), timed"; timed pairs_b pair_shared --dataset "$R/groups_b.jsonl" ;;
  load)
    check_arm; echo "=== load curve ×1 ×10 ×30 (ARM=$ARM; open loop, 600 per step, 512 workers, no retry)"
    healthy || { echo "server down"; exit 1; }
    local d="$R/$ARM/load"; run "mkdir -p '$d'"
    for x in $LT_MULTS; do
      local f="$d/loadtest_${M}_x${x}.json"
      if [ "$DRY" = 0 ] && [ -f "$f" ]; then
        if [ -z "${FORCE:-}" ]; then note "keep $f"; continue; fi
        local p="$d/passes/$(utc)"; mkdir -p "$p"; mv "$f" "$p/"; note "previous pass → $p"
      fi
      snap "$d/server_before_x${x}.json"
      run "MERGEONE_NO_RETRY=1 $PY -m mergeone.cli loadtest --model $M --multiplier $x --limit ${LT_LIMIT:-600} --run run9/$ARM/load --dataset $DS --qset v3 --no-retry --concurrency 512 || echo 'loadtest x$x failed'"
      snap "$d/server_after_x${x}.json"
      run "sleep ${LT_GAP:-60}"
    done ;;
  single)
    check_arm; [ "$ARM" = h5 ] || { echo "single runs with ARM=h5 (BF16 server)"; exit 2; }
    echo "=== R9-H5 local single-question (7,050 requests)"; untimed single test v3 --dataset "$DS" --single-question ;;
  hosted)
    echo "=== R9-H5 hosted pplx-flash v3 (key; same UTC day as the single stage)"
    [ "$DRY" = 1 ] || [ -n "${PERPLEXITY_API_KEY:-}" ] || { echo "PERPLEXITY_API_KEY not set (set -a; source .env; set +a)"; exit 1; }
    run "mkdir -p '$R/h5/hosted'"
    run "$PY -m mergeone.cli predict --model pplx-flash --split test --run run9/h5/hosted --qset v3 --dataset $DS --append --only-missing-from $R/h5/hosted/predictions_pplx-flash_test_all.jsonl 2>&1 | tee -a '$R/h5/hosted/predict.log'" ;;
  report)   echo "=== report"; run "$PY -m mergeone.eval.run9_report" ;;
  stop)
    echo "=== stop server"
    run "if [ -f '$LOG/server.pid' ] && kill -0 \"\$(cat '$LOG/server.pid')\" 2>/dev/null; then kill \"\$(cat '$LOG/server.pid')\"; sleep 5; echo stopped; else echo 'not running'; fi" ;;
  *) echo "unknown stage $1"; exit 2 ;;
  esac
}

stages() { local s; for s in $1; do stage "$s"; done; }   # stages "<list>" for the current ARM

h2_config() {  # the §R9.3 rule as the report applies it: fp8 | buckets | bf16 | need_buckets | pending
  [ "$DRY" = 1 ] && { echo "<from run9_report>"; return; }
  $PY -m mergeone.eval.run9_report >/dev/null
  $PY - <<'PYEOF'
import json
m = json.load(open("results/run9/metrics.json"))
cfg, h3 = m["h2"]["configuration"], m["h3"]["R9-H3"]
if cfg:
    print(cfg)
elif h3.get("pass") is False or h3.get("unavailable"):
    print("need_buckets")
else:
    print("pending")
PYEOF
}

if [ "$MODE" = sequence ]; then
  [ "$DRY" = 1 ] || [ -f "$DS" ] || { echo "missing $DS — copy data/run3/dataset.jsonl (and manifest.json) first"; exit 1; }
  [ "$DRY" = 1 ] || [ -n "$USD_PER_HOUR" ] || { echo "set USD_PER_HOUR=<pod \$/h> and VM_TYPE='runpod L4 <secure|community> <dc>'"; exit 1; }
  echo "##### run 9 pod sequence (§R9.5)$([ "$DRY" = 1 ] && echo ' — dry run')"
  stages "setup groups"
  ARM=bf16;   stages "stop serve equiv pairs_a pairs_b"           # BF16 control: equivalence set + per-candidate pair arm
  ARM=fp8                                                          # R9-H3
  rc=0; (stages "stop serve") || rc=$?
  if [ "$rc" = 0 ]; then stages "equiv val"; elif [ "$rc" != 3 ]; then exit "$rc"; fi
  ARM=shared; stages "stop serve shared_a shared_b"               # R9-H4 shared-A arm (BF16)
  ARM=bf16;   stages "stop serve load"                            # control load curve
  cfg="$(h2_config)"; echo "H2 configuration (§R9.3 rule): $cfg"
  if [ "$cfg" = need_buckets ]; then
    ARM=buckets; rc=0; (stages "stop serve") || rc=$?
    if [ "$rc" = 0 ]; then stages "equiv"; elif [ "$rc" != 3 ]; then exit "$rc"; fi
    cfg="$(h2_config)"; echo "H2 configuration after the bucket row: $cfg"
  fi
  case "$cfg" in
    fp8|buckets) ARM=$cfg; stages "stop serve load" ;;
    bf16) note "H2 configuration is the control; its curve is the bf16 load above" ;;
    "<from run9_report>") note "dry run: then ARM=<fp8|buckets> stages 'stop serve load' as the report's rule says" ;;
    *) echo "H2 configuration still pending ($cfg): check the report"; exit 1 ;;
  esac
  ARM=h5;     stages "stop serve single stop"                     # R9-H5 local half
  stage report
  echo "##### pod part done. Same UTC day: the hosted half of R9-H5 (STAGES=hosted, key), then terminate the pod."
  exit 0
fi

STAGES="${STAGES:?set STAGES (setup groups serve equiv val pairs_a pairs_b shared_a shared_b load single hosted report stop) or use: sequence}"
stages "$STAGES"
