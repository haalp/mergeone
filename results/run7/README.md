# Run 7 — compact state, prompt tuning, evidence floor, typed relation

Complete (2026-10-23/24). **Four hypotheses, four fails** — and one finding that was not a hypothesis: the hosted
decision model changed under an unchanged version string between run 5 and run 7, so two of the four comparisons
measured weeks of provider drift together with the change under test. The pre-registered verdicts stand; a same-day
control, made after them and labelled post-hoc, is reported beside each. Protocol: PROTOCOL.md §R7. Tables:
[`tables.md`](tables.md), [`relation_tables.md`](relation_tables.md); numbers: [`metrics.json`](metrics.json),
[`relation_metrics.json`](relation_metrics.json), `evidence_metrics_*.json`; tuning log: `data/run7/tuning_log.jsonl`,
frozen prompt: `data/run7/frozen.json` → `prompts/jev_v3_r2.json`, tag `r7-tuned`.

| | hypothesis | bar | result |
|---|---|---|---|
| R7-H1 | compact state (diff 2,000 chars, body 600, 20 files) loses ≤ 0.02 AUC vs the full state | mean v3 Δ ≥ −0.02 (jev and pplx-flash) and pair Δ ≥ −0.02 | ❌ pplx-flash v3 −0.049 vs run 5 · post-hoc same-day: +0.007 |
| R7-H2 | a tuned prompt beats the untuned one on test | mean v3 Δ ≥ +0.02, iteration eval drop ≤ 0.01 | ❌ −0.059 vs run 5 · same-day −0.008 |
| R7-H3 | structural evidence alone predicts review iteration | AUC ≥ 0.76 or +0.02 stacked | ❌ 0.48 alone, stacking hurts |
| R7-H4 | self-hosted registry row | — | deferred to run 8 |
| R7-H5 | a typed relation question separates duplicate from pointer | related-vs-unrelated ≥ 0.80 and same_change-vs-rest ≥ 0.80 | ❌ 0.73 / 0.69 (run-6 pair question: 0.70 / 0.67) |

## The finding first: the model moved, the version string did not

Run 5 (2026-10-18) and run 7 round 0 (2026-10-23) asked `pplx-decider-flash-v1.2` the same ten questions with the same
prompt on the same 1,491 validation PRs. Two questions answer differently now:

| question (pplx-flash, val, n 1,491) | mean probability run 5 → run 7 | AUC vs iterated run 5 → run 7 |
|---|---|---|
| `follows_conventions` | 0.26 → **0.82** | 0.660 → 0.430 |
| `message_matches_diff` | 0.67 → **0.93** | 0.525 → 0.423 |
| `needs_human_review` | 0.49 → 0.57 | 0.694 → 0.713 |
| other seven | within 0.16 | within 0.03 |

Jev answers as `jev-1.14.0` (runs 1–6: `jev-1.13.0`) and shifts less (mean v3 AUC on test 0.633 then, 0.617 now).
Nothing in the gateway changed between the two dates; the prompt sha256 is the same. A pinned version string from a
hosted provider is therefore not a pinned model, and every cross-run comparison in this benchmark is now understood to
carry that caveat. The practical consequence for the gateway is the one run 5 already recommended — recalibrate from
the gatelog, do not trust week-old calibration maps — and for the benchmark it is a rule for run 8: **any comparison
against an earlier run's numbers gets a same-day full-state control, pre-registered.**

## C1 — compact state

The v3 request carries the PR state once per question (ten times per call); the compact rendering cuts the diff to
2,000 characters, the body to 600, the file list to 20. Pre-registered comparison: compact today vs the full state as
run 5 (v3) and run 4 (pair) measured it.

| model | mean v3 AUC compact / full (run 5) | Δ [95 % CI] | pair AUC compact / full (run 4) | Δ | tokens per request, median |
|---|---|---|---|---|---|
| jev (1.14.0 / 1.13.0) | 0.631 / 0.633 | −0.002 [−0.013, 0.009] | 0.822 / 0.835 | −0.013 | 1,760 vs 2,529 (−30 %) |
| pplx-flash | 0.631 / 0.680 | **−0.049** [−0.077, −0.024] | 0.825 / 0.842 | −0.018 | 9,870 vs 16,980 (−42 %) |

**R7-H1 ❌** on pplx-flash. The −0.049 is `follows_conventions` (−0.348) and `message_matches_diff` (−0.103) — the two
questions that drifted — plus `tests_cover_change` (−0.111 on both models, the one loss that looks like the compaction
itself: the test files are what the 20-file cap drops first).

