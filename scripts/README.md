# scripts/

How each run's numbers were produced, in the order they ran. Everything here is reproducible from the committed
inputs; model calls need the keys named in each script's header.

| run | script | side |
|---|---|---|
| 1 | `run_jev.sh`, `run_jev_test.sh` | Jev (val, test, `--words`, load tests) |
| 1 | `run_api_models.sh` | Haiku, rules, GBM |
| 1 | `smoke_api.sh` | 20-example API smoke test before spending money |
| 2 | `run2_judge_and_haiku.sh`, `run2_jev.sh` | judge labels + Haiku; Jev |
| 3 | `run3_collect.sh`, `run3_haiku.sh`, `run3_jev.sh` | agent-PR collection; Haiku; Jev |
| 4 | `run4_pairs.sh` | pair question on retrieved candidates |
| 5 | `run5_mac.sh`, `run5_v3_predict.sh`, `run5_assumptions_predict.sh` | retrieval v2, v3 question set, assumptions |
| 6A | `run6_gpu.sh` (+ `docs/run6-gpu.md`) | self-hosted serving, probe, load tests on L4 / H100 |
| 6B | `run6b_mac.sh` | candidate pool k, pairs, blind sample, labels |
| 8 prep | `run8_prep.py` | routing, v4 and drift numbers from existing predictions, no model call → `docs/run8-numbers.md`, `results/run8prep/` |
| v4 maps | `mergeone fit-calibration --run results/run5 --model <jev\|pplx-flash\|haiku> --qset v4 --from-qset v3 --label iterated --dataset data/run3/dataset.jsonl` | `mergeone/gate/calibration_<model>_v4.json`: isotonic per question on the run-5 val v3 rows vs `iterated` (as R5-H5), the eight v4 questions, plus `fit_stats`; no model call; `tests/test_calibration_v4.py` checks the refit is byte-identical |
| — | `extract.sh` | dataset extraction (run 1 base data) |
| — | `fig_decision_models_under_load.py` | the load figure in `results/figures/` |

A CI workflow is no longer kept as an example here: `mergeone init --ci` writes it (`gate/internal/initcmd/templates/workflow.yml.tmpl`).
| `drift_slow.py` | slow drift signal, Python reference (AUC vs outcome on identical rows, −0.05) → `results/run8/drift_slow.json` for R8-H5 |
