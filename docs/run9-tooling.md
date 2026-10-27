# Run 9 tooling

Commands for run 9 (PROTOCOL.md §R9), in order. Nothing below calls a model unless marked **GPU** (the local server on
the pod) or **key** (Perplexity). Run everything from the repository root. The harness is run 6A's
(`serve/systemone_server.py`, `scripts/run6_gpu.sh`, `docs/run6-gpu.md` for the pod); what run 9 adds is listed in §1
and marked **to write** where it does not exist at freeze.

**Rule: every treatment has a same-day vendor-BF16 control on the same pod** (the run-7/8 rule). Control and treatment
rows go to separate directories under `results/run9/`; run-6A numbers are shown beside the control, never instead of it.

## 0. Freeze

The freeze commit holds §R9 in `PROTOCOL.md`, `prompts/jev_pair_shared.json`, this file, and the CHANGELOG entry
(including the dated R6A.4 FP8 exception). Tag it before any run-9 call (`git tag r9-frozen <commit>`; CLAUDE.md: no
test-split call before the freeze commit and tag).

## 1. What exists, what is still to write (tooling commit, after freeze, before the pod; no model call)

| piece | status at freeze | what it must do |
|---|---|---|
| server: vendor `FastOptions` | **to write** — `serve/systemone_server.py` has no such flag; `load_decider` calls `Decider.from_pretrained(ckpt, device=…, fast=True)` only | new flag `--fast-options '<json>'`, parsed into `pplx_decider.FastOptions(**json)` and handed to the decider; absent = today's behaviour. `serve_meta.json` must record the options and stop hard-coding `"dtype": "bf16 (vendor default; no quantisation)"` when `fp8` is set |
| server: FP8 | **flag** `--fast-options '{"fp8": true}'` (vendor `FastOptions(fp8=True)`, research note §4) | nothing else; same `--fast`, `--batch-max 32`, `--batch-ms 20`, `--batch-tokens 131072` |
| server: bucket row (H2 fallback) | **flag** `--fast-options '{"buckets": [2560, 3072, 4096, 8192, 16384, 32768]}'` | only if H3 fails |
| server: shared-A request | **no server option** — the server already passes any number of `choice` questions through unchanged (`to_vendor_request`), and the vendor encoder answers each named question in one sequence | — |
| client: shared-A request | **to write** — `mergeone predict --qset pair_shared` | build one request per group from `prompts/jev_pair_shared.json` (state template, `same_change_<i>` questions); map each answer to `duplicate_of = P(same) + 0.5 P(subset)`; write one row per (A, candidate) with `mode: "shared"`, `group_id`, `group_size`, `position`, and the request's `usage` on the group (per-candidate tokens = request tokens / k) |
| pair groups | **to write** — `python -m mergeone.eval.run9_pairs build` | set (a): `results/run6/pairs_eval_test.jsonl` ids grouped by A in `results/run4/pairs.jsonl` order, ≤ 10 per group; set (b): the 100 `data/run6/hold_labels_adjudicated.jsonl` A's with their k = 10 run-6B candidates (B replaces the 10th if absent) → `results/run9/pair_groups_{a,b}.jsonl` and the per-candidate rows `results/run9/pairs_b.jsonl` (run-4 renderer) |
| throughput timing | **to write** — in `scripts/run9_gpu.sh` | wrap each timed `predict` pass: wall-clock start/end, `/healthz` before/after → `results/run9/<arm>/throughput_<set>.json` `{n_items, wall_s, items_per_s, concurrency, decide_s}` (items = requests for the equivalence set, candidates for set (b)) |
| runbook | **to write** — `scripts/run9_gpu.sh`, from `scripts/run6_gpu.sh` (same `setup`, `serve`, `stop` stages; `PASS` → `ARM` directory) | stages below, resumable |
| report | **to write before scoring** — `mergeone.eval.run9_report`, derived from `run8_report`; reuses `run6`'s H1 equivalence code and run 6A's load-curve reader | R9-H2…H5 per §R9.3; "not run yet" for a missing input |
| load test with pair calls (H2, no bar) | **unknown / probably not written** — `mergeone loadtest --qset` accepts `v1`–`v4` only | skipped and reported "not run" unless the loader gains a mixed v3 + pair mode before the pod |

