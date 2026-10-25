# Inference engineering for the decision calls — what can make them faster or cheaper, and a draft serving row (run 9)

Research note, 2026-10-25. No model call and no GPU time went into it. Every number from this repository comes from a
committed file under `results/` (paths given); the few numbers computed for this note (token splits, tokens/s, the
hosted-vs-local comparison in §3) are one-pass reductions of committed prediction rows, labelled as such, and are
**post-hoc**: none is a run verdict. Everything else is cited (§10). Nothing here is a hypothesis until it is in
`PROTOCOL.md`.

## 1. Diagnosis

The self-hosted decision call is **prefill-bound and already batched**: pplx-decider-flash returns no generated
tokens (one readout per question marker), the vendor's fast path already packs whole requests padding-free into ≤32k-token
passes with compiled layers and CUDA graphs, and run 6A measured per-request time flat inside batches on both cards.
What the earlier write-up got wrong is the size of the request: on the vendor package the joint v3 request is **one
sequence of median 2,344 tokens** (state ≈1.6k + ten questions ≈0.7k), not 16.9k, so the L4 runs at roughly a quarter of
its dense BF16 peak, not at it; there is kernel and precision headroom, but no batching headroom. The **10× redundancy**
is real only where a question is sent with its own copy of the state: our `--single-question` rows (7.3× the tokens)
and — found while writing this note, post-hoc — the **hosted** endpoint since run 7, whose billed tokens (16,980) and
answers (agreement 0.990, mean |Δp| 0.014 against our local single-question rows) say it now answers each question in
its own sequence; that, not weight drift, is the most likely cause of run 7's "drift". The **k× redundancy** is the
pair call: the gateway asks `same_change` once per retrieved candidate (up to `top_k: 10`), each request re-reading
PR A (≈77 % of the pair text), so with memory on, pair calls are ≈90 % of the prefill tokens per PR — and run 6A's load
curve replayed the v3 call only. Classic prefix caching cannot remove either redundancy on this checkpoint: its
full-attention layers are **noncausal**, so the state's hidden states depend on whatever follows it; the only exact
reuse is the first three (causal, linear-attention) layers. The levers that remain are fewer tokens per decision
(the vendor's own joint format applied to the pair call; compact state), cheaper arithmetic (the vendor's FP8 path; Ada
has FP8 tensor cores), and right-sized pack buckets for latency.

## 2. What the repository already measured

| quantity | value | source |
|---|---|---|
| model, code | `pplx-decider-flash-v1.2` @ `6d771ad7…`, base Qwen3.5-4B, `attention_mode: noncausal_full_attention`, `pooling: last`; `pplx_decider` 0.3.0 `fast=True`, BF16 | `results/run6/serve_meta.json` |
| v3 joint request, vendor package | **2,344** tokens median (p90 2,817, max 4,827), `usage.sequences = 1` on all 705 | `results/run6/predictions_pplx-flash-local_test_all.jsonl` (mode joint) |
| same PRs, one question per request | **17,050** median per PR (10 sequences), median ratio to joint **7.27×** | same file (mode single) |
| split of the joint sequence (computed: S = (single − joint)/9) | state + template ≈ **1,634**, ten questions ≈ **710** (30 % of the sequence) | same file, 705 paired PRs |
| pair call (`same_change`, one question) | 2,468 local / 2,198 hosted median tokens | run 6 local and hosted pair rows |
| A's share of the pair text | **0.77** median (p10 0.66), by characters | `results/run7/relation_pairs.jsonl` (100 pairs) |
| pair calls per PR in the gateway | one per candidate, up to `PairK` = `memory.top_k` (10) | `gate/internal/engine/engine.go` (`Memory.Candidates(meta, state, k)`) |
| L4, per request alone (×1, pass 2) | 370 s of `decide` for 600 requests = **0.62 s** | `results/run6/pass2-l4/healthz_after_x1.json` |
| L4, saturated | ≈ **2.2 req/s** (7,050 calls / ~50 min); ×10 p99 29.8 s, 89 % timeouts | `results/run6/README.md` part A |
| H100, per request in the ×30 step | (146.3 − 107.5) s / 600 = **0.065 s**; ×30 p99 160 ms, 0 errors; ×100 29.7 % errors | `results/run6/pass2-h100/healthz_after_x{10,30}.json`, README |
| hosted pplx-flash v3, run 5 (2026-10-18) | 2,344 tokens, p50 **276 ms**, $0.0000234 per call | `results/run5/predictions_pplx-flash_test_all.jsonl` |
| hosted pplx-flash v3, run 7 (2026-10-23) | **16,980** tokens, p50 **998 ms**, $0.00017 per call (7.2×) | `results/run7/control/predictions_pplx-flash_test_all.jsonl` |
| hosted pplx-flash pair, run 7 | 2,195 tokens, p50 848 ms (run 5: 214 ms) | same file (qset pair) |
| hosted Jev v3, run 7 | 2,529 tokens, p50 195 ms | `results/run7/control/predictions_jev_test_all.jsonl` |

