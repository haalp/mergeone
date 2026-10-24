# Run 8 — draft addendum (becomes PROTOCOL.md §R8 when frozen)

Draft, 2026-10-25. Nothing here is binding until it is committed as §R8; no run-8 model call before that commit.
Numbers this draft rests on: `docs/run8-numbers.md` (routing, v4, drift — from existing predictions, no model call),
`results/run8/relation_tables.md` (run 7's relation answers re-scored through the metadata rule: the baseline),
`docs/drift-monitor.md` (the spec the replay tests). Commands: `docs/run8-tooling.md`.

## R8.1 Why

Run 7 missed four bars and found that two of its four comparisons were measuring provider drift (pplx-flash changed
under `v1.2`; Jev 1.13 → 1.14). Run 8 therefore (a) pairs every comparison with a same-day full-state control, (b)
asks the model only what the diff can answer and hands it the rest, (c) drops the two questions that drifted and
were never used by a shipped rule, and (d) adds the measurement that would have caught run 7's drift. One item from
the run-7 ideas list is **not** in run 8 because the preparation already answered it: routing each question to its
better model gains +0.003 [−0.008, +0.016] when the choice is made on validation (the pre-registerable version) and
+0.022 only when chosen on test; it is reported in `docs/run8-numbers.md` as a negative pre-result and not run.

## R8.2 Components

**C1 — compact state, with its control.** Treatment: `--compact` (diff 2,000 chars, body 600, 20 files) for the v4
call on the run-3 test split and for the pair call on the run-4 test pairs; control: the same calls, full state, same
day, same directory. Models `jev`, `pplx-flash`. Run 7's post-hoc reading (+0.014 / +0.007 v3, −0.015 pair, −30 % /
−42 % tokens) is the prior; this is the pre-registered version of it.

**C2 — relation v2.** `relation2` asks three binaries (`same_change`, `builds_on`, `same_goal`) and the class rule takes
`same_base` from metadata (base branch of A = base branch of B; dataset then PR memory). Scored on the 100 adjudicated
6B pairs against the gold sub-classes. Baseline, on record before any call (`results/run8/relation_metrics.json`): run
7's four-binary answers through the same metadata rule score **0.722** (pplx-flash) / **0.740** (jev) class accuracy,
with competing 0/7 and same-area 0/10 for both. Control: run 7's `relation` question asked again the same day, scored
through the same rule (separates "new question" from "new day").

**C3 — v4 question set (product change, measured, no bar).** v3 minus `follows_conventions` and `message_matches_diff`.
Not a hypothesis: the mean rises mechanically (+0.085 to +0.100 on test) because the dropped questions score below
the rest. What is measured and reported: per-question AUC of the eight on the same-day control vs run 5, so the
drift-free movement of each surviving question is on record. Policy consequence, dated in CHANGELOG at freeze: the
shipped `message-mismatch` rule (reads `message_matches_diff`) is removed from the template and `policy.yaml`; under
the drifted model it already never fires (mean 0.93), and under v4 a missing question is a non-match, so keeping it
would be a rule that silently does nothing. `docs/POLICY.md` says so.

**C4 — one more registry row, self-hosted.** The run-7 deferral: a 26B-MoE / ~4B-active open-weight decider on one
L4 through the run-6A harness (`serve/`, `docs/run6-gpu.md`), v4 + pair on the test splits, `loadtest` at ×1 / ×10 /
×30. Same bars as R6A-H3/H4 (quality within the CI of the hosted row it is compared with; ×10 clean). Candidate named
at freeze; weights must be Apache-2.0 or equivalent.

**C5 — drift replay.** The monitor in `docs/drift-monitor.md` (fast signal: raw-mean shift > 0.20 or spread halved,
n ≥ 200; slow signal: AUC vs outcome fell > 0.05) replayed over the run-5 and run-7 predictions and the run-6A
self-hosted row, through the Go code path once it exists (Python reference in `scripts/run8_prep.py` until then).

## R8.3 Pre-registered criteria (test split; same-day control is the comparison in every case)

- **R8-H1 (compact):** compact − control ≥ −0.02 mean v4 AUC for `jev` and `pplx-flash`, and pair AUC ≥ −0.02 for both.
  Pass → `--compact` becomes the default for the **pair** call (the one made k times per PR), v4 stays full. Fail →
  reported; nothing changes.
- **R8-H2 (relation v2):** class accuracy ≥ **0.80** for at least one model (baseline 0.722 / 0.740) **and** competing +
  same-area combined accuracy ≥ **0.40** (baseline 0 / 17) **and** same_change-vs-rest AUC not below the run-7 number
  for that model (0.728 / 0.685) by more than 0.02. Pass → the relation question ships opt-in with `on_related`
  (cross-link, no hold) and `on_competing` (route to a human) in the policy; a hold still never closes a PR. Fail →
  reported; `duplicate_of` stays the only edge.
- **R8-H3 (v4):** no bar; reported.
- **R8-H4 (registry row):** R6A-H3 and R6A-H4 as written, against the hosted `pplx-flash` same-day control.
- **R8-H5 (drift replay):** the fast signal flags exactly `follows_conventions` and `message_matches_diff` for
  pplx-flash between run 5 and run 7 and nothing on jev or on the run-6A row; the slow signal flags jev
  `tests_cover_change`; no question with |AUC delta| < 0.03 on identical rows is flagged by the fast signal. Pass →
  `drift.enabled` defaults to true in the next gateway release. Fail → the thresholds are reported and the flag stays
  off.

## R8.4 What is not allowed

- No comparison against an earlier run's number without the same-day control beside it.
- No edit to `prompts/jev_v4.json`, `prompts/jev_relation2.json` or the relation2 class rule after the freeze commit.
- No threshold in the drift monitor moved after the freeze; 0.20 / halved spread / 0.05 are the numbers.
- No tuning rounds. Run 7's tuning log stands as the result on that question.
- No haiku calls (cost); no run on the validation split except C4's calibration fit, as R6A.

## R8.5 Budget and order

Perplexity ≈ $4 (pair compact + control ≈ $2.6, v4 + control ≈ $0.6, relation2 + control ≈ $0.4), Jev on credit,
one L4 ≈ 2 h ≈ $1.2. Order: freeze → C5 replay (no key) → C3/C1 controls and treatments (same day, one script) →
C2 → C4 → report (`mergeone.eval.run8_report`, to be written from the run-7 builder before freeze).

## R8.6 Open before freeze

1. C4 candidate model and its licence.
2. Whether C2's control (re-asking the run-7 question) is worth $0.2 or whether the run-7 answers suffice as baseline
   — draft says run it; it is the difference between "new question" and "new day".
3. The relation2 `LOW` threshold (0.25) is the one constant not derived from a run.
