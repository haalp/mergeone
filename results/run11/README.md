# Run 11 — three cheaper calls

Complete for two of three hypotheses (2026-11-08, hosted rows 15:41–15:44Z from the laptop; the third is conditional
and waits). **Both fail.** Protocol: PROTOCOL.md §R11 (frozen 2026-11-08, `r11-frozen`, drafted and frozen the same day
after run 10). Tables: [`tables.md`](tables.md); numbers: [`metrics.json`](metrics.json), [`prefilter.json`](prefilter.json);
every number here is rebuilt by `python -m mergeone.eval.run11_report`. The H1 comparison is against a same-day hosted
`pplx-flash` control in this directory (`main/predictions_pplx-flash_test_all.jsonl`); run 8's compact numbers are shown
beside it, never instead.

| | hypothesis | bar | result |
|---|---|---|---|
| R11-H1 | compact state for the main call loses nothing | mean v4 Δ ≥ −0.02 **and** every question Δ ≥ −0.03 **and** tokens ≤ 0.75× | ❌ mean **−0.003** [−0.020, 0.015] · tokens **0.675×** · `tests_cover_change` **−0.032** |
| R11-H2 | a model-free prefilter keeps the duplicate in the top k' | recall ≥ 0.95 and dup-vs-rest AUC ≥ k'=10 − 0.02, primary score `retrieval`, k' ∈ {3, 5} | ❌ recall **0.891** (k'=3), **0.913** (k'=5); AUC −0.042 / −0.016 |
| R11-H3 | a team's own outcomes recalibrate better than the benchmark map | ECE −0.02 on ≥ 5/8, AUC unchanged; needs ≥ 200 outcomes | not run (precondition unmet) |

## R11-H1 — compact for the main call: the mean holds, one question does not

On the 705 test PRs, the compact rendering (diff 2,000 chars, body 600, 20 files — run 8's limits) against the
same-day full-state control: mean of the eight v4 AUCs **0.707 vs 0.710** (Δ −0.003, paired CI [−0.020, 0.015]),
median input tokens **1,492 vs 2,209** (0.675×). Two of three clauses pass. The third, written for this run and not
in run 8 — *every* question within −0.03 — fails on `tests_cover_change`: 0.603 vs 0.635, Δ **−0.032**, paired CI
[−0.095, 0.020]. A fail by 0.002 on a question whose interval includes zero is still a fail; the bar was set to catch
exactly a question that the compact diff starves, and that is the question that reads whether tests cover the change
— the one most likely to need the diff past 2,000 characters. Seven questions move by less than 0.02 in either
direction (`hard_to_revert` +0.012, `exceeds_scope` +0.010, `is_logic_change` −0.016). Run 8, beside: v4 mean Δ −0.001
(pplx-flash), tokens −42 %. **Consequence, as pre-registered:** the main call keeps the full state; `state: compact`
is not added to the policy. Post-hoc, beside the verdict: a compact rendering with a larger diff budget (3,000–4,000
chars) would be the next candidate — a new section, not an edit to this one.

## R11-H2 — the memory's retrieval order does not keep the duplicate often enough

No model call: run 9's recorded per-candidate `same_change` answers on set (b) — 100 adjudicated A's (46 duplicate,
52 pointer, 2 unrelated), ten candidates each. Keeping the top 5 by the memory's own retrieval score keeps the
adjudicated duplicate for **42 of 46** (0.913; bar 0.95); the top 3 for 41 (0.891). The duplicate-vs-rest AUC on the
kept candidates is 0.956 (k'=5) and 0.929 (k'=3) against 0.972 on all ten — the second clause fails at k'=3 only. The
two secondary scores are worse on recall: file-overlap 0.826 / 0.783, blend 0.891 / 0.848. (The AUC here counts every
retrieved candidate as a negative; run 9's set-(b) figure of 0.706 counted only the adjudicated B's, duplicate vs
pointer/unrelated. The definition used is the one §R11.3 states; both are in the file.) **Consequence:** all ten
candidates are called; `memory.prefilter_k` is not added. Four of the 46 duplicates sit at retrieval rank 6–10 and one
was outside the top ten entirely (run 9 forced it into slot 10) — the retrieval is the thing to improve if pair calls
are to be cut, not the cut.

## R11-H3 — waiting

No gatelog has 200 decided PRs with outcomes yet. The bar stands as frozen; the result goes here as a dated addendum.

## A note on the control, beside the verdicts

The hosted endpoint reported a median of **2,209** input tokens per joint v4 request today; run 9 (2026-11-06)
measured **17,050** for the same request shape on the same model name, and read that as the provider decomposing a
joint request into single questions (R9-H5). The answers are consistent with run 10's same-day control two days ago
(`needs_human_review` 0.759 both days). Nothing in run 11 rests on the token count of the control except the H1 ratio,
which is computed on the same day for both arms; the drift monitor is the place that watches this.

## What run 11 changes

- Gateway: nothing. Full state for the main call, compact for the pair call (run 8), ten pair calls per PR.
- Benchmark: the per-question clause did its job once and is kept for any future state-rendering hypothesis.
- Spend: $0.026 (705 × 2 hosted requests); H2 cost nothing.
