# mergeone

**One decision per merge, at swarm scale.**

`mergeone` is an open-source **merge gateway** for repositories where most commits are written by agents. It replaces the merge queue's single slow question ("is CI green?") with a handful of fast, typed, calibrated decisions per pull request, made by a System One decision model in a few hundred milliseconds, and turns them into a verdict and a route through a policy file you own.

```
PR state ──▶ decision model ──▶ calibrated probabilities ──▶ policy rules ──▶ verdict + route
            (Jev / Haiku)        is_logic_change 0.91          .mergeone.yaml     pass / hold / block
                                 weakens_tests   0.07                             auto-merge / human / domain-review
                                 message_matches 0.88
                                 touches_concurr 0.63
                                 needs_human     0.41
                                             └── every decision + its input is written to .mergeone/gatelog.jsonl
```

Why this exists: Anthropic's [*Redesigning source control at Anthropic*](https://www.anthropic.com/engineering/redesigning-source-control) describes a monorepo at >30,000 merges/day where the merge queue no longer makes sense and testing moves post-merge. Their system, Scion, will not be released. What such a pipeline needs is a cheap decision layer that can run on every merge; `mergeone` is that layer, on top of plain git and any forge.

## Quick start

```bash
pip install mergeone            # or: pip install -e . from a clone
export TYPESAFE_API_KEY=...     # Jev; or set model: haiku and ANTHROPIC_API_KEY
cp mergeone/gate/policy.yaml .mergeone.yaml
gate pr --policy .mergeone.yaml --base origin/main --head HEAD
```

```json
{
  "verdict": "hold", "route": "human", "rule": "message-mismatch",
  "reason": "Message does not explain the behavioural change in the diff",
  "probs": {"is_logic_change": 0.93, "weakens_tests": 0.05, "message_matches_diff": 0.31, "touches_concurrency": 0.12, "needs_human_review": 0.58},
  "model_version": "jev-1.13.0", "latency_ms": 318, "cost_usd": 0.00007
}
```

Exit code 0 = pass, 1 = hold, 2 = block, so it drops into any CI. `examples/github-action.yml` labels PRs with `gate:<verdict>` and `route:<route>`. `gate replay` dry-runs a policy over saved predictions so you can tune thresholds before touching a real PR.

### What the gate asks

Five typed questions, in one request (see `prompts/jev_v2.json`): does the PR change behaviour, does it weaken tests, does the message match the diff, does it touch concurrency-hazard code, and would a careful maintainer want to look. The first four are inputs a policy can reference; the fifth is the default routing signal. Probabilities are calibrated on labelled history before rules run (`gate fit-calibration`).

### Running it as a service (the 30,000-merges-a-day mode)

```bash
export GITHUB_TOKEN=... MERGEONE_WEBHOOK_SECRET=...
mergeone serve --policy .mergeone.yaml --db .mergeone/gate.db --workers 16 --port 8787
# point a GitHub webhook (pull_request events) at https://<host>/webhook/github
```

Every PR head gets one decision (idempotent on `repo:pr:head_sha`), stored in SQLite, posted back as a check-run (`success` / `neutral` / `failure`) and two labels (`gate:<verdict>`, `route:<route>`). Agents can skip the webhook and ask directly:

```bash
curl -X POST :8787/decide -d '{"repo":"acme/x","head_sha":"…","state":"<rendered PR>","paths":["pkg/a.go"]}'
curl :8787/verdict/acme/x/<sha>      # later, from anywhere
curl :8787/stats?window=3600         # verdict mix, p50/p99, cost, error rate
```

Measured on one container with the mock model (300 ms simulated inference), open-loop:

| load | decisions/s | p50 | p99 | errors |
|---|---|---|---|---|
| 1× (30k/day) | 0.35 | 312 ms | 429 ms | 0 |
| 10× (300k/day) | 3.5 | 304 ms | 449 ms | 0 |
| 100× (3M/day) | 35 | 304 ms | 448 ms | 0 |

The gateway adds ~5 ms over the model. With Jev the model itself held 35/s at p99 527 ms in run 1, so a single instance covers Anthropic's published volume with two orders of magnitude of headroom. `python -m mergeone.server.loadtest --rate 35 --n 1200` reproduces the table against any running instance; `--mock` on `serve` needs no API key.

### Design rules

- **Fail closed.** Model error or timeout → `hold` / `human`.
- **Mechanical rules stay mechanical.** `always_human_paths` is a prefix match, free, and runs before the model.
- **Every decision is auditable.** System One models return no rationale, so the gatelog stores the verdict *with the rendered input and its hash*.
- **The model is a variable.** Jev, Haiku, or your own; one interface (`mergeone/models/base.py`).

