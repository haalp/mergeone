# Run 9 — report tables

computed 2026-11-06T03:29:52+00:00 · `python -m mergeone.eval.run9_report`

## Verdicts

| hypothesis | result | bar |
|---|---|---|
| R9-H1 | deferred | — |
| R9-H2 | fail | chosen configuration carries ×10 (3.47/s): 0 errors and p99 ≤ its ×1 p99 + 200 ms; saturation reported |
| R9-H3 | fail | FP8 vs same-day BF16: agreement ≥ 0.95, mean \|Δp\| ≤ 0.02 (7,050 answers) and throughput ≥ 1.3× |
| R9-H4 | fail | shared vs per-candidate: candidates/s on set (b) ≥ 2.5×; set-(a) AUC and set-(b) dup-vs-rest AUC each ≥ per-candidate − 0.02 |
| R9-H5 | pass | hosted joint vs local single-question: agreement ≥ 0.95, mean \|Δp\| ≤ 0.02, median hosted/local-joint input tokens ≥ 6 |

Every comparison is against the same-day vendor-BF16 control on the same pod (results/run9/bf16); run-6A numbers are shown beside it, never instead of it.

## Rows served (serve_meta.json per arm)

| arm | revision | GPU | dtype | fast options in effect | expected | matches | batch tokens | started |
|---|---|---|---|---|---|---|---|---|
| bf16 | 6d771ad7b5ec | NVIDIA L4 | bf16 | compile="layer", cuda_graphs=true, buckets=[2048, 4096, 8192, 16384, 32768], token_budget=32768, prune_last=true, fp8=false, prefetch=true | fp8=false, buckets=[2048, 4096, 8192, 16384, 32768] | pass | 131072 | 2026-11-05T22:58:30.196308+00:00 |
| fp8 | 6d771ad7b5ec | NVIDIA L4 | fp8 | compile="layer", cuda_graphs=true, buckets=[2048, 4096, 8192, 16384, 32768], token_budget=32768, prune_last=true, fp8=true, prefetch=true | fp8=true, buckets=[2048, 4096, 8192, 16384, 32768] | pass | 131072 | 2026-11-05T23:16:20.921321+00:00 |
| buckets | 6d771ad7b5ec | NVIDIA L4 | bf16 | compile="layer", cuda_graphs=true, buckets=[2560, 3072, 4096, 8192, 16384, 32768], token_budget=32768, prune_last=true, fp8=false, prefetch=true | fp8=false, buckets=[2560, 3072, 4096, 8192, 16384, 32768] | pass | 131072 | 2026-11-06T01:32:32.372499+00:00 |
| shared | 6d771ad7b5ec | NVIDIA L4 | bf16 | compile="layer", cuda_graphs=true, buckets=[2048, 4096, 8192, 16384, 32768], token_budget=32768, prune_last=true, fp8=false, prefetch=true | fp8=false, buckets=[2048, 4096, 8192, 16384, 32768] | pass | 131072 | 2026-11-05T23:46:58.954078+00:00 |
| h5 | 6d771ad7b5ec | NVIDIA L4 | bf16 | compile="layer", cuda_graphs=true, buckets=[2048, 4096, 8192, 16384, 32768], token_budget=32768, prune_last=true, fp8=false, prefetch=true | fp8=false, buckets=[2048, 4096, 8192, 16384, 32768] | pass | 131072 | 2026-11-06T02:38:18.367704+00:00 |

one revision across arms: True (6d771ad7b5ec) · batch tokens 131072 everywhere: True

## R9-H3 — FP8 row vs same-day BF16 control

Equivalence (FP8 − BF16, joint v3, test): 705 PRs joined (705 treatment, 705 control), 7050 answers

| question | n | agreement | mean \|Δp\| | max \|Δp\| | mean Δp | yes-rate treatment | yes-rate control |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.983 | 0.0260 | 0.343 | +0.0142 | 0.634 | 0.626 |
| weakens_tests | 705 | 0.984 | 0.0264 | 0.185 | +0.0039 | 0.068 | 0.067 |
| message_matches_diff | 705 | 0.912 | 0.0560 | 0.374 | +0.0123 | 0.824 | 0.813 |
| touches_concurrency | 705 | 1.000 | 0.0133 | 0.320 | -0.0083 | 0.018 | 0.018 |
| needs_human_review | 705 | 0.950 | 0.0265 | 0.154 | +0.0029 | 0.597 | 0.565 |
| exceeds_scope | 705 | 0.950 | 0.0339 | 0.260 | +0.0029 | 0.251 | 0.241 |
| breaking_change | 705 | 0.960 | 0.0339 | 0.270 | +0.0093 | 0.174 | 0.166 |
| tests_cover_change | 705 | 0.949 | 0.0487 | 0.421 | +0.0154 | 0.566 | 0.546 |
| hard_to_revert | 705 | 0.948 | 0.0583 | 0.677 | -0.0406 | 0.062 | 0.087 |
| follows_conventions | 705 | 0.991 | 0.0406 | 0.317 | -0.0339 | 0.003 | 0.009 |
| **all** | 7050 | **0.963** | **0.0364** | 0.677 | | | |

