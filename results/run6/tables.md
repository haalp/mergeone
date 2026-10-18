# Run 6 part A — self-hosted pplx-decider-flash (`pplx-flash-local`)

served as `pplx-decider-flash-v1.2@6d771ad7b5ecff5e93b8fc7b90d4c5ccc341954d` · GPU NVIDIA L4 · VM RunPod NVIDIA L4 Secure EU-RO-1 (pod b9nme44vl2gh5i) (—) · $0.590/h · torch 2.14.0+cu130 · transformers 5.17.0 · pplx_decider 0.3.0 · fast=True · batch ≤ 32 / 20.0 ms

rows: joint val 1491, joint test 705, single val 0, single test 705, pair test 187; hosted v3 test 705

## R6A-H1: equivalence with the hosted pplx-flash (run-5 test, v3, joint)

705 PRs joined, 7050 answers · agreement **0.952** (bar ≥ 0.95) · mean |Δp| **0.0469** (bar ≤ 0.02) · p99 |Δp| 0.2594 · versions: local pplx-decider-flash-v1.2@6d771ad7b5ecff5e93b8fc7b90d4c5ccc341954d / hosted pplx-decider-flash-v1.2

| question | n | agreement | mean \|Δp\| | max \|Δp\| | mean Δp | Pearson | yes-rate local | yes-rate hosted |
|---|---|---|---|---|---|---|---|---|
| is_logic_change | 705 | 0.973 | 0.0312 | 0.302 | -0.0191 | 0.991 | 0.627 | 0.648 |
| weakens_tests | 705 | 0.976 | 0.0294 | 0.216 | -0.0007 | 0.973 | 0.071 | 0.072 |
| message_matches_diff | 705 | 0.902 | 0.0700 | 0.434 | -0.0307 | 0.842 | 0.818 | 0.860 |
| touches_concurrency | 705 | 0.999 | 0.0157 | 0.258 | -0.0073 | 0.981 | 0.020 | 0.021 |
| needs_human_review | 705 | 0.932 | 0.0393 | 0.182 | -0.0312 | 0.985 | 0.563 | 0.611 |
| exceeds_scope | 705 | 0.948 | 0.0439 | 0.289 | -0.0253 | 0.975 | 0.243 | 0.272 |
| breaking_change | 705 | 0.940 | 0.0429 | 0.260 | -0.0321 | 0.977 | 0.166 | 0.214 |
| tests_cover_change | 705 | 0.945 | 0.0548 | 0.533 | -0.0277 | 0.968 | 0.540 | 0.567 |
| hard_to_revert | 705 | 0.936 | 0.0614 | 0.812 | 0.0397 | 0.818 | 0.087 | 0.060 |
| follows_conventions | 705 | 0.970 | 0.0808 | 0.297 | -0.0765 | 0.846 | 0.007 | 0.026 |

## R6A-H3: quality (needs_human_review → iterated, test)

ROC-AUC **0.747** [0.667, 0.806] (calibrated (isotonic on self-hosted val vs iterated); raw 0.770) vs hosted 0.758 · ECE 0.052 · n_val 1491, n_test 705

same_change pair AUC (run-4 test pairs): ok — **0.840** [0.753, 0.903], n 187 (29 positive) vs hosted 0.842; hosted on the same pairs 0.842, agreement 0.989, mean |Δp| 0.0111

## R6A-H4: joint (10 questions in one request) vs single (one question per request)

705 test PRs in both modes · basis: raw (no single-question val rows)

| mode | nhr → iterated AUC | 95% CI | p50 ms / PR | p99 ms / PR | requests / PR |
|---|---|---|---|---|---|
| joint | 0.770 | [0.693, 0.839] | 7384 | 26025 | 1.0 |
| single | 0.770 | [0.698, 0.834] | 58698 | 117545 | 10.0 |

gain 0.000 (bar ≥ 0.02) → default for this model: **joint**

| question | joint raw AUC vs iterated | single raw AUC vs iterated |
|---|---|---|
| is_logic_change | 0.660 | 0.619 |
| weakens_tests | 0.706 | 0.714 |
| message_matches_diff | 0.508 | 0.323 |
| touches_concurrency | 0.647 | 0.658 |
| needs_human_review | 0.770 | 0.770 |
| exceeds_scope | 0.748 | 0.751 |
| breaking_change | 0.691 | 0.719 |
| tests_cover_change | 0.675 | 0.618 |
| hard_to_revert | 0.714 | 0.758 |
| follows_conventions | 0.644 | 0.281 |

## R6A-H2: load (open loop, v3 joint, loopback) and saturation curve

