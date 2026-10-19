# Evidence, including what did not work

The long version of the README's scoreboard. Everything rests on **gatebench**, a pre-registered benchmark: hypotheses and thresholds were committed before any model ran ([PROTOCOL.md](../PROTOCOL.md)); every deviation is dated in [CHANGELOG.md](../CHANGELOG.md); results are published whichever way they fall. Short dated entries: [mergeone.dev/updates](https://mergeone.dev/updates/).

## Scoreboard

| run | question | result | number |
|---|---|---|---|
| 1 | Can a diff predict a 30-day revert? | ❌ negative | PR-AUC 0.010 vs base 0.0075, all four systems |
| 2 | Can a System One model answer typed triage questions? | ✅ positive | ROC-AUC 0.87–0.96 vs a strong judge, 347 ms, $0.074/1k — bar met 5/5 (Jev), 4/5 (Haiku) |
| 3 | Does the gate predict rejection of **real agent PRs**? | ❌ negative | ROC-AUC 0.55 (bar 0.70). 67% of closures are superseded/duplicate/direction — not visible in a diff. Volume: Jev p99 417 ms, 0 errors at 100× |
| 4 | Can a per-repo PR memory catch the duplicates? | 🟡 mixed | The pair question works: `same_change` ROC-AUC **0.87** pplx-decider, 0.84 pplx-flash, 0.83 Jev (bar 0.80); Haiku 0.76 fails. The pipeline does not yet: retrieval recall@5 0.55 (bar 0.60), dedup rule cuts pass-group bad rate 0.41 → 0.36 (bar 0.23), retrieval p99 198 ms (bar 20). Memory stays opt-in |
| 5 | Can better retrieval, declared assumptions and a wider question set move the gate? | 🟡 mixed | Retrieval recall@5 0.55 → **0.66** (bar 0.70), Go p99 **3.3 ms** ✅. "Flag ⇒ more review rounds" replicates pre-registered: `needs_human_review` → force-push ≥ 2 AUC **0.76** Jev, **0.76** pplx-flash (bar 0.70); 10-question stack misses 0.75. Declared assumptions not evaluable (9 labelled relations in 2,196 PRs). Perplexity hosted API: 71% HTTP 429 at 100× (10 rps/org) |