p99 \|Δp\| 0.2316 · bars: agreement ≥ 0.95, mean \|Δp\| ≤ 0.02 → **fail**

Throughput, equivalence set: BF16 705 items in 367.9 s = **1.92/s** (errors 0, concurrency 32, decide_s 367.8) · FP8 705 items in 573.6 s = **1.23/s** (errors 0, concurrency 32, decide_s 573.6) → ratio **0.64×** (bar ≥ 1.3×)

FP8 val rows for the isotonic refit: 1491 PRs (not a bar).

**R9-H3: fail** — bar: FP8 vs same-day BF16: agreement ≥ 0.95, mean |Δp| ≤ 0.02 (7,050 answers) and throughput ≥ 1.3×

## R9-H2 — L4 at ×10

Bucket row equivalence (buckets − BF16; only if R9-H3 fails): 705 PRs joined (705 treatment, 705 control), 7050 answers

| question | n | agreement | mean \|Δp\| | max \|Δp\| | mean Δp | yes-rate treatment | yes-rate control |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.996 | 0.0051 | 0.048 | -0.0001 | 0.630 | 0.626 |
| weakens_tests | 705 | 0.997 | 0.0058 | 0.033 | +0.0003 | 0.070 | 0.067 |
| message_matches_diff | 705 | 0.980 | 0.0127 | 0.086 | +0.0001 | 0.810 | 0.813 |
| touches_concurrency | 705 | 1.000 | 0.0028 | 0.030 | +0.0001 | 0.018 | 0.018 |
| needs_human_review | 705 | 0.991 | 0.0063 | 0.057 | -0.0013 | 0.567 | 0.565 |
| exceeds_scope | 705 | 0.986 | 0.0072 | 0.045 | -0.0005 | 0.238 | 0.241 |
| breaking_change | 705 | 0.993 | 0.0071 | 0.066 | +0.0000 | 0.167 | 0.166 |
| tests_cover_change | 705 | 0.994 | 0.0105 | 0.089 | +0.0003 | 0.546 | 0.546 |
| hard_to_revert | 705 | 0.990 | 0.0115 | 0.176 | -0.0015 | 0.082 | 0.087 |
| follows_conventions | 705 | 0.999 | 0.0064 | 0.069 | -0.0007 | 0.007 | 0.009 |
| **all** | 7050 | **0.993** | **0.0076** | 0.176 | | | |

p99 \|Δp\| 0.0463 · bars: agreement ≥ 0.95, mean \|Δp\| ≤ 0.02 → **pass**

Configuration: **buckets** (R9-H3 failed; bucket row meets the equivalence bars vs BF16)

| row | step | rate /s | n | p50 ms | p99 ms | errors | p99 schedule lag s |
|---|---|---|---|---|---|---|---|
| BF16 control (same day) | ×1 | 0.35 | 600 | 694 | 1,436 | 0 | 0.00 |
| BF16 control (same day) | ×10 | 3.47 | 600 | 58,497 | 173,867 | 0 | 0.00 |
| BF16 control (same day) | ×30 | 10.42 | 600 | 112,844 | 252,671 | 0 | 0.00 |
| BF16 + bucket row | ×1 | 0.35 | 600 | 445 | 1,432 | 0 | 0.00 |
| BF16 + bucket row | ×10 | 3.47 | 600 | 150,537 | 447,918 | 0 | 0.00 |
| BF16 + bucket row | ×30 | 10.42 | 600 | 235,136 | 519,062 | 0 | 28.67 |
| run 6A pass 2, L4 (2026-10-18; reference, not the comparison) | ×1 | 0.35 | 600 | 709 | 1,487 | 0 | 0.00 |
| run 6A pass 2, L4 (2026-10-18; reference, not the comparison) | ×10 | 3.47 | 600 | 20,317 | 29,817 | 535 | 0.00 |
| run 6A pass 2, L4 (2026-10-18; reference, not the comparison) | ×30 | 10.42 | 600 | — | — | 600 | 0.00 |

