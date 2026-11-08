# Run 10 — report tables

computed 2026-11-08T01:58:50+00:00 · `python -m mergeone.eval.run10_report` · label `iterated` from data/run3/dataset.jsonl · bootstrap seed 7, 1000 resamples

## Verdicts

| hypothesis | result | bar (PROTOCOL.md §R10.3) |
|---|---|---|
| R10-H1 | fail | on the 100 adjudicated pairs, accuracy on competing + same-area (17 pairs) ≥ 0.40, with class accuracy ≥ the control's same-day class accuracy − 0.02 |
| R10-H2 | pass | needs_human_review AUC on the 705 test PRs ≥ control + 0.03 with a bootstrap CI excluding zero (1,000 resamples) |
| R10-H3 | fail | per-PR cost (main call + ten pair calls) and p50 latency for every row; a row that passes H1 or H2 ships only if its per-PR cost ≤ 5× the control's |
| R10-H4 | fail | d3-flash vs the hosted control: AUC on each question ≥ control − 0.02 on at least six of eight; throughput ≥ 1.0 PR/s on the 48 GB GPU; agreement and mean \|Δp\| reported (no bar) |

Every comparison is against the same-day hosted pplx-flash control (results/run10/control); run-8 numbers are shown beside it, never instead of it.

UTC dates per row: control 2026-11-08; full 2026-11-08; haiku 2026-11-08; d3 2026-11-08 · one UTC day: True

d3 served: vllm-sr/d3-flash @ 67e03e1bc157 as `d3-flash` on NVIDIA A40, bf16 (vendor default; §R10.4), batch size 8, $0.59/h (runpod A40 secure EU-SE-1), started 2026-11-08T00:05:51.638715+00:00

## v4 questions, test split (AUC vs `iterated`, 95% bootstrap CI, 1,000 resamples)

| question | control (pplx-flash) | full (pplx) | d3-flash | run 8 pplx-flash (reference) |
|---|---|---|---|---|
| is_logic_change | 0.611 [0.538, 0.674] | 0.570 [0.507, 0.630] | 0.638 [0.574, 0.708] | 0.611 [0.539, 0.678] |
| weakens_tests | 0.744 [0.679, 0.799] | 0.787 [0.730, 0.843] | 0.778 [0.728, 0.834] | 0.724 [0.657, 0.789] |
| touches_concurrency | 0.682 [0.622, 0.739] | 0.663 [0.599, 0.724] | 0.631 [0.567, 0.690] | 0.677 [0.614, 0.736] |
| needs_human_review | 0.759 [0.685, 0.823] | 0.742 [0.668, 0.807] | 0.792 [0.724, 0.853] | 0.768 [0.695, 0.831] |
| exceeds_scope | 0.751 [0.686, 0.810] | 0.756 [0.698, 0.810] | 0.773 [0.712, 0.821] | 0.753 [0.695, 0.814] |
| breaking_change | 0.716 [0.637, 0.782] | 0.744 [0.679, 0.808] | 0.733 [0.664, 0.791] | 0.717 [0.651, 0.785] |
| tests_cover_change | 0.635 [0.572, 0.690] | 0.596 [0.537, 0.651] | 0.621 [0.561, 0.673] | 0.626 [0.567, 0.687] |
| hard_to_revert | 0.781 [0.722, 0.831] | 0.815 [0.757, 0.867] | 0.770 [0.707, 0.821] | 0.747 [0.686, 0.801] |
| **mean (8)** | 0.710 | 0.709 | 0.717 | 0.703 |
| n PRs | 705 | 703 | 705 | 705 |
| served as | pplx-decider-flash-v1.2 | pplx-decider-v1.2 | d3-flash | pplx-decider-flash-v1.2 |

## R10-H1 — competing and same-area (relation2, run 8's rule + metadata branch)

100 pairs · gold: duplicate 46, competing 7, backport 28, follow_up 7, same_area 10, unrelated 2 · run8_relation.relation2_class (HIGH 0.5, LOW 0.25; same_base from metadata, unknown → True)

| row | n | missing | class accuracy [95% CI] | competing | same_area | competing + same_area | same_change vs rest AUC | served as |
|---|---|---|---|---|---|---|---|---|
| control | 100 | 0 | 0.580 [0.482, 0.672] | 1/7 | 1/10 | **2/17 = 0.118** | 0.689 | pplx-decider-flash-v1.2 |
| full | 100 | 0 | 0.600 [0.502, 0.691] | 4/7 | 2/10 | **6/17 = 0.353** | 0.826 | pplx-decider-v1.2 |
| haiku | 100 | 0 | 0.750 [0.657, 0.825] | 5/7 | 0/10 | **5/17 = 0.294** | 0.550 | claude-haiku-5-5 |
| d3 | 100 | 0 | 0.670 [0.573, 0.754] | 2/7 | 1/10 | **3/17 = 0.176** | 0.705 | d3-flash |
| run 8 pplx-flash (reference, 2026-10-25) | | | 0.570 | | | 0.059 | | |
| run 8 jev (reference, 2026-10-25) | | | 0.740 | | | 0.176 | | |