---

# gatebench: the experiment behind it

Everything above rests on a pre-registered benchmark, published regardless of outcome. Read [PROTOCOL.md](PROTOCOL.md) first.

**Run 1** asked whether any cheap decision layer can predict *30-day reverts* from pre-merge state on 40,000 Kubernetes merges. Answer: no. Rules, LightGBM, Claude Haiku 5.5 and Jev all sit at PR-AUC ≈ 0.010 against a 0.0075 base rate, indistinguishable within confidence intervals. Pre-merge diffs do not contain that signal. What run 1 *did* show: on typed rule-like questions Jev was 97–98% accurate vs Haiku's 87–93%, at 3× lower p50, 5× lower p99, 4× lower cost, and held 3M decisions/day with 0% errors where Haiku hit rate limits (36% errors). Full tables: `results/run1/tables.md`.

**Run 2** (in progress) asks the question that matters for a gateway: can a System One model answer rule-*like* questions that cannot be written as rules (behaviour change, weakened tests, message/diff mismatch, concurrency risk, needs-human), accurately and calibrated, against a strong LLM judge whose labels are themselves audited by hand. Criteria are pre-registered in the PROTOCOL addendum.

## Run 1 setup

| | |
|---|---|
| Dataset | `kubernetes/kubernetes` master, most recent 15,000 merged PRs (Jul 2021 – Oct 2026), built from git history only (no GitHub API, no working copy) |
| Primary decision | `will_be_reverted` within 30 days (labels from later `Revert "..."` PRs) |
| Secondary decisions | `touches_sensitive_area`, `large_blast_radius` (mechanical ground truth; tests "typed guardrail" ability and the cost of asking several questions at once) |
| Systems | `rules`, `gbm` (LightGBM), `haiku` (Claude Haiku 5.5, thinking off, strict JSON), `jev` (TypeSafe Jev, noul questions), `cascade` (jev → haiku on low confidence) |
| Metrics | PR-AUC with bootstrap CI, recall @ 5% FPR (threshold from validation), ECE, Brier, p50/p99 latency, USD per 1,000 decisions, cascade escalation rate, 10×/100× replay stress |
| Split | Chronological 60/20/20; thresholds chosen on validation only |

## Reproduce

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
export ANTHROPIC_API_KEY=...   # for haiku
export TYPESAFE_API_KEY=...    # for jev

# 1. data (needs a clone of kubernetes/kubernetes with enough first-parent history)
python3 -m mergeone.data.extract_cli --repo /path/to/kubernetes --max-merges 15000   # or see scripts/extract.sh
mergeone prepare

# 2. baselines
mergeone train-gbm
mergeone predict --model rules --split val && mergeone predict --model rules --split test
mergeone predict --model gbm   --split val && mergeone predict --model gbm   --split test

# 3. API models (primary-only and all-three-questions variants; --words is the numbers-as-words ablation)
for s in val test; do
  mergeone predict --model jev   --split $s --questions primary
  mergeone predict --model jev   --split $s --questions all
  mergeone predict --model jev   --split $s --questions all --words
  mergeone predict --model haiku --split $s --questions primary
  mergeone predict --model haiku --split $s --questions all
done

# 4. stress replay
for k in 1 10 100; do mergeone loadtest --model jev --multiplier $k; mergeone loadtest --model haiku --multiplier $k; done

# 5. label audit + report
mergeone handcheck --n 50      # then fill results/handcheck_sample.jsonl by hand
mergeone report results/run1
```

Raw per-example predictions, token counts, timings and the price sheet are committed under `results/run1/` so every table can be rebuilt without re-spending API calls.

## What this is not

- Not a test of Scion (not available) and not a reproduction of the vendor's benchmarks.
- Kubernetes history is human-written; we make no claim about agent-authored changes.
- "Revert" is a noisy proxy for "bad merge"; the hand-check sample reports the observed label precision.
- Latency is measured from Istanbul against hosted APIs.

## Layout

```
PROTOCOL.md            pre-registered protocol (frozen)
CHANGELOG.md           any deviation from the protocol, with reason
prompts/               frozen prompt (haiku_v1.md) and question set (jev_v1.json)
mergeone/data/         git-history extraction, labels, features, state rendering
mergeone/models/       rules, gbm, haiku, jev, cascade — one interface
mergeone/eval/         metrics, report, figures
results/run1/          raw predictions + metrics.json + tables.md + figures
data/manifest.json     exact SHA window, split boundaries, label rules
```

## License

Apache-2.0. Author: Hasan Alp Temiz. Built with Claude as a coding assistant.
