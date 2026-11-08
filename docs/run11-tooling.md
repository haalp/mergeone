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

To be written at freeze: `scripts/run11_hosted.sh` runs the control (full state, `pplx-flash`) and the compact
rendering on the 705 test PRs the same UTC day into `results/run11/{control,compact}/`; `mergeone.eval.run11_report`
builds the table (mean v4 Δ, per-question Δ, tokens ratio) and the verdict.

## R11-H3 — team recalibration (conditional)

Waits for ≥ 200 decided PRs with outcomes in one gatelog. `mergeone calibrate --from-gatelog` is the code path; the
report splits by time (70/30) and compares ECE per question against the shipped map.
