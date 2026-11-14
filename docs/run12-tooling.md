# Run 12 tooling (PROTOCOL.md R12 — draft, nothing frozen)

No model call until a section is frozen and tagged. What exists so far makes none.

## Holdout-2 (R12.0a, as amended 2026-11-14)

```bash
# lab machine: data/run3/*.jsonl on disk, a public read-only GITHUB_TOKEN in the environment
python -m mergeone.data.agent_prs discover --pages 10 --out data/run12/repos_candidates.json   # fresh discovery, run-3 thresholds
python -m mergeone.data.prepare_holdout2 select      # → data/run12/repos.json: run 3's test repositories + the first 40 new ones
python -m mergeone.data.prepare_holdout2 collect     # → data/run12/agent_prs.jsonl (gitignored); resumable
python -m mergeone.data.prepare_holdout2 diffs       # → data/run12/diffs.jsonl (gitignored): one whole diff per PR; resumable
python -m mergeone.data.prepare_holdout2 prepare     # → data/run12/holdout2.jsonl + data/run12/manifest.json
python -m pytest -q tests/test_holdout2.py tests/test_focused.py   # synthetic checks, any machine
```

The first collection (run 3's test repositories only) gave 360 PRs and 0 `iterated` positives; the amendment in
PROTOCOL.md R12.0a says why the set is widened and by which rule. `select` applies that rule and nothing else: every
repository run 3 used (validation or test) is removed from the fresh discovery, the rest are ordered by agent-PR
count, the first 40 are kept beside run 3's test repositories. Each row of `repos.json` carries `origin`
(`run3_test` | `new`); the manifest reports rows, repositories and `iterated` positives per origin.

`collect` fetches agent PRs **created after run 3's last collected date** from those repositories, skipping every PR
run 3 already holds. `diffs` fetches each collected PR's whole diff once (the dataset's `diff_head` is the first
4,000 characters in file order, which is not enough to build the `focused` rendering) and records whether its first
4,000 characters still equal the stored `diff_head`; a PR whose diff changed since collection is counted and left out
of both renderings. `prepare` applies the filters again, renders each PR with the run-3 renderer, labels it with the
run-3 outcome rules and writes the manifest. The manifest is committed; the rows and diffs are not (same policy as
run 3).

Holdout-2 is not scored by any model until a frozen section names it. `manifest.json` carries
`"scored_by_any_model": false`; the first run that scores it flips that in its own commit.

## The `focused` rendering (R12-H1)

`mergeone/data/focused.py`. `classify_path` puts a changed path in one of four classes, checked in this order:
lockfiles / generated / vendored / binary (shown last), tests (shown first), configuration and docs (third),
everything else — source (second). `focused_diff` splits the whole diff at its `diff --git` headers, orders the
sections by class (the diff's own order inside a class) and shows the first 4,000 characters; `full_from_diff` is the
control's block from the same fetched text. `render_focused` changes nothing else in the state.

## Next, before a freeze

- A runner that renders both states for every holdout-2 PR from `diffs.jsonl` and scores them (written before the freeze, refuses to run without the tag).
- m₁ from holdout-2's positive count (R12.0b), written into the frozen text.
- For R12-H2: a labelled duplicate set of ≥ 150 from holdout-2 (run 6's procedure).
