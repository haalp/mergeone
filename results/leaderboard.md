# Gateway leaderboard

Reporting only (not pre-registered). Ranks every decision model that has been run under PROTOCOL.md on the four things a merge
gateway pays for, with the scale bar as a hard gate: a model that fails H5 (errors or queueing at 100× load) is listed but cannot
rank above one that passes. Numbers are copied from the frozen per-run artefacts; nothing is re-scored here.

Axes: **quality** = mean ROC AUC vs the run's ground truth over the runs the model took part in · **calibration** = mean ECE after
calibration (lower better) · **speed** = p99 ms at 100× base rate (loadtest) or in-run p99 · **cost** = USD per 1 000 PRs at recorded
list prices. **Overall** = Borda over the four axes (lower better); missing axis counts last.

| # | model | kind | scale (H5) | quality AUC | calibration ECE | speed p99 ms | cost $/1k PRs | runs | overall |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `jev` | decision model | pass | 0.689 (#1) | 0.076 (#3) | 527 (loadtest x100) (#3) | 0.080 (#3) | 3 | 10 |
| 2 | `gbm` | floor (trained classifier) | n/a | 0.605 (#3) | 0.003 (#1) | 9 (in-run) (#1) | 0.000 (#1) | 1 | 6 |
| 3 | `rules` | floor (no model) | n/a | 0.499 (#4) | 0.125 (#4) | 13 (in-run) (#2) | 0.000 (#2) | 2 | 12 |
| 4 | `haiku` | LLM judge | **fail** | 0.684 (#2) | 0.060 (#2) | 9662 (loadtest x100) (#4) | 0.332 (#4) | 3 | 12 |

## Per run

| model | run | task | ROC AUC | ECE | p99 ms | p99 @100× | $/1k | errors | H5 |
|---|---|---|---|---|---|---|---|---|---|
| `jev` | run1 | revert (k8s) | 0.614 | 0.086 | 613 | 527 | 0.076 | 0.0% | pass |
| `jev` | run2 | judge labels ×5 | 0.904 | 0.032 | 736 | — | 0.074 | 0.0% | — |
| `jev` | run3 | agent-PR outcome | 0.550 | 0.110 | 353 | 417 | 0.091 | 0.0% | pass |
| `gbm` | run1 | revert (k8s) | 0.605 | 0.003 | 9 | — | 0.000 | 0.0% | — |
| `rules` | run1 | revert (k8s) | 0.471 | 0.130 | 0 | — | 0.000 | 0.0% | — |
| `rules` | run3 | agent-PR outcome | 0.528 | 0.120 | 26 | — | 0.000 | 0.0% | — |
| `haiku` | run1 | revert (k8s) | 0.599 | 0.034 | 2510 | 9662 | 0.308 | 0.0% | fail |
| `haiku` | run2 | judge labels ×5 | 0.903 | 0.028 | 2461 | — | 0.314 | 0.0% | — |
| `haiku` | run3 | agent-PR outcome | 0.551 | 0.119 | 2580 | 2880 | 0.373 | 0.0% | fail |

## In the registry, not yet run

Listed so the next run can pick them up with `--model <name>`; the numbers here are vendor or public-leaderboard claims, **not gatebench measurements**.

| model | dialect | weights | params (B) | list $/M in | claim |
|---|---|---|---|---|---|
| `pplx` | systemone | perplexity-ai/pplx-decider-v1.2 | 26 | 0.020 | Open-weight 26B decider. Decision Index #1 (62.8, v1.1). Run 4 candidate (R4.6). |
| `pplx-flash` | systemone | perplexity-ai/pplx-decider-flash-v1.2 | 4.5 | 0.010 | 4.5B flash variant, same API. Run 4 candidate (R4.6). |
| `clef` | systemone | cloudflare/clef | 27 | 0.000 | Cloudflare decision model. Decision Index #19 (53.1); new-domain score 41.9 vs public 61.7 — generalisation gap to verify. |
| `clef-flash` | systemone | cloudflare/clef-flash | 9.4 | 0.000 | Decision Index #33 (47.6). |
| `open-jev-27b` | decide | autotrust/JEV-27B | 27 | 0.000 | Distilled from Jev; S1MB #4. Calibration reported against the teacher, not ground truth. |
| `open-jev-9b` | decide | autotrust/JEV-9B | 9 | 0.000 | S1MB #3. |
| `h2o-lightning-4b` | systemone | h2oai/h2o-lightning-4b | 4 | 0.000 | JevBench open-weights composite #1 (72.5). 29 ms measured locally (209 ms after JevBench's adjustment). |
| `laya` | systemone | convai/laya | 0.42 | 0.000 | 421M, runs on CPU. ECE 0.466 → 0.081 after temperature refit on own labels — a calibration-first candidate for the cheapest gate tier. |

## How to read it

- Quality AUCs are **not comparable across runs** (different tasks, different base rates); the average is a tie-breaker, not a score.
  Run 3's AUCs (~0.55 for everyone) say the task is unpredictable from the diff, not that the models are equal.
- The scale gate is the decisive column. In every run so far the LLM-judge baseline (`haiku`) fails it and the System One models pass it;
  that, not a few AUC points, is why the gateway defaults to a System One model and keeps the judge for escalation.
- `rules` is the free floor: no model call, ~0 ms, cost 0. Any paid model that does not beat it on quality in a run is not earning its latency.
- Cost is at list price for hosted models; self-hosted open weights show 0 and are compared on p99 and quality only.
