<p align="center">
  <img src="docs/img/mark.svg" width="112" alt="mergeone">
</p>

<h1 align="center">mergeone</h1>

<p align="center"><b>One decision per merge, at swarm scale.</b><br>
<sub>An open-source merge gateway for repositories where agents write most of the commits — and the pre-registered benchmark that says what it can and cannot do.</sub></p>

<p align="center">
  <a href="https://github.com/haalp/mergeone/actions/workflows/ci.yml"><img src="https://github.com/haalp/mergeone/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <a href="https://github.com/haalp/mergeone/releases"><img src="https://img.shields.io/github/v/release/haalp/mergeone?include_prereleases&color=A371F7&labelColor=0E0F12" alt="release"></a>
  <img src="https://img.shields.io/badge/throughput-30M%20decisions%2Fday%20%40%20p99%20441ms-A371F7?labelColor=0E0F12" alt="throughput">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-ECECEA?labelColor=0E0F12" alt="license"></a>
  <a href="https://mergeone.dev/updates/"><img src="https://img.shields.io/badge/updates-mergeone.dev-ECECEA?labelColor=0E0F12" alt="updates"></a>
</p>

mergeone sits in front of your main branch. For every pull request it asks a small, fast decision model a fixed set of typed questions (*does this change logic? weaken tests? need a human? duplicate an open PR?*), gets calibrated probabilities back in one ~300 ms call, and turns them into `pass` / `hold` / `block` through a policy file you own. Every verdict is logged with its exact input. It never merges anything itself. One static Go binary; runs as a CI step, a GitHub webhook, or an MCP tool your agent calls before it merges.

---

## 1. Install

```bash
curl -fsSL https://mergeone.dev/install.sh | sh        # macOS / Linux, one static binary → /usr/local/bin/mergeone
```

