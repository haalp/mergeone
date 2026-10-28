# Portal

The hosted portal (queue of PRs that need a person, the PR braid, the health page) lives in a separate, private
repository: `haalp/mergeone-portal`. This repository stays the open-source gateway and benchmark. The gateway talks to
the portal through one optional block in `.mergeone.yaml`:

```yaml
portal:
  url: https://app.mergeone.dev
  token: ${MERGEONE_PORTAL_TOKEN}   # issued per repository when you connect it in the portal
  api_token: ${MERGEONE_API_TOKEN}  # optional: lets the portal read this gateway's /api/* routes
```

`mergeone init --portal <token>` writes `url` and `token: "${MERGEONE_PORTAL_TOKEN}"` and prints the
`export MERGEONE_PORTAL_TOKEN=…` line to run where the gateway runs; `--inline-token` writes the token into the file. The full reference is `docs/POLICY.md` → "Portal".

**Gateway → portal.** With `url` and `token` set, every gatelog record (`store.Record`: head-SHA keyed, raw and
calibrated probabilities, verdict, rule, route, matched candidate, `context`, the exact `state`) is also posted to
`<url>/ingest` as `{"records": [...], "events": [...]}` with `Authorization: Bearer <token>`: batched, asynchronous,
retried with backoff, dropped and counted (`/stats` → `portal.dropped`) after 5 failed attempts.

`events` (only in batches that have any) are what happened to a PR after its decision, so the portal can drop it from
"Needs you" without polling the gateway:

| `kind` | fields | sent when |
|---|---|---|
| `closed` | `repo, pr, merged, ts` | the webhook's `closed` event (merged or not) |
| `resolution` | `resolution: {repo, pr, head_sha, action, target_pr?, login, verdict, route, rule, actions?, ts}`, `ts` | a resolution recorded through `POST /api/resolve` |
| `outcome` | `outcome: {repo, pr, merged?, reverted?, iterated?, source, ts}`, `ts` | the gateway records outcome labels (at close, or `POST /api/outcome`) |

A closed PR leaves the queue when its `closed` event is newer than its latest record; a head with a `resolution`
leaves it until a new push. A verdict never waits on the portal; the
gateway stays the source of truth and works unchanged without the block.

**Portal → gateway.** The gateway serves, under bearer `api_token` and CORS for `origin` (default: the origin of `url`):

| route | what |
|---|---|
| `GET /api/queue` | open holds (route `dedup`/`human`) and blocks without a resolution, oldest first, plus the latest record of each one's duplicate candidate |
| `GET /api/graph?repo=&since=` | PR nodes and `duplicate_of` edges (`p` = calibrated probability; typed edges once relation records exist) |
| `GET /api/verdict/{sha}?repo=` | the whole record, `state` included |
| `GET /api/stats?window=` | `/stats` plus `history.daily`, `resolutions`, `calibration`, `portal` |
| `POST /api/resolve` | records a resolution (approve, link as duplicate, request rebase) with the acting login (`X-Mergeone-Login`) |
| `POST /api/outcome` | records a PR's outcome labels (`merged`, `reverted`, `iterated`) for the slow drift signal and recalibration |
| `POST /api/calibrate` | refits the calibration from the gatelog (`mergeone calibrate --from-gatelog`), writes it beside the gatelog, returns the fit summary; loads nothing |

`Record.context` (`author, base, created_at, title, additions, deletions, files`) is filled for PRs fetched from GitHub (the webhook,
or `mergeone pr` with `GITHUB_TOKEN`). The portal never merges or closes anything; a resolution comes back to the PR as a `mergeone:*` label and
one comment through the same path the verdict actions use (only with `actions.enabled`), and never changes the commit
status.
