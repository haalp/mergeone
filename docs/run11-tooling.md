# Run 11 tooling (PROTOCOL.md §R11 — draft, not frozen)

No model call until the freeze commit and tag `r11-frozen`. Order: freeze → H2 from recorded answers → H1 control then
compact, same UTC day → report.

## R11-H2 — pair prefilter (no model call; gatebench-data files on the lab machine)

```bash
python -m pytest -q tests/test_run11_prefilter.py                   # synthetic check, any machine
python -m mergeone.eval.run11_prefilter --i-have-frozen-the-protocol  # lab machine: needs results/run9/pairs_b.jsonl,
                                                                      # results/run6/candidates_k20.jsonl (gitignored)
# → results/run11/prefilter.json; the printed line is the verdict (primary `retrieval`, k' ∈ {3, 5})
```

The script refuses to run without the flag: computing a pre-registered number before the freeze is the thing the
protocol forbids. Scores and weights are in the module docstring and `WEIGHTS`; they are not edited after the freeze.

## R11-H1 — compact main call (hosted, ≈ $0.10)

```bash
scripts/run11_hosted.sh          # lab machine; refuses until tag r11-frozen exists; control first, then --compact, same UTC day
scripts/run11_hosted.sh report   # python -m mergeone.eval.run11_report → results/run11/{metrics.json,tables.md}
```

Rows land in `results/run11/main/predictions_pplx-flash_test_all.jsonl` (control) and `…_compact.jsonl` (treatment),
as run 8's v4 directory did; `run11_report.h1` scores them with run 8's `h1_v4` logic plus the per-question and token
clauses.

## R11-H3 — team recalibration (conditional)

Waits for ≥ 200 decided PRs with outcomes in one gatelog. `mergeone calibrate --from-gatelog` is the code path; the
report splits by time (70/30) and compares ECE per question against the shipped map.
