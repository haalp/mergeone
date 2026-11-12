# Run 12 tooling (PROTOCOL.md R12 — draft, nothing frozen)

No model call until a section is frozen and tagged. What exists so far makes none.

## Holdout-2 (R12.0a)

```bash
# lab machine: data/run3/*.jsonl on disk, a public read-only GITHUB_TOKEN in the environment
python -m mergeone.data.prepare_holdout2 collect     # → data/run12/agent_prs.jsonl (gitignored); resumable
python -m mergeone.data.prepare_holdout2 prepare     # → data/run12/holdout2.jsonl + data/run12/manifest.json
python -m pytest -q tests/test_holdout2.py           # synthetic check, any machine
```

`collect` fetches agent PRs **created after run 3's last collected date** from the **eight test repositories** only,
skipping every PR run 3 already holds. `prepare` applies the same filters again, renders each PR with the run-3
renderer, labels it with the run-3 outcome rules and writes the manifest: rows per repository, each label's count,
`iterated_positives` (the number the power statement needs), and how many rows are too recent for their
`reverted_within_30d` label to be final. The manifest is committed; the rows are not (same policy as run 3).

Holdout-2 is not scored by any model until a frozen section names it. `manifest.json` carries
`"scored_by_any_model": false`; the first run that scores it flips that in its own commit.

## Next, before a freeze

- `focused` renderer + path classifier (R12-H1), with tests; no model.
- m₁ from holdout-2's positive count (R12.0b), written into the frozen text.
- For R12-H2: a labelled duplicate set of ≥ 150 from holdout-2 (run 6's procedure).