Post-hoc, same day, full state vs compact (not pre-registered): jev mean +0.014, pplx-flash +0.007, pair −0.015 on both
— all inside the bar. The pre-registered consequence of a fail is what applies: the full rendering stays the default,
`--compact` stays an option, and the token saving is reported, not used. Run 8 re-asks the question with its own
same-day control, pre-registered.

## C2 — prompt tuning

Three rounds on validation (400-PR tuning set, ~12 positives), each scored on the primary (`needs_human_review` →
review iteration), a held-out iteration eval (1,091 PRs) and the k8s revert eval (7,933 PRs). Rounds 3–5 were not run.

| round | edit | primary | iteration eval | revert eval | decision |
|---|---|---|---|---|---|
| 0 | untuned `jev_v3` | 0.695 | 0.719 | 0.675 | in force |
| 1 | `needs_human_review` rewritten as a list of concrete cues | 0.581 | 0.628 | 0.621 | rejected |
| 2 | round-0 text + one sentence on breadth | 0.698 | 0.702 | 0.677 | kept, frozen |

Round 1 made the model conservative — the share of PRs it sends to a human fell from 61 % to 19 % and the ranking
collapsed. Round 2's +0.003 is inside the noise of twelve positives, and the larger iteration eval went the other way.
The state length alone scores 0.669 on this label; the untuned question is a little better than size and no rewording
of it did more. On test the frozen prompt scores mean v3 0.621 against run 5's untuned 0.680 (**R7-H2 ❌**, −0.059);
against the same-day untuned control it is −0.008, and `needs_human_review` itself −0.037. The untuned prompt stays
default; `jev_v3_r2` is not shipped.

## C3 — structural evidence floor

A logistic model over seven frozen structural features (tests touched, e2e/property tests added, a test log or CI
list in the body, evidence attached, number of test files, test-line share; no model call), fit on validation and
frozen before test. On review iteration it scores **0.484** AUC on test (0.678 on val); stacked on each model's
`needs_human_review` it subtracts 0.10–0.17. On rejection it is 0.494 and stacking is ±0.01. **R7-H3 ❌.** The free
`evidence:` policy block does not ship. One detail is worth keeping: two single features score above the fitted
combination on test — `n_test_files` 0.731 and `body_has_test_log` 0.707 — so the val fit learned weights that do not
transfer rather than the features carrying nothing; still below the 0.76 bar, and not the pre-registered model.

## C5 — typed relation on the 6B pairs

Four binaries (`same_change`, `same_goal`, `same_branch`, `builds_on`) on the 100 adjudicated hold pairs (46 duplicate,
28 backport, 10 same-area, 7 competing, 7 follow-up, 2 unrelated), mapped to a class by a frozen rule.

| model | same_change vs rest AUC | run-6 pair question | class accuracy | $/PR at k = 20 |
|---|---|---|---|---|
| pplx-flash | 0.728 | 0.697 | 0.32 [0.24, 0.42] | $0.0016 |
| jev | 0.685 | 0.673 | 0.08 [0.04, 0.15] | $0.0022 |

**R7-H5 ❌.** Related-vs-unrelated is not reportable (two unrelated pairs; the sample is drawn from holds). The typed
question is only +0.03 / +0.01 over the single pair question on same-change, and the class accuracy fails for a reason
the confusion matrices make plain: the rule separates duplicate from backport by the model's `same_branch` answer, and
neither model can tell branches (pplx-flash calls 26 of 27 backports duplicate; jev calls 42 of 46 duplicates backport).
The base branch is metadata the gateway already has. A post-hoc reading with that fact substituted is in `tables.md`;
the design lesson is that the relation question must ask only what the diff can answer — *is this the same change,
does one build on the other* — and let the gateway supply branch, author and timing.

## What run 7 changes

- Gateway: nothing ships from this run — full rendering and the untuned prompt stay the defaults; `--compact` and `jev_v3_r2` remain selectable.
- Benchmark: same-day controls become part of every cross-run comparison (run 8); the relation question is redesigned
  with branch as an input, not a question; the hold → human/dedup rule from 6B is unchanged by anything here.
- Spend: ≈ $9 Perplexity (about $5 of it on a 13,600-row pair call the report never needed), Jev on credit, no GPU.
  Dated deviations and the two tooling errors: CHANGELOG.md "Run 7 complete".