| row | competing + same_area ≥ 0.40 | class accuracy ≥ control − 0.02 | result |
|---|---|---|---|
| full | 0.353 → fail | 0.600 vs 0.580 − 0.02 → pass | **fail** |
| haiku | 0.294 → fail | 0.750 vs 0.580 − 0.02 → pass | **fail** |
| d3 | 0.176 → fail | 0.670 vs 0.580 − 0.02 → pass | **fail** |

### control: confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 30 | 0 | 3 | 7 | 6 | 0 |
| competing | 1 | 1 | 0 | 5 | 0 | 0 |
| backport | 0 | 0 | 19 | 4 | 5 | 0 |
| follow_up | 1 | 0 | 0 | 6 | 0 | 0 |
| same_area | 2 | 0 | 0 | 7 | 1 | 0 |
| unrelated | 0 | 0 | 1 | 0 | 0 | 1 |

### full: confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 37 | 2 | 3 | 1 | 3 | 0 |
| competing | 2 | 4 | 0 | 0 | 1 | 0 |
| backport | 0 | 0 | 12 | 2 | 7 | 7 |
| follow_up | 0 | 0 | 0 | 4 | 3 | 0 |
| same_area | 2 | 3 | 1 | 1 | 2 | 1 |
| unrelated | 0 | 0 | 1 | 0 | 0 | 1 |

### haiku: confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 37 | 6 | 3 | 0 | 0 | 0 |
| competing | 1 | 5 | 1 | 0 | 0 | 0 |
| backport | 1 | 0 | 27 | 0 | 0 | 0 |
| follow_up | 1 | 1 | 0 | 5 | 0 | 0 |
| same_area | 2 | 4 | 2 | 2 | 0 | 0 |
| unrelated | 0 | 0 | 1 | 0 | 0 | 1 |

### d3: confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 37 | 3 | 2 | 2 | 2 | 0 |
| competing | 1 | 2 | 0 | 3 | 1 | 0 |
| backport | 0 | 4 | 20 | 1 | 3 | 0 |
| follow_up | 1 | 0 | 0 | 6 | 0 | 0 |
| same_area | 3 | 3 | 1 | 2 | 1 | 0 |
| unrelated | 0 | 0 | 1 | 0 | 0 | 1 |

**R10-H1: fail** — bar: on the 100 adjudicated pairs, accuracy on competing + same-area (17 pairs) ≥ 0.40, with class accuracy ≥ the control's same-day class accuracy − 0.02

## R10-H2 — needs_human_review

| row | AUC | control AUC | Δ | Δ same PRs | 95% CI (paired) | n paired | result |
|---|---|---|---|---|---|---|---|
| full | 0.742 | 0.759 | **-0.016** | -0.016 | [-0.073, 0.032] | 703 | **fail** |
| d3 | 0.792 | 0.759 | **+0.034** | +0.034 | [0.003, 0.068] | 705 | **pass** |
| haiku | not asked (relation only, §R10.2) | | | | | | pending |

**R10-H2: pass** — bar: needs_human_review AUC on the 705 test PRs ≥ control + 0.03 with a bootstrap CI excluding zero (1,000 resamples)

## R10-H3 — cost and speed per PR (main call + ten pair calls)

| row | formula | c_main | t_main | t_pair | per-PR cost | × control | ≤ 5× | passes H1 or H2 | ships | p50 latency ms | spent |
|---|---|---|---|---|---|---|---|---|---|---|---|
| control | c_main × (1 + 10 × t_pair / t_main) | $0.000021 | 2209 | 2320 | **$0.00025** | 1.00 | — | — | — | 1041 | $0.0173 |
| full | c_main × (1 + 10 × t_pair / t_main) | $0.000043 | 2209 | 2320 | **$0.00049** | 2.00 | pass | no | no | 1040 | $0.0345 |
| haiku | c_main(control) + 10 × c_pair(haiku) | — | — | 3747 | **$0.00382** | 15.47 | fail | no | no | 980 (relation2 pair call) | $0.0380 |
| d3 | (usd_per_hour ÷ 3600 ÷ PR/s) × (1 + 10 × t_pair / t_main) (0.37 PR/s at c 32, $0.59/h) | $0.000443 | 13711 | 6400 | **$0.00251** | 10.18 | fail | yes | no | 89018 | (GPU-hours) |

c_main: mean cost_usd per v4 answer (hosted; list prices as recorded per answer) or GPU $/h ÷ 3600 ÷ PR/s (d3); t_main / t_pair: median input tokens of the row's v4 / relation2 answers; the ten pair calls are an estimate from the main call's price per token (haiku: measured pair cost). p50 latency of the d3 row includes queueing at its concurrency.

