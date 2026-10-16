# Contributing to mergeone

Thanks for looking. This project is an open-source merge gateway **and** a pre-registered benchmark
(gatebench). Both halves have rules; the benchmark's are stricter.

## Ground rules

1. **Protocol first.** Any experiment — a new run, question set, model, label, or threshold — is written into
   `PROTOCOL.md` as an addendum *before* code or data exists, with hypotheses and pass/fail bars. The commit that
   adds the addendum must precede any commit that adds results.
2. **Deviations go in `CHANGELOG.md`**, dated, with the reason. Never edit an existing protocol section; append.
3. **Negative results ship.** If a hypothesis fails, the tables, figures and write-up go into `results/<run>/`
   exactly like a success. Do not open a PR that drops a failed run.
4. **No frontier models as candidates.** The candidates are System One models (Jev), small LLMs (Haiku), and
   rules. Larger models may act as a judge or a classifier for *reporting only*, and the write-up must say so.
5. **Every number in README or the website traces to `results/`.** If you change a number, link the file.

## What is welcome

- **Model adapters** (`mergeone/models/`, `gate/internal/models/`): implement `decide(state, features, questions)`,
  keep the rendered state identical, record latency/tokens/cost, never hide an error. Add prices to
  `results/prices.json`. Then run it through the existing runs before proposing it as a candidate.
- **Policy rules and routes** (`mergeone/gate/policy.yaml`, `gate/internal/engine`): typed, deterministic, tested.
- **Integrations**: GitLab/Gitea webhooks, new MCP tools, CI recipes.
- **Data sources** for the benchmark: new repositories must meet the discovery rule in the relevant run
  addendum; add them as a *fresh* slice, never mixed into a split that has been evaluated.
- **Docs, landing page** (`haalp/mergeone.dev`), figures.

## Development

```bash
# Python reference + benchmark
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest -q
# Go gateway
cd gate && go test ./... && go build ./cmd/mergeone
# rebuild every table
python -m mergeone.cli report results/run2 --v2
python -m mergeone.cli report results/run3 --run3
```

CI runs the Python tests, the Go tests, and a throughput gate (350 decisions/s with p99 ≤ 600 ms on the
mock model). A PR that lowers throughput or raises p99 needs a reason in its description.

## Pull requests

- One topic per PR. Keep the diff explainable in the PR template's three questions.
- Commits are authored by you. Tooling-generated trailers are fine to omit.
- If a PR touches `PROTOCOL.md`, say which run it belongs to and confirm no result for that run exists yet.
- Benchmark results are committed as the exact files the CLI writes (`predictions_*.jsonl`, `metrics.json`,
  `tables.md`, `figures/`). Do not hand-edit them.

## Licence

Apache-2.0. By contributing you agree your contribution is licensed the same way.