| multiplier | rate /s | n | p50 ms | p99 ms | p99 schedule lag s | errors | concurrency |
|---|---|---|---|---|---|---|---|
| x1 | 0.35 | 600 | 708 | 1462 | 0.003 | 0.000 | 512 |
| x10 | 3.47 | 600 | 22060 | 29897 | 0.003 | 0.715 | 512 |
| x30 | 10.42 | 600 | — | — | 0.002 | 1.000 | 512 |
| x100 | 34.72 | 600 | — | — | 15.287 | 1.000 | 512 |
| x300 | 104.17 | 600 | — | — | 25.117 | 1.000 | 512 |

Saturation: first p99 > 2 × ×1 p99 at ×10; first errors at ×10. Hosted pplx-flash (run 5): x1 p99 561 ms / errors 0.000, x10 p99 462 ms / errors 0.000, x100 p99 501 ms / errors 0.713

## Cost

| row | $/1k PRs | basis |
|---|---|---|
| self-hosted (compute) | 0.4720 | $0.590/h ÷ 1250 PRs/h (×1.0, 0 errors) |
| hosted pplx-flash (list price) | 0.0228 | run-5 in-run tokens × $0.01/M |

## Pre-registered criteria

| hypothesis | result | bar | pass |
|---|---|---|---|
| R6A-H1 | {"n_prs_joined": 705, "n_answers": 7050, "agreement": 0.9521, "mean_abs_dp": 0.0469} | argmax agreement >= 0.95 and mean abs Δp <= 0.02 (705 × 10 answers) | ❌ |
| R6A-H3 | {"roc_auc": 0.7472, "ci95": [0.667, 0.806], "basis": "calibrated (isotonic on self-hosted val vs iterated)", "hosted": 0.7581, "delta": -0.0109, "status": "R6A-H1 failed: reported as a different model, not compared to the hosted row"} | abs(AUC − hosted 0.758) <= 0.02 | pending |
| R6A-H3-pair | {"roc_auc": 0.8405, "n": 187, "hosted": 0.8422, "status": "ok"} | reported; abs(AUC − hosted 0.842) <= 0.02 shown for reference | pending |
| R6A-H4 | {"joint": 0.7695, "single": 0.7699, "gain": 0.0004, "basis": "raw (no single-question val rows)", "default": "joint", "status": "ok"} | single >= joint + 0.02 → single-question default for this model; else joint | ❌ |
| R6A-H2 | {"x1_p99_ms": 1461.9252, "x100_p99_ms": null, "x100_error_rate": 1.0, "saturation": "first p99 > 2 × ×1 p99 at ×10", "status": "ok"} | ×100: p99 <= ×1 p99 + 200 ms and 0 errors | ❌ |

## Notes

- R6A-H1: rows joined on pr_number (self-hosted joint v3 test ∩ hosted run-5 v3 test, non-error); an answer agrees when both probabilities are on the same side of 0.5 (>= 0.5 = yes). Δp = self-hosted − hosted. Not used to tune anything (R6A.4).
- R6A-H3: run5._h5_model unchanged — isotonic (out_of_bounds=clip) fitted on the self-hosted val answers vs `iterated`, applied to the self-hosted test answers. If no self-hosted val rows exist, the raw answer's AUC is reported and marked 'val not run'. Pair AUC: run-4 R4-H2 test pairs (kind != retrieved, or retrieved with a_label = superseded_by), duplicate_of = P(same) + 0.5 P(subset); needs results/run4/pairs.jsonl for the pair kinds. If R6A-H1 fails the row is 'a different model': H3 is reported, not compared.
- R6A-H4: single = one request per question (predict --single-question; 10 sequential calls per PR, latency = wall-clock of the PR). Compared on the PRs answered in both modes. Calibrated AUCs when both modes have their own val rows (each refit on its own val answers), otherwise raw AUCs for both (isotonic is monotone, so raw ranks ≈ calibrated ranks); which one is stated as `basis`.
- R6A-H2: open-loop replay (cli loadtest, v3, joint, 600 test PRs, 429 retry off) from the gate client on the same VM (loopback). Bar = R3-H5: ×100 p99 <= ×1 p99 + 200 ms and 0 errors. Saturation = first multiplier in 1,10,30,100,300 whose p99 exceeds 2 × the ×1 p99. Load-test p99 is per decision (one joint request).
- Cost: $/1k PRs = usd_per_hour (serve_meta.json) / (rate_per_s × 3600 / 1000) at the highest multiplier with 0 errors (PROTOCOL R6A.3). Hosted: run-5 in-run mean list-price cost per PR × 1000.