Tokens/s implied (computed; mean joint request 2,279 tokens; FLOPs = 2 × 3.57 B decoder matmul parameters from the
checkpoint's `config.json`, attention scores and the gated-delta recurrence ignored, ≈4 % at this length):

| card | tokens/s | dense-peak BF16 used | note |
|---|---|---|---|
| L4, one request at a time | ≈ 3.7k | ≈ 22 % of 121 TFLOPS | each lone request is padded to the 4,096 bucket (§4), so ≈ 39 % counting filler |
| L4, saturated (2.2 req/s) | ≈ 5.0k | ≈ 30 % | |
| H100, ×30 step | ≈ 35k | ≈ 25 % of 989 (SXM) / 33 % of 756 (PCIe) | the pod's H100 variant is not recorded |

Two corrections to earlier text follow from these rows (recorded here, not edited there): (a) run 6A pass 2's "16k
tokens ≈ 1.3e14 FLOP ≈ 1 s at the card's peak" used the per-question token count; the joint sequence is ~2.3k, so the
L4 is far from peak; (b) R7.2 C1's "the v3 request carries the rendered state once per question (… p90 19.6k per
request, max 42k)" matches the single-question rows (p90 21,780, max 41,880), not the joint ones. Pass 2's padding
metric measured server-side padding the vendor fast path never pays (it packs padding-free), which is why moving it
from 0.64 to 0.91 changed nothing.

## 3. Post-hoc: the hosted endpoint now answers one question per sequence

Computed for this note from committed rows; 7,050 (or 6,820) answers per line; argmax at 0.5:

| comparison | PRs | argmax agreement | mean \|Δp\| |
|---|---|---|---|
| hosted run 5 vs local joint (the R6A-H1 verdict) | 705 | 0.952 | 0.047 |
| hosted run 5 vs local single | 705 | 0.814 | 0.177 |
| hosted run 7 vs local joint | 682 | 0.805 | 0.191 |
| **hosted run 7 vs local single** | 682 | **0.990** | **0.014** |
| hosted run 5 vs hosted run 7 | 682 | 0.817 | 0.176 |
| local joint vs local single | 705 | 0.802 | 0.191 |

Hosted run 7 is, within R6A-H1's bars, our local weights asked one question per request, and its token count is the
single-question count (16,980 vs 17,050). The two questions run 7 reported as drifted (`follows_conventions`,
`message_matches_diff`) are the two R6A-H4 found *more* inverted when asked alone. The flash model card recommends
one question per request for this model; a hosted change to that default between 2026-10-18 and 2026-10-23 explains
the token jump, the 3.6× latency rise on v3 and the answer shift at once. It does not explain the pair call's
214 → 848 ms (one question; same tokens), so a second, load-side change is also likely. Not verifiable from outside:
Perplexity has published no changelog for it that we found. This is post-hoc and is reported beside run 7's verdicts,
not in place of them; a one-call check is proposed as R9-H5.

## 4. How the decider builds its input (read from the checkpoint's `source/`)

From `pplx_decider` 0.3.0 as published on the Hub (`protocol.py`, `model.py`, `fast.py`):

