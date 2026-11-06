# Run 9 — the serving row: FP8, shared-A pair call, one L4 at ×10, hosted check

Complete (second pod: 2026-11-05 22:53Z – 2026-11-06 03:30Z, 1 × L4 on RunPod Secure, US-MO-2, $0.59/h; the hosted
half of R9-H5 from the laptop 03:09–03:12Z the same UTC day). **One passes, three fail, one was deferred at freeze.**
Protocol: PROTOCOL.md §R9 (frozen 2026-10-27, `r9-frozen`). Tables: [`tables.md`](tables.md); numbers:
[`metrics.json`](metrics.json); every table and number here is rebuilt by `python -m mergeone.eval.run9_report`.
Every comparison is against the same-day vendor-BF16 control on the same pod (`bf16/`); run 6A is shown beside, never
instead.

| | hypothesis | bar | result |
|---|---|---|---|
| R9-H1 | the same weights under a non-vendor engine | — | deferred at freeze (no port exists) |
| R9-H2 | the chosen configuration carries ×10 (3.47 decisions/s) on one L4 | 0 errors and p99 ≤ its ×1 p99 + 200 ms | ❌ buckets row: ×10 p99 447,918 ms vs 1,432 + 200; control ×10 p99 173,867 ms; both saturate at ×10 |
| R9-H3 | vendor FP8 row | agreement ≥ 0.95, mean \|Δp\| ≤ 0.02, throughput ≥ 1.3× | ❌ 0.963 · **0.036** · **0.64×** |
| R9-H4 | shared-A pair call, k = 10 | candidates/s ≥ 2.5×; set-(a) AUC and set-(b) dup-vs-rest AUC ≥ per-candidate − 0.02 | ❌ **2.46×** · −0.031 · **−0.136** |
| R9-H5 | hosted joint = local single-question | agreement ≥ 0.95, mean \|Δp\| ≤ 0.02, token ratio ≥ 6 | ✅ 0.990 · 0.014 · 7.27 |

## R9-H5 — run 7's "drift" was the request, not the weights

The same 705 test PRs, the same UTC day: the hosted `pplx-flash` endpoint answering the ten v3 questions in one request
agrees with the vendor BF16 checkpoint on our pod answering them **one question per request** on 0.990 of the 7,050
answers, mean |Δp| 0.014 (p99 0.10); the hosted endpoint reports a median 17,050 input tokens per PR, 7.27× the 2,344
a joint request costs locally and exactly what ten single-question requests cost (17,050). The same checkpoint,
served joint, is what run 5 measured; served one question at a time, it is what run 7 measured. Run 7's finding stands
as written (the hosted model's answers moved under an unchanged version string; two questions lost 0.10–0.23 AUC); what
moved was the provider's request decomposition, and `docs/EVIDENCE.md` now carries that reading beside the run-7
verdicts. Two consequences hold without a new bar: the drift monitor was right to fire on the questions and not on a
version event, and the hosted endpoint bills ≈ 7× the tokens a joint request needs.

## R9-H3 — FP8 on the L4 is slower, and not the same answers

Vendor `FastOptions(fp8=True)` (rowwise-scaled `float8_e4m3fn` decoder matmuls; the R6A.4 exception) against the
same-day BF16 control on the equivalence set: argmax agreement **0.963** clears 0.95, but mean |Δp| is **0.036**
(bar 0.02; `message_matches_diff` 0.056, `hard_to_revert` 0.058, max 0.68), and the timed pass is **slower** — 1.23
PR/s against the control's 1.92 (0.64×, bar 1.3×), with 0 errors on both. On this GPU, at these sequence lengths and
batch shapes, the per-row scaling costs more than the narrower matmuls save. BF16 stays; the FP8 val rows (1,491 PRs)
are kept for the record and no FP8 calibration map ships.

## R9-H2 — one L4 does not carry ×10 in any configuration

The §R9.3 rule chose the bucket row (H3 failed; the bucket row is equivalent to the control: 0.993 / 0.0076). Its ×1
step is clean (p99 1,432 ms, 0 errors), but ×10 — 3.47 decisions/s open-loop — queues from the first minute: p99
**447,918 ms**; the control's own curve the same day queues the same way (×10 p99 173,867 ms). The arithmetic was
visible before the load step: the equivalence pass measures the pod's capacity at **1.92 PR/s** (BF16, 32 in flight),
and 3.47/s is 1.8× that. The bucket row is slower still on the equivalence set (1.12/s). Saturation is ×10 for both
rows, as it was for run 6A. **A caveat on the "0 errors" clause:** the load client ran with the 900 s read timeout set
for the shared-A step (CHANGELOG, 2026-11-06), so queued requests waited instead of timing out; run 6A's ×10 (30 s
timeout) had 535 errors. The p99 clause fails under either timeout; the error count is not comparable with run 6A's
and is not what the verdict rests on. ×1 / ×10 with pair calls mixed in: not run (the loadtest has no v3 + pair mix).

## R9-H4 — the shared-A pair call is faster but reads the candidates worse

One request with A and its ten candidates, against one request per candidate (both BF16, same day): **5.77 vs 2.34
candidates/s** on set (b) — 2.46× (bar 2.5×; tokens per candidate 767 vs 2,206, −65 %). The quality clauses are
where it fails: `same_change` AUC on set (a) 0.808 vs 0.839 (−0.031, bar −0.02), and duplicate-vs-rest AUC on set (b)
**0.570 vs 0.706** (−0.136). Between the arms the answers agree on 0.957 / 0.934 of candidates with mean |Δp| 0.046 /
0.071 — the shared request is a different request, and when ten candidates share one context the model separates the
duplicate from its neighbours less well than when it sees each pair alone. One request per candidate stays; the
gateway does not get a shared-pair mode. The token arithmetic (research note) was right about the cost and wrong
about the answers.

## What run 9 changes

- Gateway: nothing ships from H2–H4. The documented self-hosted configuration stays vendor BF16 with the default
  buckets; the pair call stays one request per candidate; one L4 is documented as **≈ 1.9 decisions/s, saturating
  at ×10** (the hosted endpoint or a second GPU carries more).
- Evidence: run 7's drift row gains the H5 reading beside it ("request decomposition changed"); the drift monitor's
  thresholds are unchanged.
- Benchmark: the serving levers that remain are the ones run 9 did not test — a second GPU and the engine port
  (H1, still without a port). Run 10 (ROADMAP) is about the decider, not the server.
- Pod log: the first pod's results were lost before they were copied off (CHANGELOG, 2026-11-02); the second pod's
  tooling deviations (warm pass, client timeout, `stop` waiting for the GPU) are dated there. Spend: pod 1 $3.77
  (lost), pod 2 ≈ $2.8, one idle pod ≈ $0.6, Perplexity $0.12 (724 hosted requests, 19 retried after 429s).
