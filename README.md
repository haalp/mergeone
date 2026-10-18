<p align="center">
  <img src="docs/img/mark.svg" width="112" alt="mergeone">
</p>

<h1 align="center">mergeone</h1>

<p align="center"><b>One decision per merge, at swarm scale.</b><br>
<sub>An open-source merge gateway for repositories where agents write most of the commits — and the pre-registered study that says what it can and cannot do.</sub></p>

<p align="center">
  <a href="https://github.com/haalp/mergeone/actions/workflows/ci.yml"><img src="https://github.com/haalp/mergeone/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <a href="https://github.com/haalp/mergeone/releases"><img src="https://img.shields.io/github/v/release/haalp/mergeone?include_prereleases&color=A371F7&labelColor=0E0F12" alt="release"></a>
  <img src="https://img.shields.io/badge/throughput-30M%20decisions%2Fday%20%40%20p99%20441ms-A371F7?labelColor=0E0F12" alt="throughput">
  <img src="https://img.shields.io/badge/binary-11.7%20MB%2C%20zero%20runtime-ECECEA?labelColor=0E0F12" alt="binary">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-ECECEA?labelColor=0E0F12" alt="license"></a>
  <a href="https://mergeone.dev"><img src="https://img.shields.io/badge/study-mergeone.dev-ECECEA?labelColor=0E0F12" alt="website"></a>
</p>

<p align="center">
  <img src="docs/img/sorting-turnstile.png" width="760" alt="Fig 1 — Sorting turnstile: three agent chutes feed one rotary gate; each PR gets one verdict: green rides the belt to main, amber goes to the human shelf, red drops to the bin.">
</p>

Anthropic's monorepo went from 2,500 to **30,000 merges a day**, almost all agent-written, and they rebuilt source control to cope ([Redesigning source control](https://www.anthropic.com/engineering/redesigning-source-control)). Their post is about storage. It leaves the harder question open: **at that rate, who decides what merges?**

`mergeone` is an attempt at the answer: a small, fast, *typed* decision layer in front of trunk. A System One model answers five fixed questions per PR with calibrated probabilities in one ~300 ms call; a policy file you own turns them into `pass` / `hold` / `block`; a planner lands non-conflicting PRs in waves. It ships as one Go binary with a GitHub webhook, an HTTP API and an MCP server for agents.

It is also a benchmark. Every claim below was pre-registered in [`PROTOCOL.md`](PROTOCOL.md) before the data existed, and **three of the six runs failed outright, two half-failed**. They are reported anyway.

## Scoreboard

| run | question | result | number |
|---|---|---|---|
| 1 | Can a diff predict a 30-day revert? | ❌ negative | PR-AUC 0.010 vs base 0.0075, all four systems |
| 2 | Can a System One model answer typed triage questions? | ✅ positive | ROC-AUC 0.87–0.96 vs a strong judge, 347 ms, $0.074/1k — bar met 5/5 (Jev), 4/5 (Haiku) |
| 3 | Does the gate predict rejection of **real agent PRs**? | ❌ negative | ROC-AUC 0.55 (bar 0.70). 67% of closures are superseded/duplicate/direction — not visible in a diff. Volume: Jev p99 417 ms, 0 errors at 100× |
| 4 | Can a per-repo PR memory catch the duplicates? | 🟡 mixed | The pair question works: `same_change` ROC-AUC **0.87** pplx-decider, 0.84 pplx-flash, 0.83 Jev (bar 0.80); Haiku 0.76 fails. The pipeline does not yet: retrieval recall@5 0.55 (bar 0.60), dedup rule cuts pass-group bad rate 0.41 → 0.36 (bar 0.23), retrieval p99 198 ms (bar 20). Memory stays opt-in |
| 5 | Can better retrieval, declared assumptions and a wider question set move the gate? | 🟡 mixed | Retrieval recall@5 0.55 → **0.66** (bar 0.70), Go p99 **3.3 ms** ✅. "Flag ⇒ more review rounds" replicates pre-registered: `needs_human_review` → force-push ≥ 2 AUC **0.76** Jev, **0.76** pplx-flash (bar 0.70); 10-question stack misses 0.75. Declared assumptions not evaluable (9 labelled relations in 2,196 PRs). Perplexity hosted API: 71% HTTP 429 at 100× (10 rps/org) |

