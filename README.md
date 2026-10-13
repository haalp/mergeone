# scm-decision-bench

**How well do System One decision models work as the decision layer of agent-scale source control?**

A pre-registered, reproducible benchmark that pits [TypeSafe AI's Jev](https://typesafe.ai) (a "System One" model that returns typed probabilities instead of text) against Claude Haiku 5.5, gradient-boosted trees, and hand-written rules on a real source-control decision: *will this merged pull request be reverted within 30 days?*

Motivation and framing come from Anthropic's [*Redesigning source control at Anthropic*](https://www.anthropic.com/engineering/redesigning-source-control): at >30,000 merges/day, the merge queue is gone, testing moves post-merge, and the pipeline is full of small typed decisions that must be fast, cheap and calibrated. Anthropic will not release Scion; this repo tests the *decision layer* such a system needs, on top of plain git.

Read [PROTOCOL.md](PROTOCOL.md) first. It was frozen before any model call, and it states the success criteria we committed to before seeing results.

## What is measured

| | |
|---|---|
| Dataset | `kubernetes/kubernetes` master, most recent 15,000 merged PRs, built from git history only (no GitHub API, no working copy) |
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
python -m scmbench.data.extract_cli --repo /path/to/kubernetes --max-merges 15000   # or see scripts/extract.sh
scmbench prepare

# 2. baselines
scmbench train-gbm
scmbench predict --model rules --split val && scmbench predict --model rules --split test
scmbench predict --model gbm   --split val && scmbench predict --model gbm   --split test

# 3. API models (primary-only and all-three-questions variants; --words is the numbers-as-words ablation)
for s in val test; do
  scmbench predict --model jev   --split $s --questions primary
  scmbench predict --model jev   --split $s --questions all
  scmbench predict --model jev   --split $s --questions all --words
  scmbench predict --model haiku --split $s --questions primary
  scmbench predict --model haiku --split $s --questions all
done

# 4. stress replay
for k in 1 10 100; do scmbench loadtest --model jev --multiplier $k; scmbench loadtest --model haiku --multiplier $k; done

# 5. label audit + report
scmbench handcheck --n 50      # then fill results/handcheck_sample.jsonl by hand
scmbench report results/run1
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
scmbench/data/         git-history extraction, labels, features, state rendering
scmbench/models/       rules, gbm, haiku, jev, cascade — one interface
scmbench/eval/         metrics, report, figures
results/run1/          raw predictions + metrics.json + tables.md + figures
data/manifest.json     exact SHA window, split boundaries, label rules
```

## License

Apache-2.0. Author: Hasan Alp Temiz. Built with Claude as a coding assistant.
