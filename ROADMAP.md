# Roadmap

Ordered. Each item ships only with a measurement in `results/`, per [PROTOCOL.md](PROTOCOL.md).

## Now — run3: real agent PRs
Discover → fetch → Jev / Haiku predictions → stack-level rule → report. Settles R3-H1..H5 and
decides whether the numbers on the landing page can be claimed for agent-authored PRs.

## v0.2 — PR memory and duplicate detection (run 4: question ✅, retrieval ❌)
Run 3 showed 36% of agent-PR closures are *superseded or duplicate* and the gate could not see
them. v0.2 adds a per-repository PR memory (`mergeone index owner/name`, kept fresh by the
webhook), deterministic retrieval (file overlap + BM25), one typed pair question
`same_change(A, B)` per candidate, and two policy signals: `duplicate_of` and `silent_days`.
Shipped in the Go gateway behind `memory.enabled`; MCP tool `gate_similar` lets an agent check
before it opens a PR. Pre-registered as run 4 in PROTOCOL.md; ships as default-on only if
R4-H1..H3 pass.
- **Run 4 result (2026-10-17):** the pair question passes (0.83–0.87 AUC for three System One
  models), retrieval does not (recall@5 0.55, p99 198 ms), so H3/H4 land at a third of the bar.
  Stays opt-in. Next: retrieval that reads the diff (not the file list) and the Go store's latency
  as the pre-registered number — a run-5 addendum, not a re-run.
- **Run 5 result (2026-10-18):** retrieval v2 (shared-issue refs + directory overlap) 0.55 → 0.66 recall@5
  (bar 0.70); the changed-line content stage moved nothing on test; Go store p99 3.3 ms ✅. Coverage of
  closed PRs crosses +15 pp for pplx/pplx-flash; pass-group bad rate still does not halve for anyone —
  hold *precision* is the unmeasured thing. v2 replaces v1 in the store; memory stays opt-in. Next lever is
  a wider candidate pool (gold is usually at rank 6–20), which is a protocol change.

## v0.3 — `assumptions` (agent-declared premises) — run 5: component built, claim untestable on GitHub
Run 5 extracted typed premises for 2,298 PRs from descriptions alone (Haiku, $0.0004/PR, 99.9% non-empty)
and found that maintainers write a numbered "depends on / conflicts with #N" comment on 9 of 2,196 agent
PRs — no ground truth to score against. `assumptions` ships as a `gate_decide` input the policy can use,
with no accuracy claim. A test needs a repo where agents already declare premises, or a merge-conflict label.

Agents know what their change *relies on*; today the gate has to infer it from the diff.
Let the agent say it:

- MCP `gate_decide` / `gate_plan` accept `assumptions: [{ kind, target, claim }]`
  (e.g. `{kind: "api", target: "auth.validate_token", claim: "returns None on expiry"}`).
- `.mergeone.yaml` can require assumptions for paths matching `sensitive:` prefixes.
- The landing plan gets an **assumption-conflict wave**: two open PRs whose assumptions
  contradict (same `target`, incompatible `claim`), or one PR that *changes* a target another
  PR *assumes*, land in separate waves and the later one is put on `hold` with the reason.
- Inference stays the fallback: a PR without assumptions is gated exactly as today.
- Measured: how often declared assumptions catch a conflict the file/semantic waves miss,
  on run3 stacks. Ships only if it adds recall at the same FPR.

Credit: the "changes collide on beliefs, not lines" framing is from
[bimsonz/keel](https://github.com/bimsonz/keel); mergeone adopts the idea as an optional
input on top of existing Git hosting rather than as a new platform.

## v0.4 — conflict-proving tests
When an assumption conflict or `touches_concurrency` overlap is detected between two PRs,
emit a minimal failing test that demonstrates the interaction, attach it to the hold
reason, and offer it as a guard for trunk. A test is a verdict a human can run.
- Overlap at the syntax-tree level, not the line level: run 4 measures file Jaccard; two PRs
  that touch the same *function* (tree-sitter node) are a stronger conflict signal than two that
  touch the same file, and formatting-only diffs stop counting. Candidate retrieval feature for run 5.

## Decision-model registry (done, v0.2)
- `prompts/models.yaml`: one row per model (dialect, endpoint, key env, id, price, weights). Python and
  Go read the same file; `model: <row>` / `provider:` block in the policy; `mergeone models` lists it.
- `mergeone leaderboard` ranks every model run under the protocol on quality / calibration / speed / cost
  with the 100× scale bar as a hard gate (`results/leaderboard.md`).
- Next: run the open-weight rows (clef, JEV-9B/27B, H2O Lightning 4B, Laya) on a local llama.cpp
  `/v1/systemone` server under the run-2 judge labels, so self-hosted candidates get a measured row.

## Verdict actions (done, gateway v0.2)
- ✅ `actions:` in the policy writes the verdict back to the PR in webhook/CI mode: commit status for branch
  protection / auto-merge, exclusive `mergeone:*` labels, one comment per head sha, reviewers on hold/block
  routed to `human`. Off by default, deterministic, never merges, never changes a verdict.

## Later
- Campaign dedup: N agents opening the same fix across repos → one decision, N applications.
- Agent-specific priors: per-agent calibration maps once run3 shows they differ.
- GitLab / Gitea webhooks.