- **One sequence per request.** `Encoder.messages` builds `system` + `"State:\n<state>"` + for each question
  `"Question i:\n<instructions>\nOptions:\nA: …\nB: …\n<|fim_suffix|>"`. The readout head (`[255, 2560]`) is applied at
  each marker. A request is split into question chunks (each with the full state) only above `max_length` (262,144).
  So our joint request sends the state **once**; the gateway's adapter (`mergeone/models/jev.py`) sends one HTTP
  request with all ten questions, and `serve/systemone_server.py` hands it to `decide` unchanged.
- **Attention.** 32 layers, `full_attention_interval: 4`: 24 Gated-DeltaNet (linear) layers, causal, and 8 gated
  full-attention layers (indices 3, 7, …, 31) made **noncausal over the whole request** by a forward hook
  (`enable_noncausal_full_attention`); the hook raises if a KV cache is requested ("Noncausal classification does not
  support a KV cache").
- **No shared-prefix API.** `decide(list)` packs independent requests; the linear-attention state and the causal
  convolution restart at every request boundary; nothing is carried between requests.
- **Fast path defaults** (`FastOptions()`): `compile="layer"`, CUDA graphs, buckets 2k/4k/8k/16k/32k, `prune_last=True`,
  `fp8=False`. A pack is filled with filler sequences to the next bucket. 99 % of our joint requests are 2,049–4,096
  tokens, so a lone request runs as a 4,096-token pass (useful/bucket 0.63 on the 705 test requests).
- **FP8 is built in**: `FastOptions(fp8=True)` replaces decoder `Linear`s with rowwise-scaled `float8_e4m3fn`
  matmuls (`torch._scaled_mm`). On the 27B sibling the card reports +17 % requests/s on one GB200 (21.2 → 24.9 on top of
  pruning) with argmax agreement 99.4 % and mean |Δp| 0.006 (max 0.24) against the reference path.

## 5. The research questions

**(1) Can vLLM / SGLang / TensorRT-LLM serve it, and does prefix caching apply?** The backbone, yes: vLLM has
Qwen3.5 recipes and a GDN prefill path; SGLang lists Qwen3.5-4B (BF16) with a mamba radix cache; TensorRT-LLM has a
Qwen3-Next PyTorch-backend PR. **This checkpoint, not as shipped**: the card says plainly that "a serving system needs
both the noncausal attention and the per-marker readout" and that "default causal inference does not reproduce this
checkpoint". None of the three ships a hybrid model with causal GDN layers, noncausal full-attention layers and a
marker readout; it would be a custom model definition in any of them (vLLM's pooling/token-classify runner is the
nearest fit). SGLang's `/v1/decisions` endpoint is for a generative Qwen3.5 MoE, not for this checkpoint. What prefix
caching would reuse: for GDN layers the engines cache the recurrent (and conv) state at block-aligned boundaries
(vLLM `--mamba-cache-mode align`, experimental; GDN does not support `all`), for attention layers the K/V of the
prefix. Both are exact only if the prefix's activations do not depend on the suffix. Here every layer from index 3
upward mixes the whole request (noncausal attention), and every later layer, GDN included, consumes those mixed
activations. **Exactly reusable: the outputs of layers 0–2 for the prefix (3 of 32 layers, ≤ 9 % of the prefix's
compute), plus tokenization and image-encoder output.** Nothing else. Turning prefix caching on in an engine that runs
the full-attention layers causally would serve a different model. Hydragen-style shared-prefix attention rests on the
same causal assumption.

**(2) INT8 / FP8 / INT4 on an L4.** The L4 (Ada, 72 W) lists 121 TFLOPS dense BF16 and 242 dense FP8 / 242 dense INT8 TOPS
(datasheet figures are with sparsity; dense is half). Prefill is compute-bound, so only formats that cut arithmetic
help: FP8 or INT8 W8A8 (2× peak), not weight-only INT4 (W4A16 dequantises to 16-bit and runs the same matmuls; it
helps memory-bound decode, which this workload does not have, and the 9 GiB of BF16 weights already fit). vLLM's FP8
docs claim "up to 1.6×" throughput and "minimal" accuracy impact with no benchmark detail; the vendor's own FP8 path
measured +17 % on a GB200 for the 27B model. Expected on our L4: **1.2–1.6×**, with the 72 W power cap the main
unknown. Accuracy risk for a classification head: the |Δp| that matters is near p = 0.5 and at the readout margin; the
vendor's 0.006 mean / 0.24 max |Δp| would pass R6A-H1 on average while flipping near-ties, and our questions are
calibrated by isotonic maps fit on BF16 answers — an FP8 row needs its own val refit. INT8 W8A8 (SmoothQuant-style)
has no vendor path and the same Ada peak as FP8; it adds engineering for no extra speed.

**(3) Continuous batching for prefill-heavy, one-token-output work.** Continuous batching is a decode-phase technique:
Sarathi-Serve reports "batching boosts decode phase throughput immensely but has little effect on prefill throughput"
and that "a prefill request with modest sequence length can effectively saturate GPU compute". This model has no decode
phase; the vendor's packing is the prefill analogue and is already on. Run 6A pass 2 is the local confirmation. Expected
gain from a continuous-batching engine on this workload: none beyond what packing gives; its value would be operational
(HTTP server, metrics, multi-GPU), not speed.

**(4) Shared-prefix batching for "same A, k candidates".** Exact sharing is impossible for the reason in (1): A's
activations depend on B. Two inexact options exist. (a) An attention mask in which A attends only to A and each B_i to
A and itself — a model the checkpoint was never trained as; it would need to pass equivalence and very likely would
not. (b) The vendor's **own joint format**: state = A followed by the k candidates, k `same_change` questions, one
sequence. It is native to the checkpoint, and tokens fall from k·(A + B + q) to A + Σ B_i + k·q. With A ≈ 0.70–0.77 of
a 2.2–2.5k-token pair, k = 10 gives a **2.7–3.3× token cut**. Its risk is quality, not equivalence: the flash card
measures candidate grouping at −4.2 (BRIGHT) to −15.4 (BFCL) points and recommends one candidate per request; the 27B
card measures duplicate-question reranking (CQADupStack, Quora) at +1.7 / +0.4 when grouped. Our pair question is
duplicate detection, so the sign is open; it has to be measured against the per-candidate call on our labels.

**(5) Published serving numbers.** Perplexity: the v1.2 launch says inference is "up to 5x faster", "all questions in
a request run in one pass", via "a new attention schema to enable better batching" (posts linked from the Digg
summary); the 27B card gives 19.8 req/s default fast path on one GB200 at 1,050 tokens per request (≈ 21k tokens/s)
and 96 ms median for a lone request; OpenRouter lists flash at p50 0.15 s. TypeSafe claims 70–500 ms end to end for
Jev and a 114 ms single-decision demo; the only third-party figure we found is Vercel's "up to 18× faster (p95)",
relayed by a blog that ran no benchmark. Our own hosted measurements (§2) are the better reference: Jev p50 195 ms at
2.5k tokens; pplx-flash 276 ms (run 5) → 998 ms (run 7).

