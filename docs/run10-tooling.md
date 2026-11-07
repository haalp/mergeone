# Run 10 tooling

Commands for run 10 (PROTOCOL.md §R10, frozen 2026-11-07, tag `r10-frozen`), in order. Nothing below calls a model unless
marked **GPU** (the d3-flash server on the pod) or **key** (Perplexity, Anthropic). Run everything from the repository root.

**Rule: every row is compared with a same-day hosted pplx-flash control** in `results/run10/control/`; run-8 numbers are
shown beside it, never instead of it. The d3 rows and the hosted rows run on the same UTC day (control first on the Mac).

## 1. What exists (tooling commit; no model call)

| piece | what it does |
|---|---|
| `prompts/models.yaml` row `d3-flash-local` | dialect `systemone`, endpoint `${MERGEONE_D3_ENDPOINT:-http://127.0.0.1:8000/v1/systemone}`, `model_id: d3-flash`, weights `vllm-sr/d3-flash`, Apache-2.0, 8.4B, prices 0. The adapter (`mergeone/models/jev.py`) sends `model: d3-flash` (`self.pinned` = the model id, which does not end in `-latest`); every answer stores the reply's own `model` as `model_version`, so predictions carry the served name (`d3-flash`, as `results/run10prep/d3_check.json` shows) |
| `python -m mergeone.eval.run10_rows` | one resumable command per row (`run <row> --i-have-frozen-the-protocol`; refuses without the flag; `--dry-run` counts what is still to ask); `list` prints every row's `mergeone predict` equivalents; `set-aside` moves an interrupted timed pass to `passes/<UTC>/`; `snapshot` / `serve-meta` read the server's `/health` and `/v1/models` (no model call) |
| `scripts/run10_gpu.sh` | pod stages `setup serve d3 stop report`; `sequence` = all five; `--dry-run` prints every command; idempotent; `results/run10` → `/workspace/run10`; `MERGEONE_HTTP_TIMEOUT=900` |
| `scripts/run10_warm.py` | 12 val rows spread by state length, the eight v4 questions, one request at a time, no client timeout → `results/run10/d3/warm.json` |
| `python -m mergeone.eval.run10_report` | `results/run10/tables.md` + `metrics.json` from results only: per row and per v4 question the test AUC with a 1,000-resample bootstrap CI, R10-H1…H4 with the §R10.3 bars quoted, inputs with sha256. Runs on partial or empty `results/run10` (missing parts listed as "not run yet") |

Rows (inputs as they are; nothing is built):

| row | model | parts → `results/run10/<row>/` |
|---|---|---|
| control | `pplx-flash` (hosted) | v4 on the 705 test PRs; relation2 on the 100 pairs |
| full | `pplx` (hosted) | v4 test; relation2 |
| haiku | `haiku` (Anthropic) | relation2 only (`prompts/haiku_relation2.md`) |
| d3 | `d3-flash-local` (pod) | v4 test (timed); v4 **val** (1,491, calibration-map input, not a bar); relation2 |

v4 = `prompts/jev_v4.json` on `data/run3/dataset.jsonl`; relation2 = `prompts/jev_relation2.json` on
`results/run8/relation_pairs.jsonl` (the 100 adjudicated run-6B hold pairs). v4 and relation2 rows share
`predictions_<model>_test_all.jsonl`, kept apart by `qset`; meta files are `meta_<model>_<split>_all_<qset>.json`.

```
results/run10/
  tables.md  metrics.json  README.md (by hand)  logs/ (gitignored: snapshot.json, pip_freeze.txt, server log)
  control/  predictions_pplx-flash_test_all.jsonl  meta_*.json
  full/     predictions_pplx_test_all.jsonl         meta_*.json
  haiku/    predictions_haiku_test_all.jsonl        meta_*.json
  d3/       serve_meta.json  warm.json  server_before.json  server_after.json  throughput.json
            predictions_d3-flash-local_test_all.jsonl  predictions_d3-flash-local_val_all.jsonl  meta_*.json  [passes/]
```

## 2. Pod — **GPU** (one 48 GB A40 / L40S, RunPod)

Copy over `data/run3/dataset.jsonl` (gitignored) and start the sequence in one sitting; the Mac pulls
`/workspace/run10` every five minutes while the pod is up (`rsync -a -e "ssh -p <port>" root@<ip>:/workspace/run10/ results/run10/`).

```bash
bash scripts/run10_gpu.sh sequence --dry-run       # print every command first
USD_PER_HOUR=<pod $/h> VM_TYPE="runpod A40 <secure|community> <dc>" bash scripts/run10_gpu.sh sequence 2>&1 | tee -a /workspace/run10_gpu.out
```

