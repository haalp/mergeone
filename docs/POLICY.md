# Policy reference

`.mergeone.yaml` is the whole product surface. This page is the full reference; the README has the short version.

## The file

The default policy ships as [`mergeone/gate/policy.yaml`](../mergeone/gate/policy.yaml) and `mergeone init` writes a commented copy into your repo. Keys, with the value the shipped file sets (— = not in the file; the default applies):

| key | shipped `policy.yaml` | meaning |
|---|---|---|
| `version` | `1` | policy format version |
| `model` | `jev` | a row of [`prompts/models.yaml`](../prompts/models.yaml) (`jev`, `pplx`, `pplx-flash`, `haiku`, `local`, …) |
| `model_id` | — (commented) | pins one model version, e.g. `pplx-decider-flash-v1.2` |
| `provider` | — (commented) | defines or overrides the model row inline (same keys as a `models.yaml` row), e.g. a private endpoint |
| `question_set` | `v2` | `v2` (default, five questions) or `v4` (opt-in, eight; calibration maps ship for `jev`, `haiku`, `pplx-flash`). `v3` (ten; run 5) is asked only when `calibration_<model>_v3.json` exists; otherwise the gateway refuses to start: `no calibration map for v3; use v2 or v4` — below |
| `calibration` | `auto` | `auto`: `calibration_<model>_<question_set>.json` from the assets directory (`--assets`, default the policy's directory), next to the binary or in `~/.mergeone`; identity if absent; `none`: raw probabilities; or a path |
| `always_human_paths` | three Kubernetes prefixes | path prefixes that always `hold → human`, before any model call |
| `memory` | `enabled: true`, `top_k: 10`, `pair_state: compact` | PR memory (runs 4–6): `enabled` (default false), `top_k` (default 10, run 6B), `pair_state` (`compact` default, or `full`; below); adds `duplicate_of` and `silent_days` to the probabilities |
| `rules` | six rules | evaluated top to bottom on calibrated probabilities; first match wins; each has `name`, `when`, `verdict`, `route`, `reason` |
| `default` | `pass → auto-merge-after-ci` | the outcome when no rule matches (default `pass → auto-merge`) |
| `on_error` | `hold → human` | the outcome when the model fails (fail closed; the default is the same) |
| `drift` | `enabled: true` | drift monitor, on by default since v0.3; `enabled: false` turns it off — below |
| `on_drift` | — (commented) | what a PR gets when a rule reads a degraded question (default `{verdict: hold, route: human}`, or `ignore`) — below |
| `actions` | — | what the gateway writes back to the PR in webhook/CI mode (default off) — below |
| `max_rounds` | — | convergence cap: after N non-pass verdicts on one PR, route to a human (default 0 = off) — below |
| `reuse_verdict_on_same_patch` | — | v0.4, default `true`: a new head with the same patch id, policy hash and served model version as an earlier decision on the PR copies its verdict without a model call; `false` asks the model on every head — below |
| `debounce` | — | v0.4, default `45s`: `synchronize` events on one PR within the window are decided once, on the head when the timer fires; `0` disables — below |
| `portal` | — | `url` + `token`: post every gatelog record to the hosted portal; `api_token` + `origin`: auth and CORS for the gateway's own `/api/*` read API — below |

## Question set v4 and the removed `message-mismatch` rule (2026-10-25)

From v0.3 the gateway can ask question set **v4**: v3 without `follows_conventions` and `message_matches_diff` (eight
questions, `prompts/jev_v4.json` / `prompts/haiku_v4.md`). It is opt-in (`question_set: v4`); calibration maps ship
for `jev`, `haiku` and `pplx-flash` (`calibration_<model>_v4.json`, fit on run-5 validation rows). The default stays
`v2`. `v3` ships no map: the gateway asks its ten questions only when `calibration_<model>_v3.json` is present (for
example one fit with `mergeone calibrate --from-gatelog`) and otherwise refuses to start. Runs 1–7 are scored with the
sets they were run with. The check-run summary and the verdict comment list the questions of the policy's set.

The two questions are gone because run 7 showed they drift: on identical input, under an unchanged version string,
`pplx-decider-flash-v1.2` moved `follows_conventions` from a mean probability of 0.26 to 0.82 and `message_matches_diff`
from 0.67 to 0.93, and both fell below chance against the outcome (PROTOCOL.md §R8 C3; `docs/run8-numbers.md`).
Neither cost a shipped decision: no shipped rule ever read `follows_conventions` (run 5 already advised against it), and
the one rule that read `message_matches_diff`, `message-mismatch` (`message_matches_diff <= 0.4` and
`is_logic_change >= 0.5` → hold, human), never fired under the drifted model (mean 0.93). Under v4 the question is not
asked, a missing probability is a non-match, and the rule would silently do nothing, so it is removed from the default
`policy.yaml` and from the template `mergeone init` writes. A policy of your own that still has the rule keeps working
under `v2` / `v3`; under `v4` it never matches.

## Pair-call rendering (`memory.pair_state`)

The PR-memory pair call (`same_change(A, B)`, one per retrieved candidate) is sent the compact rendering by default
from v0.3: A with its diff cut to 2,000 characters, body to 600 and at most 20 files listed; B with body 600 and at
most 20 files; the run-4 caps (A 6,000, B 3,000 characters) re-applied. These are the rules frozen in PROTOCOL.md
§R8 C1. Run 8 (R8-H1, bar Δ ≥ −0.02) measured them against a same-day full-state control: pair AUC −0.018 (jev,
CI [−0.035, −0.004]) / −0.015 (pplx-flash), median pair request 1,564 vs 2,468 tokens (`results/run8/README.md`).

```yaml
memory:
  enabled: true
  top_k: 10
  pair_state: compact   # default; `full` sends the run-4 rendering
```

The main decision call always gets the full state. The Go renderer (`gate/internal/render`) is a port of
`mergeone/data/features.py` `compact_state` / `compact_pair_state` and is checked byte for byte against their output
(`gate/testdata/compact_fixture.json`). A decision with pair calls records `extra.render` (`compact` | `full`); a
state the compact rules cannot parse (no `SUMMARY` / `DIFF` markers, e.g. a hand-written MCP state) is sent full
and counted in `extra.render_fallback`.

## Drift monitor (`drift`, `on_drift`)

The gateway keeps, per model version and question, daily sums of the **raw** (pre-calibration) probabilities it
records (sum, sum of squares, yes = raw ≥ 0.5, n). A question is **degraded** when, with n ≥ 200 in the current
window (trailing 7 days), its mean moved more than **0.20** from the reference or its spread (standard deviation)
fell to half or less. The reference is the 28 days before the current window, or the calibration file's optional
`fit_stats` block while the gatelog is younger than 35 days (no reference: nothing is flagged). The thresholds are
the ones pre-registered in PROTOCOL.md §R8 and are moved only by a run. Spec: [drift-monitor.md](drift-monitor.md).

```yaml
drift:
  enabled: true                           # default since v0.3 (run 8 R8-H5 passed); false turns it off
on_drift: { verdict: hold, route: human } # default; `on_drift: ignore` keeps the rules as they are
```

A policy without a `drift:` key gets the monitor on. Run 8 replayed the run-5 → run-7 predictions through this code
path (`results/run8/drift_replay.json`): it flags exactly `follows_conventions` and `message_matches_diff` for
pplx-flash and nothing for jev or the run-6A self-hosted row (R8-H5 pass).

- `GET /stats` always carries a `drift` block (per model: `reference` = `gatelog` | `fit_stats` | `none`, per question
  `mean_raw`, `shift`, `yes_rate`, `spread`, `n`, the reference values, `degraded`). With `drift.enabled: false` it
  is measurement only and nothing acts on it.
- With `drift.enabled: true` (the default): rules run top to bottom as usual; the first rule whose `when` reads a degraded question
  is not evaluated and the PR gets `on_drift` (rule `on_drift`, reason `drift:<question>`). Rules above it decide as
  before; a degraded question that no rule reads changes no verdict. The result lists the degraded questions
  (`drift`), the commit status description ends in `drift: <question>` while they last, and the server logs each
  degraded question once an hour.
- **Slow signal.** Where PRs have closed with an outcome, the gateway also computes, per model and question, the
  ROC-AUC of the raw answer against `iterated` on PRs decided in the trailing 7 days and in the 28 before. A drop
  of more than **0.05** with n ≥ 200 labelled decisions in the current window flags the question (PROTOCOL.md §R8).
  It is reported as `drift.<model>.slow` (`label`, `reference`, per question `auc`, `ref_auc`, `delta`, `n`, `ref_n`,
  `degraded`; an AUC is null when a window has one class) and its flagged questions are acted on like the fast
  signal's. It lags by the time PRs take to close.
- **Outcomes.** The webhook's `closed` event records `merged` and `iterated` for the PR. `iterated` is the gateway's
  proxy for the run-5 label (two or more force pushes): the PR was decided on 3 or more distinct head SHAs. CI can
  add or correct labels with `POST /api/outcome` (below); fields merge per PR, the latest value winning.
- `mergeone drift-replay --predictions then.jsonl --predictions now.jsonl` runs the same computation over two
  prediction files (both sides restricted to the PRs both answered); a comma joins several files into one side.
  `--labels data/run3/dataset.jsonl --label iterated` adds the slow signal (any JSONL whose rows carry `pr_number`
  and `labels` works, prediction files included). `--mode joint` (default) reads joint rows only; `--mode any` reads
  every request mode, last row per PR, which is the selection `scripts/drift_slow.py` makes. With `--mode any` the
  replay reproduces `results/run8/drift_slow.json` on all four comparisons (flags, n, AUCs to 4 decimals).

## Recalibration (`mergeone calibrate --from-gatelog`)

```
mergeone calibrate --from-gatelog [--since 28d] [--model m] [--label iterated] [--out calibration_<m>_<qset>.json] [--force]
```

Per question, an isotonic map (the fit `gate fit-calibration` makes: sklearn `IsotonicRegression(out_of_bounds="clip",
y_min=0, y_max=1)`, tabulated on 101 points) of the raw answers of PRs decided in the window whose outcome carries the
label. The file has the shape the engine loads, plus `fit_stats` (mean, yes-rate, spread, n per question) and
`fit_meta` (`source: gatelog`, label, since, fit_at, n, model versions). `--model` takes a model version or a registry
name (`jev` matches `jev-*`); the default is the newest labelled decision's version. The default output is
`calibration_<policy model>_<question_set>.json` beside the policy; an existing file is replaced only with `--force`.

- It refuses under 200 labelled decisions (exit 1) and says where outcomes come from.
- Nothing refits automatically. A running gateway keeps its map until it is restarted with the new file.
- A map refit from the gatelog within the last 35 days is the drift reference (its `fit_stats`) even when the gatelog
  is older, so the refit clears the flag it answered.

## Portal (`portal:`) and the `/api/*` read API

```yaml
portal:
  url: https://app.mergeone.dev           # outbound: every gatelog record is also POSTed to <url>/ingest
  token: ${MERGEONE_PORTAL_TOKEN}         # issued per repository by the portal; ${VAR} is read from the environment
  api_token: ${MERGEONE_API_TOKEN}        # inbound: bearer token on /api/*; unset = /api/* only on a loopback bind
  origin: https://app.mergeone.dev        # CORS origin for /api/* (default: the origin of url)
```

All four keys are optional; without the block the gateway behaves as before. `mergeone init --portal <token>
[--portal-url URL]` writes `url` (default `https://app.mergeone.dev`) and `token: "${MERGEONE_PORTAL_TOKEN}"`, and
prints `export MERGEONE_PORTAL_TOKEN=<token>` (the only place the token appears), so the policy can be committed.
`--inline-token` writes the token itself into the file instead.

**Outbound.** With `url` and `token` set, each `store.Record` written by `serve`, `mcp` or `pr` is also queued for
`POST <url>/ingest` with `Authorization: Bearer <token>` and body `{"records": [Record, ...], "events": [Event, ...]}`:
batches of up to 100 or every second, asynchronous, never in a verdict's path. `events` (present only when the batch
has any) carries what the gateway writes to its sidecar log: `{kind: "closed", repo, pr, merged, ts}` when a PR is
closed, `{kind: "resolution", resolution: Resolution, ts}` for a resolution made through `/api/resolve`, and
`{kind: "outcome", outcome: {repo, pr, merged?, reverted?, iterated?, source}, ts}`. A failed POST (network, 5xx, 408, 429) is retried with backoff
(0.5 s doubling, 5 attempts) and then dropped; a full buffer (10,000 records) also drops. `/stats` carries a `portal`
block: `url`, `sent` (records), `events_sent`, `dropped`, `failed_attempts`, `pending`, `last_error`. The gatelog stays the source of truth.

