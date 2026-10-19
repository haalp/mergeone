# Policy reference

`.mergeone.yaml` is the whole product surface. This page is the full reference; the README has the short version.

## The file

The default policy ships as [`mergeone/gate/policy.yaml`](../mergeone/gate/policy.yaml) and `mergeone init` writes a commented copy into your repo. Keys:

| key | meaning |
|---|---|
| `model` | a row of [`prompts/models.yaml`](../prompts/models.yaml) (`jev`, `pplx`, `pplx-flash`, `haiku`, `local`, …); `model_id` pins a version; `provider:` defines a private endpoint inline |
| `question_set` | `v2` (default, five questions) or `v3` (ten; `hard_to_revert`, `exceeds_scope` recommended, `follows_conventions` not — run 5) |
| `calibration` | `auto` uses the shipped isotonic map for the model |
| `always_human_paths` | path prefixes that always `hold → human`, before any model call |
| `memory` | PR memory (run 4–6): `enabled`, `top_k`; adds `duplicate_of` and `silent_days` to the probabilities |
| `rules` | evaluated top to bottom on calibrated probabilities; first match wins; each has `name`, `when`, `verdict`, `route`, `reason` |
| `default` / `on_error` | the outcome when no rule matches / when the model fails (fail closed: `hold → human`) |
| `actions` | what the gateway writes back to the PR in webhook/CI mode — below |

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