## 6. Techniques

Gain is on one L4 unless stated; "cost to test" is beyond the run-6A harness, which is reused as is.

| technique | expected gain | what it would cost to test | risk |
|---|---|---|---|
| Joint pair call (A + k candidates, k questions, one sequence) | 2.7–3.3× fewer pair tokens at k = 10; pair calls are ≈ 90 % of per-PR tokens, so ≈ 2.5× per PR with memory on | a request builder for the grouped state (no server change); same-day per-candidate control; ≈ 1 L4-hour | quality: flash card says grouping candidates hurts on retrieval-like tasks (−4 to −15); not an equivalence question, a quality one |
| Vendor FP8 (`FastOptions(fp8=True)`) | 1.2–1.6× prefill throughput on Ada (vendor: +17 % on GB200) | one flag in `serve/`; equivalence vs BF16 same day; val refit; ≈ 1 L4-hour | near-tie argmax flips (vendor max \|Δp\| 0.24); needs an explicit amendment of R6A.4's "no quantised weights" |
| Right-sized buckets (e.g. add 2,560 and 3,072) | lone-request latency ≈ −25 % to −35 % (4,096 → ≤ 3,072 tokens per pass); ≈ 0 at saturation | one `FastOptions(buckets=…)` change; ×1 curve | more compiled graphs, longer warm-up; nothing else |
| Compact state (R8-H1) | ≈ −29 % joint tokens (state is 70 % of the sequence, compaction cut it ≈ 42 % on the hosted count) | already pre-registered as run 8 C1 for quality; throughput is free to measure alongside | quality, decided by R8-H1 |
| Shorter / fewer questions (v4 drops two) | ≈ −6 % per v3 call (the ten questions are 30 % of the sequence) | already run 8 C3 | prompt change = model change; R7 showed rewording hurts |
| vLLM / SGLang / TRT-LLM port | 0–1.3× (the vendor path already packs, compiles and graphs) | a custom model definition with noncausal full attention and marker readout; days of work | equivalence; maintaining a fork of the vendor protocol |
| Engine prefix caching | exact: ≤ 9 % of the prefix's compute (layers 0–2 only); otherwise a different model | not worth a row | serving a causal model by accident |
| Hydragen-style masked pair packing | ≈ 3× tokens | custom mask + kernel | will not be equivalent: A no longer sees B |
| INT8 W8A8 | same peak as FP8 on Ada | custom kernels, calibration | as FP8, plus engineering |
| INT4 weight-only (AWQ/GPTQ) | none or negative for prefill | quantised checkpoint | dequant overhead on a compute-bound path; reject |
| Continuous batching | none (no decode phase) | engine port | — |
| Faster card / more cards | H100 ≈ 9× L4 per request (measured); cards scale linearly | measured in run 6A | cost: $0.106/1k PRs at ×30 on one H100 vs $0.023 hosted list (v3 only) |

