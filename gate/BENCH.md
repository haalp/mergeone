# Gate latency: parallel pair calls

Speed-only change to `engine.Gate.DecideFor`. No decision, probability, prompt, threshold or output field
changed. Two things now run concurrently:

1. the k `same_change` pair calls, bounded by `Gate.PairConcurrency` (default 8; 1 = the old sequential loop),
   aggregated in candidate order (`duplicate_of` = max p, `extra.candidates` in retrieval order, cost summed);
2. the main `QuestionsV2` call alongside retrieval + pair calls (the pair stage needs only `state` and `meta`).
   Calibration and policy matching run after both have finished. With `Memory == nil` the old code path runs unchanged.

`Result.LatencyMS` is now the **wall clock** of the decision when a memory is configured. The old number
(the sum of every counted model call) is kept in the new `Result.ModelLatencySumMS` (`model_latency_sum_ms`).

## Setup

`mergeone loadtest --mock --k K --rate R --n N --policy mergeone/gate/policy.yaml` (new in-process mode): mock
model at 300 ± 60 ms per call (deterministic per input), synthetic memory of 200 PRs (`memory.Synthetic`, seed 7),
open-loop arrivals, default policy, identity calibration. 1× = 0.347 decisions/s (n = 30), 100× = 34.7/s (n = 600).
"Before" is the same harness built against the unchanged engine. `model` = the main call, `pairs` = first
pair-call start to last pair-call end, `wall` = the whole `DecideFor` call. All values are in ms.

| k | load | engine | model p50 / p99 | pairs p50 / p99 | **wall p50 / p99** | pair calls / decision | decisions digest |
|---|---|---|---|---|---|---|---|
| 5 | 1× | sequential (before) | 312 / 380 | 1,550 / 1,890 | **1,867 / 2,116** | 5 | `fe68d25412448c74` |
| 5 | 1× | parallel (after) | 312 / 380 | 375 / 523 | **380 / 523** | 5 | `fe68d25412448c74` |
| 5 | 100× | sequential (before) | 307 / 423 | 1,516 / 1,815 | **1,822 / 2,151** | 5 | `a0bd57214a40cdcb` |
| 5 | 100× | parallel (after) | 307 / 422 | 369 / 492 | **375 / 492** | 5 | `a0bd57214a40cdcb` |
| 20 | 1× | sequential (before) | 311 / 380 | 6,078 / 6,511 | **6,391 / 6,865** | 20 | `805b94fc2f6637cf` |
| 20 | 1× | parallel (after) | 312 / 380 | 945 / 1,040 | **945 / 1,040** | 20 | `805b94fc2f6637cf` |
| 20 | 100× | sequential (before) | 307 / 423 | 6,010 / 6,689 | **6,312 / 6,966** | 20 | `04f38d76724fd072` |
| 20 | 100× | parallel (after) | 306 / 423 | 920 / 1,063 | **920 / 1,063** | 20 | `04f38d76724fd072` |
| 20 | 100× | parallel, `PairConcurrency` 20 | 307 / 423 | 410 / 501 | **411 / 502** | 20 | `04f38d76724fd072` |
| 5 | 100× | after, `PairConcurrency` 1 (no pair parallelism, overlap only) | 308 / 423 | 1,517 / 1,816 | **1,517 / 1,816** | 5 | `a0bd57214a40cdcb` |

Wall clock per decision drops from ≈ (1 + k) × 300 ms to ≈ (1 + ⌈k / PairConcurrency⌉) × 300 ms minus the overlap
with the main call: about one call's time at k = 5 (5.0× faster at p50) and three waves at k = 20 with the default
8 (6.9× faster). `PairConcurrency: 20` brings k = 20 down to about one call as well. The digest is a SHA-256 over every decision's verdict, route, rule,
reason, probabilities, raw probabilities, `extra` (candidate order, per-candidate `duplicate_of`,
`duplicate_candidate`) and cost. It is identical before and after in every row, so all decisions are identical.
The 100× rows are no slower than 1×: the mock sleeps rather than computes, so this measures the gate's
orchestration, not a model server's capacity. Against a real endpoint, k = 20 with concurrency 8 sends up to
9 requests at once per decision, so any per-org rate limit (e.g. the hosted Perplexity 10 rps limit) is reached
sooner.

`TestPairConcurrencyIdenticalDecisions` (internal/engine/parallel_test.go) replays 50 synthetic PRs at
k = 5 and k = 20 through `PairConcurrency: 1` and `8`. The mock has jitter and a 5 % error rate, so pair calls
finish out of order and some fail. The test asserts identical `Probs`, `RawProbs`, `Verdict`, `Rule`, `Reason`,
`Route`, `Extra` (including `candidates` order) and `duplicate_of`. `TestDecideForOverlapsAndReportsWallClock`
checks that the main call and 5 pair calls are in flight together, that `LatencyMS` is the wall clock and
`ModelLatencySumMS` the sum, and that the no-memory path is unchanged.

## Also in this change

- **HTTP transport.** `models.Transport()` is one shared `*http.Transport` (a clone of `http.DefaultTransport`,
  so proxy, dial and handshake settings are unchanged). It sets `MaxIdleConns` and `MaxIdleConnsPerHost` to 256,
  `IdleConnTimeout` to 90 s, `ForceAttemptHTTP2` and an LRU TLS session cache (256). Every `Jev`/pplx/registry decider
  (30 s timeout) and `Haiku` (60 s) uses it, including models from `models.Build`. Previously the default
  transport kept only 2 idle connections per host, so concurrent pair calls would have re-dialled TLS. The mock
  does not exercise this path, so the table above does not measure it.
- `Jev.pinned` (first-response model pin) is now guarded by a mutex. Pair calls hit one client concurrently,
  and the old write was a data race.
- **Diff cache** (`internal/server/diffcache.go`). The GitHub client keeps file stats and the unified diff for 512 PR heads
  in an LRU keyed by `repo#pr@sha`. A re-decision of the same head (reopened, ready_for_review, a retried job) fetches
  only the PR metadata, so title, body and timestamps stay fresh and the rendered state is unchanged. A `synchronize` webhook
  drops the PR's other heads. Only complete fetches are cached (every file page and the diff returned 200).
  Caveat: GitHub computes the PR diff against the merge base. If the base branch is force-moved without a new
  head sha, the cached diff can differ from a fresh one until the head changes or the entry is evicted.
  Not benchmarked (it needs the GitHub API). `TestPRStateDiffCache` covers hit, miss, invalidate and eviction.