**R10-H3: fail** — bar: per-PR cost (main call + ten pair calls) and p50 latency for every row; a row that passes H1 or H2 ships only if its per-PR cost ≤ 5× the control's

## R10-H4 — d3-flash (self-hosted BF16) vs the hosted control

| question | d3 AUC | control AUC | Δ | ≥ control − 0.02 | agreement | mean \|Δp\| | mean Δp (d3 − control) |
|---|---|---|---|---|---|---|---|
| is_logic_change | 0.638 | 0.611 | +0.027 | pass | 0.949 | 0.0597 | +0.0254 |
| weakens_tests | 0.778 | 0.744 | +0.034 | pass | 0.959 | 0.0538 | +0.0245 |
| touches_concurrency | 0.631 | 0.682 | -0.051 | fail | 0.979 | 0.0305 | +0.0111 |
| needs_human_review | 0.792 | 0.759 | +0.034 | pass | 0.794 | 0.1436 | -0.1116 |
| exceeds_scope | 0.773 | 0.751 | +0.022 | pass | 0.960 | 0.0715 | -0.0277 |
| breaking_change | 0.733 | 0.716 | +0.017 | pass | 0.935 | 0.0752 | -0.0461 |
| tests_cover_change | 0.621 | 0.635 | -0.014 | pass | 0.950 | 0.0687 | -0.0100 |
| hard_to_revert | 0.770 | 0.781 | -0.011 | pass | 0.936 | 0.0724 | -0.0050 |
| **all** | | | | **7/8** | 0.933 | 0.0719 | |

Throughput (timed v4 test pass): 705 PRs in 1907.3 s = **0.370 PR/s** (errors 0, concurrency 32); bar ≥ 1.0 PR/s.

Calibration map (isotonic on d3 val vs `iterated`, no bar): ok, 1491 val PRs.

| question | n val | test ECE raw | test ECE mapped |
|---|---|---|---|
| is_logic_change | 1491 | 0.635 | 0.070 |
| weakens_tests | 1491 | 0.066 | 0.057 |
| touches_concurrency | 1491 | 0.080 | 0.059 |
| needs_human_review | 1491 | 0.437 | 0.060 |
| exceeds_scope | 1491 | 0.087 | 0.060 |
| breaking_change | 1491 | 0.100 | 0.063 |
| tests_cover_change | 1491 | 0.538 | 0.071 |
| hard_to_revert | 1491 | 0.088 | 0.062 |

**R10-H4: fail** — bar: d3-flash vs the hosted control: AUC on each question ≥ control − 0.02 on at least six of eight; throughput ≥ 1.0 PR/s on the 48 GB GPU; agreement and mean |Δp| reported (no bar)

## Inputs

| path | sha256 |
|---|---|
| data/run3/dataset.jsonl | `d1a6c6325666cdab` |
| data/run4/memory/Azure__azure-sdk-for-net.jsonl | `ab0a7072fffde828` |
| data/run4/memory/Comfy-Org__ComfyUI_frontend.jsonl | `b88bf2e4e74f5d48` |
| data/run4/memory/VocaHQ__vocalinux.jsonl | `5f0ab486c4ba6a5a` |
| data/run4/memory/compdemocracy__polis.jsonl | `f1c56bdf7e25c487` |
| data/run4/memory/mattermost__mattermost.jsonl | `e211c9b1230834ff` |
| data/run4/memory/microsoft__vscode.jsonl | `ccf72293752e5e1a` |
| data/run6/hold_labels.jsonl | `bad6d0d3c916acdf` |
| data/run6/hold_labels_adjudicated.jsonl | `df4e0dbe38a2f921` |
| data/run6/hold_labels_fable.jsonl | `d45f8664232420f5` |
| results/run10/control/predictions_pplx-flash_test_all.jsonl | `42b166816c652ee4` |
| results/run10/d3/predictions_d3-flash-local_test_all.jsonl | `1e694cbbab1eb074` |
| results/run10/d3/predictions_d3-flash-local_val_all.jsonl | `6c5cf1be52effebd` |
| results/run10/d3/serve_meta.json | `93296cb81ce6127f` |
| results/run10/d3/throughput.json | `5379355a860d0317` |
| results/run10/d3/warm.json | `5bee4bf6f85240bb` |
| results/run10/full/predictions_pplx_test_all.jsonl | `19f7bbb7a75c8b9b` |
| results/run10/haiku/predictions_haiku_test_all.jsonl | `80fe501cb6331be0` |
| results/run8/relation_metrics.json | `c52cb5c289eb2790` |
| results/run8/v4/predictions_pplx-flash_test_all.jsonl | `5db226500127139d` |