## 7. Back of the envelope

- **Demand.** Base rate 0.347 PRs/s (30k/day). v3 joint alone: 2,279 tokens/PR → ×10 needs 7.9k tokens/s, ×100 79k.
  With memory on, add up to 10 pair calls × 2,468 tokens = 24.7k → **≈ 27k tokens/PR**; ×1 alone needs 9.4k tokens/s.
- **Supply.** L4 3.7k (alone) to 5.0k (saturated) tokens/s; H100 ≈ 35k tokens/s.
- **So today:** v3 only, one L4 carries ≈ 6× base rate (measured); **with pair calls it carries ≈ 0.5× base rate**
  (5.0k / 27k = 0.19 PRs/s) and one H100 ≈ 3.7× — the run-6A curve did not include pair calls, so its "6×" is the v3
  call's number, not the gateway's.
- **Joint pair call at k = 10:** per PR 2.3k + (A 1.9k + 10 × 0.6k) ≈ 10k tokens → 2.7× → one L4 ≈ 1.4× base rate,
  one H100 ≈ 10×.
- **FP8 on top (1.2–1.6×) and compact state (−29 % on v3, similar on the pair text if R8-H1 passes):** an L4 reaches
  roughly 2–3× base rate with memory on; **×10 on one L4 stays out of reach** by this arithmetic. For v3 alone, ×10
  needs 7.9k tokens/s, i.e. 1.6–2.1× on the L4 — FP8 plus compact state is about exactly that, so R9-H2 below is a
  coin flip, stated as such before the run.
- **What prefix sharing of the state across the ten questions would cut:** on the vendor joint path, nothing — the
  state is already sent once. Against the per-question path (hosted since run 7, or `--single-question`), joint is the
  7.3× cut; it is a different model behaviour, and R6A-H4 measured it at +0.000 AUC on our primary target.

## 8. Draft §R9 — serving row (for PROTOCOL.md; not a hypothesis until committed there)

