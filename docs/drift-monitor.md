# Drift monitor — spec (v0.3, from run 7)

Run 7 found that a hosted decision model answers differently under an unchanged version string: between 2026-10-18
and 2026-10-23, on identical input, `pplx-decider-flash-v1.2` moved its mean `follows_conventions` probability from
0.26 to 0.82 and `message_matches_diff` from 0.67 to 0.93, while eight other questions held. A calibration map fit
on the old answers is then wrong for the new ones, and nothing in the gateway notices. This document specifies the
smallest thing that would have noticed, and what the policy does when it does. It is a spec; the Go work is briefed
from it and ships behind a flag with a measurement in `results/`.

## What is measured

Per model, per question, per repository (and pooled), over the gatelog the gateway already keeps (`store.Record`,
`Result.RawProbs` — the **raw** probabilities, before calibration; drift in the model must be seen before the map):

| statistic | definition | why |
|---|---|---|
| `mean_raw` | mean raw probability over decisions in the window | the run-7 signal: a question going flat shows as a shift of the mean long before anyone labels anything |
| `shift` | `|mean_raw(window) − mean_raw(reference)|` | one number per question |
| `yes_rate` | share of decisions with raw ≥ 0.5 | cheaper to read than the mean, same story |
| `spread` | standard deviation of raw | a question that drifts to "always yes" also loses spread |
| `n` | decisions in the window | a shift on 20 decisions is noise |

Windows: current = trailing 7 days; reference = the 28 days before that, or the shipped calibration's fit set when
the gatelog is younger than 35 days (`calibration_<model>_<qset>.json` grows a `fit_stats` block: mean, yes-rate,
spread, n per question, written by gatebench when the map is fit).

## When it fires

A question is **degraded** for a model when, with `n ≥ 200` in the current window,
`shift > 0.20` **or** `spread` fell by half. The threshold is the run-7 number (`docs/run8-numbers.md`, drift table):
the two questions that broke moved 0.56 and 0.26 and lost 0.10–0.23 AUC; `exceeds_scope` moved 0.16–0.18 with its
AUC intact, so 0.15 would have flagged a healthy question; the other seven moved ≤ 0.10; and the same weights served
on our own GPU (run 6A) moved at most 0.077, which is the noise floor for "different serving, same model". 0.20 is
pre-registered for run 8 and may be moved only by a run.

The mean-shift monitor has a known blind spot: a question can lose ranking with its mean unmoved (jev
`tests_cover_change` lost 0.111 AUC at a shift of 0.015). Where the gatelog has outcomes for the window (merged,
reverted, iterated — the run-5 labels, available for closed PRs only), a second signal flags a question whose AUC
against that outcome fell by more than 0.05 from the reference window. It needs labels and lags by the time PRs take
to close, so it is the slow check beside the fast one, never a replacement.

A model is **degraded** when any question a policy rule reads is degraded.

## What happens

1. The gatelog `Stats` output and `GET /stats` gain a `drift` block: per question `mean_raw`, `shift`, `yes_rate`,
   `n`, `degraded`. The portal's evidence page draws it as one sparkline per question (v0.3).
2. A degraded question is logged once per hour and the commit-status description carries `drift: <question>` while it
   lasts, so it is visible on the PR, not only in a dashboard.
3. Policy: a new top-level key

   ```yaml
   on_drift: { verdict: hold, route: human }    # default; `ignore` keeps the rules as they are
   ```

   Rules whose `when` reads a degraded question are not evaluated; the PR gets the `on_drift` outcome with reason
   `drift:<question>`. Rules that read only healthy questions still run. So a degraded `follows_conventions` costs
   nothing (no shipped rule reads it); a degraded `needs_human_review` turns every PR into a human hold until the
   map is refit — exactly the fail-closed behaviour `on_error` has for an outage, applied to a quieter failure.
4. Recalibration: `mergeone calibrate --from-gatelog --since 7d` refits the isotonic map from gatelog decisions that
   have an outcome (merged / reverted / iterated, as run 5 defined them) and writes a new `calibration_*.json` with
   `fit_stats`; the degraded flag clears when the reference is the new fit set. Automatic refit is **not** in v0.3:
   a map that silently refits can hide the drift the flag exists to show.

## What it does not do

- It does not detect a model getting *worse at the same mean* — that needs labels, which is what the runs are for.
- It does not refit anything on its own.
- It is per model and per question; a provider swapping weights under a version string is reported as drift on the
  questions that moved, not as a version event, because the version string is the thing that lied.

## Pre-registered for run 8 (R8-H-drift)

Replaying the run-5 and run-7 predictions through the monitor (no model call) flags exactly `follows_conventions`
and `message_matches_diff` for pplx-flash on the fast signal and nothing on jev; the slow signal flags jev
`tests_cover_change`. False-positive bar: no question with `|AUC delta| < 0.03` on identical rows is flagged by the
fast signal; the run-6A self-hosted row flags nothing. (`scripts/run8_prep.py` already reproduces the table this is
set on; the run-8 version replays through the Go code path.)

## Implementation notes (Go, `gate/internal/`)

- `store`: a ring of per-(model, question) daily sums (`sum`, `sumsq`, `yes`, `n`) is enough — no raw row scan per
  stats call. Persist with the store.
- `engine`: `Calibrator` gains `FitStats`; `Evaluate` takes the set of degraded questions and the `on_drift` outcome.
- `server`: `/stats` block; status description suffix; hourly log line.
- Flag: `drift.enabled: false` by default in v0.3.0; `true` by default once run 8 reports the replay.
