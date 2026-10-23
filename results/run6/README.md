# Run 6 — self-hosted open weights (part A) · wider candidate pool and hold precision (part B)

Both parts are complete. Part A (2026-10-18/19): negative — one GPU moves the ceiling, it does not remove it.
Part B (2026-10-23): **positive on the question that mattered** — of 100 PRs the gate holds as duplicates, 98 were
right to hold (dup ∪ pointer 0.98, bar 0.60) and 44 are the same change (bar 0.30); the wider pool is not the
lever for the verdict-level bar (R6B-H1 ❌), and k = 20 costs $0.0004 per PR. Protocol: PROTOCOL.md R6A / R6B.
Tables: [`tables.md`](tables.md) (A), [`b_tables.md`](b_tables.md) (B); numbers: [`metrics.json`](metrics.json),
[`b_metrics.json`](b_metrics.json), [`serve_meta.json`](serve_meta.json), `loadtest_pplx-flash-local_x*.json`.

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


### Pass 2 — length-aware batching on the same L4 (not the verdict; a VM measurement under R6A.4)

Hypothesis after pass 1: the L4 was spending its time on padding (batches formed in arrival order, padded to
the longest request). `serve/systemone_server.py --batch-tokens N` groups queued requests by length. Measured
on a second L4 pod (`31ayow6p3cx1lb`, EU-RO-1, $0.59/h, 19:02–20:10Z, $0.69), files in `pass2-l4/`:

| probe (200 PRs, closed loop, conc. 32) | req/s | padding efficiency |
|---|---|---|
| budget 0 (pass-1 batching) | 1.54 | 0.64 |
| 16k | 1.47 | **0.91** |
| 32k | 2.15 | 0.79 |
| 64k | 1.79 | 0.71 |
| 128k (chosen) | **2.33** | 0.66 |

