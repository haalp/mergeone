# Policy reference

`.mergeone.yaml` is the whole product surface. This page is the full reference; the README has the short version.

## The file

The default policy ships as [`mergeone/gate/policy.yaml`](../mergeone/gate/policy.yaml) and `mergeone init` writes a commented copy into your repo. Keys:

| key | meaning |
|---|---|
| `model` | a row of [`prompts/models.yaml`](../prompts/models.yaml) (`jev`, `pplx`, `pplx-flash`, `haiku`, `local`, …); `model_id` pins a version; `provider:` defines a private endpoint inline |
| `question_set` | `v2` (default today, five questions), `v3` (ten; run 5) or `v4` (v3 minus `follows_conventions` and `message_matches_diff`; the set the gateway asks from v0.3) — below |
| `calibration` | `auto` uses the shipped isotonic map for the model |
| `always_human_paths` | path prefixes that always `hold → human`, before any model call |
| `memory` | PR memory (run 4–6): `enabled`, `top_k`; adds `duplicate_of` and `silent_days` to the probabilities |
| `rules` | evaluated top to bottom on calibrated probabilities; first match wins; each has `name`, `when`, `verdict`, `route`, `reason` |
| `default` / `on_error` | the outcome when no rule matches / when the model fails (fail closed: `hold → human`) |
| `actions` | what the gateway writes back to the PR in webhook/CI mode — below |
| `max_rounds` | convergence cap: after N non-pass verdicts on one PR, route to a human (default 0 = off) — below |
| `drift` / `on_drift` | drift monitor (`drift: {enabled: false}` default) and what a PR gets when a rule reads a degraded question (default `{verdict: hold, route: human}`, or `ignore`) — below |

## Question set v4 and the removed `message-mismatch` rule (2026-10-25)

From v0.3 the gateway asks question set **v4**: v3 without `follows_conventions` and `message_matches_diff` (eight
questions, `prompts/jev_v4.json`). The shipped default stays `v2` until a v4 calibration map is fit and shipped; the
v2 and v3 sets stay selectable, and runs 1–7 are scored with the sets they were run with.

The two questions are gone because run 7 showed they drift: on identical input, under an unchanged version string,
`pplx-decider-flash-v1.2` moved `follows_conventions` from a mean probability of 0.26 to 0.82 and `message_matches_diff`
from 0.67 to 0.93, and both fell below chance against the outcome (PROTOCOL.md §R8 C3; `docs/run8-numbers.md`).
Neither cost a shipped decision: no shipped rule ever read `follows_conventions` (run 5 already advised against it), and
the one rule that read `message_matches_diff`, `message-mismatch` (`message_matches_diff <= 0.4` and
`is_logic_change >= 0.5` → hold, human), never fired under the drifted model (mean 0.93). Under v4 the question is not
asked, a missing probability is a non-match, and the rule would silently do nothing, so it is removed from the default
`policy.yaml` and from the template `mergeone init` writes. A policy of your own that still has the rule keeps working
under `v2` / `v3`; under `v4` it never matches.

## Drift monitor (`drift`, `on_drift`)

The gateway keeps, per model version and question, daily sums of the **raw** (pre-calibration) probabilities it
records (sum, sum of squares, yes = raw ≥ 0.5, n). A question is **degraded** when, with n ≥ 200 in the current
window (trailing 7 days), its mean moved more than **0.20** from the reference or its spread (standard deviation)
fell to half or less. The reference is the 28 days before the current window, or the calibration file's optional
`fit_stats` block while the gatelog is younger than 35 days (no reference: nothing is flagged). The thresholds are
the ones pre-registered in PROTOCOL.md §R8 and are moved only by a run. Spec: [drift-monitor.md](drift-monitor.md).

```yaml
drift:
  enabled: false                          # default
on_drift: { verdict: hold, route: human } # default; `on_drift: ignore` keeps the rules as they are
```

- `GET /stats` always carries a `drift` block (per model: `reference` = `gatelog` | `fit_stats` | `none`, per question
  `mean_raw`, `shift`, `yes_rate`, `spread`, `n`, the reference values, `degraded`). It is measurement only; nothing
  acts on it while `drift.enabled` is false.