Alternatives: download a release archive from [Releases](https://github.com/haalp/mergeone/releases), or `go install github.com/haalp/mergeone/gate/cmd/mergeone@latest`.

```bash
mergeone version
```

## 2. Set up a repository

```bash
cd your-repo
mergeone init
```

`init` prints what it detected (git repository, default branch, which API keys are present), writes `.mergeone.yaml` with safe defaults (PR memory off, verdict actions off, fail closed), and prints the next steps. It never overwrites an existing file unless you pass `--force`.

| flag | what it adds |
|---|---|
| `--model jev` | which decision model the policy points at (`mergeone models` lists the rows; default `jev`) |
| `--ci` | writes `.github/workflows/mergeone.yml` — the gate as a CI step |
| `--mcp` / `--mcp --write` | prints (or appends to `CLAUDE.md`) the snippet that makes an agent ask the gate before merging |
| `--all` | `--ci --mcp --write` |
| `--dir`, `--force`, `--quiet` | target directory, overwrite, no banner |

Running `mergeone` with no arguments on a terminal opens a session with the same commands inside (`init`, `pr`, `serve`, `models`, `help`, `quit`).

### Keys

The decision model is a config line; each one needs its own key. Set the one for the model you chose:

| model (`.mergeone.yaml: model:`) | key | notes |
|---|---|---|
| `jev` (default) | `TYPESAFE_API_KEY` | hosted System One model; fastest at 100× load in every run |
| `pplx-flash`, `pplx` | `PERPLEXITY_API_KEY` | hosted open-weight deciders (4B / 27B) |
| `haiku` | `ANTHROPIC_API_KEY` | an LLM used as a decision model; slower, for comparison |
| `local` rows | `MERGEONE_LOCAL_KEY` (optional) | your own `/v1/systemone` endpoint, e.g. the weights served by [`serve/`](serve/) |

`GITHUB_TOKEN` is needed only for the webhook mode and for verdict actions (commit status, labels, comments). No key yet? `mergeone serve --mock` and `mergeone mcp --mock` run the whole pipeline with a stand-in model.

## 3. First verdict

```bash
export TYPESAFE_API_KEY=...
mergeone pr --policy .mergeone.yaml --base origin/main --head HEAD
```

```json
{ "verdict": "hold", "route": "human", "rule": "maintainer-judgement",
  "reason": "Decision model thinks a maintainer should look",
  "probs": { "is_logic_change": 0.93, "weakens_tests": 0.05, "message_matches_diff": 0.31,
             "touches_concurrency": 0.12, "needs_human_review": 0.58 },
  "model_version": "jev-1.13.0", "latency_ms": 318, "cost_usd": 0.00007 }
```

Exit code `0` pass, `1` hold, `2` block, `3` error. To see what your policy would have done on real history before trusting it: `mergeone replay --policy .mergeone.yaml --predictions results/run2/predictions_jev_test_all.jsonl` dry-runs it over 1,200 real PRs shipped in this repository.

## 4. Pick how it runs

**CI step** — `mergeone init --ci` writes the workflow. On every `pull_request` it runs `mergeone pr`; `hold` leaves the job green with a warning (a human decides), `block` and errors fail it (fail closed). With [verdict actions](#policy) on, it also sets a commit status `mergeone` you can require in branch protection, an exclusive `mergeone:*` label, and one comment per head SHA with the typed reasons.

**Webhook** — one always-on process:

```bash
export GITHUB_TOKEN=... MERGEONE_WEBHOOK_SECRET=...
mergeone serve --policy .mergeone.yaml --addr :8787
```

Point a GitHub webhook (`pull_request` events) at `POST /webhook/github`. Decisions are idempotent per head SHA; the HTTP API also exposes `POST /decide`, `GET /verdict`, `GET /plan` (non-conflicting landing waves), `GET /stats`, `GET /healthz`. The gatelog is `.mergeone/gatelog.jsonl`.

**Agent (MCP)** — the agent asks before it merges:

```bash
claude mcp add mergeone -- mergeone mcp --policy .mergeone.yaml
```

```
# CLAUDE.md  (written by `mergeone init --mcp --write`)
Before merging, call gate_decide with repo_path="." base="main" head="HEAD".
Merge only on "pass". On "hold" or "block", open a PR and request the route named.
```

The MCP tools (`gate_decide`, `gate_similar`) never write to GitHub.

## 5. Policy

`.mergeone.yaml` is the whole product surface: which model, which question set, paths that always need a human, whether PR memory is on, the rules, the convergence cap, the drift monitor, the portal, and what to write back to the PR.

```yaml
model: jev                     # or pplx-flash, haiku, local, … — one YAML row, no code
question_set: v2               # v2 (default, five questions) | v4 (opt-in, eight)
always_human_paths: ["migrations/", "auth/"]
memory:                        # opt-in: asks whether the PR duplicates a recent one
  enabled: false
  top_k: 10                    # candidates compared per PR (default 10)
  pair_state: compact          # compact (default) | full: how the two PRs are sent to the pair call
max_rounds: 0                  # 3 = after three non-pass verdicts on one PR, route to a human
rules:
  - { name: weakens-tests, when: { weakens_tests: ">= 0.6" }, verdict: block, route: human }
  - { name: needs-human,   when: { needs_human_review: ">= 0.5" }, verdict: hold,  route: human }
  - { name: duplicate-of,  when: { duplicate_of: ">= 0.7" },  verdict: hold,  route: dedup }
default: { verdict: pass, route: auto-merge }
on_error: { verdict: hold, route: human }      # fail closed
drift: { enabled: true }       # default on; a rule that reads a drifted question holds for a human instead
actions:                       # webhook / CI mode only; off by default; never merges
  enabled: false
  on_pass:  { status: success, labels: ["mergeone:pass"] }
  on_hold:  { status: failure, labels: ["mergeone:hold"], reviewers: [] }
  on_block: { status: failure, labels: ["mergeone:block"] }
portal:                        # optional: written by `mergeone init --portal <token>`
  url: https://app.mergeone.dev
  token: ${MERGEONE_PORTAL_TOKEN}
```

Rules run top to bottom on *calibrated* probabilities; first match wins; a model outage holds the PR for a human. Verdict actions are deterministic GitHub calls and never change a verdict; the token needs statuses, pull requests and issues write.

- **`question_set`** — `v2` asks five questions. `v4` asks eight (`is_logic_change`, `weakens_tests`, `touches_concurrency`, `needs_human_review`, `exceeds_scope`, `breaking_change`, `tests_cover_change`, `hard_to_revert`); calibration maps ship for `jev`, `haiku` and `pplx-flash`. `v3` runs only with a `calibration_<model>_v3.json` of your own; without one the gateway refuses to start. The check-run and the verdict comment list the questions of the set you chose.
- **`memory.pair_state`** — the duplicate check compares the PR with each of `top_k` similar recent PRs in one small call each. `compact` sends shortened diffs and bodies (32–42 % fewer tokens, pair AUC within 0.02 of `full` in run 8, bar −0.02); the main decision always sees the full PR.
- **`drift`** — the gateway watches each question's answers in the gatelog. When a question's mean moves by more than 0.20 or its spread halves (n ≥ 200, trailing 7 days against the 28 before), or, once PRs close, its AUC against review iteration drops by more than 0.05, rules that read it stop deciding and the PR is held for a human (`on_drift`). `GET /stats` shows the numbers; `drift: { enabled: false }` turns it off.
- **`portal`** — every gatelog record is also posted to the hosted portal; the gateway's own read API (`/api/queue`, `/api/graph`, `/api/verdict/{sha}`, `/api/stats`, `POST /api/resolve`, `POST /api/outcome`, `POST /api/calibrate`) is behind `portal.api_token` (without one, it answers only on a loopback bind). A resolution there is a label and a comment, never a merge or a close.

Two commands look after the model over time:

```bash
mergeone calibrate --from-gatelog --since 28d        # refit the calibration maps from your own decided-and-closed PRs (needs ≥ 200)
mergeone drift-replay --predictions then.jsonl --predictions now.jsonl   # would the drift monitor flag this change of model?
```

`calibrate` writes `calibration_<model>_<question_set>.json` beside the policy (an existing file only with `--force`); nothing refits by itself, and a running gateway keeps its map until restarted. Full reference: [`docs/POLICY.md`](docs/POLICY.md).

---

## How it works

```
                 ┌─────────────────────────────── mergeone ───────────────────────────────┐
  PR opened      │  render once      decision model        calibrate        .mergeone.yaml │   verdict + route
  (agent / CI) ─▶│  diff + message ─▶ ~5 typed questions ─▶ isotonic maps ─▶ rules, first  │─▶ pass   → auto-merge
                 │  + PR memory       one call, ~300 ms      0.6 = 60%        match wins    │   hold   → human / dedup
                 │                                                                          │   block  → stop
                 └──────────────────────────────────┬───────────────────────────────────────┘
                                                    └─▶ gatelog: every verdict, its input, its hash
```

The model never sees a rule and never writes prose. It returns probabilities for questions like `is_logic_change`, `weakens_tests`, `message_matches_diff`, `touches_concurrency`, `needs_human_review`, and — when PR memory is on — `duplicate_of` an existing PR. Calibration maps (isotonic, fit on validation data) make `0.6` mean 60%. The policy is the only thing you edit.

## What it costs, what it carries

The gateway itself adds single-digit milliseconds: 350 decisions/s on one 2-vCPU box at p99 441 ms with a mock model, re-proved by CI on every push. The model you plug in sets the real ceiling — measured, not quoted:

![Decision models under agent-scale load](results/figures/decision_models_under_load.png)

*One line per decision model behind one API key or one GPU; the line runs as far as every call was answered, ✕ is the first load with failures. Only the hosted System One model reaches 100× (3M decisions/day) clean; one H100 is the fastest row and stops at 30×. Rebuilt from the frozen load-test files by `scripts/fig_decision_models_under_load.py`.*

## Evidence, including what did not work

mergeone is also a benchmark, **gatebench**. Every claim was pre-registered in [`PROTOCOL.md`](PROTOCOL.md) before the data existed; thresholds are never edited after results exist; deviations are dated in [`CHANGELOG.md`](CHANGELOG.md); every table rebuilds with one command from `results/run*`. Eight runs, fourteen results; eight failed and are reported the same way.

| run | question | result |
|---|---|---|
| 1 | Can a diff predict a 30-day revert? | ❌ PR-AUC 0.010 vs base 0.0075 — nothing in the diff |
| 2 | Can a System One model answer typed triage questions? | ✅ ROC-AUC 0.87–0.96 vs a strong judge, 347 ms, $0.074/1k |
| 3 | Does the gate predict rejection of real agent PRs? | ❌ 0.55 (bar 0.70): 67% of closures are coordination, not quality |
| 4 | Can a PR memory catch the duplicates? | 🟡 `same_change` 0.83–0.87 (bar 0.80) ✅ · retrieval recall@5 0.55 (bar 0.60) ❌ |
| 5 | Does a flag mean more review? | 🟡 flag ⇒ ≥ 2 review rounds AUC 0.76 (bar 0.70) ✅ · retrieval 0.66 (bar 0.70) ❌ · store p99 3.3 ms ✅ |
| 6A | Do self-hosted open weights clear the scale bar? | ❌ one H100: 30× at 160 ms, 100× fails; batching changes nothing |
| 6B | When the gate holds a PR as a duplicate, is it right? | ✅ 98 of 100 blind-labelled holds were right (bar 0.60); 44 the same change (bar 0.30); κ 0.77 with a second labeller · pool size not the lever ❌ · $0.0004/PR ✅ |
| 7 | Compact state, tuned prompt, structure instead of the model, a typed relation question? | ❌ ❌ ❌ ❌ all four bars missed — and the hosted model changed under an unchanged version string, so cross-run comparisons now get same-day controls |
| 8 | Compact state against a same-day control, relation v2, a drift monitor? | ✅ compact state loses ≤ 0.02 (bar −0.02): v4 +0.010 / −0.001, pair −0.018 / −0.015, tokens −32–42 % · ❌ relation v2 class accuracy 0.74 / 0.57 (bar 0.80) · ✅ the drift monitor flags exactly run 7's two questions, nothing else |

What survives: typed triage of *what a change does* at System One speed and cost; a gateway that is never the bottleneck; a cheap model can tell two PRs are the same change, and a hold for duplication is right 98 times in 100 — though more than half the time the right action is a link, not a close. What does not: predicting what a project will *accept* from one diff, and — so far — finding the right PR to compare against. Full write-ups: [`docs/EVIDENCE.md`](docs/EVIDENCE.md), per run `results/run*/README.md`, model ranking [`results/leaderboard.md`](results/leaderboard.md), dated short versions at [mergeone.dev/updates](https://mergeone.dev/updates/).

## Limits, plainly

- The gate judges what a change *does*, not whether a project will *take* it. Most agent-PR rejections are coordination (duplicates, superseded, direction); PR memory addresses part of that and is still opt-in.
- Run 2's ground truth is a model's judgement, audited for self-consistency, not by hand; run 3's labels are real GitHub outcomes.
- The default model is a hosted API in early access; pricing may change. The model is one config line.
- GitHub only, for now.

## Layout

```
gate/            Go: the mergeone binary (init, pr, serve, decide, replay, drift-replay, calibrate, loadtest, index, mcp, models)
mergeone/        Python: gatebench (extraction, judge, calibration, reports) + reference gateway
prompts/         frozen question sets, prompts and the decision-model registry (models.yaml)
serve/           self-hosting an open-weight decision model behind /v1/systemone (run 6)
scripts/         how each run's numbers were produced (scripts/README.md)
results/run*/    raw predictions, labels, metrics, figures · results/leaderboard.md
PROTOCOL.md      pre-registered protocol · CHANGELOG.md every deviation, with reason · ROADMAP.md
```

Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md) · security: [`SECURITY.md`](SECURITY.md)

Apache-2.0 · Hasan Alp Temiz · [mergeone.dev](https://mergeone.dev)
