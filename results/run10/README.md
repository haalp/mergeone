# Run 10 — is a stronger decider worth it?

Complete (2026-11-08, 00:03–01:47Z; the hosted rows 00:03–00:07Z from the laptop, the d3-flash rows on one A40 on
RunPod Secure, EU-SE-1, $0.59/h; all four rows the same UTC day). **One passes, three fail.** Protocol: PROTOCOL.md
§R10 (frozen 2026-11-07, `r10-frozen`). Tables: [`tables.md`](tables.md); numbers: [`metrics.json`](metrics.json);
every number here is rebuilt by `python -m mergeone.eval.run10_report`. Every row is compared with the same-day hosted
`pplx-flash` control (`control/`); run 8's numbers are shown beside it, never instead.

| | hypothesis | bar | result |
|---|---|---|---|
| R10-H1 | a row reads competing fixes and same-area pairs | competing + same-area ≥ 0.40 of 17, class accuracy ≥ control − 0.02 | ❌ full **6/17**, haiku 5/17, d3 3/17 (control 2/17) |
| R10-H2 | a row's `needs_human_review` beats the control | AUC ≥ control + 0.03, paired bootstrap CI excluding zero | ✅ **d3-flash 0.792 vs 0.759, +0.034 [0.003, 0.068]**; full −0.016 |
| R10-H3 | cost and speed; a passing row ships only at ≤ 5× the control's per-PR cost | ≤ 5× | ❌ d3 **10.2×** ($0.0025 vs $0.00025); full 2.0×; haiku 15.5× |
| R10-H4 | d3-flash as the self-hosted registry row | ≥ 6/8 questions ≥ control − 0.02, throughput ≥ 1.0 PR/s | ❌ **7/8** questions, **0.37 PR/s** |

## R10-H2 — d3-flash reads "needs a human" better, by the smallest margin the bar allows

On the 705 test PRs, d3-flash's `needs_human_review` AUC is **0.792** against the same-day control's **0.759**: +0.034
on the same PRs, paired bootstrap CI [0.003, 0.068] — over the +0.03 bar with 0.004 to spare and a CI whose lower
edge is 0.003. It is a pass, and a narrow one; the next run that asks the question will say whether it holds. The
full `pplx` row does not move the question (0.742, −0.016). d3-flash's eight-question mean is 0.717 against 0.710,
and it is at or above the control on seven of eight (H4's first clause), losing only `touches_concurrency` (−0.051).
Its raw probabilities sit on a different scale (agreement 0.933, mean |Δp| 0.072; `needs_human_review` answers run
0.11 lower), which the isotonic map fit on its 1,491 val PRs flattens (test ECE 0.44 → 0.06); the map is in the
results, not shipped.

## R10-H3 — and costs ten times the control per PR, so nothing ships

The rule in §R10.3 is cost before shipping: a row that passes H1 or H2 ships only at ≤ 5× the control's per-PR
cost. d3-flash on the A40 costs **$0.0025 per PR** (main call + ten pair calls at the measured 0.37 PR/s, $0.59/h)
against the hosted control's **$0.00025** — 10.2×. Two things make it so: the A40 serves d3-flash at 0.37 PR/s
(the model card's 95 req/s is one-question text on an MI325X), and d3 tokenises the same PR to 13.7k input tokens
where pplx-flash reads 2.2k. The `review` rule keeps pplx-flash. A faster GPU would change the arithmetic, and that
is a run, not a footnote: the pass is recorded, the price is recorded, the gateway does not change.

## R10-H1 — competing fixes move, same-area does not

The 17 hard pairs (7 competing, 10 same-area) stay under the bar for every row, but not in the same way. **Competing**
fixes, unreadable for the 4B deciders since run 4 (control 1/7), are read by the stronger rows: full `pplx` **4/7**,
Haiku **5/7**, d3 2/7. **Same-area** work does not move for anyone: 1/10, 2/10, 0/10, 1/10. Class accuracy over all
100 pairs: Haiku 0.750, d3 0.670, full 0.600, control 0.580 (run 8: Jev 0.740, pplx-flash 0.570). Haiku's gain is
bought at 15× the cost and with `same_change` ranking of 0.550 — it names classes well and ranks duplicates badly.
Post-hoc, beside the verdict: "same goal, different method" is now partly readable from two diffs by a 26B or an
LLM-class model; "same files, different work" is not readable by any of them. If a relation question is asked again,
the same-area class needs a different signal (timing, the issue, the review thread — run 8's note), not a bigger model.

## R10-H4 — the registry row is equivalent enough and too slow

Self-hosted d3-flash (vendor BF16, `d3_server.py --batch-size 8`, `flash-linear-attention` 0.6.0, concurrency 32)
meets the quality clause — **7 of 8** questions within −0.02 of the control, five above it — and fails the serving
clause: **0.370 PR/s** on the A40 (705 PRs in 1,907 s, 0 errors) against a 1.0 PR/s bar; p50 latency 89 s under the
queue of 32. The 4B pplx-flash checkpoint served the same way on an L4 ran 1.92 PR/s (run 9). d3-flash stays a
reported row with its calibration map in `results/run10/d3/`; it is not the documented self-hosted alternative.

## What run 10 changes

- Gateway: nothing. `needs_human_review` keeps pplx-flash (d3-flash passes the quality bar and fails the cost bar);
  relation keeps `duplicate_of` as the only edge.
- Benchmark: the first open Apache-2.0 decider is measured on our questions (d3-flash, Jev Decision Index 55.8) and
  reads them at least as well as the hosted 4B model; cost on a 48 GB GPU is the obstacle. A relation question, if
  asked again, targets same-area with non-diff signals.
- Spend: GPU ≈ $2.1 (A40 3.6 h, incl. the setup hour before 00:00Z) + $0.23 for the pre-freeze check; Perplexity
  $0.052 (control $0.017, full $0.035); Anthropic $0.038 (Haiku relation).
