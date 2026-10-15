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
