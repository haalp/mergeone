# Pre-registered experiment protocol

**Status:** frozen before any model was run. Changes after this point are recorded in `CHANGELOG.md` with a reason; the evaluation thresholds below are not changed after results are seen.

**Project:** mergeone (experiment: gatebench)
**Authors:** Hasan Alp Temiz (with Claude as coding assistant)
**Date frozen:** 2026-10-13

## 1. Motivation

Anthropic's engineering post [*Redesigning source control at Anthropic*](https://www.anthropic.com/engineering/redesigning-source-control) describes a monorepo that went from ~2,500 to >30,000 merges per day in four months, driven almost entirely by coding agents. At that scale they dropped the merge queue, moved to post-merge and pre-deploy testing, and rely on fast automated bisect/revert pipelines. Every one of those pipelines contains small, repeated, typed decisions: *is this change risky, does it touch sensitive code, does this failure look like a flake, should a human look at it.*

Anthropic also states that Scion, their version control system, will not be offered externally, and invites others to apply its ideas on top of git.

This experiment asks one narrow question about that decision layer:

> **At agent-scale merge volume, how well do "System One" decision models (TypeSafe AI's Jev) perform, relative to a fast LLM (Claude Haiku 5.5) and classical ML, on source-control decisions, measured by accuracy, calibration, latency and cost?**

It is not a test of Scion (which is not available) and not a test of Jev's vendor benchmarks. All numbers reported come from this harness.

## 2. Dataset

- **Repository:** `kubernetes/kubernetes`, `master` branch, read from git history only (no GitHub API). Chosen for: very high PR merge volume, strong CI, explicit revert convention (`Revert "..."` PR titles referencing the original PR number), directory-level ownership (OWNERS files) that mirrors Scion's directory-scoped permissions.
- **Unit of analysis:** one merged pull request = one merge commit on `master` whose message matches `Merge pull request #N`.
- **Time window:** the most recent N merge commits that fit in a bounded `git fetch --depth` (target: ≥4,000 merges). Exact SHAs and the window boundaries are written to `data/manifest.json` and committed.
- **Train/validation/test split:** strictly chronological. Oldest 60% train, next 20% validation (threshold selection, GBM tuning), newest 20% test. No model sees test labels before final scoring. LLM-based models receive no training data at all; they are zero-shot, with the same frozen prompt for every example.
- **Leakage rule:** every feature used to describe PR *P* must be computable from information that existed at *P*'s merge time: the diff, the merge message, the files touched, the history *before* P. Nothing after P's merge timestamp, including the revert that labels it, may enter the state.

## 3. Decisions

### 3.1 Primary decision (all headline numbers)

**`will_be_reverted`** — will this merged PR be reverted on `master` within 30 days?

Label construction: a later merge commit on `master` whose PR title starts with `Revert` and whose message or body references `#N` (the original PR number) or the original merge SHA, within 30 days of P. Reverts of reverts are excluded from the positive class. The last 30 days of the window are dropped from the test set so every example has a complete label horizon.

Known label noise: a revert is not always a defect (some are policy reversals, some are "revert to re-land"). We hand-check a random sample of 50 positives and 50 negatives and report the observed label precision in the results. We do not relabel.

### 3.2 Secondary decisions (reported, not used for thresholds)

These exist to measure the cost of asking several questions at once, which is the specific property System One models claim.

- **`touches_sensitive_area`** — does the PR modify any path under a pre-declared list of security- or API-critical directories (e.g. `pkg/apis/`, `staging/src/k8s.io/apiserver/`, `pkg/kubelet/`, `plugin/pkg/auth/`)? Ground truth is mechanical (path match); the model only sees the diff header and must judge it. This measures whether a model can reproduce a rule from unstructured text, which is a proxy for the "typed guardrail" use case.
- **`large_blast_radius`** — does the PR touch more than 10 files across more than 3 top-level directories? Also mechanical ground truth.

Secondary decisions are asked in the *same* request as the primary one for models that support it (Jev: multiple typed questions in one call; Haiku: one JSON schema with three fields). Per-request cost and latency are recorded with and without the secondary questions.

## 4. Systems under test

| ID | System | Role | Notes |
|---|---|---|---|
| `rules` | Hand-written heuristics on structural features | Floor | Lines changed, files touched, directories, "WIP"/"fix" keywords |
| `gbm` | Gradient boosted trees (LightGBM) on structural features | Serious classical baseline | Tuned on validation split only |
| `haiku` | Claude Haiku 5.5, `claude-haiku-5-5`, structured JSON output, thinking off (effort minimum) | Fast LLM | Prompt frozen in `prompts/haiku_v1.md` |
| `jev` | TypeSafe Jev, pinned version recorded at first call | System One model | Questions frozen in `prompts/jev_v1.json` |
| `cascade` | `jev` first; if `jev` confidence on the primary decision is below threshold *t*, escalate to `haiku` | Tiered | *t* chosen on validation to hit the escalation budget in §6 |

Frontier models (Opus, Sonnet, Fable) are **not** candidates: the question is about cheap decision layers. They may be used for label auditing only, and that use is reported separately.

All LLM and Jev inputs are the same rendered state: PR title, merge message body, list of changed files with per-file insertions/deletions, and the first 4,000 characters of the unified diff. Numeric features are additionally rendered as words for `jev` (which handles words better than numbers per vendor documentation); whether this rendering is used is an ablation, not a hidden choice.

## 5. Metrics

Primary decision, test split:

- **PR-AUC** (positives are rare; ROC-AUC is reported but not headline).
- **Recall at 5% false-positive rate** (`R@FPR5`) — the operating point a merge pipeline would actually use.
- **Calibration:** Expected Calibration Error (15 equal-mass bins) and Brier score. Reliability diagrams are saved per model.
- **Latency:** p50 and p99 wall-clock per decision, measured from Istanbul with 8 concurrent requests. Reported separately for primary-only and primary+secondary requests.
- **Cost:** USD per 1,000 decisions, computed from the provider's list price at run date and the measured token counts. Prices and date are recorded in `results/prices.json`.
- **Cascade:** fraction of decisions escalated to `haiku`, cascade PR-AUC and R@FPR5, cascade cost per 1,000.
- **Throughput stress (agent-scale proxy):** replay the test-split merge stream at 10× and 100× its historical rate; report p99 latency and any error/rate-limit rate per system. This is the only part of the experiment that speaks to "scale" rather than "accuracy", and it is labelled as such.

Secondary decisions: accuracy and ECE only.

## 6. Pre-registered success criteria

Written before any model call. These define what we will *say*, not what we hope.

- **H1 (usefulness):** A decision layer is "useful" for this task if, on the test split, it reaches `R@FPR5 ≥ 0.30` and `PR-AUC` at least 2× the positive base rate. If no system meets this, the headline finding is "revert prediction from pre-merge state is not solvable at this bar with these inputs", and the comparison is reported as secondary.
- **H2 (System One vs fast LLM):** `jev` is "competitive" with `haiku` if its PR-AUC is within 0.05 of `haiku`'s and its ECE is lower. It is "better" only if PR-AUC is higher *and* ECE is lower.
- **H3 (vs classical ML):** If `gbm` matches or beats both `jev` and `haiku` on PR-AUC, we report that the structural features carry most of the signal and that the LLM/System One layer did not add accuracy. This is a legitimate and expected outcome.
- **H4 (cascade):** The cascade is "worthwhile" if, with ≤25% of decisions escalated, it keeps PR-AUC within 0.02 of `haiku` alone while costing ≤40% of `haiku` alone.
- **H5 (scale):** A system "holds at agent scale" (within this proxy) if p99 latency at 100× replay is ≤2× its p99 at 1× and the error rate stays below 1%.

Thresholds for `cascade` and for `R@FPR5` are selected on the validation split and frozen before test scoring.

## 7. What this experiment cannot show

- Kubernetes history is human-written. Agent-authored changes may have a different revert profile. We make no claim about agent-authored code.
- A revert label is a noisy proxy for "bad merge".
- Jev is a closed API in early access; pricing may be subsidised (the vendor says it cannot prove otherwise) and may change. We record the price on the run date.
- Latency is measured from one location against hosted APIs; it includes network, not only inference.
- Scion itself is not tested. We only reproduce its *query pattern* (ask the server, don't clone) by computing state from history queries rather than a working copy.

## 8. Reproducibility

- `data/manifest.json`: repo, SHA range, split boundaries, label rules, sensitive-path list.
- `prompts/`: frozen prompts/question sets, versioned.
- `results/<run_id>/`: raw per-example predictions for every system, token counts, timings, prices, environment info.
- Model versions pinned: `claude-haiku-5-5`; Jev version string recorded from the first response.
- Random seeds fixed where applicable (GBM, sampling for hand-check).
- One command reproduces the tables and figures from the raw predictions: `python -m mergeone.report results/<run_id>`.

## 9. Publication commitment

Results are published regardless of direction (positive, negative, mixed), with the hand-check sample, the raw predictions, and this protocol unchanged.

---

# Run 2 addendum: semantic typed triage

**Status:** written 2026-10-15, after run1 results were seen (see CHANGELOG). Run1 thresholds are untouched. Run2 asks a different question and has its own pre-registered criteria below, written before any run2 model call.

## R2.1 Motivation

Run1 showed that no system predicts 30-day reverts from pre-merge state (H1 failed for all; GBM ≈ Jev ≈ Haiku ≈ base rate × 1.4). It also showed that on *typed rule-like* questions Jev was far more accurate than Haiku (97–98% vs 87–93%) at 3× lower latency and 4× lower cost, and held 3M decisions/day with 0% errors where Haiku hit account rate limits. But run1's rule-like questions were mechanical (path prefix, file count), so a regex would do.

Run2 tests the claim that matters for a merge gateway: **can a System One model answer rule-*like* questions that cannot be written as rules, accurately and calibrated, at agent-scale volume?**

## R2.2 Decisions (all `noul`, asked in one request)

| name | question |
|---|---|
| `is_logic_change` | Does the PR change program behaviour (as opposed to only docs, comments, formatting, renames, tests, generated files, or dependency bumps)? |
| `weakens_tests` | Does the PR remove tests, add skips/`t.Skip`/`Pending`, loosen assertions, raise timeouts, or mark tests flaky? |
| `message_matches_diff` | Does the title + message accurately describe what the diff does (no undisclosed changes, no claims the diff doesn't contain)? |
| `touches_concurrency` | Does the diff touch code with concurrency hazards (locks, goroutines, channels, caches, retries, shared mutable state)? |
| `needs_human_review` | Would a careful maintainer want a human to look at this before it merges (a *judgement* question that aggregates the above)? |

`needs_human_review` is the gateway's routing output; the other four are its inputs and are also what a policy file can reference.

## R2.3 Ground truth

No mechanical labels exist. Labels come from an **LLM judge** (Claude Sonnet 5.5, adaptive thinking, full diff up to 40,000 chars, frozen prompt `prompts/judge_v1.md`) over a stratified sample: 1,200 test PRs and 800 val PRs. The judge is a *stronger, slower, more-context* model than any candidate, and sees 10× more diff than candidates do (candidates still see 4,000 chars, as in run1).

Judge reliability is estimated two ways and reported: (a) a 60-PR subset judged twice with identical settings (the 5.5 API rejects temperature; agreement rate measures sampling noise), (b) a 60-PR subset labelled by hand by the author, blind to the judge (judge-vs-human agreement). If judge-vs-human agreement on a question is below 80%, that question is reported as "label unreliable" and excluded from the headline.

The judge is never a candidate. Haiku 5.5 remains a candidate; Jev remains a candidate. Opus/Fable are not used.

## R2.4 Calibration

Each candidate's raw probability is calibrated on the val sample with isotonic regression (per question) and the fitted map applied to test. Report both raw and calibrated ECE/Brier. The gateway ships with the calibration map, so the calibrated numbers are what a user would see.

## R2.5 Metrics and pre-registered criteria

Per question, on test: accuracy at calibrated 0.5, ROC-AUC, PR-AUC, ECE raw and calibrated, Brier. Plus per-request latency p50/p99 and USD/1k with all five questions in one call.

- **R2-H1 (typed triage is solvable):** a candidate is "useful" on a question if ROC-AUC ≥ 0.80 and calibrated ECE ≤ 0.05. Reported per question.
- **R2-H2 (System One vs fast LLM):** Jev is "competitive" with Haiku on a question if ROC-AUC is within 0.03 and calibrated ECE is not worse by more than 0.02. "Better" if ROC-AUC higher *and* calibrated ECE lower.
- **R2-H3 (cost of asking five at once):** Jev's five-question request costs ≤ 1.3× its one-question request in latency p50 and in USD. (Vendor claim: parallel questions are nearly free.)
- **R2-H4 (routing quality):** using `needs_human_review` as the gate, at the operating point where the gateway escalates 25% of PRs to humans, the gate captures ≥ 70% of the judge's `needs_human_review = true` PRs.

## R2.6 Limitations added

The ground truth is a model's judgement, not an outcome. Run2 therefore measures *agreement with a careful reviewer*, not correctness in the world. The hand-labelled subset bounds how far the judge can be trusted.

---

# Run 3 addendum: agent-authored PRs, outcome labels

**Status:** written 2026-10-15 after run2 results, before any run3 data was fetched or any model call. Runs 1 and 2 are untouched.

## R3.1 Why

Runs 1–2 used human-written Kubernetes PRs. The product's claim is about agent-written PRs at volume. Run 3 tests the gate on real agent PRs from public GitHub, with labels that are **outcomes**, not a judge's opinion.

## R3.2 Data

- Sources: public repositories with ≥ 500 stars, not forks or archived, with ≥ 20 PRs authored by a recognised coding agent (Copilot coding agent, Claude Code trailer, Claude app, Devin, Codex, Cursor; list in `mergeone/data/agent_prs.py`). Discovery via the GitHub Search API; the exact repo list and counts are committed to `data/run3/repos.json`.
- Per repo: up to 150 agent PRs, balanced between merged and closed-unmerged where available. Per PR: title, body, files, diff head (4,000 chars, as in runs 1–2), human review/comment counts, change-requested flag, force-push count, GitHub stack metadata if present, and revert-within-30-days.
- Splits: by repository (no repo in both train/val and test) so calibration cannot memorise a project's style. 70% of repos → val (threshold/calibration), 30% → test.

## R3.3 Labels and the questions they test

| outcome label | what it says about | gate signal it tests |
|---|---|---|
| `closed_unmerged` | humans rejected or abandoned the change | `needs_human_review` should be high; `pass` should be rare |
| `change_requested` | a human asked for changes before merge | `needs_human_review`, `message_matches_diff` |
| `human_review_comments ≥ 3` | the change needed discussion | `needs_human_review` |
| `force_pushes ≥ 2` | agent iterated under review pressure | `is_logic_change`, `needs_human_review` |
| `reverted_within_30d` | merged then undone | all; expected weak (run 1) |
| `merged ∧ human_review_comments = 0` | merged untouched | `pass` should be common |

## R3.4 Pre-registered criteria

- **R3-H1 (gate separates rejected from accepted):** on test repos, ROC-AUC of `needs_human_review` (calibrated on val repos) for predicting `closed_unmerged ∨ change_requested` ≥ 0.70. Below 0.60 we report the gate as not transferring to agent PRs.
- **R3-H2 (pass is safe):** among PRs the default policy marks `pass`, the rate of `closed_unmerged ∨ change_requested ∨ reverted_within_30d` is ≤ half the dataset's base rate.
- **R3-H3 (hold has precision):** among PRs marked `hold` or `block`, that same rate is ≥ 1.5× the base rate.
- **R3-H4 (stacks):** for PRs with stack metadata, a stack-level rule (block below ⇒ hold above) changes verdicts for ≥ 5% of stacked PRs; reported, no threshold on correctness because labels for stack ordering are not available.
- **R3-H5 (volume):** the full test set is processed by one `mergeone serve` instance in a single open-loop replay at 100× its historical rate with p99 ≤ model p99 + 200 ms and 0 errors.

Outcome labels are noisy in a known direction: a PR can be closed for reasons unrelated to quality (duplicate, superseded, project direction). We will classify close reasons on a sample with Claude Fable 5.1 **for reporting only**, never as a label or as a candidate, and state that in the results.

## R3.5 Candidates

`jev` and `haiku`, question set v2, calibration refit on val repos. `rules` as floor. No frontier model.

# Run 4 addendum: PR memory and duplicate detection

**Status:** written 2026-10-17 after run3 results were published (`results/run3/README.md`), before any
indexing code, retrieval code, pair prompt or model call for run 4 existed. Runs 1–3 are untouched.

## R4.1 Why

Run 3 showed the gate cannot predict GitHub rejection of agent PRs (AUC 0.55) and showed why: in a
200-PR sample, 36% of closures were *superseded or duplicate*, 31% *direction*, 19% *quality*, 10% *stale*.
The gate reads one diff. It cannot see that another PR already did the work. Run 4 adds a component that
can — a per-repository PR memory and a typed pairwise question — and tests whether that closes the gap
the labels revealed. Direction remains a human decision (hold is the correct verdict); stale is a timer.

## R4.2 Data

- Repositories: the 26 run-3 repositories with the same val/test assignment (`data/run3/manifest.json`).
  **The run-3 test set has been seen in aggregate (run-3 tables). We state this; we do not reshuffle.**
  If time allows, up to 6 additional repositories discovered with the same rule are added as a *fresh*
  test slice and reported separately.
- PR memory per repository: every PR (any author) created in the 180 days ending at the newest PR in our
  sample for that repository — number, title, body, author, state, created/merged/closed timestamps,
  base/head refs, changed file paths with +/- counts. Fetched with the GitHub GraphQL API. No diffs.
- Human labels for "superseded": for each closed-unmerged PR in the run-3 dataset, the issue comments
  and review comments by non-bot authors are fetched. A PR is labelled `superseded_by = N` when a
  human comment matches a fixed regex (committed in `mergeone/data/supersede_labels.py` before fetching)
  of the form *duplicate of / superseded by / already fixed in / closing in favour of / replaced by* … `#N`,
  and `N` exists in the memory. A PR with a human comment that matches the regex but with no number is
  labelled `superseded_unknown` and excluded from pair metrics. Everything else is `not_marked`.
  These are the only labels; the Fable close-reason classifier from run 3 is not used.

## R4.3 Components under test

1. **Retrieval** (no model): for a PR, rank memory PRs created or merged within ±45 days by
   file-path Jaccard, title/body BM25 and same-author bonus; return top-k, k = 5. Deterministic.
2. **Pair question** `same_change(A, B)` asked once per (PR, candidate) with the same rendered state
   format as runs 1–3 for each side; answer type *choice* over {same, subset, related, unrelated};
   `duplicate_of = P(same) + 0.5·P(subset)`. Candidates: `jev`, `haiku`, question set v3 = v2 + pair.
   Prompts are committed before any call.
3. **Policy**: `duplicate_of >= 0.7 → hold, route: dedup`; `silent_days >= 14 → stale`.
   Everything else as run 3 (default policy, calibration as run 3).

## R4.4 Pre-registered criteria (test repositories)

- **R4-H1 (retrieval):** among PRs labelled `superseded_by = N`, N is in the top-5 candidates for
  ≥ 60% (recall@5). Below 40% the memory design is reported as not working.
- **R4-H2 (pair question):** ROC-AUC of `duplicate_of` ≥ 0.80 on pairs: positives = (PR, N) from
  `superseded_by`; negatives = (PR, top-5 candidates that are not N) for the same PRs plus an equal
  number of random same-repo pairs. Haiku and Jev each.
- **R4-H3 (verdict level, the one that matters):** with dedup and stale rules added, among PRs the policy
  marks `pass`, the rate of `closed_unmerged ∨ change_requested ∨ reverted` is ≤ 0.5 × the dataset base
  rate (run-3 R3-H2, unchanged). Reported for Jev and Haiku.
- **R4-H4 (coverage):** the share of closed-unmerged test PRs that receive a non-`pass` verdict rises
  from the run-3 value by ≥ 15 percentage points.
- **R4-H5 (cost):** retrieval adds ≤ 20 ms p99 per decision on the run-3 hardware; model calls per PR
  are ≤ 1 + k.

## R4.5 Candidates and what is not allowed

`jev` and `haiku` for the pair question; `rules` floor for retrieval-only (duplicate_of = retrieval score).
No frontier model as a candidate. No threshold or prompt is changed after the first test-split call;
changes before that are logged in CHANGELOG with timestamps.

### R4.6 Added candidate (dated amendment)

2026-10-17 00:14Z — before any run-4 model call (labels, retrieval and pairs had not been
built yet; memory fetch was still running). Perplexity released open-weight decision models on 2026-10-15/16
(`pplx-decider-v1.2`, 26B; `pplx-decider-flash-v1.2`, 4.5B; Apache-2.0). Their Decisions API is wire-compatible
with the System One schema used in runs 1–4, so the same question files apply unchanged. Both are added as
run-4 candidates for the pair question with the same bars (R4-H2). This amends R4.5; it adds candidates, it does
not change any hypothesis, threshold, prompt or label.

---

# Run 5 addendum — retrieval v2, declared assumptions, question set v3 (pre-registered)

**Status:** added 2026-10-17 23:07Z, after run-4 results were published and before any run-5 label,
candidate list or model call existed. Run 4 is not re-run; its tables stand. Everything below is
evaluated on the run-3/run-4 repositories and split (`data/run3/manifest.json`), whose **test set has been
seen in aggregate three times** (run-3 and run-4 tables, the run-4 leaderboard). We state this again and do
not reshuffle; no per-PR test prediction has been inspected.

Goal of the run, stated plainly: make the gateway better on the failure mode run 3 found (coordination)
and on the one outcome the questions were seen to track (review iteration), with every change measured.

## R5.1 Components and what each one is allowed to use

**A — Retrieval v2** (replaces run-4 R4.3-1; the pair question and `duplicate_of` are unchanged)

- **A1, metadata stage (no model, no network):** for a query PR, score every memory PR created or merged
  within ±90 days (run 4: ±45) by
  `w_issue·[share ≥ 1 issue/PR reference in title/body] + w_dir·jaccard(directories, depth 2) +
   w_file·jaccard(files) + w_text·bm25(title+body) + w_author·[same author]`
  and keep the top-20. Issue references are `#N`, `GH-N`, `owner/repo#N` and full issue/PR URLs in the text,
  excluding self-references. Weights are chosen **on the 22 val gold pairs only** by a grid over
  {0, 0.1, …, 0.6} per weight (sum = 1), maximising val recall@20 then val recall@5 as tie-break, and are
  frozen in `mergeone/memory/retrieval_v2.py` with the chosen values and the grid result **before** any
  test PR is scored. Val recall is reported alongside test; it is a tuned number and is labelled as such.
- **A2, content stage (network, no model):** for the top-20 of A1 only, the candidate's changed-line
  content is fetched once from the REST `pulls/{n}/files` endpoint (patch field, ≤ 300 files, first 4,000
  characters of patch per file) and cached under `data/run5/patches/`. The query PR's diff is the one already
  rendered in the dataset. `ident(P)` = set of identifiers matching `[A-Za-z_][A-Za-z0-9_]{2,}` on added or
  removed lines, minus a fixed stop-list of language keywords (committed with the code). Re-rank the top-20 by
  `0.5·A1_score_normalised + 0.5·jaccard(ident(query), ident(candidate))` → top-5. The 0.5/0.5 split is
  fixed here and not tuned. PRs whose patch cannot be fetched (deleted fork, >300 files, API error) keep
  their A1 rank and are counted.
- **Exploratory, reported, not a hypothesis:** the same re-rank with tree-sitter function-level overlap for
  the languages where a parser is available (Python, TypeScript/JavaScript, Go, C#, Java, Ruby), to answer
  whether symbol-level beats identifier-level. Not used in any verdict.
- **Latency:** the pre-registered number is the **Go store** (`gate/internal/memory`) implementing A1 on the
  same memory, measured as run 4 (p99 over the 2,196 PRs, run-3 hardware). A2 is a network stage and is
  excluded from the latency bar but its calls-per-PR are reported. Python stays the reference for scores
  and must agree with Go to 1e-6 on every candidate score.

**B — Declared assumptions** (v0.3)

- Agents in the dataset did not declare assumptions. Proxy, stated as such: for every PR, a list
  `[{kind ∈ {api, behaviour, data, config, dependency, test}, target, claim}]` is extracted **from the PR's
  own title and body text only — never the diff —** by Claude Haiku 5.5 with the frozen prompt
  `prompts/assumptions_v1.md` (temperature 0, JSON schema, max 8 items). This is a *component*, not a
  candidate: it stands in for what an agent would send in `gate_decide.assumptions`, and its quality is
  bounded by what the agent wrote in the description. PRs with an empty body get an empty list and are
  gated exactly as today (inference fallback).
- **Labels (human, regex, frozen before fetching):** comments by non-bot authors on **every** PR in the
  run-3 dataset (merged ones are fetched now; closed ones exist from run 4) are matched by a fixed regex in
  `mergeone/data/relation_labels.py` for *conflicts with / rebase on|after / depends on / blocked by /
  after #N lands|merges / needs #N first / wait for #N* … `#N`, giving `related_to = N` with
  `relation ∈ {conflicts, depends}`; `N` must exist in the memory. Supersedes from run 4 are a third
  relation and are not re-labelled. A match without a number is `relation_unknown` and excluded from pair
  metrics. Nothing else labels a relation.
- **Pair question** `assumption_conflict(A, B)` — choice over {conflict, depends, independent} given both
  PRs' rendered state **plus** both declared-assumption lists; `conflict_of = P(conflict) + 0.5·P(depends)`.
  Asked for every (PR, top-5 A2 candidate) pair, so it shares retrieval with A. Candidates: `jev`, `pplx`,
  `pplx-flash`, `haiku`. Questions in `prompts/jev_pair.json` / `prompts/haiku_pair.md` extended before
  any call. Floor: `conflict_of` = directory Jaccard only (no assumptions, no model).
- **Policy:** `conflict_of ≥ 0.7` on a candidate that is *open* → `hold, route: human, reason: conflicts
  with #N`; on a candidate already *merged* within 14 days → `hold, route: rebase`. Added below the dedup
  rule; everything else unchanged.

**C — Question set v3 and the review-iteration outcome**

- `v3 = v2 ∪ {exceeds_scope, breaking_change, tests_cover_change, hard_to_revert, follows_conventions}`,
  all `noul`, frozen in `prompts/jev_v3.json` / `prompts/haiku_v3.md` before any call. Ten questions per PR
  in one request.
- **Outcome:** `iterated` = force-pushes ≥ 2 after the PR was opened (run-3 label, test base rate ≈ 8%).
  It is the run-3 exploratory finding, now pre-registered. `rejected` is reported for continuity, not scored.
- **Calibration:** per question, isotonic on val repositories against `iterated`; the gate's
  `needs_human_review` keeps its run-3 calibration for H3/H4 comparability and gets a second, v3-calibrated
  copy for R5-H5.
- **Question pruning, val only:** a v3 question whose val marginal gain in a 10-question logistic stack
  (fit on val, 5-fold within val) is < 0.005 AUC is dropped *for the stack only*; all ten are still reported.
  Nothing is pruned on test.
- **Candidates:** `jev`, `pplx`, `pplx-flash`, `haiku`, `rules` (v2 heuristic floor + nothing for the new
  questions). **Load tests** at 1×/10×/100× for `pplx` and `pplx-flash` as run 3 (R3-H5 bar), so the
  leaderboard's scale column is measured for them.

## R5.2 Pre-registered criteria (test repositories unless stated)

- **R5-H1 (retrieval v2 recall):** recall@5 of the gold superseding PR ≥ **0.70** on the 29 run-4 test
  gold pairs (run 4: 0.55, bar was 0.60). Reported for A1-only and A1+A2 separately; the hypothesis is on
  A1+A2. Below 0.60 (the run-4 bar) v2 is reported as no improvement.
- **R5-H2 (retrieval latency):** Go A1 p99 ≤ **20 ms** over 2,196 PRs; A2 ≤ 20 patch fetches per PR.
- **R5-H3 (verdict level, dedup with v2 candidates):** run-4 R4-H3 and R4-H4 recomputed with the v2 top-5
  and the *same* run-4 pair predictions where the pair exists, new predictions only for new pairs, same
  models (jev, haiku; pplx and pplx-flash now also evaluable because run-5 C gives them gate verdicts).
  Bars unchanged: pass-group bad-outcome ≤ 0.5 × base; closed-PR coverage ≥ +15 pp over run 3.
- **R5-H4 (assumptions):** ROC-AUC of `conflict_of` ≥ **0.75** on pairs: positives = (PR, N) with
  `relation ∈ {conflicts, depends}`; negatives = the PR's other top-5 candidates plus an equal number of
  random same-repo, same-window pairs. For every candidate model, and for the floor. Additionally,
  **R5-H4b:** among labelled relations whose two PRs share **no** changed file, the share caught at
  `conflict_of ≥ 0.7` is ≥ **+15 pp** above the directory-Jaccard floor at the same false-positive rate on
  random pairs. H4b is the only number that says declared assumptions see something file overlap cannot.
- **R5-H5 (iteration):** calibrated `needs_human_review` (v3 calibration) → `iterated`: ROC-AUC ≥ **0.70**
  on test for at least one System One candidate; the 10-question stack ≥ **0.75**. Below 0.60 the run-3
  exploratory finding is reported as not replicating.
- **R5-H6 (scale, pplx):** R3-H5 bar for `pplx` and `pplx-flash` at 100×: p99 ≤ single-call p99 + 200 ms,
  error rate 0 (429s count as errors; the adapter's retry is disabled for the load test).

## R5.3 What is not allowed

No frontier model as a candidate. Haiku's role in B is extraction with a frozen prompt, reported as a
component; Haiku is also a pair-question candidate and the two uses are kept in separate files. No weight,
threshold, prompt or regex changes after the first test-split call in each component; A1 weights are fixed
from val before the first test retrieval and the grid result is committed. The run-4 test predictions are
reused only where the pair is identical (same A, same B); they are never re-scored. Negative results are
published as in runs 1–4.

## R5.4 Order of operations (so the "before" claims are checkable by commit time)

1. Commit this addendum. 2. Commit `relation_labels.py` regex and `assumptions_v1.md`, `jev_v3.json`,
`haiku_v3.md`, pair-question extensions. 3. Fetch merged-PR comments; build relation labels. 4. Fit A1
weights on val; commit the grid result; freeze. 5. Fetch A2 patches for the top-20 of every PR. 6. Build
candidates and pairs. 7. Extract assumptions for all PRs. 8. Predict (v3 questions; pair questions for new
pairs). 9. Load tests. 10. Report. Each step's timestamp lands in CHANGELOG.

---

# Run 6 addendum, part A — a self-hosted System One row (pre-registered)

**Status:** added 2026-10-18 14:19Z, after run-5 results were published and before any self-hosted call.
Nothing in runs 1–5 is re-run or re-scored.

## R6A.1 Why

Run 5 showed the two Perplexity models lead on quality in runs 4–5 and fail the 100× bar only because the
hosted API refuses calls above 10 rps per organisation. Their weights are Apache-2.0. The gateway's claim
is that a model is a `provider:` row; this part measures whether the same weights, served by us, clear the
scale bar the hosted endpoint cannot, and whether they still answer the same way.

## R6A.2 What is served, exactly

- Model: `perplexity-ai/pplx-decider-flash-v1.2`, BF16, revision pinned in `results/run6/serve_meta.json`.
- Inference code: the vendor's own `pplx_decider` package from the checkpoint's `source/` directory
  (`Decider.from_pretrained(..., fast=True)`, flash-linear-attention path), unmodified. No quantisation.
  The 27B model is out of scope for this part (does not fit one 24 GB GPU in BF16).
- Serving: a thin HTTP wrapper in `serve/systemone_server.py` that exposes `POST /v1/systemone` with the
  run 1–5 request/answer schema and batches concurrent requests through `decider.decide(list)` with a
  bounded micro-batch (≤ 32 requests or 20 ms, whichever first). The wrapper does no prompt, question or
  temperature changes; `decision_config.json`'s temperature is applied by the vendor code exactly once.
- Hardware: one NVIDIA L4 (24 GB) on a cloud VM (type and region recorded in `serve_meta.json`), the gate
  benchmark client on the same VM so the network leg is loopback. Nothing else runs on the GPU.
- Registry: the row is `pplx-flash-local` in `prompts/models.yaml` (dialect `systemone`, `${MERGEONE_LOCAL_ENDPOINT}`,
  no key, price 0 — compute is reported separately as $/1k PRs from the VM's hourly rate × measured throughput).

## R6A.3 Pre-registered criteria

- **R6A-H1 (equivalence):** on the run-5 **test** v3 predictions (705 PRs × 10 noul questions, joint request
  as run 5) the self-hosted answers agree with the hosted `pplx-flash` answers: argmax agreement ≥ **95%**
  and mean |Δp| ≤ **0.02** over all 7,050 answers. If this fails, the row is reported as "a different
  model" and R6A-H2/H3 are still reported but not compared to the hosted row.
- **R6A-H2 (scale, the point of the part):** the R3-H5 bar unchanged — open-loop replay at 100× base rate
  (34.7 rps, 600 decisions, v3 questions): p99 ≤ 1× p99 + 200 ms and **0 errors**. Additionally the
  *saturation curve*: rate stepped 1×, 10×, 30×, 100×, 300×; the first multiplier at which p99 exceeds
  2 × (1× p99) is reported as the VM's capacity. Not a hypothesis; the number people will ask for.
- **R6A-H3 (quality unchanged):** calibrated `needs_human_review` → `iterated` on test with the run-5 val
  calibration refit on the self-hosted answers: ROC-AUC within **0.02** of the hosted row (0.758). Reported
  for the run-4 pair task too (`same_change` AUC, hosted 0.842).
- **R6A-H4 (joint vs single, the model card's warning):** the same 705 test PRs asked **one question per
  request** (10 calls per PR). Pre-registered bar: single-question `needs_human_review` → `iterated` AUC
  ≥ joint + **0.02** means the model card's advice holds for this task and the gateway's default for this
  model switches to single-question; below that, joint stays (it is 10× cheaper in calls). Both p99s reported.
- **Cost row:** $/1k PRs = (VM $/h) / (PRs per hour at the highest multiplier with 0 errors), reported next
  to the hosted list price.

## R6A.4 What is not allowed

No change to questions, prompts, calibration method or thresholds. No quantised weights. The equivalence
set is the run-5 test set and is *not* used to tune anything. The saturation curve may be run more than once
(it is a measurement of the VM, not of the model); every pass is kept in `results/run6/`.

## R6A.5 Order

1. Commit this addendum. 2. Commit `serve/` and the registry row. 3. Bring the VM up, record `serve_meta.json`.
4. Equivalence (H1). 5. Quality (H3), joint vs single (H4). 6. Load (H2 + curve). 7. VM down; report.

---

# Run 6 addendum, part B — wider candidate pool and hold precision (pre-registered)

**Status:** added 2026-10-18 15:05Z, after run-5 results and before any part-B retrieval, label or model call.
Same repositories and split as runs 3–5 (test seen in aggregate four times; stated, not reshuffled).

## R6B.1 Why

Run 5 left two numbers unexplained. Retrieval v2 finds the gold superseding PR in the top 5 for 66% of test
cases but in the top 20 for 86%: the answer is usually there, one cut-off too deep. And every dedup variant
in runs 4–5 held 100–180 PRs per model without halving the pass group's bad-outcome rate, because the labels
cannot say whether a hold on an unmarked PR was a useful pointer or a false alarm. Part B measures both.

## R6B.2 Components

**B1 — pool size k.** Retrieval v2 (A1, frozen weights; A2 unchanged, it moved nothing) with `k ∈ {5, 10, 20}`.
The pair question `same_change` is asked for every (PR, candidate) not already answered in runs 4–5
(predictions reused where the pair is identical; this is the run-5 rule). `duplicate_of` per PR = max over
its k candidates. Candidates: `jev`, `pplx-flash` (the two models that cleared R5-H5), `haiku` for continuity.
Cost per PR is reported per k.

**B2 — hold precision, human-labelled.** From the test PRs that the `duplicate_of ≥ 0.7` rule holds at
k = 20 for `pplx-flash`, a stratified random sample of **100** (seed 6; strata: maintainer-labelled
superseded / closed-unmarked / merged) is labelled by one human (the author) **blind to the model's score
and to the candidate's rank**, seeing only PR A, the top candidate B and the repository, with three labels:
- `duplicate` — A and B are the same change or B makes A unnecessary; holding A was right;
- `pointer` — not the same change, but B is something A's author should have looked at first (same issue,
  same area, conflicting approach); holding A with "see #B" is useful;
- `unrelated` — the hold was a false alarm.
Labels are written to `data/run6/hold_labels.jsonl` with the labeller's timestamp **before** any precision
number is computed. The same 100 PRs' holds at k = 5 are evaluated from the same labels (no second pass).
A second labeller is not pre-registered; if one exists, agreement is reported.

**B3 — policy consequence, mechanical.** With the labelled precision, the rule `duplicate_of ≥ 0.7 → hold`
is re-evaluated at thresholds {0.6, 0.7, 0.8, 0.9} on the sample; the threshold is *not* changed in this run.

## R6B.3 Pre-registered criteria (test repositories)

- **R6B-H1 (pool):** recall@k of the gold superseding PR at k = 20 ≥ **0.80** (run 5: 0.86 at k = 20 for
  A1; this re-states it under the pair pipeline). The hypothesis that matters: with k = 20, **R4-H3** (pass-group
  bad-outcome rate ≤ 0.5 × base) for at least one of `jev`, `pplx-flash`. If it fails at every k, the pool size
  is reported as not the missing lever.
- **R6B-H2 (precision):** on the 100-PR sample, `duplicate ∪ pointer` ≥ **0.60** and `duplicate` alone ≥
  **0.30** for `pplx-flash` at k = 20. Below 0.40 on the union the dedup rule is reported as not fit to hold PRs
  and is demoted to `annotate` (comment, no hold) in the default policy.
- **R6B-H3 (cost of k):** model calls per PR = 1 + k; the k = 20 row must stay ≤ **$0.002** per PR at list
  price for `pplx-flash` (it is $0.0004 at k = 5), else k = 10 is the product default regardless of H1.
- **R6B-H4 (k = 5 vs k = 20 on the same labels):** precision at k = 5 is computed on the subset of the 100 held
  at both; reported, not a bar.

## R6B.4 What is not allowed

No change to retrieval weights, the pair prompt, or the 0.7 threshold before the labels are written. The
labeller does not see scores, ranks or model names. The sample is drawn once; a PR whose B is deleted or
inaccessible is labelled `unrelated` and counted. Haiku's precision is reported only on PRs it also holds.

## R6B.5 Order

1. Commit this addendum. 2. Retrieve at k = 20 (deterministic), build pairs, predict new pairs (jev,
pplx-flash, haiku; Mac). 3. Draw the 100-PR sample, commit `data/run6/hold_sample.jsonl` (PR ids only).
4. Human labels → `data/run6/hold_labels.jsonl`, committed. 5. Report. Timestamps in CHANGELOG.

---

# R7 — compact state, prompt tuning, evidence floor, relation question (addendum, 2026-10-23)


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

**C1 — tokens per request.** Measured, not assumed: on the run-6A server the v3 request carries the rendered state
once per question (10 questions → median 2.8k tokens per question, p90 19.6k per request, max 42k), so compute per PR
is `questions × state tokens`. The stored diff is already capped at 4,000 characters; the state's other parts are not.
C1 compares the current state against a **compact** rendering — diff 2,000 characters, body 600 characters, at most 20
files listed — on the run-5 v3 questions and the pair question, **test** split only, `jev` and `pplx-flash`. Tokens per
request and cost per PR are recorded for both; the compact state's throughput on one L4 is re-measured only if C4 runs.

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

- **R7-H1 (compact state):** with the compact rendering the mean AUC over the v3 questions drops by **≤ 0.02** for both
  `jev` and `pplx-flash`, and the pair AUC by ≤ 0.02. Pass → compact becomes the gateway's default rendering. Fail → the
  current rendering stays; the tokens-per-request saving is reported, not used.
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

## R7.6 Decisions taken at freeze (2026-10-23)

- **C4 is deferred to run 8.** R7-H4 is not a run-7 hypothesis; the section stays as the specification for run 8.
- **R7-H2 primary target: mean AUC over the v3 questions against the run-5 judge labels** (the per-question AUCs and the
  `needs_human_review` → iteration AUC are recorded in the tuning log and reported beside it).
- **No `haiku` rows in run 7.** Candidates are `jev` and `pplx-flash` only.
- **C1 is defined as the compact rendering** (diff 2,000 characters, body 600, 20 files), after measuring that the v3
  request carries the state once per question; see C1.
- Tooling (`mergeone/eval/run7_*.py`, `--compact`, `--prompt-file`, relation prompts) was committed before this freeze
  (`931fe9e`) and makes no model call; `predict` and `score` subcommands refuse to run without the freeze flag.

---

# R8 — compact with same-day control, relation v2, v4, registry row, drift replay (addendum, 2026-10-25)

Numbers this addendum rests on: `docs/run8-numbers.md` (routing, v4, drift — from existing predictions, no model call),
`results/run8/relation_tables.md` (run 7's relation answers re-scored through the metadata rule: the baseline),
`docs/drift-monitor.md` (the spec the replay tests). Commands: `docs/run8-tooling.md`. No run-8 model call before the
commit that adds this section.

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
through the same rule (separates "new question" from "new day"). The control is run (R8.6).

**C3 — v4 question set (product change, measured, no bar).** v3 minus `follows_conventions` and `message_matches_diff`.
Not a hypothesis: the mean rises mechanically (+0.085 to +0.100 on test) because the dropped questions score below
the rest. What is measured and reported: per-question AUC of the eight on the same-day control vs run 5, so the
drift-free movement of each surviving question is on record. Policy consequence, dated in CHANGELOG at freeze: the
shipped `message-mismatch` rule (reads `message_matches_diff`) is removed from the template and `policy.yaml`; under
the drifted model it already never fires (mean 0.93), and under v4 a missing question is a non-match, so keeping it
would be a rule that silently does nothing. `docs/POLICY.md` says so.

**C4 — one more registry row, self-hosted.** The run-7 deferral: a 26B-MoE / ~4B-active open-weight decider on one
L4 through the run-6A harness (`serve/`, `docs/run6-gpu.md`), v4 + pair on the test splits, `loadtest` at ×1 / ×10 /
×30. Same bars as R6A-H3/H4 (quality within the CI of the hosted row it is compared with; ×10 clean). Candidate: the
26B-MoE / ~4B-active open-weight decider named in ROADMAP; its exact repository id and licence are recorded in
CHANGELOG before the first C4 call; if no candidate with an Apache-2.0-equivalent licence is found, C4 is deferred
again and R8-H4 is not a run-8 hypothesis.

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
- **R8-H4 (registry row):** R6A-H3 and R6A-H4 as written, against the hosted `pplx-flash` same-day control. A run-8
  hypothesis only if C4's candidate is recorded as in C4.
- **R8-H5 (drift replay):** the fast signal flags exactly `follows_conventions` and `message_matches_diff` for
  pplx-flash between run 5 and run 7 and nothing on jev or on the run-6A row; the slow signal flags jev
  `tests_cover_change`; no question with |AUC delta| < 0.03 on identical rows is flagged by the fast signal. Pass →
  `drift.enabled` defaults to true in the next gateway release. Fail → the thresholds are reported and the flag stays
  off.

## R8.4 What is not allowed

- No comparison against an earlier run's number without the same-day control beside it.
- No edit to `prompts/jev_v4.json`, `prompts/haiku_v4.md`, `prompts/jev_relation2.json`, `prompts/haiku_relation2.md`
  or the relation2 class rule (HIGH 0.5, LOW 0.25, rule order as in `mergeone/eval/run8_relation.py`) after the
  freeze commit.
- No threshold in the drift monitor moved after the freeze; 0.20 / halved spread / 0.05 are the numbers.
- No tuning rounds. Run 7's tuning log stands as the result on that question.
- No haiku calls (cost); no run on the validation split except C4's calibration fit, as R6A.

## R8.5 Budget and order

Perplexity ≈ $4 (pair compact + control ≈ $2.6, v4 + control ≈ $0.6, relation2 + control ≈ $0.4), Jev on credit,
one L4 ≈ 2 h ≈ $1.2. Order: freeze → C5 replay (no key) → C3/C1 controls and treatments (same day, one script) →
C2 → C4 → report (`mergeone.eval.run8_report`, written from the run-7 builder before any run-8 number is scored; it
does not exist at freeze).

## R8.6 Decisions taken at freeze (2026-10-25)

- **C4 candidate.** The 26B-MoE / ~4B-active open-weight decider named in ROADMAP. Its exact repository id and licence
  are recorded in CHANGELOG before the first C4 call; if no candidate with an Apache-2.0-equivalent licence is found,
  C4 is deferred again and R8-H4 is not a run-8 hypothesis.
- **C2 control is run.** Run 7's `relation` question is asked again the same day as relation2 and scored through the
  same metadata rule (≈ $0.2); it is the difference between "new question" and "new day".
- **relation2 `LOW` = 0.25.** It is the one constant in the relation2 rule not derived from a run; it is frozen with
  `HIGH` = 0.5 and the rule order and is not moved by run 8.
