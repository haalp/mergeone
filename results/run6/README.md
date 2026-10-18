# Run 6 — self-hosted open weights (part A) · wider candidate pool and hold precision (part B)

Part A is complete; part B's data stage is done and its 100-PR blind labels are being written (see the
end of this file). Protocol: PROTOCOL.md R6A / R6B. Tables: [`tables.md`](tables.md); numbers:
[`metrics.json`](metrics.json), [`serve_meta.json`](serve_meta.json), `loadtest_pplx-flash-local_x*.json`.

## Part A — pplx-decider-flash on one rented L4

**Headline: the weights are not the limit, one GPU is.** The same 4B open-weight model that lost 71% of
calls to Perplexity's per-organisation rate limit in run 5 was served by us on a single NVIDIA L4
(RunPod, EU-RO-1, $0.59/h) with the vendor's own inference code. It answers like the hosted endpoint
(argmax agreement **95.2%**) but not identically (mean |Δp| **0.047**, bar 0.02), scores within a CI-width
of the hosted row on quality (AUC **0.747** vs 0.758; pair task 0.840 vs 0.842), gains **nothing** from
one-question-per-request (the model card's advice: +0.000 AUC for 10× the calls), and saturates at
**~2.2 requests/s** — about 6× the 30k-decisions/day base rate and far below the 100× bar. Three of four
pre-registered hypotheses fail; the fourth is reported, not compared, because H1 failed. Cost of the
measurement: 2 h 26 m of GPU, **$1.44**.

### What was served

`perplexity-ai/pplx-decider-flash-v1.2` at revision `6d771ad7…`, BF16, `pplx_decider` 0.3.0 with the fast
(flash-linear-attention) path, torch 2.14.0+cu130, transformers 5.17.0; `serve/systemone_server.py` in front
of it (micro-batch ≤ 32 requests / 20 ms, no prompt, question or temperature change). The gate benchmark
client ran on the same pod (loopback). Registry row `pplx-flash-local`, price 0; compute reported below.

### R6A-H1 equivalence ❌ (0.952 ✅ / 0.047 ❌)

705 run-5 test PRs × 10 questions, joint request, against the hosted `pplx-flash` answers from run 5.

| | agreement (bar ≥ 0.95) | mean \|Δp\| (bar ≤ 0.02) | p99 \|Δp\| |
|---|---|---|---|
| all 7,050 answers | **0.952** | **0.047** | 0.26 |

The self-hosted model leans toward *no* on every question but `hard_to_revert`: `needs_human_review`
yes-rate 0.563 local vs 0.611 hosted, `breaking_change` 0.166 vs 0.214. Per-question Pearson is 0.97–0.99
for eight questions and 0.82–0.85 for `message_matches_diff`, `hard_to_revert`, `follows_conventions` —
the three run 5 already flagged as inverted or weakest. We did not look for the cause (R6A.4 forbids tuning
on this set); the candidates are the hosted endpoint serving a later revision of the same version string,
or a numerical difference between its serving stack and the public `pplx_decider` package. Under the
protocol the row is **a different model** and nothing below is a comparison with the hosted row.

### R6A-H3 quality (reported, not compared)

| | self-hosted | hosted (run 5 / run 4) |
|---|---|---|
| calibrated `needs_human_review` → iterated, test AUC | **0.747** [0.667, 0.806] (raw 0.770) | 0.758 |
| `same_change` pair AUC, run-4 test pairs (187, 29 positive) | **0.840** [0.753, 0.903] | 0.842 |

On the pair task the two agree on 98.9% of argmaxes with mean |Δp| 0.011 — the equivalence gap is on the
long v3 gate prompt, not on the short pair prompt. ECE after isotonic refit on the self-hosted val
answers: 0.052.

### R6A-H4 joint vs single ❌ (joint stays)

| mode | nhr → iterated AUC (raw) | p50 / p99 ms per PR | requests per PR |
|---|---|---|---|
| joint (10 questions, one request) | 0.770 [0.693, 0.839] | 7,384 / 26,025 | 1 |
| single (one question per request) | 0.770 [0.698, 0.834] | 58,698 / 117,545 | 10 |

Gain +0.000 against a +0.02 bar. Per question, single is better on `hard_to_revert` (0.76 vs 0.71) and worse
on `message_matches_diff` (0.32 vs 0.51) and `follows_conventions` (0.28 vs 0.64) — the two questions run 5
found inverted get *more* inverted when asked alone. The model card's one-question-per-request advice does
not hold for this task; the gateway keeps the joint request. (The per-PR latencies above are from the
prediction run at client concurrency 16 against an already-saturated server; the unloaded latency is the ×1
row below.)

### R6A-H2 scale ❌ — saturation at ×10

| multiplier | rate /s | p50 ms | p99 ms | errors |
|---|---|---|---|---|
| ×1 | 0.35 | 708 | **1,462** | 0 |
| ×10 | 3.47 | 22,060 | 29,897 | 0.715 |
| ×30 | 10.4 | — | — | 1.000 |
| ×100 | 34.7 | — | — | 1.000 |
| ×300 | 104 | — | — | 1.000 |

Every error is a client-side 30 s timeout; the server logged none. Throughput from the prediction runs is
**≈ 2.2 requests/s** (7,050 single-question calls in ~50 min), so one L4 carries ~6× base rate and ×10 queues
without bound. Hosted pplx-flash held p99 at 462–561 ms through ×10 and failed ×100 on HTTP 429 only; the
self-hosted ×1 p99 (1,462 ms) is 2.6× the hosted one, which is a 4B BF16 forward pass on an L4 versus
whatever Perplexity runs. The ×30–×300 steps started with the ×10 backlog still queued (the 60 s gap did not
drain it); re-running the curve is allowed by R6A.4 and was not done, because ×10 alone is past capacity.

### Cost

| row | $/1k PRs | basis |
|---|---|---|
| self-hosted, one L4 | **0.472** | $0.59/h ÷ 1,250 PRs/h (×1, the highest 0-error multiplier) |
| hosted pplx-flash, list price | 0.023 | run-5 tokens × $0.01/M |
| hosted Jev, list price | 0.103 | run 5 |

At the one rate that passed, self-hosting costs 20× the hosted list price per PR. The utilisation-adjusted
number (2.2 rps ≈ 7,900 PRs/h → $0.075/1k) would need the queue to be sized for it, i.e. a load balancer and
a second GPU before the 100× bar is in reach. The honest reading: the open weights remove the vendor's rate
limit and replace it with a capacity-planning problem, and a single 24 GB card is below the gateway's
stated scale target by an order of magnitude.

### Scorecard and product consequence

R6A-H1 ❌ · H2 ❌ · H3 reported (0.747, not compared) · H4 ❌ (joint stays). Rows in
[`results/leaderboard.md`](../leaderboard.md): `pplx-flash-local` ranks below the hosted row on every axis
but calibration. The registry keeps the row; the default for self-hosted deployments stays the joint request;
`docs/run6-gpu.md` records what a pod needs. The next measurement that would change a decision is the same
server on an A100/H100 or two L4s behind a balancer — a VM measurement, not a protocol change.

### Housekeeping

- Pod `b9nme44vl2gh5i`, 16:20Z → 18:46Z on 2026-10-18, terminated (verified 404 on `get-pod`). Setup from
  the network-mounted `/workspace` took 27 min; warm-up 80 s. No HF token (public repo).
- The first H1 pass lost 32 requests to 30 s client timeouts during warm-up; only that stage was re-run
  (`STAGES=h1`), filling exactly those 32 with 0 errors. Val 1,491/1,491, single 705/705, pairs 187/187, 0 errors.
- `mergeone leaderboard` crashed on the ×100 row (no p99 when every request fails); fixed in
  `mergeone/eval/leaderboard.py` after the run, reporting only.
- `serve_meta.json` has `region: null`; the data centre is in the `vm_type` string.
- Logs are on the Mac (`results/run6/logs/`, gitignored); result files were checksum-matched pod → Mac → repo.

## Part B — pool k ∈ {5, 10, 20} and hold precision (in progress)

Data stage done 2026-10-18 (CHANGELOG): test recall@5/10/20 = 0.655 / 0.862 / 0.862 (R6B-H1 bar 0.80 ✅ at
k=20 and at k=10); 42,984 retrieved pairs scored by Jev and pplx-flash (0 errors), Haiku continuity-only.
Blind sample of 100 pplx-flash k=20 holds (strata superseded 20 / closed-unmarked 38 / merged 42) committed
before any label (`38fc829`). Labels: the human labeller's `data/run6/hold_labels.jsonl` is the
pre-registered number; a second, independent blind labelling by Fable 5.1 is in
`data/run6/hold_labels_fable.jsonl` (68 duplicate / 30 pointer / 2 unrelated) and will be reported as
agreement (Cohen κ), as R6B.2-B2 allows. Precision, H2–H4 and the threshold sweep follow once the human
file is complete.