Unknowns, to settle on the pod before the first scored call and record in CHANGELOG (none of them moves a bar):

- **How `FastOptions` reaches the decider.** The research note read `FastOptions()` and its fields (`compile`, CUDA
  graphs, `buckets`, `prune_last`, `fp8`) from `pplx_decider/fast.py` 0.3.0, but not whether `Decider.from_pretrained`
  takes `fast_options=` or another argument. Read the installed `fast.py` / `model.py` on the pod; the flag is wired to
  whatever it is.
- **Whether `fp8=True` runs on an L4.** Ada (compute capability 8.9) has FP8 tensor cores and `torch._scaled_mm`
  supports it, but the vendor measured FP8 only on a GB200. If it fails to load or run, H3 is "not run (unavailable)".
- **Whether the pinned revision `6d771ad7…` is still downloadable.** If not, the resolved revision is recorded (§R9.2).
- **Bucket list accepted by `FastOptions`.** If `buckets` rejects 2,560 / 3,072, the bucket row is "not run" and H2
  uses the control if H3 fails.

## 2. Pod — **GPU**

`docs/run6-gpu.md` §1b (RunPod, 1 × L4, PyTorch/CUDA 12.x, ≥ 60 GB, install on the container's local disk, not the
network volume: 4 min vs 27 min in run 6A). Copy over `data/run3/dataset.jsonl`, `data/run3/manifest.json`,
`results/run4/pairs.jsonl` (gitignored) and the run-6B candidate list `results/run6/candidates_k20.jsonl` (gitignored;
rebuild with `python -m mergeone.eval.run6b retrieve --k 20` if absent).

```bash
USD_PER_HOUR=<pod $/h> VM_TYPE="runpod L4 <secure|community> <dc>" STAGES="setup" bash scripts/run9_gpu.sh
python -m mergeone.eval.run9_pairs build          # no model call
```

## 3. BF16 control — **GPU**, `results/run9/bf16/`

```bash
ARM=bf16 STAGES="serve equiv pairs_a pairs_b" bash scripts/run9_gpu.sh
```

Manual equivalents (server: `--fast --batch-tokens 131072`, no `--fast-options`):

```bash
python serve/systemone_server.py --checkpoint-dir "$CK" --port 8080 --fast --batch-tokens 131072 \
  --usd-per-hour "$USD_PER_HOUR" --vm-type "$VM_TYPE" --meta-out results/run9/bf16/serve_meta.json &
export MERGEONE_LOCAL_ENDPOINT=http://127.0.0.1:8080/v1/systemone
# equivalence set, timed (H3 throughput denominator)
mergeone predict --model pplx-flash-local --qset v3 --split test --dataset data/run3/dataset.jsonl --run run9/bf16 --concurrency 32
# H4 per-candidate arm: set (a) untimed, set (b) timed
mergeone predict --model pplx-flash-local --qset pair --split test --dataset results/run4/pairs.jsonl \
  --subset results/run6/pairs_eval_test.jsonl --run run9/bf16 --concurrency 16
mergeone predict --model pplx-flash-local --qset pair --split test --dataset results/run9/pairs_b.jsonl --run run9/bf16 --concurrency 32
```

## 4. FP8 — **GPU**, `results/run9/fp8/` (R9-H3)

```bash
ARM=fp8 FAST_OPTIONS='{"fp8": true}' STAGES="stop serve equiv val" bash scripts/run9_gpu.sh
```

Server as §3 plus `--fast-options '{"fp8": true}' --meta-out results/run9/fp8/serve_meta.json`; the equivalence pass
at concurrency 32 (timed), then joint v3 on val (`--split val`, concurrency 16, untimed) for the FP8 isotonic refit.

## 5. Shared-A pair call — **GPU**, `results/run9/shared/` (R9-H4)

BF16 server (restart without `--fast-options`):

