# Run 5 — retrieval v2, declared assumptions, question set v3

**Headline: one replication, one near-miss, one not-evaluable, one vendor ceiling.** Run 3's exploratory
finding replicates as a pre-registered result: calibrated `needs_human_review` predicts whether an agent PR
goes through **≥ 2 force-push rounds** at ROC-AUC **0.76** (Jev), **0.76** (pplx-flash), 0.69 (pplx) on the
held-out repos, bar 0.70 — but the 10-question stack stays below its 0.75 bar for every System One model, so
R5-H5 as written fails. Retrieval v2 lifts recall@5 from 0.55 to **0.66** (bar 0.70; the content stage
changed nothing on test) and the Go store answers in **3.3 ms p99** (bar 20). Declared assumptions cannot be
judged: maintainers wrote a numbered *conflicts-with / depends-on* comment on **9 of 2,196 PRs**. Both
Perplexity models pass 1× and 10× load and fail 100× with 71% HTTP 429 — a per-organisation rate limit, not
model latency.

Data, split and repositories are run 3/4's (test seen in aggregate; stated in PROTOCOL and not reshuffled).
Tables: [`tables.md`](tables.md), [`assumptions_tables.md`](assumptions_tables.md); numbers:
[`metrics.json`](metrics.json), [`retrieval_metrics.json`](retrieval_metrics.json),
[`assumptions_metrics.json`](assumptions_metrics.json); labels: `data/run5/relation_labels.jsonl`,
`data/run5/assumptions.jsonl`.

## A — Retrieval v2 (R5-H1 ❌ at 0.66, R5-H2 ✅)

Weights were fit on the 22 **val** gold pairs only and frozen before any test PR was scored
(issue 0.5 · dir 0.1 · file 0.2 · text 0.2 · author 0.0; 826-cell grid in `a1_weight_grid.json`).

| | val (tuned) | **test** (29 gold) |
|---|---|---|
| A1 metadata, recall@5 | 0.86 | **0.66** |
| A1 metadata, recall@20 | 0.91 | 0.86 |
| A1 + A2 identifier re-rank, recall@5 | 0.82 | **0.66** (bar 0.70; run 4: 0.55) |
| tree-sitter re-rank (exploratory) | — | 0.66 |

The shared-issue feature does the work: the gold superseding PR is in the top 20 for 86% of test cases,
up from run 4's file-list-only retrieval, and the biggest single weight is "both PRs reference the same
issue". The content stage (16,664 patches fetched, 17,421 targets; 597 unfetchable are all one renamed
val repository, `kentcdodds/kody`, HTTP 301) **did not move a single test gold pair** in or out of the top 5
and cost 0.05 of val recall. Changed-line identifier overlap is not the missing signal; the top-5 cut-off is
too tight for a problem whose answer is usually at rank 6–20.

Latency: Go A1 over the 100k-PR memory, 2,196 queries, **p50 0.5 ms · p99 3.3 ms** (R5-H2 bar 20 ms;
2-vCPU sandbox, three passes). Python reference p99 1.2 s; Go and Python return identical top-20 lists for all
2,196 PRs (max score difference 1e-16). A2 fetches ≤ 20 per PR by construction.

## Verdict level with v2 candidates (R5-H3)

Run-4 pair predictions reused where the pair is identical (1,317 test pairs), new predictions for the 2,118
new pairs. Same policy, same rule (`duplicate_of ≥ 0.7 → hold`).

| model | pass-group bad-outcome rate (bar ≤ 0.234) | closed PRs not passed, Δ vs run 3 (bar +15 pp) |
|---|---|---|
| Jev | 0.41 → **0.35** ❌ | +8.5 pp ❌ |
| pplx | 0.48 → 0.40 ❌ | **+21.6 pp ✅** |
| pplx-flash | 0.46 → 0.38 ❌ | **+18.4 pp ✅** |
| Haiku | 0.45 → 0.41 ❌ | +10.5 pp ❌ |
| rules | 0.47 → 0.43 ❌ | +12.5 pp ❌ |

Coverage now clears the bar for the two Perplexity models; the pass-group bad rate still does not halve for
anyone. The dedup rule holds 137–184 PRs per model, and run 4's finding stands: most of those holds are not
maintainer-confirmed duplicates. Halving the pass group's bad rate needs a hold that is *precise*, not only
more holds — that is a precision measurement run 5 did not pre-register.

## B — Declared assumptions (R5-H4 not evaluable)

| | n |
|---|---|
| PRs with a non-bot comment matching the relation regex **with** a PR number | 9 (test 3, val 6) |
| … pointing outside the 180-day memory | 3 |
| … matching without a number | 88 |
| assumptions extracted (Haiku, title + body only) | 2,196 PRs + 102 pair partners, mean 6.3 per PR, $0.86 |

Three test positives against 30 negatives give ROC-AUCs with intervals from 0.2 to 1.0 (pplx 0.90, Jev 0.86,
pplx-flash 0.74, Haiku 0.61); R5-H4b (relations with no shared file) has one test case. **Not evaluable.**
The honest reading is about the label, not the model: maintainers almost never write "depends on #N" on an
agent PR, so this ground truth cannot be mined from comments at the scale run 4's supersedes could. The
extraction half works as a component (99.9% of PRs yield a non-empty, typed premise list for $0.0004); the
*test* needs either a repository where agents already declare premises, or a label built from merge-conflict
state, which the protocol did not pre-register.

