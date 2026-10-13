# mergeone

**One decision per merge, at swarm scale.**

`mergeone` is a merge gateway for agent-scale source control: a fast, cheap, calibrated decision layer that sits where a merge queue used to be. This repository currently holds **gatebench**, the pre-registered experiment that tests whether such a layer works, using TypeSafe AI's Jev (a "System One" model that returns typed probabilities instead of text) against Claude Haiku 5.5, gradient-boosted trees, and hand-written rules.

**Research question:** how well do System One decision models work as the decision layer of agent-scale source control?

The benchmark is reproducible end to end and asks one real source-control question first: *will this merged pull request be reverted within 30 days?* ([Jev](https://typesafe.ai), [Haiku 5.5](https://platform.claude.com/docs/en/models/haiku-5-5/overview))

Motivation and framing come from Anthropic's [*Redesigning source control at Anthropic*](https://www.anthropic.com/engineering/redesigning-source-control): at >30,000 merges/day, the merge queue is gone, testing moves post-merge, and the pipeline is full of small typed decisions that must be fast, cheap and calibrated. Anthropic will not release Scion; this repo tests the *decision layer* such a system needs, on top of plain git.

Read [PROTOCOL.md](PROTOCOL.md) first. It was frozen before any model call, and it states the success criteria we committed to before seeing results.

## What is measured

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
pip install -e .
export ANTHROPIC_API_KEY=...   # for haiku
export TYPESAFE_API_KEY=...    # for jev

# 1. data (needs a clone of kubernetes/kubernetes with enough first-parent history)
python -m mergeone.data.extract_cli --repo /path/to/kubernetes --max-merges 15000   # or see scripts/extract.sh
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
