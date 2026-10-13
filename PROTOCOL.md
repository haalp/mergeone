# Pre-registered experiment protocol

**Status:** frozen before any model was run. Changes after this point are recorded in `CHANGELOG.md` with a reason; the evaluation thresholds below are not changed after results are seen.

**Project:** scm-decision-bench
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
- One command reproduces the tables and figures from the raw predictions: `python -m scmbench.report results/<run_id>`.

## 9. Publication commitment

Results are published regardless of direction (positive, negative, mixed), with the hand-check sample, the raw predictions, and this protocol unchanged.