## C — Question set v3 → review iteration (R5-H5 ❌ as written; the half that mattered replicates)

Outcome `iterated` = force-pushes ≥ 2 (test base rate 8.2%). Calibration isotonic on val vs `iterated`.

| model | calibrated `needs_human_review` → iterated, AUC [95% CI] | bar 0.70 | 10-question stack | bar 0.75 | p99 | $/1k |
|---|---|---|---|---|---|---|
| **pplx-flash** (4B, open) | **0.758** [0.690, 0.819] | ✅ | 0.737 | ❌ | 365 ms | 0.023 |
| **Jev** | **0.756** [0.681, 0.813] | ✅ | 0.718 | ❌ | 384 ms | 0.102 |
| pplx (27B, open) | 0.686 [0.625, 0.748] | ❌ | 0.665 | ❌ | 844 ms | 0.046 |
| Haiku 5.5 (not a candidate) | 0.740 | — | 0.784 | — | 2,619 ms | 0.472 |
| rules floor | 0.728 | — | 0.731 | — | 1 ms | 0 |

Two System One models clear the single-question bar, so the run-3 exploratory finding is now a result: a PR
the gate flags is a PR that goes through more review rounds. The stack bar is missed by all three — and the
pruning rule is why. Chosen on val's 46 positives, it dropped `hard_to_revert` for every model, which on test is
the **strongest single question** (raw AUC 0.85 Jev, 0.84 pplx, 0.81 Haiku) and `exceeds_scope` (0.80 Jev).
Val and test disagree about which questions matter; the rule followed val, as pre-registered, and paid for it.
Two questions are consistently *inverted*: `message_matches_diff` and `follows_conventions` score 0.23–0.37
raw — a PR whose message matches its diff iterates *less*, which calibration flips but a noul question should
not need flipping. They are candidates for removal, not for keeping.

The free `rules` floor at 0.73 sits one CI-width from every model. Structural features (size, test touch,
file mix) carry most of what "will iterate" means; the models add 0.03 AUC and the typed *reasons*.

## R5-H6 — Perplexity at scale ❌ (vendor ceiling, not model)

| | 1× p99 | 10× p99 | 100× p99 | 100× error rate |
|---|---|---|---|---|
| pplx | 958 ms | 988 ms | 1,032 ms | **0.713** (all HTTP 429) |
| pplx-flash | 561 ms | 462 ms | 501 ms | **0.713** (all HTTP 429) |

Latency is flat across 1×–100×; the API simply refuses 71% of calls above its 10 rps per-organisation limit
(100× = 34.7 rps). With the adapter's retry off, as the protocol requires, that is a fail. It is also the
clearest argument yet for the open weights: the same model self-hosted has no such limit, and the registry
already takes it as a `provider:` row. Jev, measured in run 3, stays the only hosted candidate to pass 100×.

## Reading it together

- **What got better:** retrieval recall 0.55 → 0.66 and 198 ms → 3.3 ms; two cheap models now carry a
  pre-registered "flag means more review" result; coverage of closed PRs crosses +15 pp for two models.
- **What did not:** no model halves the pass group's bad rate (precision of holds is the untested thing);
  the stack rule, chosen on a 46-positive val set, discarded the best question; declared assumptions have no
  usable ground truth in GitHub comments; hosted Perplexity cannot take 100× agent traffic on one key.
- **Scorecard:** R5-H1 ❌ (0.66/0.70), H2 ✅, H3a ❌ ×5, H3b ✅ pplx & pplx-flash, H4 not evaluable, H5 ❌
  as written (single-question bar ✅ for Jev and pplx-flash, stack ❌), H6 ❌ ×2.

**Product consequence.** Retrieval v2 replaces v1 in the Go store (default `top_k` stays 5; the data says
a wider candidate pool is the next lever, which is a protocol change, not a config flip). `memory` stays
opt-in. The gate's default question set stays v2; v3 ships as an option with `hard_to_revert` and
`exceeds_scope` recommended and `follows_conventions` not. `assumptions` lands in `gate_decide` as an input
the policy can use, with no claim attached to it. The leaderboard's scale column now records the Perplexity
429 result; self-hosted rows are the way to clear it.

## Housekeeping (CHANGELOG)

- Component B's merged-PR comment fetch and component A's patch fetch could not run from the sandbox
  (GitHub API not reachable through its proxy); both ran on the Mac in `scripts/run5_mac.sh` stage 0/2,
  after their code was committed. Timestamps in CHANGELOG.
- 597 patch targets returned HTTP 301 (`kentcdodds/kody` renamed); all val. They keep their A1 rank as the
  protocol says. Not re-fetched.
- Haiku 5.5 rejects `temperature` and `maxItems` in structured output; extraction ran at the model default
  and the 8-item cap was applied in code (25 of 2,298 responses truncated).
- A 3-PR Haiku v3 smoke call ran on val 20 s before the wiring commit; discarded, logged.
- `pplx` v3 in-run calls used concurrency 2 with the 429 retry on (as run 4); the load tests had it off.
