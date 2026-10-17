# Run 4 — PR memory and duplicate detection

**Headline: mixed, and the split is clean.** The typed pair question works: asked *is PR A the same
change as PR B*, every System One model clears the pre-registered bar on held-out repositories —
pplx-decider **0.87**, pplx-flash **0.84**, Jev **0.83** ROC-AUC (bar 0.80); the LLM judge (Haiku 5.5)
does not (0.76); the retrieval-score floor is 0.58. The *pipeline around the question* does not yet:
deterministic retrieval finds only 55% of the gold duplicates in its top 5 (bar 0.60), the dedup rule
lowers the pass group's bad-outcome rate by 5–6 points instead of halving it, and the Python retrieval
index misses the 20 ms latency bar by an order of magnitude. R4-H2 passes for three of five candidates;
R4-H1, H3, H4 (for the model candidates) and H5 fail. Memory stays behind `memory.enabled`, not default-on.

Data: the 2,196 run-3 agent PRs, plus a 180-day pull-request memory for each of the 26 repositories
(100,228 PRs via GraphQL) and the comments of all 942 closed-unmerged PRs. Labels are maintainer
comments matched by a regex frozen before any label was read (`mergeone/data/supersede_labels.py`);
no model produced a label. Repo-level split is inherited from run 3 (18 val / 8 test). Full tables:
[`tables.md`](tables.md); numbers: [`metrics.json`](metrics.json); labels: `data/run4/labels.jsonl`.

## What the labels say

| label on closed-unmerged PRs (n = 942) | n |
|---|---|
| not marked by a maintainer | 757 |
| "superseded" without a PR number | 113 |
| **superseded by #N** (gold pair) | 51 |
| superseded by #N outside the 180-day memory | 21 |

Fifty-one gold pairs across 26 repos — 22 val, 29 test. Run 3's close-reason sample said one in three
closures is a supersede or duplicate; maintainers *write it down with a number* in one in eighteen.
The question is real; the labelled sample is small, and every confidence interval below is wide.

## R4-H1 — does retrieval find the duplicate? ❌

`score = 0.55·jaccard(files) + 0.35·bm25(title+body) + 0.10·[same author]`, ±45 days, top 5.

| split | gold pairs | recall@5 |
|---|---|---|
| val | 22 | 0.73 |
| **test** | **29** | **0.55** (bar 0.60; "not working" < 0.40) |

Below the bar on test, above it on val. Looking at the 13 misses: the superseding PR usually touches
a *different* file set (a maintainer's rewrite of an agent's fix) and shares few title tokens, so file
Jaccard — the heaviest weight — is near zero. The weights were fixed before any label existed and are
not tuned here; a retrieval that reads the diff, not the file list, is the obvious next candidate.

## R4-H2 — can a model tell "same change" from text? ✅ for System One models

187 test pairs (29 positive): every retrieved pair whose A was gold-labelled, the 13 gold pairs
retrieval missed (rendered the same way), and random negatives.

| model | ROC-AUC [95% CI] | PR-AUC | p50 / p99 per pair | $ per PR (k = 5) | bar 0.80 |
|---|---|---|---|---|---|
| **pplx-decider-v1.2** (27B, open) | **0.87** [0.80, 0.92] | 0.56 | 351 / 524 ms | $0.0002 | ✅ |
| pplx-decider-flash-v1.2 (4B, open) | 0.84 [0.76, 0.91] | 0.48 | 221 / 328 ms | $0.0001 | ✅ |
| Jev 1.13 | 0.83 [0.76, 0.90] | 0.52 | 265 / 462 ms | $0.0005 | ✅ |
| Claude Haiku 5.5 | 0.76 [0.68, 0.84] | 0.42 | 1,145 / 2,582 ms | $0.0019 | ❌ |
| retrieval score (floor) | 0.58 [0.45, 0.70] | 0.34 | — | 0 | ❌ |

The three System One models are inside each other's intervals; all three are clearly above the floor
and above the judge. This is the first run where an open-weight model (Perplexity's, Apache-2.0,
added as a dated amendment R4.6 before any run-4 call) is the best candidate on the primary number.
Val AUCs are 0.90–0.92 for every model, so the test numbers are not a fluke of split choice.