**Why.** Run 6A showed one GPU, not the weights, is the limit, and attributed it to compute per request. This note
finds that compute per request is ~25 % of peak, that pair calls carry ≈ 90 % of the tokens and were never load-tested,
and that the hosted row changed its request decomposition. Run 9 measures the levers that touch tokens and arithmetic,
on the same harness, against a **same-day vendor-BF16 control on the same pod** (no comparison against run 6's or the
hosted endpoint's numbers).

**Served.** `pplx-decider-flash-v1.2` at the run-6A revision; vendor `pplx_decider` from the checkpoint; one L4
(H100 only for H1 if the port needs it); `serve/systemone_server.py` with `--fast-options` exposed (new flag, committed
before freeze). Equivalence set: the 705 run-3 test PRs × 10 v3 questions, joint (7,050 answers). Load: open-loop,
600 decisions per step, ×1 / ×10 / ×30 / ×100, 512 workers, no retry, 60 s between steps (as R6A.3). Pair set: the
run-4 test pairs grouped by A, and the 100 adjudicated 6B hold pairs.

- **R9-H1 (engine equivalence).** The same weights served by a non-vendor engine (vLLM or SGLang custom model with
  noncausal full attention and marker readout; prefix caching **off**) vs the vendor fast path BF16, same day, same
  GPU: argmax agreement ≥ **0.95** and mean |Δp| ≤ **0.02** over the 7,050 answers; reported beside it, saturation
  throughput ratio (port / vendor). A port that passes but is < 1.2× the vendor's throughput is reported and not
  shipped. If no port exists at freeze, H1 is deferred (as C4 in run 8) and is not a run-9 hypothesis.
- **R9-H2 (L4 at ×10).** The best configuration that passes its equivalence bar (H3 if it passes, else vendor BF16 with
  buckets (2,560, 3,072, 4,096, 8,192, 16,384, 32,768)) carries **×10** (3.47 v3 decisions/s) with p99 ≤ ×1 p99 + 200 ms
  and **0 errors**; saturation multiplier reported (first p99 > 2 × ×1 p99). Measured alongside, no bar: the same
  curve with the gateway's pair calls included (v3 + up to 10 `same_change` per PR, from the 6B candidates), so the
  gateway's real capacity per L4 is on record.
- **R9-H3 (8-bit row equivalence).** Vendor `FastOptions(fp8=True)` vs vendor BF16, same day, same GPU: argmax
  agreement ≥ **0.95**, mean |Δp| ≤ **0.02**; and saturation throughput ≥ **1.3×** BF16. Pass → `fp8` becomes the
  documented L4 configuration (with its own isotonic refit on val); fail on either bar → BF16 stays. INT8 W8A8 is
  tested only if FP8 is unavailable on the pod. Requires a dated amendment that lifts R6A.4's "no quantised weights"
  for this row only.
- **R9-H4 (shared-A pair call).** The joint pair request (state = A followed by the k candidates, one `same_change`
  question per candidate, prompt text otherwise as `prompts/jev_pair.json`) vs one request per candidate, same day,
  k = 10: (a) candidates scored per second ≥ **2.5×** at saturation (the arithmetic ceiling is 2.7–3.3×; a 3× bar would
  sit inside the ceiling's range and fail on the token arithmetic alone); (b) `same_change` AUC on the run-4 test pairs
  and duplicate-vs-rest AUC on the 6B pairs each ≥ per-candidate − **0.02**. Argmax agreement and |Δp| are reported, not
  barred (it is a different request, by design). Pass → the gateway may send grouped pair calls to self-hosted
  pplx-flash (opt-in); fail → one request per candidate stays.
- **R9-H5 (hosted decomposition, a check, ≈ $0.2).** Same day: hosted `pplx-flash` v3 on the 705 test PRs vs local
  single-question: agreement ≥ 0.95 and |Δp| ≤ 0.02, and hosted `usage.input_tokens` / local joint tokens ≥ 6.
  Pass → run 7's drift finding is re-labelled "request decomposition changed" in `docs/EVIDENCE.md` (beside, not in
  place of, the run-7 verdicts); fail → reported.

**Not allowed.** No prompt, question, temperature or calibration-method change except H4's grouped state (frozen at
freeze); no engine prefix caching in any equivalence row; no tuning on the equivalence set; every pass of a load curve
kept.