Padding efficiency went from 0.64 to 0.91 and throughput did not move; the load curve with the chosen budget
is the pass-1 curve (×1 p99 1,487 ms, 0 errors; ×10 p99 29,817 ms, 89% errors; saturation at ×10; $0.472/1k).
The server's own counters say why: under load a batch of ~30 requests took ~30 s, i.e. **~1 s per request,
the same as one request alone**. The vendor's `decide(list)` gives no throughput over sequential calls on this
card — a 4B BF16 forward pass over a 4–16k-token diff is enough to saturate an L4 by itself (16k tokens ≈
1.3e14 FLOP ≈ 1 s at the card's peak). The bottleneck is compute per request, not scheduling; batching
policy, micro-batch window and padding are not levers here. What would be: a faster card (H100 ≈ 8× the
BF16 throughput; $3.99/h Secure at the time, not run), more cards behind a balancer (cost scales linearly),
or fewer tokens per PR (the 4k-vs-16k diff ablation, a quality question for run 7). The pass-1 numbers stay
the run-6A verdict; pass 2 changes the explanation, not the result.

Setup note for the next pod: installing into the network-mounted `/workspace` took 27 min in pass 1; on the
container's local disk it took 4 min.


### Pass 2 on one H100 80 GB — the only lever is a faster card

Same server, same budget sweep, pod `h56ajj6bdk1pzn` (RunPod Secure AP-IN-1, $3.99/h, 21:17–22:18Z, ≈$4.04).
Probe: 4.05 → 6.67 → **9.68** req/s at budgets 0 / 32k / 128k (chosen 128k; padding efficiency unchanged at
0.63, so again not a padding effect).

| step | rate /s | p50 ms | p99 ms | errors | L4 pass 2 | hosted (run 5) |
|---|---|---|---|---|---|---|
| ×1 | 0.35 | 105 | 1,921 † | 0 | p99 1,487 · 0 | p99 561 · 0 |
| ×10 | 3.47 | 96 | **159** | 0 | p99 29,817 · 89% | p99 462 · 0 |
| ×30 | 10.4 | 95 | **160** | 0 | ≥ 99.8% | — |
| ×100 | 34.7 | 12,205 | 28,878 | 29.7% | ≥ 99.8% | 71% HTTP 429 |
| ×300 | 104 | 13,236 | 29,980 | 41.8% | ≥ 99.8% | — |

† six of 600 requests: three cold-start calls (7.5 / 4.8 / 1.9 s) and one 8 s stall at ~435 s that held the
next two; without the first three the ×1 p99 is 185 ms. The measured number stands.

- **Saturation at ×100** (protocol rule: first p99 > 2 × the ×1 p99); the L4's was ×10. One H100 carries
  **×30 = 10.4 PRs/s with p99 160 ms and 0 errors** — faster than the hosted endpoint at every multiplier it
  survives, and the first self-hosted configuration to beat it on latency.
- **R6A-H2 still ❌:** at ×100 (34.7/s) 29.7% of calls time out. The card's ceiling is 11–15 req/s; the 100×
  bar needs three H100s behind a balancer, or the per-request token cut.
- **Per-request time inside batches is flat again** (65–110 ms alone, 88–98 ms inside a batch of ~29): the
  H100 is faster because each forward pass is ~9× faster, not because batching works. Across both cards the
  vendor's `decide(list)` behaves like sequential calls.
- **Cost:** $3.99/h ÷ (10.4 × 3.6) = **$0.106/1k PRs at ×30**, the highest 0-error multiplier (R6A.3 rule);
  $0.32/1k at ×10, $3.19/1k at ×1. Hosted list price $0.023/1k. Self-hosting is 4.6× the hosted price when the
  box is used at its 0-error ceiling, 20× (L4) to 140× (H100) at the base rate. The crossover does not exist on
  one card at list prices; what self-hosting buys is the absence of the 10 rps/org ceiling and a p99 of 160 ms.

### Scorecard and product consequence

R6A-H1 ❌ · H2 ❌ (L4 saturates at ×10; H100 at ×100, clears ×30 at 160 ms p99) · H3 reported (0.747, not compared) · H4 ❌ (joint stays). Rows in
[`results/leaderboard.md`](../leaderboard.md): `pplx-flash-local` ranks below the hosted row on every axis
but calibration. The registry keeps the row; the default for self-hosted deployments stays the joint request;
`docs/run6-gpu.md` records what a pod needs. The next measurement that would change a decision is
three H100s behind a balancer at ×100, or the 4k-token diff ablation (a quality question, run 7).

### Housekeeping

- Pod `b9nme44vl2gh5i`, 16:20Z → 18:46Z on 2026-10-18, terminated (verified 404 on `get-pod`). Setup from
  the network-mounted `/workspace` took 27 min; warm-up 80 s. No HF token (public repo).
- The first H1 pass lost 32 requests to 30 s client timeouts during warm-up; only that stage was re-run
  (`STAGES=h1`), filling exactly those 32 with 0 errors. Val 1,491/1,491, single 705/705, pairs 187/187, 0 errors.
- `mergeone leaderboard` crashed on the ×100 row (no p99 when every request fails); fixed in
  `mergeone/eval/leaderboard.py` after the run, reporting only.
- `serve_meta.json` has `region: null`; the data centre is in the `vm_type` string.
- Logs are on the Mac (`results/run6/logs/`, gitignored); result files were checksum-matched pod → Mac → repo.

## Part B — pool k ∈ {5, 10, 20} and hold precision

Data stage 2026-10-18 (CHANGELOG): test recall@5/10/20 = 0.655 / 0.862 / 0.862; 42,984 retrieved pairs scored by Jev
and pplx-flash (0 errors), Haiku continuity-only. Blind sample of 100 pplx-flash k = 20 holds (strata superseded 20 /
closed-unmarked 38 / merged 42) committed before any label (`38fc829`). Labels written 2026-10-18 → 2026-10-23;
the human labeller's `data/run6/hold_labels.jsonl` is the pre-registered number. Report: `python -m mergeone.cli
report results/run6 --run6b` → [`b_tables.md`](b_tables.md).

### R6B-H2 hold precision ✅ — 0.98 [0.93, 0.99] useful, 0.44 the same change

| subset | n | duplicate | pointer | unrelated | dup ∪ pointer [95% CI] | duplicate share |
|---|---|---|---|---|---|---|
| all (pre-registered) | 100 | 44 | 54 | 2 | **0.980** [0.930, 0.994] | **0.440** |
| stratum: superseded | 20 | 11 | 9 | 0 | 1.000 | 0.550 |
| stratum: closed-unmarked | 38 | 28 | 9 | 1 | 0.974 | 0.737 |
| stratum: merged | 42 | 6 | 35 | 1 | 0.976 | 0.143 |

Bars: union ≥ 0.60 ✅, duplicate ≥ 0.30 ✅; the demotion trigger (union < 0.40) is nowhere near. Two false alarms in
100: a release-branch cherry-pick of an unrelated PR that shares `ldap.go` (item 64), and two unrelated WIP agent
PRs (item 99). What the strata say: when the held PR was later *closed without a maintainer label*, three quarters
are the same change as the candidate; when it was *merged*, six in seven are pointers — backports, promotes
(edge → stable), follow-ups, competing fixes — i.e. the hold was useful but closing would have been wrong.

The labels carry a sub-class in the note. Over the 54 pointers, from the notes and the adjudication rationales
(not a typed field): backport / promote / cherry-pick across branches ≈ 28, competing fix (same goal, different
method, independent) ≈ 8, follow-up or repro ↔ fix ≈ 6, revert 2, same area ≈ 10. Run 7's C5 turns the sub-class
into a question the model answers.

**Taxonomy, dated.** The labeller wrote the rule down at item 35 (2026-10-19 22:56Z): `duplicate` = same change /
B makes A unnecessary (a superset revision counts); `pointer` = A's author should have looked at B first — backport,
promote, competing fix, follow-up, revert, same area; `unrelated` = file overlap only. Promote-edge→stable pairs are
`pointer` (same change, second branch, both merge). Eleven first-pass items noted "need to check B first" were
re-read under the written rule on 2026-10-20 and one promote pair on 2026-10-23; the re-reads are appended rows
(note prefix `pass2`), the first-pass rows stay in the file, and the table below shows both. The interim look at
n = 50 (`b_interim_n50.md`) changed nothing.

### Second labeller — agreement 0.88, Cohen κ 0.77

Not pre-registered; reported as R6B.2-B2 allows. A second, independent blind labelling of all 100 by Fable 5.1
(`hold_labels_fable.jsonl`, 2026-10-18, before the taxonomy was written) under the older reading "same work =
duplicate"; its promote pairs are mapped to `pointer` for comparison, the as-written numbers are beside it.

| labeller | duplicate | pointer | unrelated | union | duplicate share |
|---|---|---|---|---|---|
| human, final (pre-registered) | 44 | 54 | 2 | 0.98 | 0.44 |
| human, first pass (before the rule was written) | 35 | 63 | 2 | 0.98 | 0.35 |
| second, promote → pointer | 56 | 42 | 2 | 0.98 | 0.56 |
| second, as written | 68 | 30 | 2 | 0.98 | 0.68 |

Agreement 0.88, κ **0.77** (human final vs second, promote → pointer); 0.77 / κ 0.57 as written; 0.77 / κ 0.57
for the human's first pass. The two labellers agree on the number that carries the bar (union 0.98 either way)
and disagree on where duplicate ends and pointer begins: 11 of the 12 disagreements are "human pointer, second
duplicate" — competing fixes and same-issue-different-work pairs the older reading called duplicates.

**Adjudication (post-hoc, separate file).** The 12 disagreements were re-read together against the written rule
and recorded with a rationale each in `data/run6/hold_labels_adjudicated.jsonl` (`source: adjudicated`; the 88
agreed rows carry `source: agreed`). Ten resolve to `pointer` (competing fixes 9/17/37/87, subsets or extra work
29/36/52/84/100, different tasks 51) and two to `duplicate` (53: the same coming-soon page built twice from the
same template; 97: a superset revision with a test). Adjudicated: **46 duplicate / 52 pointer / 2 unrelated**,
union 0.98, duplicate share 0.46. The pre-registered number stays the human final (0.44); the adjudicated file is
the ground truth for run 7's relation question (C5).

### R6B-H4 k = 5 on the same labels (reported)

93 of the 100 are also held at k = 5 (91 with the same B): union 0.989, duplicate share 0.441 — indistinguishable
from k = 20. The seven held only at k = 20: 4 duplicate, 3 pointer, 0 unrelated.

### Threshold sweep (reporting only; the threshold is not changed)

| `duplicate_of` ≥ | n held | duplicate | pointer | unrelated | union | duplicate share |
|---|---|---|---|---|---|---|
| 0.7 (in force) | 100 | 44 | 54 | 2 | 0.980 | 0.44 |
| 0.8 | 80 | 42 | 37 | 1 | 0.988 | 0.53 |
| 0.9 | 50 | 30 | 20 | 0 | 1.000 | 0.60 |

A stricter threshold buys purity, not safety: even at 0.9, two in five holds are pointers, so no threshold makes
auto-closing a held PR right. Other models on the sample PRs they also hold: Jev 68 held (38 / 30 / 0, same B 57),
Haiku 54 (27 / 26 / 1, same B 42).

### R6B-H1 pool size ❌ and R6B-H3 cost ✅

Recall@20 = 0.862 clears 0.80, but the verdict-level bar (R4-H3: pass-group bad-outcome rate ≤ 0.5 × base =
0.234) fails at every k for every model: pplx-flash 0.383 → 0.379 → 0.375 at k = 5 / 10 / 20, Jev 0.352 → 0.346.
Coverage of closed PRs moves 1–2 pp from k = 5 to 20. The pool is not the lever; **k = 10 is the product default**
(the consequence written for H3, which passes: $0.0004 per PR at k = 20, bar $0.002; 21 calls per PR).

### Product consequence

- The dedup rule stays `duplicate_of ≥ 0.7 → hold, route: dedup`; it is not demoted. It is still opt-in
  (`memory.enabled`) until the relation question exists, because 54% of correct holds want a *link*, not a close.
- A hold never closes a PR, at any threshold: the sweep says 40% of holds at 0.9 are pointers. Closing is a
  human's call, with both PRs linked; run 7's C5 (typed relation, `on_related` / `on_competing`) is the path to
  letting the gateway close the clear cases later, measured first.
- Default pool `top_k: 10`.

### Housekeeping

Labels: `hold_labels.jsonl` (human; multi-row per PR, last wins, `pass2` rows appended), `hold_labels_fable.jsonl`
(second labeller), `hold_labels_adjudicated.jsonl` (post-hoc). Costs for part B: Jev + pplx-flash pair calls on
42,984 pairs ≈ $2; Haiku continuity ≈ $4. Labelling time: about 7 hours of human reading over five days.
