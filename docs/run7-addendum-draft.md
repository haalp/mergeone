# Run 7 — protocol addendum, DRAFT (not pre-registered until moved into PROTOCOL.md and committed)

Status: draft, 2026-10-21. Nothing here binds anything. Candidates come from ROADMAP.md; each becomes a hypothesis only when
this text is copied into PROTOCOL.md as §R7 and committed **before** any model call for run 7. Run 6B (hold precision) is
reported first; its product consequence (hold vs annotate for dedup) is decided by R6B-H2, not here.

## R7.1 Why

Runs 1–6 leave four levers untested that are cheap to measure and were raised by the results themselves:

1. **Tokens per PR are the only single-box throughput lever** (run 6A pass 2: compute per request, batching flat). The diff
   budget is 16k tokens; nobody has measured what 4k costs in quality.
2. **Prompt tuning on a small labelled set** is reported elsewhere to lift small decision models most; run 5's val-chosen
   pruning dropped the best test question, so the risk is real and the procedure needs a freeze rule.
3. **Test evidence as a structural feature**: run 5's rules floor sat at AUC 0.73 vs 0.76 for the models on the
   review-iteration target; a modelless "evidence attached" feature set may move the floor and costs nothing at runtime.
4. **One more self-hosted row** that fits one L4 (run 6A: pplx-flash saturates at ×10 on an L4; a smaller model may not).

## R7.2 Components

**C1 — diff budget.** The run-5 v3 question set and the pair question re-asked on the **test** split only, with the rendered
diff cut at 4k tokens instead of 16k (same cut rule, same prompt otherwise). Models: `jev`, `pplx-flash`. Everything else
frozen. Cost per PR and tokens per PR recorded.

**C2 — prompt tuning, frozen before test.** Up to **5** rounds of prompt edits for `pplx-flash` on the **validation** split
only (the run-5 v3 questions; a tuning set of at most 400 labelled val PRs, drawn once with seed 7 and committed before
round 1). Each round's prompt is committed with its val AUC. A round is kept only if (a) val AUC on the primary target rises
and (b) the frozen **revert/iteration eval** (run-1 revert labels on the k8s val slice + run-5 ≥2-round labels on val) does
not drop by more than 0.01. The prompt in force after round 5 (or earlier if two consecutive rounds are rejected) is
tagged `r7-tuned` and run **once** on test. The untuned prompt's test numbers are run 5's; they are not re-run.

**C3 — structural evidence features.** Deterministic, no model: touches test files; adds E2E/property/integration tests
(path and framework patterns, listed in the code before run); has a test log, coverage line or "how tested" section in
the PR body; CI check names present in the body; diff-only vs evidence-attached flag. Fit a logistic model on val only,
weights frozen, scored on test against the run-5 review-iteration target and the run-3 rejection target.

**C4 — registry row.** One open-weight decision model that fits one L4 at ≤ 24 GB (candidate: the 4B `kev` family),
served with the run-6A harness unchanged, scored on the run-5 v3 test questions and the pair question, and load-tested
at ×1, ×10, ×30 with the run-6A client. A hosted decision API row (one of the two in ROADMAP) is optional and
only if its pricing is public on the day.

**C5 — typed relation question and `related` annotations.** Run 6B's labels say a hold is almost never a false
alarm but is often a *pointer* rather than a *duplicate* (follow-up, competing fix, backport/promote, same area).
The pair question only answers `same_change`. C5 adds a typed relation question for a (PR, candidate) pair —
`relation ∈ {same_change, follow_up, competing_fix, backport, same_area, unrelated}` (one multi-class answer, or
four binary questions `same_change`, `same_goal`, `same_branch`, `builds_on`; the prompt decides, frozen before
test) — asked of `pplx-flash` and `jev` on the 100 run-6B pairs (human final labels + notes as ground truth; the
adjudicated file, when it exists, is the primary). Gateway side, behind `memory.enabled`: `gate_similar` and the
verdict carry `relation`; the policy gets `on_duplicate` (hold → dedup, as today), `on_related` (pass + one
idempotent cross-link comment on both PRs and a `mergeone:related` label, no hold) and `on_competing` (hold →
human). Off by default until R7-H5 passes.

## R7.3 Pre-registered criteria (test split; bars written before any R7 call)

- **R7-H1 (budget):** at 4k the mean AUC over the v3 questions drops by **≤ 0.02** for both `jev` and `pplx-flash`, and the
  pair AUC by ≤ 0.02. Pass → 4k becomes the gateway default (`render.max_tokens`). Fail → 16k stays; the tokens-per-PR
  saving is reported, not used.
- **R7-H2 (tuning):** `r7-tuned` beats the untuned prompt on test by **≥ +0.02** mean AUC on the v3 questions **and** does
  not lose more than 0.01 on the test revert/iteration eval. Pass → the tuned prompt ships as `v3-tuned`, untuned stays
  selectable. Fail → reported; untuned stays default. Any edit to the prompt after the `r7-tuned` tag is a protocol break.
- **R7-H3 (evidence floor):** the structural feature model scores **≥ 0.76** AUC on the review-iteration target (the models'
  run-5 number) **or** adds ≥ +0.02 when stacked on `needs_human_review`. Pass → ships as a free `evidence:` block in the
  policy. Fail → reported.
- **R7-H5 (relation):** on the 100 run-6B pairs, `related` (any non-unrelated class) vs `unrelated` ROC-AUC
  **≥ 0.80** for at least one model, and `same_change` vs the rest (the dedup decision) ≥ **0.80**; sub-class
  accuracy (follow_up / competing_fix / backport / same_area) reported with Wilson intervals, not a bar (n is
  small). Pass → `on_related` annotations ship opt-in with the relation shown; `on_competing` routes to a human.
  Fail → `relation` is reported only; the gateway keeps the two-way `duplicate_of` rule. Cost per PR at k = 20 is
  reported next to R6B-H3's bar.
- **R7-H4 (row):** the new self-hosted row is within **0.03** AUC of hosted `pplx-flash` on the v3 test questions and
  carries **×10 with 0 errors** on one L4. Pass → row in `models.yaml` marked `self-host: L4`. Fail → row stays with its
  numbers; no default changes.

## R7.4 What is not allowed

No test-split call before the C2 prompt is tagged. No change to calibration maps, thresholds, the 0.7 dedup threshold or
retrieval weights. No re-running the untuned prompt on test (run 5's numbers stand). The tuning set is drawn once; no PR
from test appears in any prompt, example or note. C3's pattern lists are committed before scoring.

## R7.5 Budget and order

Budget: ≈ $26 (`jev` ≈ $4, `pplx-flash` ≈ $3, C5 ≈ $1, `haiku` continuity ≈ $6 if run, L4 ≈ $3, hosted row ≈ $5), cap $45.
Order: 1. commit this as §R7. 2. C3 pattern lists + tuning set + C2 round log (val only). 3. `r7-tuned` tag.
4. C1, C2 test calls, C3 scoring, C5 on the 6B pairs (after the adjudication round). 5. C4 on RunPod. 6. Report; CHANGELOG timestamps.

## Open decisions (owner: Hasanalp)

- Include C4 now or defer to run 8 (saves ≈ $8 and a GPU session)?
- Primary target for R7-H2: review-iteration (run-5 H) or the v3 judge labels? (Draft says mean v3 AUC.)
- Keep `haiku` continuity rows in C1 (≈ $6)? Draft says no.