- With `drift.enabled: true`: rules run top to bottom as usual; the first rule whose `when` reads a degraded question
  is not evaluated and the PR gets `on_drift` (rule `on_drift`, reason `drift:<question>`). Rules above it decide as
  before; a degraded question that no rule reads changes no verdict. The result lists the degraded questions
  (`drift`), the commit status description ends in `drift: <question>` while they last, and the server logs each
  degraded question once an hour.
- Not yet implemented: the slow signal (AUC against outcomes fell > 0.05) and `mergeone calibrate --from-gatelog`.
- `mergeone drift-replay --predictions then.jsonl --predictions now.jsonl` runs the same computation over two
  prediction files (both sides restricted to the PRs both answered); a comma joins several files into one side.

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
  comment: true                  # one comment per head SHA (idempotent via a hidden <!-- mergeone:<sha> --> marker)
  on_pass:  { status: success, labels: ["mergeone:pass"] }
  on_hold:  { status: failure, labels: ["mergeone:hold"], reviewers: [], team_reviewers: [] }
  on_block: { status: failure, labels: ["mergeone:block"] }
```

| per verdict | GitHub REST calls |
|---|---|
| status | `POST /repos/{o}/{r}/statuses/{sha}` — `state` from the verdict, `context` = `status_context`, `description` = `<verdict> · <rule> · <reason>` (≤ 140 chars), `target_url` = the gatelog entry (`<public-url>/verdict/{o}/{r}/{sha}`) when `--public-url` is set |
| labels | `GET /issues/{n}/labels`, then `DELETE /issues/{n}/labels/{name}` for every label of the *other* verdicts that is present, and `POST /issues/{n}/labels` for this verdict's labels that are missing |
| comment | `GET /issues/{n}/comments` (up to 10 pages of 100) looking for the marker; `POST /issues/{n}/comments` only if absent. Verdict and rule, reason, the typed questions with calibrated probabilities, `memory: candidate #N (duplicate_of p=…)` when the PR memory scored a candidate |
| reviewers | `GET /pulls/{n}/requested_reviewers`, then `POST /pulls/{n}/requested_reviewers` for the users/teams not yet requested |

- `status` ∈ `success | failure | pending | error`; defaults are success / failure / failure. A missing `labels` key takes the default label; `labels: []` sets none.
- Labels are exclusive: the exclusive set is every label named under `on_pass`, `on_hold` and `on_block`; applying one verdict removes the others. Other labels are never touched.
- Reviewers are requested only when the verdict is `hold` or `block`, **and** the route is `human`, **and** that verdict's `reviewers` / `team_reviewers` list is non-empty (a hold routed to `dedup`, `stale` or `domain-review` requests nobody). `on_pass` with reviewers is a policy error.
- **Never merges.** The commit status is what lets branch protection (require the `mergeone` status) or GitHub auto-merge proceed.
- Idempotent for the same (PR, head sha, verdict): a decision served from the gatelog is not acted on again; labels and reviewers already present are not re-sent; the marker stops a second comment for the same sha.
- No retries. A failed call is recorded and the rest still run; failures **never change the verdict**. The report is added to the gatelog entry and to the response as `actions: {applied: [...], skipped: "...", errors: [...]}` (`skipped` says why nothing ran: disabled, no token, no PR number). The webhook answers `{queued}` at once, so its report is in the gatelog and in `GET /verdict/...`.
- When enabled, the status and `mergeone:*` labels replace the gateway's check-run and `gate:*` / `route:*` labels. If a webhook job fails (and may be retried), only the status is posted with the `on_error` verdict; labels, comment and reviewers wait for a real verdict. `POST /decide` with `"actions": false` opts a call out (the load generator does).
- CI mode: `mergeone pr` reads the PR number and head sha from `GITHUB_EVENT_PATH` and the repository from `GITHUB_REPOSITORY` (or `--pr`, `--head-sha`, `--gh-repo`) and authenticates with `GITHUB_TOKEN`. The exit code still follows the verdict.

Least-privilege GitHub App (or fine-grained token / workflow `permissions:`): **Commit statuses: write** (`statuses: write`), **Pull requests: write** (`pull-requests: write`, reviewers; also read for the PR fetch), **Issues: write** (`issues: write`, labels and comments), plus the implicit Metadata: read. Contents is not needed.

