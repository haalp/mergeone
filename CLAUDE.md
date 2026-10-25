# mergeone — working rules for any Claude session in this repository

This is the open-source merge gateway (Go, `gate/`) and the pre-registered benchmark gatebench (Python, `mergeone/`,
`PROTOCOL.md`, `results/`). The hosted portal lives in the private repo `haalp/mergeone-portal`; the site in
`haalp/mergeone.dev`. Read `README.md`, then `ROADMAP.md`, then the newest `## Run N` section of `CHANGELOG.md`.

## Commits
- Author every commit as `haalp <hasanalp.tmz@gmail.com>`: `git -c user.name=haalp -c user.email=hasanalp.tmz@gmail.com commit …`.
- No `Co-Authored-By`, no `Claude-Session`, no trailer of any kind, nothing mentioning Claude in messages or code.
- Never print API keys or tokens; `.env` is loaded with `set -a; source .env; set +a` and never echoed. Redact
  tool output before pasting it anywhere.

## The protocol is the product
- Every hypothesis, bar and threshold is committed in `PROTOCOL.md` **before** any model call for that run. Bars
  are never edited after results exist. Every deviation is a dated line in `CHANGELOG.md`, with the reason.
- Negative results are published exactly like positive ones (`results/runN/README.md`, `docs/EVIDENCE.md`, the
  site's Updates page). Never soften a fail; a post-hoc reading goes **beside** the verdict, labelled post-hoc,
  never in its place.
- Since run 7: every comparison against an earlier run's number has a same-day full-state control in the same
  `--run` directory (hosted models drift under an unchanged version string).
- Never re-run a completed run; never call a model on the test split before the run's freeze commit and tag.
- Report builders (`mergeone.eval.runN_report`) rebuild every table from `results/`; numbers in prose must come
  from them, never typed by hand.

## Product rules that came from measurement
- A `hold` never closes a PR; the gateway never merges anything. Closing is gated on a future run (C5 / relation v2).
- `top_k: 10` for PR memory; the dedup rule stays `hold → route: dedup`.
- Nothing from a failed run ships. Run 7: full state and the untuned prompt stay the defaults; `--compact` and
  `jev_v3_r2` remain selectable.
- Drift monitor thresholds (`docs/drift-monitor.md`): shift > 0.20 or spread halved, n ≥ 200; moved only by a run.

## Where things are
- Benchmark commands per run: `docs/runN-tooling.md`. Run 8 is frozen (§R8); its order is in `docs/run8-tooling.md`.
- Gateway policy: `docs/POLICY.md`. Portal contract: `docs/portal.md`. Site copy and Updates: `haalp/mergeone.dev`.
- Scratch and logs belong outside the repo; `data/` and `results/` are inputs and outputs, never edited by hand.

## Style
- Prose in README/docs: short, concrete, numbers with their bars and CIs, no marketing adjectives.
- Tests must pass before a commit (`python -m pytest -q`, `cd gate && go test ./...`).