| stage | what |
|---|---|
| setup | `uv venv --seed --python 3.12 .venv`; `pip install -e .`; `transformers==5.17.0 torch torchvision pillow opencv-python-headless safetensors accelerate flash-linear-attention`; `snapshot_download("vllm-sr/d3-flash")` under `HF_HOME=/workspace/hf`; the snapshot's `requirements.txt`; revision → `logs/snapshot.json`, `pip freeze` → `logs/pip_freeze.txt`. Warns if `requirements.txt` moved `transformers` off 5.17.0 or `fla` does not import (CHANGELOG before the first call) |
| serve | `<snapshot>/d3_server.py --model <snapshot> --host 127.0.0.1 --port 8000 --batch-size 8`; waits for `/health` = ready; `d3/serve_meta.json` (revision, `/health`, `/v1/models`, GPU, `USD_PER_HOUR`); warm pass |
| d3 | **timed** v4 test at concurrency `D3_CONCURRENCY` (32, §R10.3) → `throughput.json` (`run9_step throughput`: error-free PRs ÷ the pass's wall seconds); v4 val; relation2 (both resumable). A finished timed pass is kept (`FORCE=1` repeats it); an interrupted one is set aside and re-run whole |
| stop | stop the server, wait for the GPU to be released |
| report | `python -m mergeone.eval.run10_report` |

Manual equivalents of the `d3` stage:

```bash
export MERGEONE_D3_ENDPOINT=http://127.0.0.1:8000/v1/systemone MERGEONE_HTTP_TIMEOUT=900
python -m mergeone.eval.run10_rows run d3 --part v4_test --concurrency 32 --i-have-frozen-the-protocol     # timed by the runbook
python -m mergeone.eval.run10_rows run d3 --part v4_val --i-have-frozen-the-protocol
python -m mergeone.eval.run10_rows run d3 --part relation2 --i-have-frozen-the-protocol
```

## 3. Hosted rows — **key**, on the Mac, the same UTC day as the d3 rows

Keys from `.env` (`PERPLEXITY_API_KEY`, `ANTHROPIC_API_KEY`), never echoed. Control first:

```bash
set -a; source .env; set +a
python -m mergeone.eval.run10_rows run control --dry-run                         # counts only
python -m mergeone.eval.run10_rows run control --i-have-frozen-the-protocol      # ≈ $0.15
python -m mergeone.eval.run10_rows run full --i-have-frozen-the-protocol         # ≈ $0.3
python -m mergeone.eval.run10_rows run haiku --i-have-frozen-the-protocol        # ≈ $1
```

Each command is resumable: re-run it after any failure; only missing or errored rows are asked again. If the hosted rows
cross UTC midnight, the report lists both dates and says so; nothing is re-run.

## 4. Pod down, report — no key

```bash
rsync -a -e "ssh -p <port>" root@<ip>:/workspace/run10/ results/run10/    # last pull; then terminate the pod and verify it is gone
python -m mergeone.eval.run10_report                                      # → results/run10/tables.md, metrics.json
```

## How the report scores (fixed in `mergeone/eval/run10_report.py` before any run-10 number)

- AUC per v4 question vs `iterated` on the test PRs a row answered; CI = bootstrap over PRs, 1,000 resamples, seed 7.
- R10-H2 (full, d3): Δ = AUC(needs_human_review) − control's, each on its own answered PRs, and the paired bootstrap of
  the difference over the PRs both answered; pass = Δ ≥ +0.03 and the paired CI's lower bound > 0.
- R10-H1 (full, haiku, d3): `run8_relation.score` (run 8's rule, HIGH 0.5 / LOW 0.25, metadata `same_base`);
  competing + same_area accuracy (17 pairs) ≥ 0.40 and class accuracy ≥ control's same-day class accuracy − 0.02.
- R10-H3: per-PR cost = main call + ten pair calls. Control and full: `c_main × (1 + 10 × t_pair / t_main)` (mean
  `cost_usd` of the v4 answers; median input tokens of the relation2 / v4 answers). Haiku: `c_main(control) + 10 ×
  c_pair(haiku)`. d3: `(USD_PER_HOUR ÷ 3600 ÷ PR/s) × (1 + 10 × t_pair / t_main)`. p50 latency of the v4 answers. A row
  that passes H1 or H2 ships only if its cost ≤ 5× the control's; "n/a" when no row passes.
- R10-H4 (d3): AUC ≥ control − 0.02 on ≥ 6 of 8 questions and ≥ 1.0 PR/s; agreement and mean |Δp| reported; the
  calibration map (isotonic on val vs `iterated`) reported as test ECE raw vs mapped, no bar.

## What could not be checked before the pod

- That `d3_server.py` and `requirements.txt` sit at the snapshot root (the prep check ran the package's server; the
  setup stage fails loudly if `d3_server.py` is not there).
- Whether the snapshot's `requirements.txt` keeps `transformers==5.17.0` (setup warns).
- The served name with `model: d3-flash` in the request (the prep check sent `vllm-sr/d3-flash` and got `d3-flash`
  back; the adapter records whatever the reply says).
- Throughput with `flash-linear-attention` and `--batch-size 8` (prep: 3.6 s per request without the kernel ≈ 0.28 PR/s).

## Time and cost (estimate)

| step | requests | ≈ minutes on an A40 |
|---|---|---|
| setup (venv, torch, 17 GB weights) | — | 15 |
| server start + warm | 12 | 5 |
| v4 test (timed) | 705 | 15–45 |
| v4 val | 1,491 | 30–90 |
| relation2 | 100 | 3 |
| total | | ≈ 1.1–2.6 h ≈ $0.7–1.6 at $0.59/h |