## R4-H3 / R4-H4 — does the dedup rule change verdicts for the better? ❌

Default run-3 policy, then `duplicate_of ≥ 0.7 → hold/dedup`, on the 705 test PRs. Only models that
were run-3 candidates have run-3 verdicts to combine with; pplx and pplx-flash are not evaluable here.

| model | pass % | bad-outcome rate in pass group | closed PRs *not* passed | dedup holds |
|---|---|---|---|---|
| Jev, no dedup | 35.3 | 0.410 | 67.5% | — |
| Jev + dedup | 30.8 | **0.355** (bar ≤ 0.234) | 75.7% (**+8.2 pp**, bar +15) | 136 |
| Haiku, no dedup | 59.0 | 0.445 | 44.9% | — |
| Haiku + dedup | 50.9 | 0.409 | 55.7% (+10.8 pp) | 112 |
| rules + dedup | 50.4 | 0.473 | 49.2% (+18.0 pp ✅) | 183 |

The rule moves things in the right direction for the model candidates — fewer bad PRs pass, more
closed PRs are caught — but by a third of the pre-registered amount. The holds themselves are
enriched, not precise: of Jev's 136 dedup holds, 59% were later closed (base 43%) and 20% carry a
maintainer "superseded" label (base ≈ 8%), i.e. four in five holds are PRs the model judged a
duplicate that no maintainer marked as one. Rules "passes" H4 by holding 183 PRs with a *worse* bad-rate
in the pass group — H3 and H4 have to be read together, and together rules fails.

## R4-H5 — retrieval latency ❌

p50 **5.9 ms**, p99 **198 ms** over 2,196 PRs (bar p99 ≤ 20 ms). The p99 is the four repositories
with more than 10,000 PRs in memory (litellm 16.8k, vscode 13.5k, gh-aw 11.3k, camunda 11.1k): the
reference retrieval is pure-Python BM25 scanning every candidate in the window. The Go store in the
gateway is byte-equivalent on scores and is not what was timed; the pre-registered number is the
Python one and it fails. Model calls per PR stayed at 1 + k as required.

## Reading it together

- The expensive, uncertain part — *can a cheap model tell two PRs are the same change* — is answered
  yes, by three vendors, at ≤ $0.0005 per PR and ≤ 0.5 s p99. That survives.
- The cheap, deterministic part — finding the right candidate to ask about — is the bottleneck, and it
  is the part we built in an afternoon with fixed weights. Recall 0.55 caps everything downstream:
  the verdict-level deltas (H3, H4) are roughly recall × pair accuracy, and they land where that
  product predicts.
- The holds the rule does make are mostly not maintainer-confirmed duplicates. Whether they are
  *useful* holds (the agent should have looked at #N first) or false positives is not something the
  labels can say; it needs a human-check sample, which run 4 did not pre-register.

**Product consequence.** `memory` ships in the gateway and stays opt-in; `duplicate_of` is exposed as
a probability for policy rules, and the default policy keeps the dedup rule at 0.7 as a hold, not a
block. The retrieval will be replaced before any run calls this feature default-on.

## Housekeeping (CHANGELOG)

- Perplexity's first pass errored on 75% of pairs with HTTP 429 (8 concurrent calls against a 10 rps
  org limit). The adapter now retries 429 with backoff and the runner uses concurrency 2 for pplx;
  the errored rows were re-predicted and the files compacted to one row per pair. No prompt, question
  or threshold changed between the two passes. Jev and Haiku had zero errors in one pass.
- The TypeSafe organisation returned HTTP 402 (billing) after Jev's run-4 predictions were complete;
  nothing in this run is affected.

## Reproduce

```
mergeone run4-labels && mergeone run4-retrieve && mergeone run4-pairs
scripts/run4_pairs.sh            # predict jev, pplx, pplx-flash, haiku → report → leaderboard
mergeone report results/run4 --run4
```
`data/run4/memory/*.jsonl` (100k PRs) and `comments.jsonl` are not committed; `python -m mergeone.data.pr_memory memory`
and `… comments` rebuild them from GitHub with a token (`GITHUB_TOKEN`). `pairs.jsonl` and `candidates.jsonl` are
deterministic from those plus `labels.jsonl` (committed).