**Inbound: `/api/*`.** Bearer `api_token` on every route (preflight `OPTIONS` excepted). With no `api_token` the routes
answer only when the server is bound to loopback (`--addr 127.0.0.1:8787`), else 403. The acting login is the
`X-Mergeone-Login` header the portal sends (the portal holds the token and vouches for its viewer), else `api` /
`local`. CORS: exact `origin`, credentials allowed, headers `Authorization, Content-Type, X-Mergeone-Login`.

| route | response |
|---|---|
| `GET /api/queue[?repo=]` | `{holds: Record[], matches: {"owner/repo#N": Record}}`. `holds`: the latest record of every open PR whose verdict is `hold` with route `dedup` or `human`, or `block`, and whose head SHA has no resolution yet; oldest first. `matches`: the latest record of each hold's `result.extra.duplicate_candidate`. |
| `GET /api/graph?repo=&since=` | `{nodes, edges}`. Node per open PR decided since `since` (RFC 3339 or an age: `7d`, `36h`, seconds): `{id: "owner/repo#N", repo, pr, title, head_sha, verdict, route, rule, additions, deletions, files, author?, updated}`. Edge per `duplicate_candidate` whose PR is also a node: `{source, target, relation: "duplicate_of", p}` (`p` = calibrated `duplicate_of`); `type` appears only from a typed relation record, and none exist yet. |
| `GET /api/verdict/{sha}[?repo=]` | the whole `store.Record`, `state` included (without `repo`: the newest record for that SHA in any repository); 404 if none |
| `GET /api/stats[?window=]` | `/stats` (window in seconds or `7d`/`36h`; default 24 h): `window_s, decisions, per_hour, verdicts, routes, rules, cost_usd, reused, debounced, error_rate, p50_ms, p99_ms, drift`, `history.daily[] {day, decisions, pass, hold, block, cost_usd}`, and when present `resolutions {total, agreed, approved_as_is, linked, rebase_requested, by_rule{rule: {agreed, overridden}}}`, `calibration {model, fit_at, n, file}` (`fit_at` is the calibration file's modification time; `n` the largest `fit_stats` n, 0 without one), `portal` |
| `POST /api/resolve` | body `{repo, pr, head_sha, action: approve \| link_duplicate \| request_rebase, target_pr?}` → `{ok, logged_at, resolution}`. Records a `store.Resolution` keyed on the head SHA with the acting login (in `<gatelog>.events.jsonl`, fsync'd; the head leaves the queue). `link_duplicate` without `target_pr` takes the record's duplicate candidate. 400 on a bad body, 404 when no decision exists for that PR head. |
| `POST /api/outcome` | body `{repo, pr, merged?, reverted?, iterated?}` (at least one) → `{ok, outcome}`: records a PR's outcome for the slow drift signal and recalibration (CI, e.g. after a revert); merged with what the webhook recorded |
| `POST /api/calibrate` | body (optional) `{since: "28d", model?, label?}` → `{ok, summary, file, note}`: runs `mergeone calibrate --from-gatelog`, writes `calibration_<model>_<qset>.gatelog-<UTC time>.json` beside the gatelog and loads nothing (`summary`: label, since, fit_at, model_versions, n, positives, per question n / positives / mean / yes_rate / spread / fitted). 422 under 200 labelled decisions |

A resolution never merges, closes, or changes the commit status. With `actions.enabled` it is mirrored on the PR through
the verdict-action path: one exclusive label (`mergeone:approved`, `mergeone:duplicate`, `mergeone:rebase`) and, when
`comment` is on, one comment per (head SHA, action) with a hidden `<!-- mergeone:resolve:<sha>:<action> -->` marker.
`resolutions.agreed` counts `link_duplicate` and `request_rebase` (the PR stays held); `approve` is an override.

**`Record.context`.** Decisions on a PR the gateway fetched from GitHub (the webhook path) carry
`context: {author, base, created_at, title, additions, deletions, files}` from the PR metadata. `mergeone pr` with a PR
(`mergeone pr owner/name N`, `--gh-repo`/`--pr`, or the GitHub Actions event) and `GITHUB_TOKEN` fetches the PR the
same way (title, body, files and diff for that head SHA; an older `--head-sha` is rendered from base...sha) and
carries the same context. Without a token it renders the local diff, prints one notice that context is partial, and
records only what git knows (`base` when `--base` names a branch, `additions, deletions, files`); `title` and
`author` stay empty rather than taken from a commit. Decisions whose state was supplied by the caller (MCP,
`POST /decide`, `mergeone pr` without a PR) have none.

**v0.4 record fields.** `patch_id` and `policy_hash` on every decision (`patch_id` empty when the gateway saw no
diff), `reused_from` on a reused verdict, `debounced_shas` on a decision that superseded debounced pushes — above.

## Verdict reuse (`reuse_verdict_on_same_patch`) and webhook debounce (`debounce`) — v0.4

Two model-free savings for agent PRs, which are rebased and pushed in bursts. Neither changes a rule, a threshold or
what a verdict does: a `hold` never closes a PR, the gateway never merges, and every verdict is keyed on the head SHA
it was recorded for.

```yaml
reuse_verdict_on_same_patch: true   # default; false = ask the model on every head
debounce: 45s                       # default; a Go duration or seconds; 0 = decide every push at once
```

**Patch id.** Every decision records `patch_id`: SHA-256 over the PR's diff normalised in the spirit of
`git patch-id --stable` — `index` lines and hunk headers (line numbers, function context) dropped, whitespace removed
from context lines, added/removed lines kept with trailing whitespace trimmed, each file hashed separately and the
sorted file hashes hashed again (file order does not matter). On the webhook path it is computed from the full diff
the gateway fetches; on `POST /decide` and MCP, from the state's `FILES CHANGED` block plus its diff section; a state
without a diff section has none (and is never reused). The same patch on a new base gives the same id; one changed
added or removed line gives a different one.

**Reuse.** Before calling the model for a new head of a PR, the gateway looks for the latest error-free decision on
the same repository and PR with the same `patch_id`, the same `policy_hash` (rules, default, on_error,
always_human_paths, question_set, model, model_id, provider, calibration, memory, max_rounds, drift, on_drift, plus
the model name and the calibration file's path and modification time), a `model_version` equal to the one the
gateway is serving (the newest model answer in the gatelog), and the same drift-degraded questions as now. If one
exists, a new record is written for the new head SHA with that decision's verdict, probabilities, rule, route and
reason, `reused_from: <old head sha>`, `cost_usd: 0` and `latency_ms: 0`; the commit status, labels and per-SHA
comment are posted for the new head as for any decision. Reused records are skipped by the drift monitor (their
answers were counted once already), counted in `/stats` → `reused`, and still count as a head for `max_rounds` and
the `iterated` outcome proxy. The PR's title and body are not part of the patch id: a rebase that also rewrote the
description keeps the earlier verdict.

**Debounce.** A `synchronize` webhook does not enqueue a decision at once: it starts (or extends) a per-PR timer of
`debounce` and posts a `pending` commit status with description `mergeone: waiting for pushes to settle` on the pushed
SHA (context `actions.status_context`, default `mergeone`). When no push arrives for the window, the PR is fetched and
its current head is decided; the record carries `debounced_shas`, the heads pushed in the window that were never
decided (`/stats` → `debounced`). The decision's status replaces the pending one on the head that ran (with actions
off, a commit status mirroring the check-run is posted on that head: `success` for pass and hold, `failure` for block);
superseded heads keep `pending`, which is accurate: they were not checked. `opened`, `reopened` and
`ready_for_review` decide at once and take over a pending window's heads. `on_error` is unchanged: a failed decision
of the head that ran is held, fail closed, as before.

*Restart safety.* Timers live in memory. A gateway restart (or stop) while a window is open drops it: those heads keep
`pending` and are decided on the PR's next push, reopen or `ready_for_review`; nothing is decided twice and nothing
is marked as passed.

**Measuring the savings.** From the gatelog alone: `reused` / `decisions` is the share of decisions that made no model
call; records with the same `(repo, pr, patch_id)` and different `head_sha` count rebases; the summed length of
`debounced_shas` counts pushes never sent to the model. `mergeone stats` prints the first and last.

## Webhook hardening — v0.5

`POST /webhook/github` goes through four checks before anything is queued, all in memory:

1. **Signature.** `X-Hub-Signature-256` is verified against `MERGEONE_WEBHOOK_SECRET` (constant-time). With the
   secret unset, a gateway bound to a non-loopback address refuses every delivery with 401 and logs the reason at
   start; `mergeone serve --allow-unsigned-webhooks` accepts them (only behind a proxy that verifies). A loopback
   bind (a tunnel or reverse proxy in front) accepts unsigned deliveries, as before.
2. **Replay.** The `X-GitHub-Delivery` id is remembered for 24 h; a second delivery with the same id (GitHub's
   *Redeliver*, or a replayed capture) answers `200 {"duplicate": true}` and enqueues nothing. Deliveries without an
   id are not deduplicated. The id is on every record the delivery produced (`delivery_id`).
3. **Rate limit.** One token bucket per repository, `--webhook-rate` events per minute (default 300, burst one
   minute's worth). Beyond it the delivery gets `429` with `Retry-After`; GitHub keeps the failed delivery in the
   repository's webhook log for redelivery, so nothing is lost silently, and one noisy repository cannot fill the
   queue for the others. `/metrics` counts `mergeone_webhooks_total{action="duplicate"|"rate_limited"}`.
4. **Payload.** Bodies are capped at 8 MB, unknown events and actions are acknowledged and ignored, a payload without
   a repository is a 400.

The policy file itself is fuzzed (`go test ./internal/engine -fuzz=FuzzLoadPolicy`): any bytes either load or return
a named error, never a crash at start.

## Observability (`/stats`, `/metrics`, record fields) — v0.5

Every number comes from the gatelog or from counters the gateway keeps since start; nothing is sampled or estimated.

**Record fields** (besides the verdict): `latency_ms` (wall clock), `model_latency_sum_ms` (sum over the calls made),
`input_tokens` / `output_tokens` (main call + pair calls, as the endpoints reported them), `model_calls` (calls that
answered), `pair_errors` (pair calls that did not; the candidate counts as not a duplicate), `retries` (extra calls
the resilience layer made for the main decision), `cost_usd`, `error`, `reused_from`, `debounced_shas`, `shadow`,
`delivery_id` (the webhook's `X-GitHub-Delivery`, also on the fail-closed `worker-error` record).

**`GET /stats?window=86400`** (and `/api/stats`, `mergeone stats`): `decisions`, `per_hour`, `verdicts`, `routes`,
`rules`, `error_rate`, `p50_ms` / `p99_ms` (decision wall clock), `cost_usd`, `reused`, `debounced`, `shadow`, `mode`,
`drift`, `history.daily`, `resolutions`, `calibration`, `breaker` (resilience block), and a `model` block: `calls`,
`retries`, `pair_errors`, `errors_by_kind` (`connect` · `http_429` · `http_5xx` · `http_4xx` · `circuit_open` ·
`bad_reply` · `other`), `input_tokens`, `input_tokens_p50` / `p99` per decision that called the model,
`output_tokens`, `model_latency_p50_ms` / `p99_ms`, `cost_per_decision_usd`.

**`GET /metrics`** is the same information as Prometheus text (no auth; bind the gateway to loopback or a private
network, or put it behind the reverse proxy that already fronts the webhook): `mergeone_decisions_total{verdict,rule,
source}`, `mergeone_model_errors_total{kind}`, `mergeone_model_calls_total`, `mergeone_model_retries_total`,
`mergeone_pair_call_errors_total`, `mergeone_model_tokens_total{direction}`, `mergeone_model_cost_usd_total`,
`mergeone_verdicts_reused_total`, `mergeone_pushes_debounced_total`, `mergeone_shadow_decisions_total`,
`mergeone_webhooks_total{event,action}`, `mergeone_decision_latency_seconds` (histogram, buckets 0.1 s … 300 s, decisions
that called the model), `mergeone_queue_jobs{status}`, `mergeone_mode_shadow`, `mergeone_breaker_open`,
`mergeone_breaker_opened_total`, `mergeone_breaker_short_circuited_total`. Counters restart with the process; the
gatelog is the durable record.

**`GET /healthz`**: `ok`, `workers`, `queue`, `model`, `mode`, `breaker`.

## Policy tests (`mergeone policy check|test|scaffold`) — v0.5

```bash
mergeone policy check    [--policy .mergeone.yaml] [--json]          # load, validate, print the rules in order, the hash, every switch
mergeone policy scaffold [--policy …] [--tests .mergeone-tests.yaml] # write a starter test file from the policy
mergeone policy test     [--policy …] [--tests …] [--json]            # run it; exit 1 on any failed case
```

A test file is YAML, one case per entry:

```yaml
cases:
  - name: weakened tests are blocked
    probs: { weakens_tests: 0.8, needs_human_review: 0.2 }   # raw probabilities; run through the calibration map
    expect: { verdict: block, rule: weakens-tests }            # route is checked when given, rule when given
  - name: migrations always need a human
    paths: [migrations/0007_users.sql]
    expect: { verdict: hold, route: human, rule: always_human_paths }
  - name: a quiet refactor passes
    calibrated: true                                           # skip the map: these are the numbers the rules see
    probs: { is_logic_change: 0.1 }
    expect: { verdict: pass }
```

Questions not named are 0, so a rule fires only on what the case says. `paths` run the protected-path check first,
as the gateway does. `scaffold` writes one case per rule at a probability just past its threshold (earlier rules'
questions at 0, `calibrated: true`), one for the default and one per protected path — the baseline of what the
policy does today, to edit as intent rather than regenerate. The three commands make no model call and no GitHub
call; `policy test` in CI next to `replay --gatelog` is the pair: the cases say what you mean, the replay says what
changes.

## Policy regression check (`mergeone replay --gatelog`) — v0.5

```bash
mergeone replay --gatelog .mergeone/gatelog.jsonl [--policy .mergeone.yaml] [--since 28d] [--json] [--max 20]
```

Every recorded decision is re-run, with no model call, through the policy file and the calibration map the gateway
would load now: the record's `raw_probs` → calibration → rules. The report counts decisions that give the same
verdict, route and rule, the ones that change, and the verdict flips by transition (`hold→pass`), groups changes by
recorded rule → new rule, and lists the first `--max` changed PRs with their head SHAs. Records that never reached the
model are not replayed and are counted by reason: `on_error`, `always_human_paths`, `max_rounds` (the pre-cap
verdict was not recorded), `no_probs`. Exit code 1 when any verdict flips, so a CI step on the policy file fails on a
change that would have decided a past PR differently; `--json` gives the same report as data.

Use it before editing a threshold ("how many of last month's holds become passes at 0.6?"), before a gateway upgrade
(same policy, new binary: the count must be 0 — it is also the test `go test ./cmd/mergeone` runs on fixtures), and
after `mergeone calibrate` refits a map (the replay uses the new map; the gatelog keeps the old probabilities).

## Shadow mode (`mode: shadow`) — v0.5

```yaml
mode: shadow     # enforce (default) | shadow
```

In shadow mode the gateway does everything it does in enforce mode except write to GitHub: webhooks are received
and debounced, PRs are fetched and decided, verdicts are recorded with their input, patch id and policy hash, the
drift monitor counts them, the portal receives them, and `GET /stats` and `mergeone stats` report them (`shadow`
counts the decisions made in this mode; `mode` names the mode). No commit status, label, comment, pending status,
check-run or reviewer request is made — `actions.enabled` is ignored while shadow is on, and the legacy check-run
write-back is off too. A record made in shadow mode carries `shadow: true`.

This is how a team starts: a week in shadow, a look at the gatelog or the portal's *Needs you* screen (every row is
"what the gate would have done"), the thresholds adjusted, then `mode: enforce`. Switching modes does not re-ask the
model: the next event on a head already decided in shadow applies the recorded verdict's actions (the decision is
reused from the gatelog — same input, no error — and only the write-back runs). `mode` is not part of the policy hash,
so verdict reuse across rebases works the same in both modes.

## Failure handling (`on_error`, `resilience:`) — v0.5

What happens when the decision endpoint does not answer is defined, not accidental. Three layers, in order:

```yaml
on_error:                 # the verdict when the model cannot be asked (default below; fail closed)
  verdict: hold
  route: human
  reason: "Decision model unavailable"
resilience:               # retries and the circuit breaker around every model call (main and pair calls)
  retries: 2              # extra attempts on a transient failure (default 2; 0 = one call only)
  backoff: 500ms          # first wait; doubled per attempt, ±25 % jitter (default 500ms)
  max_backoff: 8s         # cap on a wait (default 8s)
  breaker_failures: 5     # consecutive failed decisions that open the breaker (default 5; 0 = never)
  breaker_cooldown: 30s   # how long the breaker stays open before one probe call (default 30s)
```

**Retries** are for transient failures only: a connection error (which is how a client timeout surfaces), HTTP 429,
HTTP 5xx. A 4xx other than 429 and a reply the gateway cannot read are returned at once — repeating them repeats the
failure. Every attempt is one more call to the endpoint with the same body; decisions are idempotent on the server
side (one record per head SHA), so a retry that lands after a timeout costs tokens, never a second verdict. The
record carries `retries` (extra calls the main decision needed; absent when 0).

**The breaker** counts failed *decisions* (after retries), shared by the main call and the pair calls. After
`breaker_failures` in a row it opens: for `breaker_cooldown` every call fails immediately with
`CircuitOpen: N consecutive failures, retrying at <time>` and the PR gets `on_error` — `hold → human`, with the error
on the record — instead of waiting through another set of timeouts. When the cooldown ends one probe call is let
through; success closes the breaker, failure reopens it for another cooldown. `GET /healthz` and `/stats` carry
`breaker`: `state` (`closed` · `open` · `half_open`), `consecutive_failures`, `retry_at`, and totals for `calls`,
`failures`, `retries`, `opened`, `short_circuited`.

**What a held PR looks like.** The commit status is `mergeone: hold` with the `on_error` reason, the label is the
hold label, the comment says the model was unavailable; nothing is passed and nothing is closed. A new push, or
`POST /decide` for the same head once the endpoint is back, decides it normally. The verdict-reuse path needs an
error-free earlier decision, so an `on_error` record is never copied to a later head.

## max_rounds (convergence cap)

With agent authors and automated review a PR can loop: every push is a new head SHA and a new verdict. `max_rounds: N` stops the loop.

```yaml
max_rounds: 3   # default 0 = off
```

- A round is a distinct head SHA of the PR whose latest verdict in the gatelog is `hold` or `block` (re-deciding the same SHA, re-acting and worker retries do not add rounds; model errors are not rounds).
- When a new verdict is not `pass` and the PR already has ≥ N earlier rounds, the verdict is kept, `route` becomes `human`, `rule` becomes `max-rounds`, `capped_rule` keeps the rule that matched, and `reason` is `"<rounds> verdicts without pass on this PR; routed to a human (max_rounds; last rule: <capped_rule>)"`. Probabilities are unchanged.
- A `block` keeps its verdict and only gains `route: human` and `rule: max-rounds`.
- A `pass` is never overridden. Non-pass results carry `rounds` (this PR's non-pass verdicts, including this one) whenever the cap is on.
- A later `pass` does not reset the count (the PR merges on pass, so the loop ends there anyway).
- PR identity is (repository, PR number): the webhook, `POST /decide` with a `pr`, MCP `gate_decide` with a `pr`, and `mergeone pr` with `--gh-repo`/`--pr` (or the GitHub Actions event). No PR number, no counting. In CI the count needs the gatelog (`--log`) to persist between runs.

## Verdict actions

The policy (`.mergeone.yaml`; the default is [`mergeone/gate/policy.yaml`](../mergeone/gate/policy.yaml)) can also say what the gateway writes back to the PR. It is deterministic (no model call), **off by default**, and runs only in webhook mode (`mergeone serve` + `/webhook/github`, or `POST /decide` with a `pr`) and CI mode (`mergeone pr` with a PR number) — never from the MCP tools.

```yaml
actions:
  enabled: false                 # default off
  status_context: mergeone       # commit status context
  comment: true                  # the verdict comment (idempotent per head SHA via a hidden <!-- mergeone:<sha> --> marker)
  comment_mode: update           # update (default, v0.5): one comment per PR, edited on every new head | per_head: a new comment per head (v0.4)
  on_pass:  { status: success, labels: ["mergeone:pass"] }
  on_hold:  { status: failure, labels: ["mergeone:hold"], reviewers: [], team_reviewers: [] }
  on_block: { status: failure, labels: ["mergeone:block"] }
```

| per verdict | GitHub REST calls |
|---|---|
| status | `POST /repos/{o}/{r}/statuses/{sha}` — `state` from the verdict, `context` = `status_context`, `description` = `<verdict> · <rule> · <reason>` (≤ 140 chars), `target_url` = the gatelog entry (`<public-url>/verdict/{o}/{r}/{sha}`) when `--public-url` is set |
| labels | `GET /issues/{n}/labels`, then `DELETE /issues/{n}/labels/{name}` for every label of the *other* verdicts that is present, and `POST /issues/{n}/labels` for this verdict's labels that are missing |
| comment | `GET /issues/{n}/comments` (up to 10 pages of 100) looking for the markers. `comment_mode: update`: the PR's one verdict comment (`<!-- mergeone:verdict -->`) is `POST`ed by the first head and `PATCH`ed (`/issues/comments/{id}`) by every later head — the body is the new head's verdict and an `earlier heads: \`abc1234\` hold · …` line (up to 8, newest first); a head already in the comment changes nothing. `comment_mode: per_head`: `POST` only if no comment carries the head's marker. Body: verdict and rule, reason, the questions of the policy's `question_set` with calibrated probabilities, `memory: candidate #N (duplicate_of p=…)` when the PR memory scored a candidate |
| reviewers | `GET /pulls/{n}/requested_reviewers`, then `POST /pulls/{n}/requested_reviewers` for the users/teams not yet requested |

- `status` ∈ `success | failure | pending | error`; defaults are success / failure / failure. A missing `labels` key takes the default label; `labels: []` sets none.
- Labels are exclusive: the exclusive set is every label named under `on_pass`, `on_hold` and `on_block`; applying one verdict removes the others. Other labels are never touched.
- Reviewers are requested only when the verdict is `hold` or `block`, **and** the route is `human`, **and** that verdict's `reviewers` / `team_reviewers` list is non-empty (a hold routed to `dedup`, `stale` or `domain-review` requests nobody). `on_pass` with reviewers is a policy error.
- **Never merges.** The commit status is what lets branch protection (require the `mergeone` status) or GitHub auto-merge proceed.
- Idempotent for the same (PR, head sha, verdict): a decision served from the gatelog is not acted on again; labels and reviewers already present are not re-sent; the marker stops a second comment (or a second edit) for the same sha. In `update` mode an agent that pushes twenty heads leaves one comment on the PR, not twenty; the report says `comment` for a post and `comment~` for an edit.
- No retries. A failed call is recorded and the rest still run; failures **never change the verdict**. The report is added to the gatelog entry and to the response as `actions: {applied: [...], skipped: "...", errors: [...]}` (`skipped` says why nothing ran: disabled, no token, no PR number). The webhook answers `{queued}` at once, so its report is in the gatelog and in `GET /verdict/...`.
- When enabled, the status and `mergeone:*` labels replace the gateway's check-run and `gate:*` / `route:*` labels. If a webhook job fails (and may be retried), only the status is posted with the `on_error` verdict; labels, comment and reviewers wait for a real verdict. `POST /decide` with `"actions": false` opts a call out (the load generator does).
- CI mode: `mergeone pr` reads the PR number and head sha from `GITHUB_EVENT_PATH` and the repository from `GITHUB_REPOSITORY` (or `--pr`, `--head-sha`, `--gh-repo`, or `mergeone pr owner/name N`) and authenticates with `GITHUB_TOKEN`; with the token the PR itself is read from GitHub (`GITHUB_API_URL` overrides the API base). The exit code still follows the verdict.

Least-privilege GitHub App (or fine-grained token / workflow `permissions:`): **Commit statuses: write** (`statuses: write`), **Pull requests: write** (`pull-requests: write`, reviewers; also read for the PR fetch), **Issues: write** (`issues: write`, labels and comments), plus the implicit Metadata: read. Contents is not needed.