```bash
ARM=shared STAGES="stop serve shared_a shared_b" bash scripts/run9_gpu.sh
# = mergeone predict --model pplx-flash-local --qset pair_shared --split test --dataset results/run9/pair_groups_a.jsonl --run run9/shared --concurrency 16
#   mergeone predict --model pplx-flash-local --qset pair_shared --split test --dataset results/run9/pair_groups_b.jsonl --run run9/shared --concurrency 32   (timed)
```

## 6. Load curves — **GPU** (R9-H2)

×1 / ×10 / ×30 only, 600 decisions per step, 512 workers, no retry, 60 s gaps (`LT_MULTS="1 10 30"`).

```bash
ARM=bf16 STAGES="serve load" bash scripts/run9_gpu.sh                                   # control curve (BF16 server)
# then the H2 configuration:
ARM=fp8 FAST_OPTIONS='{"fp8": true}' STAGES="stop serve load" bash scripts/run9_gpu.sh  # if H3 passed both bars
# if H3 failed: bucket equivalence first, then its curve only if it meets 0.95 / 0.02 against bf16
ARM=buckets FAST_OPTIONS='{"buckets": [2560, 3072, 4096, 8192, 16384, 32768]}' STAGES="stop serve equiv" bash scripts/run9_gpu.sh
ARM=buckets FAST_OPTIONS='{"buckets": [2560, 3072, 4096, 8192, 16384, 32768]}' STAGES="load" bash scripts/run9_gpu.sh
```

Each step = `MERGEONE_NO_RETRY=1 mergeone loadtest --model pplx-flash-local --multiplier <x> --limit 600 --run run9/<arm>
--dataset data/run3/dataset.jsonl --qset v3 --no-retry --concurrency 512`. Deciding H3 between §4 and §6 needs the
equivalence and throughput numbers: `python -m mergeone.eval.run9_report` reads them (it scores H3 only; the choice it
prints is the §R9.3 rule, applied mechanically).

## 7. R9-H5 — **GPU** + **key**, `results/run9/h5/`

Same UTC day as the hosted call. Local single-question on the BF16 server (7,050 requests, ≈ 50 min on an L4):

```bash
ARM=bf16 STAGES="stop serve single" bash scripts/run9_gpu.sh
# = mergeone predict --model pplx-flash-local --qset v3 --split test --dataset data/run3/dataset.jsonl --run run9/h5 --single-question --concurrency 16
```

Hosted, from any machine with the key (`set -a; source .env; set +a`, never echoed):

```bash
mergeone predict --model pplx-flash --qset v3 --split test --dataset data/run3/dataset.jsonl --run run9/h5    # ≈ $0.12–0.2
```

The token ratio's denominator is the same-day local joint tokens from `results/run9/bf16/`.

## 8. Pod down, report — no key

```bash
STAGES="stop" bash scripts/run9_gpu.sh     # then terminate the pod; verify it is gone
python -m mergeone.eval.run9_report        # → results/run9/tables.md, results/run9/metrics.json (inputs with sha256)
```

## Time and cost (estimate, one L4)

| step | requests | ≈ minutes |
|---|---|---|
| setup + warm-ups (4 server starts) | — | 20 |
| BF16 equivalence + pairs (a), (b) | 705 + 187 + 1,000 | 15 |
| FP8 equivalence + val | 705 + 1,491 | 15 |
| shared (a), (b) | ≈ 40 + 100 | 3 |
| two load curves ×1 / ×10 / ×30 | 2 × 1,800 | 70 |
| local single-question (H5) | 7,050 | 50 |
| total | | ≈ 2.9 h ≈ $1.7–2.3 at $0.59–0.80/h |

The bucket row (if H3 fails) adds ≈ 40 min; the optional pair-inclusive curve is skipped first if the pod time runs
over. Overruns are reported, bars unchanged.

## What needs a key or a GPU

| step | needs |
|---|---|
| pod, server, every `pplx-flash-local` call | one L4 (RunPod); `HF_TOKEN` optional (public repo) |
| hosted H5 call | `PERPLEXITY_API_KEY` |
| `pytest`, `run9_pairs build`, `run9_report`, `run6b retrieve` rebuild | nothing (PR memory rebuilt from GitHub with `GITHUB_TOKEN` if absent) |
