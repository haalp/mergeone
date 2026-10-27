# Run 9 tooling

Commands for run 9 (PROTOCOL.md §R9), in order. Nothing below calls a model unless marked **GPU** (the local server on
the pod) or **key** (Perplexity). Run everything from the repository root. The harness is run 6A's
(`serve/systemone_server.py`, `scripts/run6_gpu.sh`, `docs/run6-gpu.md` for the pod); what run 9 adds is listed in §1
and marked **written** with the tooling commit.

**Rule: every treatment has a same-day vendor-BF16 control on the same pod** (the run-7/8 rule). Control and treatment
rows go to separate directories under `results/run9/`; run-6A numbers are shown beside the control, never instead of it.

## 0. Freeze

The freeze commit holds §R9 in `PROTOCOL.md`, `prompts/jev_pair_shared.json`, this file, and the CHANGELOG entry
(including the dated R6A.4 FP8 exception). Tag it before any run-9 call (`git tag r9-frozen <commit>`; CLAUDE.md: no
test-split call before the freeze commit and tag).

## 1. What exists (tooling commit, after freeze, before the pod; no model call) — **written**

| piece | status | what it does |
|---|---|---|
| server: vendor `FastOptions` | **written** — `serve/systemone_server.py --fast-options` | one JSON object (`'{"fp8": true}'`) or a `key=value` list (`fp8=true buckets=2560,3072,…`), checked against the 0.3.0 fields (`compile`, `cuda_graphs`, `buckets`, `token_budget`, `prune_last`, `fp8`, `prefetch`), built into the installed `pplx_decider.FastOptions(**options)` and passed as `Decider.from_pretrained(ckpt, device=…, fast=<FastOptions>)`. Absent = `fast=True` = `FastOptions()`, the run-6A call unchanged. The options **read back from the loaded decider** (`decider.fast.options`) are in `serve_meta.json` (`fast_options_flag`, `fast_options_requested`, `fast_options_effective`, `dtype` = fp8 when set) and at `GET /meta`; `/healthz` carries them too. A vendor package without `FastOptions` or without a named field fails at start with the documented signature in the message |
| server: FP8 / bucket row | flag values, fixed per arm in `scripts/run9_gpu.sh` (`FAST_OPTIONS` cannot override them) | fp8: `'{"fp8": true}'`; buckets (only if H3 fails): `'{"buckets": [2560, 3072, 4096, 8192, 16384, 32768]}'`; every row `--fast --batch-max 32 --batch-ms 20 --batch-tokens 131072` |
| server: shared-A request | no server option (unchanged) | `to_vendor_request` passes any number of `choice` questions through |
| client: shared-A request | **written** — `mergeone predict --qset pair_shared` (`mergeone/eval/run9_pairs.py`) | one request per group from `prompts/jev_pair_shared.json` (A once ≤ 6,000 chars, B1…Bk ≤ 3,000 each, `same_change_1…k`); k rows shaped like the per-candidate `pair` rows (same `pr_number` = sha1 of `repo#A~B`, same `labels`) with `qset: "pair_shared"`, `mode: "shared"`, `group_id`, `group_size`, `slot` (1-based); `probs.same_change` = P(same) + 0.5·P(subset); `input_tokens` = request tokens / k, the request's `usage` under `raw.usage_group`. Resumable (`--only-missing-from`: a group is redone unless all its rows are error-free) |
| pair groups | **written** — `python -m mergeone.eval.run9_pairs groups` (alias `build`) | set (a): the 187 ids of `results/run6/pairs_eval_test.jsonl` from `results/run4/pairs.jsonl` in file order, grouped by A, chunks of ≤ 10; A and B text are split back out of the run-4 pair state so both arms see the same characters. Set (b): the 100 adjudicated holds, first 10 of `results/run6/candidates_k20.jsonl` in retrieval order, the adjudicated B replacing the 10th when absent (from ranks 11–20, else the PR memory). → `results/run9/groups_a.jsonl`, `groups_b.jsonl`, `pairs_b.jsonl` (per-candidate rows, run-4 renderer; gitignored, rebuildable) and `groups_summary.json` (group sizes, forced B's) |
| H4 scoring | **written** — `python -m mergeone.eval.run9_pairs score --i-have-frozen-the-protocol` | refuses without the flag; set-(a) AUC, set-(b) duplicate-vs-rest AUC, agreement / mean \|Δp\|, candidates/s ratio → `results/run9/pairs_metrics.json`. The report uses the same functions |
| throughput timing | **written** — `python -m mergeone.eval.run9_step` (called by the runbook) | `snapshot`: `GET /meta` + `/healthz` → `<step>/server_before.json` / `server_after.json`. `throughput`: error-free items ÷ the pass's own wall seconds (`wall_s`, now in every `predict` meta file; the outer process wall is kept beside it) → `<step>/throughput.json` `{n_items, n_requests, errors, wall_s, items_per_s, requests_per_s, concurrency, decide_s, server}`; items = requests for the equivalence set, candidates for set (b) |
| load test | **written** — `mergeone loadtest` summary gains `n_errors`, `n_ok`, `errors_by_kind` and `server_meta` (the server's `/meta`: fast options, dtype) per step; p99 and `error_rate` as run 6A read them | ×1 / ×10 / ×30, 600 decisions, 512 workers (§R9.2), no retry, 60 s gaps |
| load test with pair calls (H2, no bar) | **not written** — `--pair-mix` is not trivial: the replayed v3 stream is the 600 run-3 test PRs, while the set-(b) candidates belong to the 100 run-6B hold A's, so a v3 + pair mix needs per-candidate rows for every replayed PR that do not exist | reported "not run" (§R9.3 allows it) |
| runbook | **written** — `scripts/run9_gpu.sh` (`sequence` = the whole pod order; `STAGES=…` with `ARM=…` for single stages; `--dry-run` prints every command) | stages below; idempotent (a timed step or load step that finished is kept; `FORCE=1` repeats it and keeps the earlier pass under `<step>/passes/<UTC>/`; an interrupted timed pass is moved aside and re-run whole; untimed steps resume) |
| report | **written** — `python -m mergeone.eval.run9_report`, from `run8_report` | rows-served table (per arm: revision, GPU, dtype, fast options in effect vs the arm's expected options, per-step `/meta` check), R9-H3 (equivalence tables as run 6A's: per question agreement / mean, max \|Δp\|; all-answers agreement, mean and p99 \|Δp\|; throughput ratio), R9-H2 (the §R9.3 configuration rule applied mechanically, load curves with p99 and errors per step for each row, run-6A pass 2 beside, saturation), R9-H4, R9-H5 (equivalence + median per-PR hosted ÷ local-joint token ratio, UTC dates of both sides); "not run yet" for every missing input; inputs with sha256. → `results/run9/tables.md`, `metrics.json` |

Results layout (one directory per server row; each step writes its own directory with `server_before.json`):

```
results/run9/
  groups_summary.json  pairs_metrics.json  tables.md  metrics.json  logs/ (gitignored)
  bf16/     serve_meta.json  equiv/ (+ throughput.json)  pairs_a/  pairs_b/ (+ throughput.json)  load/
  fp8/      serve_meta.json  equiv/ (+ throughput.json)  val/                     [or unavailable.json]
  buckets/  serve_meta.json  equiv/  load/                                       [only if H3 fails]
  shared/   serve_meta.json  pairs_a/  pairs_b/ (+ throughput.json)
  h5/       serve_meta.json  single/  hosted/
```

What the vendor API looks like, and what is still open (read from the Hub copy of the checkpoint's `source/` on
2026-10-28, `pplx_decider` 0.3.0, `main`; `fast.py` has the same size at `6d771ad7`):

- **Settled: how `FastOptions` reaches the decider.** `pplx_decider.FastOptions` is a frozen dataclass in `model.py`
  (`compile="layer"`, `cuda_graphs=True`, `buckets=(2048, 4096, 8192, 16384, 32768)`, `token_budget=32768`,
  `prune_last=True`, `fp8=False`, `prefetch=True`); `Decider.from_pretrained(path, *, device, fast: bool | FastOptions, …)`
  passes `fast` to `Decider.__init__`, which builds `FastPath(self, fast if isinstance(fast, FastOptions) else FastOptions())`
  and keeps the options at `decider.fast.options`. The flag is wired to exactly that. `setup` writes the installed
  package's signature, fields and file hashes to `results/run9/logs/vendor_api.json`; any difference goes in CHANGELOG
  before the first call.
- **Settled: bucket list.** `buckets` is not validated beyond the sorted tuple; the largest bucket is the packing budget
  (32,768 in both lists), so 2,560 / 3,072 are accepted. "Batch tokens" in the vendor sense is `token_budget`, used only
  without CUDA graphs; the server's own `--batch-tokens 131072` (micro-batcher) is a different knob and is fixed.
- **Open: whether `fp8=True` runs on an L4.** `FP8Linear` calls `torch._scaled_mm` with a per-token activation scale and a
  per-channel weight scale (rowwise). Rowwise-scaled `_scaled_mm` is known on sm_90 (H100/GB200); whether torch 2.14 runs
  it on sm_89 cannot be checked here. Conversion happens in `FastPath.__init__` and the first matmul in `warmup()`, both
  before the server listens, so a failure kills the server and the runbook writes `results/run9/fp8/unavailable.json`
  (H3 "not run (unavailable on the pod)", §R9.6: no substitute). An FP8 server that starts but answers with errors shows
  up as errors in `fp8/equiv` and fails the equivalence bars.
- **Open: whether the pinned revision `6d771ad7…` is still downloadable** (`setup` warns if the resolved one differs;
  record it in CHANGELOG, §R9.2).
- **Open: `vendor_api.json` vs this reading** (the installed package is what the pod runs).

## 2. Pod — **GPU**

`docs/run6-gpu.md` §1b (RunPod, 1 × L4, PyTorch/CUDA 12.x, ≥ 60 GB, install on the container's local disk, not the
network volume: 4 min vs 27 min in run 6A). Copy over `data/run3/dataset.jsonl`, `data/run3/manifest.json`,
`results/run4/pairs.jsonl` (gitignored) and the run-6B candidate list `results/run6/candidates_k20.jsonl` (gitignored;
rebuild with `python -m mergeone.eval.run6b retrieve --k 20` if absent).

The whole pod part, in §R9.5 order, is one resumable command (re-run it after any failure; finished work is skipped):

```bash
bash scripts/run9_gpu.sh sequence --dry-run       # print every command first
USD_PER_HOUR=<pod $/h> VM_TYPE="runpod L4 <secure|community> <dc>" bash scripts/run9_gpu.sh sequence 2>&1 | tee -a results/run9/logs/run9_gpu.out
```

It runs, in order (each line = `ARM=<arm> STAGES="<stages>" bash scripts/run9_gpu.sh`):

| # | ARM | STAGES | what |
|---|---|---|---|
| 1 | — | `setup groups` | install, checkpoint at `6d771ad7…`, vendor package, `vendor_api.json`; build the pair groups (no model call) |
| 2 | bf16 | `stop serve equiv pairs_a pairs_b` | BF16 control: equivalence set (timed, c 32), set (a) per-candidate (c 16), set (b) per-candidate (timed, c 32) |
| 3 | fp8 | `stop serve equiv val` | R9-H3: equivalence set (timed, c 32), val for the refit (c 16); `unavailable.json` instead if the server does not start |
| 4 | shared | `stop serve shared_a shared_b` | R9-H4 shared-A arm, BF16: set (a) (c 16), set (b) (timed, c 32) |
| 5 | bf16 | `stop serve load` | control load curve ×1 / ×10 / ×30 |
| 6 | — | (`run9_report`) | applies the §R9.3 rule → fp8, or (H3 failed / unavailable) the bucket row first: |
| 6b | buckets | `stop serve equiv` | only if H3 did not pass; then the report again |
| 7 | fp8 or buckets | `stop serve load` | the H2 configuration's curve (skipped when the rule picks the control) |
| 8 | h5 | `stop serve single stop` | R9-H5 local single-question (7,050 requests, c 16) |
| 9 | — | `report` | `python -m mergeone.eval.run9_report` |

Then, the same UTC day, the hosted half of R9-H5 (§7), pod down (§8).

## 3. BF16 control — **GPU**, `results/run9/bf16/`

```bash
ARM=bf16 STAGES="serve equiv pairs_a pairs_b" bash scripts/run9_gpu.sh
```

Manual equivalents (the runbook adds `server_before/after.json` and `throughput.json` around the timed passes):

```bash
python serve/systemone_server.py --checkpoint-dir "$CK" --port 8080 --fast --batch-max 32 --batch-ms 20 --batch-tokens 131072 \
  --usd-per-hour "$USD_PER_HOUR" --vm-type "$VM_TYPE" --meta-out results/run9/bf16/serve_meta.json &
export MERGEONE_LOCAL_ENDPOINT=http://127.0.0.1:8080/v1/systemone
mergeone predict --model pplx-flash-local --qset v3 --split test --dataset data/run3/dataset.jsonl --run run9/bf16/equiv --concurrency 32     # timed
mergeone predict --model pplx-flash-local --qset pair --split test --dataset results/run4/pairs.jsonl \
  --subset results/run6/pairs_eval_test.jsonl --run run9/bf16/pairs_a --concurrency 16
mergeone predict --model pplx-flash-local --qset pair --split test --dataset results/run9/pairs_b.jsonl --run run9/bf16/pairs_b --concurrency 32  # timed
```

## 4. FP8 — **GPU**, `results/run9/fp8/` (R9-H3)

```bash
ARM=fp8 STAGES="stop serve equiv val" bash scripts/run9_gpu.sh
```

Server as §3 plus `--fast-options '{"fp8": true}' --meta-out results/run9/fp8/serve_meta.json`; equivalence pass at
concurrency 32 (timed) into `run9/fp8/equiv`, then joint v3 on val (`--split val`, concurrency 16, untimed) into `run9/fp8/val`.

## 5. Shared-A pair call — **GPU**, `results/run9/shared/` (R9-H4)

```bash
ARM=shared STAGES="stop serve shared_a shared_b" bash scripts/run9_gpu.sh
# = mergeone predict --model pplx-flash-local --qset pair_shared --split test --dataset results/run9/groups_a.jsonl --run run9/shared/pairs_a --concurrency 16
#   mergeone predict --model pplx-flash-local --qset pair_shared --split test --dataset results/run9/groups_b.jsonl --run run9/shared/pairs_b --concurrency 32   (timed)
```

## 6. Load curves — **GPU** (R9-H2)

×1 / ×10 / ×30 only, 600 decisions per step, 512 workers, no retry, 60 s gaps.

```bash
ARM=bf16 STAGES="stop serve load" bash scripts/run9_gpu.sh            # control curve (BF16 server)
python -m mergeone.eval.run9_report                                   # prints "H2 configuration: …" (the §R9.3 rule)
ARM=fp8 STAGES="stop serve load" bash scripts/run9_gpu.sh             # if H3 passed both bars
# if H3 failed or was unavailable: bucket equivalence first, then its curve only if the report picks it
ARM=buckets STAGES="stop serve equiv" bash scripts/run9_gpu.sh
ARM=buckets STAGES="load" bash scripts/run9_gpu.sh
```

Each step = `MERGEONE_NO_RETRY=1 mergeone loadtest --model pplx-flash-local --multiplier <x> --limit 600 --run run9/<arm>/load
--dataset data/run3/dataset.jsonl --qset v3 --no-retry --concurrency 512`; the summary has `p99_ms`, `n_errors`,
`errors_by_kind` and the server's options (`server_meta`).

## 7. R9-H5 — **GPU** + **key**, `results/run9/h5/`

Same UTC day as the hosted call. Local single-question on a BF16 server (7,050 requests, ≈ 50 min on an L4):

```bash
ARM=h5 STAGES="stop serve single" bash scripts/run9_gpu.sh
# = mergeone predict --model pplx-flash-local --qset v3 --split test --dataset data/run3/dataset.jsonl --run run9/h5/single --single-question --concurrency 16
```

Hosted, from any machine with the key (`set -a; source .env; set +a`, never echoed):

```bash
STAGES=hosted bash scripts/run9_gpu.sh     # ≈ $0.12–0.2
# = mergeone predict --model pplx-flash --qset v3 --split test --dataset data/run3/dataset.jsonl --run run9/h5/hosted
```

The token ratio's denominator is the same-day local joint tokens from `results/run9/bf16/equiv/`.

## 8. Pod down, report — no key

```bash
STAGES=stop bash scripts/run9_gpu.sh       # then terminate the pod; verify it is gone
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
| `pytest`, `run9_pairs groups`, `run9_report`, `run6b retrieve` rebuild | nothing (PR memory rebuilt from GitHub with `GITHUB_TOKEN` if absent) |