What survives: typed triage of *what a change does* at System One speed and cost, a gateway that handles 30 million decisions a day on a 2-vCPU box, and — new in run 4 — a cheap model can tell whether two PRs are *the same change* (0.83–0.87 AUC, ≤ $0.0005 per PR). What does not: predicting what a project will *accept* from one diff, and, so far, finding the right PR to compare against. Details in [`results/`](results/) and on [mergeone.dev](https://mergeone.dev).

```
PR ──▶ decision model ──▶ calibrated probabilities ──▶ .mergeone.yaml ──▶ verdict + route
       Jev / Haiku          is_logic_change      0.91    weakens_tests >= 0.6 → block     pass · hold · block
       ~300 ms, $0.00007    weakens_tests        0.07    needs_human    >= 0.5 → hold     auto-merge · human · dedup
                            message_matches_diff 0.88    duplicate_of   >= 0.7 → hold
                            touches_concurrency  0.63                  └─▶ gatelog: every verdict + its exact input
                            needs_human_review   0.41
```

## 60 seconds

```bash
curl -fsSL https://mergeone.dev/install.sh | sh          # one static binary; or: go install github.com/haalp/mergeone/gate/cmd/mergeone@latest
export TYPESAFE_API_KEY=...                                # Jev. Any row of prompts/models.yaml works: pplx, pplx-flash, haiku, clef, local …
cp ~/.mergeone/policy.yaml .mergeone.yaml
mergeone pr --policy .mergeone.yaml --base origin/main --head HEAD
```

```json
{ "verdict": "hold", "route": "human", "rule": "message-mismatch",
  "reason": "Message does not explain the behavioural change in the diff",
  "probs": { "is_logic_change": 0.93, "weakens_tests": 0.05, "message_matches_diff": 0.31,
             "touches_concurrency": 0.12, "needs_human_review": 0.58 },
  "model_version": "jev-1.13.0", "latency_ms": 318, "cost_usd": 0.00007 }
```

Exit 0 = pass, 1 = hold, 2 = block. Drop it into any CI; `examples/github-action.yml` labels PRs `gate:<verdict>` / `route:<route>`.

No API key yet? `mergeone serve --mock` runs the whole pipeline with a deterministic stand-in model, and `mergeone replay` dry-runs your policy over 1,200 real Kubernetes PRs shipped in the repo, so you can tune thresholds before a single real decision.

## What you get

| | |
|---|---|
| **Typed, not prose** | The model returns probabilities, not a review comment. A rule is one line of YAML. First match wins. |
| **Calibrated** | `0.6` means sixty percent. Isotonic maps fitted on labelled history ship with the binary (raw ECE 0.17 → 0.03). |
| **Fail closed** | Model down, timeout, panic: the PR holds for a human. Never a silent pass. |
| **Auditable** | System One models give no rationale, so every verdict is logged with its exact input and hash. `mergeone stats` reads it back. |
| **Agent-native** | It is an MCP server. The agent calls `gate_decide` *before* merging. `claude mcp add mergeone -- mergeone mcp` |
| **Swarm-aware** | `/plan` turns hundreds of open PRs into waves that land in parallel, with file and semantic conflicts as edges. Not a queue. |
| **Built for the volume** | 350 decisions/s on one 2-vCPU box, p99 441 ms, 67 MB RSS, zero errors. CI re-proves it on every push. |

## Serve it

```bash
export GITHUB_TOKEN=... MERGEONE_WEBHOOK_SECRET=...
mergeone serve --policy .mergeone.yaml --workers 16 --addr :8787
# GitHub webhook (pull_request) → https://<host>/webhook/github
```

Each PR head gets one idempotent decision, a check-run (`success` / `neutral` / `failure`), two labels, and a line in the gatelog. With [verdict actions](#policy-verdict-actions) enabled, a commit status, exclusive `mergeone:*` labels, one comment and reviewers replace the check-run and labels.

```bash
curl -X POST :8787/decide -d '{"repo":"acme/x","head_sha":"…","state":"<rendered PR>","paths":["pkg/a.go"]}'
curl :8787/verdict/acme/x/<sha>
curl :8787/plan/acme/x?semantic=true
curl :8787/stats?window=3600
```

| load (open-loop, mock 300 ms model, 2-vCPU GitHub runner) | decisions/s | p50 | p99 | errors |
|---|---|---|---|---|
| 100× Anthropic's volume · 3M/day | 35 | 304 ms | 436 ms | 0 |
| 1000× · 30M/day | 350 | 303 ms | 441 ms | 0 |

The gateway adds single-digit milliseconds; the rest is the model. With Jev the model itself held 35/s at p99 527 ms against the real API.

## Policy: verdict actions

The policy (`.mergeone.yaml`; the default is [`mergeone/gate/policy.yaml`](mergeone/gate/policy.yaml)) can also say what the gateway writes back to the PR. It is deterministic (no model call), **off by default**, and runs only in webhook mode (`mergeone serve` + `/webhook/github`, or `POST /decide` with a `pr`) and CI mode (`mergeone pr` with a PR number) — never from the MCP tools.

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

## The agent asks before it merges

```bash
claude mcp add mergeone -- mergeone mcp --policy .mergeone.yaml
```

```
# CLAUDE.md
Before merging, call gate_decide with repo_path="." base="main" head="HEAD".
Merge only on "pass". On "hold" or "block", open a PR and request the route named.
```

Tools: `gate_decide`, `gate_verdict`, `gate_plan`, `gate_stats`. Same calls over HTTP for everything that is not an MCP client.

## Evidence, including what did not work

Everything above rests on **gatebench**, a pre-registered benchmark: hypotheses and thresholds were committed before any model ran ([PROTOCOL.md](PROTOCOL.md)), and results are published whichever way they fall. 40,000 Kubernetes merges, four systems, two runs so far.

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

**Run 3, negative.** *Does the gate predict whether humans reject real agent PRs?* No. On 2,196 PRs by Copilot, Claude Code, Claude, Devin and Cursor across 26 public repos (held-out test: 705 PRs, 8 repos), calibrated `needs_human_review` → *closed-unmerged ∨ change-requested* scores ROC-AUC **0.55** for Jev, 0.55 for Haiku, 0.53 for rules; the pre-registered bar was 0.70. The reason is in the labels: of 200 closures we classified, 67% were *superseded, duplicate or direction* and only 19% *quality*. A diff cannot tell you someone else already landed the fix. Two things did hold: Jev at 100× agent-scale load — p99 417 ms, 0 errors (Haiku: 2,880 ms, 0.3% errors) — and, exploratory only, the questions track *review iteration* (force-pushes ≥ 2) at AUC 0.70–0.81. Full write-up: [`results/run3/README.md`](results/run3/README.md).

**Run 4, mixed.** *Can a per-repository PR memory catch the duplicates run 3 found?* Half of it. Given a candidate pair, the typed question `same_change(A, B)` separates maintainer-confirmed supersedes from the rest at ROC-AUC **0.87** (Perplexity pplx-decider, open weights), **0.84** (pplx-flash, 4B) and **0.83** (Jev) on 187 held-out pairs — all above the 0.80 bar, all at ≤ 0.5 s p99 and ≤ $0.0005 per PR; Claude Haiku 5.5 scores 0.76 and fails. But the deterministic retrieval that picks the candidates finds only 55% of the gold duplicates in its top 5 (bar 0.60), so at the verdict level the dedup rule lowers the pass group's bad-outcome rate from 0.41 to 0.36 (bar: 0.23) and catches 8 more points of closed PRs (bar: 15). Retrieval p99 is 198 ms against a 20 ms bar. Four of five hypotheses fail; the one that passes is the one that was uncertain. Memory ships opt-in. Full write-up: [`results/run4/README.md`](results/run4/README.md).

**Run 5, mixed.** *Can the gate be made better on the two things runs 3–4 exposed — finding the PR to compare against, and knowing which PRs will need review?* Partly. Retrieval v2 (shared-issue references, directory overlap, weights fit on val only) lifts recall@5 from 0.55 to **0.66** — short of the 0.70 bar, and the changed-line content stage moved nothing — while the Go store answers in **3.3 ms p99** against a 20 ms bar. Run 3's exploratory finding becomes a result: calibrated `needs_human_review` predicts ≥ 2 force-push rounds at AUC **0.76** for Jev and for the 4B open-weight pplx-flash (bar 0.70); the ten-question stack misses its 0.75 bar for every System One model because the val-chosen pruning rule dropped `hard_to_revert`, the strongest question on test (0.85). Declared assumptions could not be judged — maintainers wrote a numbered "depends on / conflicts with" comment on 9 of 2,196 PRs. Both Perplexity models hold p99 flat from 1× to 100× and then lose 71% of calls to HTTP 429: a per-org rate limit, which self-hosting the open weights removes. Full write-up: [`results/run5/README.md`](results/run5/README.md).

**Run 6A, negative.** *Do the open weights clear the scale bar when we serve them ourselves?* Not on one GPU. pplx-decider-flash-v1.2 on a rented NVIDIA L4 ($0.59/h), served with the vendor's own code, agrees with the hosted endpoint on **95.2%** of answers but not within the 0.02 mean |Δp| bar (0.047), so it counts as a different model; its quality is a CI-width away (AUC **0.747** vs 0.758, pair task 0.840 vs 0.842), one-question-per-request gains **+0.000** AUC for 10× the calls (joint stays), and the server saturates at **~2.2 requests/s** — 6× the base rate, with ×10 already queueing and ×100 losing every call. The vendor's rate limit becomes a capacity-planning problem; one 24 GB card is an order of magnitude under the target. $1.44 of GPU. Part B (wider candidate pool, 100-PR blind hold precision, two labellers) is in progress. Full write-up: [`results/run6/README.md`](results/run6/README.md).

Raw predictions, judge labels, calibration maps and figures: `results/run1` … `results/run6`. Rebuild every table with one command.

### Which decision model should sit in the gate?

[`results/leaderboard.md`](results/leaderboard.md) ranks every model that has been run under the protocol on the four things a gateway pays for — quality (AUC vs ground truth), calibration (ECE), speed (p99 at 100× load) and cost — with the scale bar as a hard gate. Reporting only, rebuilt by `mergeone leaderboard`.

| # | model | kind | scale at 100× | quality AUC (mean over runs) | ECE | p99 at 100× | $/1k PRs | runs |
|---|---|---|---|---|---|---|---|---|
| 1 | Jev | System One, hosted | **pass** | 0.73 | 0.073 | 527 ms | 0.089 | 5 |
| — | GBM / rules | floors, no model call | n/a | 0.61 / 0.58 | 0.003 / 0.105 | ≤ 9 ms | 0 | 1 / 4 |
| 4 | pplx-decider-flash 4B | System One, open weights | **fail** (71% HTTP 429) | **0.80** | 0.053 | 501 ms | 0.022 | 2 |
| 5 | pplx-decider 27B | System One, open weights | **fail** (71% HTTP 429) | 0.78 | 0.078 | 1,032 ms | 0.044 | 2 |
| 6 | pplx-decider-flash 4B, self-hosted (one L4) | System One, open weights | **fail** (saturates at ×10) | 0.75 | 0.052 | — (×1: 1,462 ms) | 0.472 | 1 |
| 7 | Claude Haiku 5.5 | LLM judge, JSON schema | **fail** | 0.71 | 0.060 | 9,662 ms | 0.370 | 5 |

The ordering is decided by the scale column, not by a few AUC points. Haiku matches Jev on quality and fails the 100× bar in every run on latency. The two Perplexity models lead on quality in runs 4–5 and hold their p99 flat from 1× to 100× — and then lose 71% of calls to the hosted API's 10 rps per-organisation limit (run 5). That is a vendor ceiling, not a model one: the same weights self-hosted (a `provider:` row pointing at llama.cpp or vLLM) carry no such limit — and run 6A measured it: on one L4 the limit becomes throughput (~2.2 requests/s, saturating at ×10), at 20× the hosted list price per PR. Quality is not comparable across runs (different tasks), so a mean over runs is a tie-breaker, not a score. The remaining open-weight candidates (Cloudflare `clef`, AutoTrust `JEV-9B/27B`, H2O Lightning 4B, Laya 421M) are in the registry; until they have a row, their public-leaderboard numbers are claims, not measurements.

**Any model, one YAML row.** The gateway does not know the model's name. [`prompts/models.yaml`](prompts/models.yaml) lists each candidate once — wire dialect (`systemone`, `decide`, `anthropic`), endpoint, key variable, model id, list price — and `model: <row>` in `.mergeone.yaml` or `--model <row>` in gatebench uses it. A private llama.cpp or vLLM server serving open weights is a `provider:` block in the policy; no code, no rebuild. `mergeone models` prints the registry.

## Limits, plainly

- The gate judges *what a change does*, not whether a project will take it. Run 3 shows most agent-PR rejections are coordination (duplicates, superseded, direction), which reading one diff cannot detect; that is what `assumptions` and campaign dedup on the roadmap are for.
- Run 2's ground truth is a model's judgement, audited for self-consistency, not yet by hand. Run 3's labels are GitHub outcomes, which are real but mostly about coordination.
- Jev is a closed API in early access; pricing may change. Haiku works today; the model is a config line.
- GitHub only, for now. Stack-level verdicts (block below ⇒ hold above) are implemented in the planner but could not be evaluated: the REST API exposes no stack object (run 3, R3-H4).

## Layout

```
gate/            Go: the mergeone binary (serve, pr, decide, replay, loadtest, mcp)
mergeone/        Python: gatebench (extraction, judge, calibration, reports) + reference gateway
prompts/         frozen question sets and prompts (jev_v2.json, haiku_v2.md, judge_v1.md)
results/run*/    raw predictions, labels, metrics, figures
PROTOCOL.md      pre-registered protocol · CHANGELOG.md every deviation, with reason
```

Apache-2.0 · Hasan Alp Temiz · built with Claude as a pair programmer · [mergeone.dev](https://mergeone.dev)

## Roadmap

See [ROADMAP.md](ROADMAP.md) — next: run3 on real agent PRs, then agent-declared `assumptions` and conflict-proving tests.