saturation (first step with p99 > 2 × its ×1 p99): BF16 control (same day) ×10; BF16 + bucket row ×10; run 6A ×10

with the gateway's pair calls included: not run (cli loadtest has no v3 + pair mix; §R9.3 'measured alongside, no bar')

**R9-H2: fail** ×10 p99 447,918 ms vs ×1 1,432 + 200; ×10 errors 0; saturation ×10. — bar: chosen configuration carries ×10 (3.47/s): 0 errors and p99 ≤ its ×1 p99 + 200 ms; saturation reported

## R9-H4 — shared-A pair call (k = 10) vs one request per candidate, both BF16

groups: set (a) 29 requests, sizes {'6': 16, '7': 13}; set (b) 100 requests, adjudicated B forced into slot 10: 4

| measure | per-candidate | shared | shared − per-candidate | bar |
|---|---|---|---|---|
| same_change AUC, set (a) (positive) | 0.839 [0.758, 0.908] (n 187, pos 29) | 0.808 [0.721, 0.890] (n 187, pos 29) | -0.031 | ≥ −0.02 |
| duplicate vs rest AUC, set (b) | 0.706 [0.606, 0.805] (n 100, pos 46) | 0.570 [0.453, 0.672] (n 100, pos 46) | -0.136 | ≥ −0.02 |
| candidates/s, set (b) (timed, c = 32) | 2.34 | 5.77 | ratio **2.46×** | ≥ 2.5× |
| input tokens per candidate, set (b), median | 2206 | 767 | | — |

Between the arms (reported, no bar; a different request by design): set (a) n 187, agreement 0.957, mean |Δp| 0.0461, p99 |Δp| 0.2817; set (b) n 1000, agreement 0.934, mean |Δp| 0.0707, p99 |Δp| 0.5535

clauses: speed fail; auc_set_a fail; dup_vs_rest_set_b fail

**R9-H4: fail** — bar: shared vs per-candidate: candidates/s on set (b) ≥ 2.5×; set-(a) AUC and set-(b) dup-vs-rest AUC each ≥ per-candidate − 0.02

## R9-H5 — hosted request decomposition (a check)

UTC dates: hosted 2026-11-06 · local single-question 2026-11-06 · same day: True · hosted version pplx-decider-flash-v1.2

Equivalence (hosted joint − local single-question): 705 PRs joined (705 treatment, 705 control), 7050 answers

| question | n | agreement | mean \|Δp\| | max \|Δp\| | mean Δp | yes-rate treatment | yes-rate control |
|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.984 | 0.0138 | 0.215 | -0.0033 | 0.699 | 0.701 |
| weakens_tests | 705 | 0.994 | 0.0062 | 0.205 | +0.0004 | 0.026 | 0.023 |
| message_matches_diff | 705 | 0.993 | 0.0102 | 0.314 | -0.0008 | 0.991 | 0.990 |
| touches_concurrency | 705 | 0.997 | 0.0037 | 0.121 | -0.0013 | 0.010 | 0.013 |
| needs_human_review | 705 | 0.972 | 0.0250 | 0.212 | -0.0045 | 0.665 | 0.685 |
| exceeds_scope | 705 | 0.991 | 0.0184 | 0.185 | -0.0145 | 0.077 | 0.085 |
| breaking_change | 705 | 0.986 | 0.0157 | 0.188 | +0.0070 | 0.115 | 0.118 |
| tests_cover_change | 705 | 0.996 | 0.0170 | 0.286 | -0.0093 | 0.662 | 0.667 |
| hard_to_revert | 705 | 0.993 | 0.0181 | 0.176 | -0.0086 | 0.051 | 0.052 |
| follows_conventions | 705 | 0.990 | 0.0160 | 0.327 | -0.0033 | 0.989 | 0.996 |
| **all** | 7050 | **0.990** | **0.0144** | 0.327 | | | |

p99 \|Δp\| 0.1016 · bars: agreement ≥ 0.95, mean \|Δp\| ≤ 0.02 → **pass**

| row | n PRs | median | p10 | p90 |
|---|---|---|---|---|
| hosted input_tokens ÷ local joint input_tokens (per PR) | 705 | **7.27** | 5.59 | 7.73 |