**Budget.** One L4 ≈ 3–4 h ≈ $2.4 (control, H3, H4, curves); H1 only if a port exists (≈ 2 h L4, or one H100 hour
$3.99); Perplexity ≈ $0.2 (H5).

## 9. What could not be verified

- Whether the hosted endpoint really splits joint requests per question (§3 is strong circumstantial evidence from our
  own rows; no Perplexity statement found), and what changed in the pair call's latency.
- Whether the Hub checkpoint still matches revision `6d771ad7…` (the repo page shows an update on 2026-10-19; we read
  the current `source/`, version 0.3.0 as installed in run 6, not the pinned revision's bytes).
- The FP8 speedup on an L4 (only the vendor's GB200 number and vLLM's unqualified "up to 1.6×" exist) and how the 72 W
  power cap limits either precision.
- Which H100 variant the run-6A pod was (SXM vs PCIe), hence its MFU.
- Whether vLLM's pooling runner can host a hybrid model with noncausal attention layers without code changes (docs
  only mention `is_causal = False` for encoder-only models in the Transformers backend); whether TensorRT-LLM's
  Qwen3-Next PR was merged.
- Perplexity's "up to 5x faster" baseline and TypeSafe's Jev latency conditions.
- The pair-text A share is measured on 100 run-7 relation pairs (characters, not tokens); the gateway's real candidate
  count per PR is ≤ 10 and lower in small repositories, so the per-PR token figures are upper bounds.

## 10. Sources

- Model card and source, `perplexity-ai/pplx-decider-flash-v1.2` (README, `config.json`, `joint_config.json`,
  `source/src/pplx_decider/{model,protocol,fast}.py`, `requirements-fast.txt`): https://huggingface.co/perplexity-ai/pplx-decider-flash-v1.2
- Model card, `perplexity-ai/pplx-decider-v1.2` (fast-path throughput table, FP8 equivalence, grouping and reranking tables): https://huggingface.co/perplexity-ai/pplx-decider-v1.2
- OpenRouter, pplx-decider-flash-v1.2 (release date, p50 latency, price): https://openrouter.ai/perplexity/pplx-decider-flash-v1.2-20261016
- Launch summary with links to Perplexity's posts ("up to 5x faster", "one pass"): https://digg.com/ai/lc20h8ke
- NVIDIA L4 datasheet: https://www.nvidia.com/en-zz/data-center/l4/
- vLLM, Qwen3.5 disaggregated serving (GDN prefill kernel, prefix caching disabled in benchmark): https://vllm.ai/blog/2026-08-06-qwen35-25k-tps
- LMCache Qwen3.5 recipe (`--mamba-cache-mode align`, GDN does not support `all`, experimental, not bit-exact): https://docs.lmcache.ai/recipes/qwen3_5.html
- vLLM supported models (`is_causal = False` only for encoder-only models): https://docs.vllm.ai/en/latest/models/supported_models/
- vLLM FP8 W8A8 (compute capability ≥ 8.9, "up to 1.6x"): https://docs.vllm.ai/en/latest/features/quantization/llm_compressor/fp8/
- SGLang Qwen3.5 cookbook (4B listed, mamba radix cache, `/v1/decisions` for the MoE): https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.5
- TensorRT-LLM Qwen3-Next PR: https://github.com/NVIDIA/TensorRT-LLM/pull/7892
- Sarathi-Serve (prefill saturates compute; batching helps decode): https://arxiv.org/html/2403.02310v3
- Hydragen (exact shared-prefix attention under the causal mask): https://arxiv.org/html/2402.05099v2
- Why INT4 weight-only does not speed up prefill: https://dev.to/ji_ai/why-int4-weight-only-quantization-doesnt-speed-up-prefill-1b45
- SmoothQuant (INT8 W8A8): https://arxiv.org/abs/2211.10438
- Jev latency claims, as relayed: https://www.eesel.ai/blog/jev-ultrafast
