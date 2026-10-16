# Run 3 — real agent PRs, outcome labels

**Headline: negative on the primary hypothesis.** The run-2 gate does not predict whether a
human will reject an agent PR on GitHub. Calibrated `needs_human_review` → `closed_unmerged ∨
change_requested` scores ROC-AUC **0.55** (Jev), **0.55** (Haiku 5.5), **0.53** (rules floor)
on 705 test PRs from 8 held-out repositories. The pre-registered bar was 0.70; the
pre-registered "not transferring" line was 0.60. All three systems are below it. R3-H1, H2, H3
fail for every candidate. R3-H5 (volume) passes for Jev and fails for Haiku.

Data: 2,196 PRs by Copilot coding agent, Claude Code, Claude app, Devin and Cursor across 26
public repositories (≥ 500 stars, ≥ 20 agent PRs), 60 merged + 60 closed per repo where
available, split by repository 18 val / 8 test. Everything here is on test repos the
calibration never saw. Full tables: [`tables.md`](tables.md); numbers: [`metrics.json`](metrics.json).

## What the labels turned out to mean

We classified 200 closed-unmerged test PRs with Claude Fable 5.1 (reporting only, never a
label or a candidate):

| close reason | n | share |
|---|---|---|
| superseded or duplicate | 72 | 36% |
| scope or direction (maintainers did not want it) | 62 | 31% |
| quality or bugs | 37 | 19% |
| abandoned or stale | 20 | 10% |
| other | 9 | 5% |

Only about one in five closures is about the change being wrong. Two in three are about
*coordination* — somebody else landed it, or the project did not want it — which no amount of
reading the diff can detect. The gate reads diffs. That is the mechanism behind 0.55, and it
is the same mechanism as run 1's revert result: pre-merge state does not contain the signal
that decides most GitHub outcomes.

## What did move (exploratory, not pre-registered)

The one outcome the questions do track is **review iteration** (`force_pushes ≥ 2`):

| question → iterated | Jev | Haiku | rules |
|---|---|---|---|
| weakens_tests | 0.75 | **0.81** | 0.49 |
| needs_human_review | 0.70 | 0.76 | 0.72 |
| is_logic_change | 0.61 | 0.63 | 0.73 |

A PR the gate flags is a PR that goes through more rounds before it lands. That is what a
"hold" is supposed to mean. It is not what we pre-registered, so it is a hypothesis for run 4,
not a result.

Per-agent rejection rates differ a lot (Devin 6%, Copilot 48%, Cursor 66% on test) and the
gate's AUC is flat across them (0.52–0.60) — the signal does not get better for any agent.

## R3-H4 (stacks)

80 of 705 test PRs target a non-default branch. None could be linked to a PR below it:
GitHub's REST API has no stack object and most of these branches had no open PR of their own.
The stack rule changed 0 verdicts. Reported as not evaluable, not as a fail.

## R3-H5 (volume)

| model | p50 | p99 at 100× | errors | bar | |
|---|---|---|---|---|---|
| Jev | 265 ms | 417 ms | 0 | model p99 390 + 200 | ✅ |
| Haiku 5.5 | 1,208 ms | 2,880 ms | 0.3% | model p99 2,797 + 200 | ❌ |

Same shape as runs 1–2: Jev is ~5× faster, 4× cheaper ($0.09 vs $0.37 per 1k), and stays
error-free under open-loop load.

## What this changes in the product

- The landing page and README stop implying the gate predicts rejection on agent PRs. The
  claim that survives: typed triage of *what the change does* (run 2, AUC 0.87–0.96 against a
  judge) at System One latency and cost (runs 1–3).
- "Rejected on GitHub" is a bad target for a merge gate. Most rejections are coordination
  failures: duplicates, superseded work, direction. That is exactly what the roadmap's
  campaign-dedup and `assumptions` items address, and run 3 is the evidence they are the
  right next step.
- Run 4 should pre-register review iteration (and possibly time-to-merge) as outcomes, and
  test a dedup/superseded signal that reads the *other* open PRs, not just the diff.

## Deviations (also in CHANGELOG)

- `per_repo` 120 instead of 150 (API budget). Sample is the most recently updated PRs per
  (agent, state), not random.
- Only `needs_human_review` was recalibrated on val repos (outcome label); the other four
  questions keep run-2 calibration maps.
- 825 Haiku val rows from an attempt with an invalid API key (HTTP 401) were removed before
  the report; every val PR has exactly one successful Haiku row.
- Jev predictions were produced in two batches (1,513 PRs while the fetch was still running,
  683 after it finished). Rendering and split assignment are deterministic; verified identical.