median tokens per PR: hosted 17050 · local joint 2344 · local single-question (10 requests) 17050; bar: median ratio ≥ 6

**R9-H5: pass** — bar: hosted joint vs local single-question: agreement ≥ 0.95, mean |Δp| ≤ 0.02, median hosted/local-joint input tokens ≥ 6

## Inputs

| path | sha256 |
|---|---|
| results/run6/pairs_eval_test.jsonl | `b5fcb0bf44f8fd1a` |
| results/run6/pass2-l4/loadtest_pplx-flash-local_x1.json | `47df8f4da75a5140` |
| results/run6/pass2-l4/loadtest_pplx-flash-local_x10.json | `2ca3f337ac0066fd` |
| results/run6/pass2-l4/loadtest_pplx-flash-local_x30.json | `a5bd9d3fd4a3d4c2` |
| results/run9/bf16/equiv/predictions_pplx-flash-local_test_all.jsonl | `5c85fc213d2f6352` |
| results/run9/bf16/equiv/server_before.json | `f836f1a5e9113942` |
| results/run9/bf16/equiv/throughput.json | `3f5cca3333a41ad5` |
| results/run9/bf16/load/loadtest_pplx-flash-local_x1.json | `7756fd53144beead` |
| results/run9/bf16/load/loadtest_pplx-flash-local_x10.json | `e056a47202bb265e` |
| results/run9/bf16/load/loadtest_pplx-flash-local_x30.json | `a9f1fc0827f14244` |
| results/run9/bf16/pairs_a/predictions_pplx-flash-local_test_all.jsonl | `4b43de586ff72369` |
| results/run9/bf16/pairs_a/server_before.json | `12055fce46ffaac9` |
| results/run9/bf16/pairs_b/predictions_pplx-flash-local_test_all.jsonl | `2dc6d868afab62e4` |
| results/run9/bf16/pairs_b/server_before.json | `1538003986f59447` |
| results/run9/bf16/pairs_b/throughput.json | `5d5ecc5254daa5ee` |
| results/run9/bf16/serve_meta.json | `40e4aa6d2dab820d` |
| results/run9/buckets/equiv/predictions_pplx-flash-local_test_all.jsonl | `f11571c0b3df1f95` |
| results/run9/buckets/equiv/server_before.json | `6700e68415409224` |
| results/run9/buckets/load/loadtest_pplx-flash-local_x1.json | `e99c15c04dd76f29` |
| results/run9/buckets/load/loadtest_pplx-flash-local_x10.json | `d0a01c559d7981f5` |
| results/run9/buckets/load/loadtest_pplx-flash-local_x30.json | `12ecb4826346cc22` |
| results/run9/buckets/serve_meta.json | `eb1a5196a0524a5a` |
| results/run9/fp8/equiv/predictions_pplx-flash-local_test_all.jsonl | `66001b4581ad029d` |
| results/run9/fp8/equiv/server_before.json | `6f084d1ddbdcc043` |
| results/run9/fp8/equiv/throughput.json | `db2e297e89ae1a7a` |
| results/run9/fp8/serve_meta.json | `399fc16e90f258aa` |
| results/run9/fp8/val/predictions_pplx-flash-local_val_all.jsonl | `832dda81f82e8b8c` |
| results/run9/fp8/val/server_before.json | `99dc6acc5e0fc2e6` |
| results/run9/groups_summary.json | `282eb74f1e2f10e2` |
| results/run9/h5/hosted/predictions_pplx-flash_test_all.jsonl | `204bbfeec9f3c823` |
| results/run9/h5/serve_meta.json | `5a706f9c1a6461ac` |
| results/run9/h5/single/predictions_pplx-flash-local_test_all.jsonl | `db3a02915a8cb8a7` |
| results/run9/h5/single/server_before.json | `7614b476505dbc46` |
| results/run9/shared/pairs_a/predictions_pplx-flash-local_test_all.jsonl | `28626165970a20d0` |
| results/run9/shared/pairs_a/server_before.json | `c376449624a5b7b3` |
| results/run9/shared/pairs_b/predictions_pplx-flash-local_test_all.jsonl | `ae2641d79cbdbd8a` |
| results/run9/shared/pairs_b/server_before.json | `5e28db41ae128c5d` |
| results/run9/shared/pairs_b/throughput.json | `0a31b3475ac001f6` |
| results/run9/shared/serve_meta.json | `9bb109b402232e00` |
