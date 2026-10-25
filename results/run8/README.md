# Run 8 — compact state with its control, relation v2, v4, drift replay

Complete (2026-10-25, 00:19–00:42Z for the model calls; Perplexity ≈ $0.40, Jev on credit, no GPU). Every comparison
in this run has a same-day full-state control in the same directory — the rule run 7 taught. **Two pass, one fails,
one is reported without a bar, one is deferred.** Protocol: PROTOCOL.md §R8. Tables: [`tables.md`](tables.md),
[`relation_tables.md`](relation_tables.md); numbers: [`metrics.json`](metrics.json), [`relation_metrics.json`](relation_metrics.json),
[`drift_replay.json`](drift_replay.json), [`drift_slow.json`](drift_slow.json).

| | hypothesis | bar | result |
|---|---|---|---|
| R8-H1 | compact state (diff 2,000 chars, body 600, 20 files) loses ≤ 0.02 vs the same-day full state | mean v4 Δ ≥ −0.02 and pair Δ ≥ −0.02, both models | ✅ v4 +0.010 / −0.001 · pair −0.018 / −0.015 · tokens −32 % / −42 % |
| R8-H2 | a three-question relation prompt + metadata branch separates the six pair classes | class accuracy ≥ 0.80, competing + same-area ≥ 0.40, same-change AUC within 0.02 of run 7 | ❌ 0.740 / 0.570 · 3/17 and 1/17 |
| R8-H3 | v4 = v3 minus the two drifted questions | no bar | reported: v4 = control on the eight (Δ +0.001 / +0.000) |
| R8-H4 | self-hosted registry row | R6A bars | deferred (no candidate with an Apache-2.0-equivalent licence named by freeze) |
| R8-H5 | the drift monitor replays run 7 correctly | fast signal flags exactly the two broken questions, nothing on jev / run 6A, no false positive; slow signal flags jev `tests_cover_change` | ✅ |

## R8-H1 — compact state passes, now that the comparison is fair

Run 7 asked the same question against run 5's numbers and failed by −0.049; the same-day control there read +0.007
post-hoc. Run 8 pre-registered that control and the result holds: against the full state rendered the same day by the
same model, the compact rendering is **+0.010** (jev) and **−0.001** (pplx-flash) on the mean of the eight v4
questions, and **−0.018 / −0.015** on the pair question over the 187 eval pairs — all inside the −0.02 bar, with the
pair deltas sitting on it (jev's CI [−0.035, −0.004] does not include zero). Tokens per request fall 32 % (jev v4:
1,655 vs 2,424 median) to 42 % (pplx-flash v4: 7,911 vs 13,647; pair: 1,564 vs 2,468).

Consequence, as pre-registered: **`--compact` becomes the default for the pair call** — the one the gateway makes up
to `top_k` times per PR, where the tokens are — and the v4/v3 call keeps the full state. The pair loss is real but
small; `docs/research/inference.md` shows the pair call is where ≈ 90 % of a PR's prefill tokens go when memory is on,
which is why this is the call to shrink. The Go gateway's pair renderer gains the compact path in v0.3.

## R8-H2 — relation v2 fails; the three-question prompt is not the lever either

The run-7 reading (substitute the metadata base branch for the model's `same_branch` answer: 0.08 / 0.32 → 0.72) was
the baseline here, and the new three-binary prompt (`same_change`, `builds_on`, `same_goal`) was supposed to add the
rest. It adds nothing: jev scores **0.740**, exactly its baseline; pplx-flash falls to **0.570** from 0.722 because its
`builds_on` answer over-fires — nine duplicates and six of seven competing fixes become "follow-up". The same-day
control (run 7's four-question prompt asked today) sits at 0.747 / 0.720, so this is the prompt, not the day.
Competing fixes stay unreadable (0/7, 2/7) and same-area too (1/10 each); both models are good at what the metadata
already decides (backport 26/28 and 19/28, duplicate 39/46 and 30/46).

What the two runs together say: the base branch does the sorting that matters (duplicate vs backport, 0.72 of the
accuracy), and "same goal, different method" is not something these models read from two diffs. The gateway keeps
`duplicate_of` as its only edge; the braid draws typed edges only when a run clears this bar. Next attempt is not a
prompt: it is giving the model the issue, the review thread and the timing — the signals a human uses to call a pair
"competing" — and asking fewer questions.

## R8-H3 — v4 is the same model with two questions removed

The same-day v3 control, scored on the eight surviving questions, moves −0.015 (jev) and −0.004 (pplx-flash) from run
5's numbers — the drift-free movement of the questions that stay, and jev's is `weakens_tests` (−0.039). v4 itself is
indistinguishable from the control on those eight: **+0.001** and **+0.000** (pplx-flash to four decimals — consistent
with the research note's reading that the hosted endpoint now answers each question on its own). Mean v4 AUC: 0.720 /
0.703. The `message-mismatch` rule is gone from the shipped policy (CHANGELOG, 2026-10-25); nothing else changes.

## R8-H5 — the drift monitor would have caught run 7

`mergeone drift-replay` (Go, the gateway's own code path) over the run-5 and run-7 predictions flags exactly
`follows_conventions` and `message_matches_diff` for pplx-flash (val and test), nothing for jev, nothing for the run-6A
self-hosted row (same weights, different serving: shift ≤ 0.077) — and no question with |AUC Δ| < 0.03 is flagged. The
slow signal (Python reference, `scripts/drift_slow.py`: AUC vs outcome on identical rows falls by > 0.05) flags jev
`tests_cover_change`, as the bar asks, and also three questions on the run-6A row — the quality side of run 6A's
"not equivalent" verdict, visible here for the first time. `drift.enabled` therefore defaults to **true** in the next
gateway release; the slow signal's Go implementation and `calibrate --from-gatelog` are the v0.3 items that remain.

## What run 8 changes

- Gateway: compact state for the pair call (v0.3); `drift.enabled: true` by default (v0.3); `message-mismatch` rule
  removed; nothing else.
- Benchmark: the same-day control rule is now proven to matter twice (H1 flipped from fail to pass; H2's control
  separated prompt from day). Run 9 is the serving row (`docs/research/inference.md`): same weights, FP8, the shared-A
  pair call, and R9-H5 — the hosted-endpoint check that would settle what run 7's "drift" was.
- Portal: the braid draws only `duplicate_of` edges until a relation question clears 0.80.