What survives: typed triage of *what a change does* at System One speed and cost, a gateway that handles 30 million decisions a day on a 2-vCPU box, and — new in run 4 — a cheap model can tell whether two PRs are *the same change* (0.83–0.87 AUC, ≤ $0.0005 per PR). What does not: predicting what a project will *accept* from one diff, and, so far, finding the right PR to compare against. Details in [`results/`](../results/) and on [mergeone.dev](https://mergeone.dev).



## Run by run

**Run 1, negative.** *Can a cheap decision layer predict 30-day reverts from a pre-merge diff?* No. Rules, LightGBM, Claude Haiku 5.5 and Jev all land at PR-AUC ≈ 0.010 against a 0.0075 base rate, indistinguishable within CIs. That signal is not in the diff. We did not build a product on it.

**Run 2, positive.** *Can it answer rule-like questions that cannot be written as rules?* Yes. Against a Sonnet 5.5 judge (self-agreement 92–100%) on 1,200 PRs:

| question | Jev AUC | Haiku AUC | Jev ECE (cal.) | Haiku ECE (cal.) |
|---|---|---|---|---|
| is_logic_change | 0.94 | 0.95 | 0.030 | 0.027 |
| weakens_tests | 0.96 | 0.96 | 0.031 | 0.009 |
| message_matches_diff | **0.80** | 0.78 | 0.037 | 0.045 |
| touches_concurrency | 0.95 | 0.96 | 0.016 | 0.017 |
| needs_human_review | 0.87 | 0.88 | 0.047 | 0.041 |
| per PR, five questions | **347 ms · $0.074/1k** | 1067 ms · $0.314/1k | | |

Jev meets the pre-registered bar on 5/5 questions, Haiku on 4/5; asking five questions instead of one costs Jev 3% latency and 12% cost. What still fails: routing the top 25% to humans captures only 44% of the judge's "needs a human" PRs (bar was 70%). That number is in the README on purpose.

**Run 3, negative.** *Does the gate predict whether humans reject real agent PRs?* No. On 2,196 PRs by Copilot, Claude Code, Claude, Devin and Cursor across 26 public repos (held-out test: 705 PRs, 8 repos), calibrated `needs_human_review` → *closed-unmerged ∨ change-requested* scores ROC-AUC **0.55** for Jev, 0.55 for Haiku, 0.53 for rules; the pre-registered bar was 0.70. The reason is in the labels: of 200 closures we classified, 67% were *superseded, duplicate or direction* and only 19% *quality*. A diff cannot tell you someone else already landed the fix. Two things did hold: Jev at 100× agent-scale load — p99 417 ms, 0 errors (Haiku: 2,880 ms, 0.3% errors) — and, exploratory only, the questions track *review iteration* (force-pushes ≥ 2) at AUC 0.70–0.81. Full write-up: [`results/run3/README.md`](../results/run3/README.md).

**Run 4, mixed.** *Can a per-repository PR memory catch the duplicates run 3 found?* Half of it. Given a candidate pair, the typed question `same_change(A, B)` separates maintainer-confirmed supersedes from the rest at ROC-AUC **0.87** (Perplexity pplx-decider, open weights), **0.84** (pplx-flash, 4B) and **0.83** (Jev) on 187 held-out pairs — all above the 0.80 bar, all at ≤ 0.5 s p99 and ≤ $0.0005 per PR; Claude Haiku 5.5 scores 0.76 and fails. But the deterministic retrieval that picks the candidates finds only 55% of the gold duplicates in its top 5 (bar 0.60), so at the verdict level the dedup rule lowers the pass group's bad-outcome rate from 0.41 to 0.36 (bar: 0.23) and catches 8 more points of closed PRs (bar: 15). Retrieval p99 is 198 ms against a 20 ms bar. Four of five hypotheses fail; the one that passes is the one that was uncertain. Memory ships opt-in. Full write-up: [`results/run4/README.md`](../results/run4/README.md).

**Run 5, mixed.** *Can the gate be made better on the two things runs 3–4 exposed — finding the PR to compare against, and knowing which PRs will need review?* Partly. Retrieval v2 (shared-issue references, directory overlap, weights fit on val only) lifts recall@5 from 0.55 to **0.66** — short of the 0.70 bar, and the changed-line content stage moved nothing — while the Go store answers in **3.3 ms p99** against a 20 ms bar. Run 3's exploratory finding becomes a result: calibrated `needs_human_review` predicts ≥ 2 force-push rounds at AUC **0.76** for Jev and for the 4B open-weight pplx-flash (bar 0.70); the ten-question stack misses its 0.75 bar for every System One model because the val-chosen pruning rule dropped `hard_to_revert`, the strongest question on test (0.85). Declared assumptions could not be judged — maintainers wrote a numbered "depends on / conflicts with" comment on 9 of 2,196 PRs. Both Perplexity models hold p99 flat from 1× to 100× and then lose 71% of calls to HTTP 429: a per-org rate limit, which self-hosting the open weights removes. Full write-up: [`results/run5/README.md`](../results/run5/README.md).

**Run 6A, negative.** *Do the open weights clear the scale bar when we serve them ourselves?* Not on one GPU. pplx-decider-flash-v1.2 on a rented NVIDIA L4 ($0.59/h), served with the vendor's own code, agrees with the hosted endpoint on **95.2%** of answers but not within the 0.02 mean |Δp| bar (0.047), so it counts as a different model; its quality is a CI-width away (AUC **0.747** vs 0.758, pair task 0.840 vs 0.842), one-question-per-request gains **+0.000** AUC for 10× the calls (joint stays), and the server saturates at **~2.2 requests/s** — 6× the base rate, with ×10 already queueing and ×100 losing every call. The vendor's rate limit becomes a capacity-planning problem; one 24 GB card is an order of magnitude under the target. $1.44 of GPU. Part B (wider candidate pool, 100-PR blind hold precision, two labellers) is in progress. Full write-up: [`results/run6/README.md`](../results/run6/README.md).

Raw predictions, judge labels, calibration maps and figures: `results/run1` … `results/run6`. Rebuild every table with one command.

### Which decision model should sit in the gate?

![Decision models under agent-scale load: p99 latency per decision vs load multiplier; each line runs as far as the model answered every call](../results/figures/decision_models_under_load.png)

*One line per decision model behind one API key or one GPU; the line runs as far as every call was answered, ✕ is the first load with failures (runs 1, 5, 6). Rebuilt by `scripts/fig_decision_models_under_load.py` from the frozen load-test files.*


[`results/leaderboard.md`](../results/leaderboard.md) ranks every model that has been run under the protocol on the four things a gateway pays for — quality (AUC vs ground truth), calibration (ECE), speed (p99 at 100× load) and cost — with the scale bar as a hard gate. Reporting only, rebuilt by `mergeone leaderboard`.

| # | model | kind | scale at 100× | quality AUC (mean over runs) | ECE | p99 at 100× | $/1k PRs | runs |
|---|---|---|---|---|---|---|---|---|
| 1 | Jev | System One, hosted | **pass** | 0.73 | 0.073 | 527 ms | 0.089 | 5 |
| — | GBM / rules | floors, no model call | n/a | 0.61 / 0.58 | 0.003 / 0.105 | ≤ 9 ms | 0 | 1 / 4 |
| 4 | pplx-decider-flash 4B | System One, open weights | **fail** (71% HTTP 429) | **0.80** | 0.053 | 501 ms | 0.022 | 2 |
| 5 | pplx-decider 27B | System One, open weights | **fail** (71% HTTP 429) | 0.78 | 0.078 | 1,032 ms | 0.044 | 2 |
| 6 | pplx-decider-flash 4B, self-hosted (one L4) | System One, open weights | **fail** (saturates at ×10) | 0.75 | 0.052 | — (×1: 1,462 ms) | 0.472 | 1 |
| 7 | Claude Haiku 5.5 | LLM judge, JSON schema | **fail** | 0.71 | 0.060 | 9,662 ms | 0.370 | 5 |

The ordering is decided by the scale column, not by a few AUC points. Haiku matches Jev on quality and fails the 100× bar in every run on latency. The two Perplexity models lead on quality in runs 4–5 and hold their p99 flat from 1× to 100× — and then lose 71% of calls to the hosted API's 10 rps per-organisation limit (run 5). That is a vendor ceiling, not a model one: the same weights self-hosted (a `provider:` row pointing at llama.cpp or vLLM) carry no such limit — and run 6A measured it: on one L4 the limit becomes throughput (~2.2 requests/s, saturating at ×10), at 20× the hosted list price per PR. Quality is not comparable across runs (different tasks), so a mean over runs is a tie-breaker, not a score. The remaining open-weight candidates (Cloudflare `clef`, AutoTrust `JEV-9B/27B`, H2O Lightning 4B, Laya 421M) are in the registry; until they have a row, their public-leaderboard numbers are claims, not measurements.

**Any model, one YAML row.** The gateway does not know the model's name. [`prompts/models.yaml`](../prompts/models.yaml) lists each candidate once — wire dialect (`systemone`, `decide`, `anthropic`), endpoint, key variable, model id, list price — and `model: <row>` in `.mergeone.yaml` or `--model <row>` in gatebench uses it. A private llama.cpp or vLLM server serving open weights is a `provider:` block in the policy; no code, no rebuild. `mergeone models` prints the registry.

