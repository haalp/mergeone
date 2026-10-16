# Roadmap

Ordered. Each item ships only with a measurement in `results/`, per [PROTOCOL.md](PROTOCOL.md).

## Now — run3: real agent PRs
Discover → fetch → Jev / Haiku predictions → stack-level rule → report. Settles R3-H1..H5 and
decides whether the numbers on the landing page can be claimed for agent-authored PRs.

## v0.2 — PR memory and duplicate detection (run 4, in progress)
Run 3 showed 36% of agent-PR closures are *superseded or duplicate* and the gate could not see
them. v0.2 adds a per-repository PR memory (`mergeone index owner/name`, kept fresh by the
webhook), deterministic retrieval (file overlap + BM25), one typed pair question
`same_change(A, B)` per candidate, and two policy signals: `duplicate_of` and `silent_days`.
Shipped in the Go gateway behind `memory.enabled`; MCP tool `gate_similar` lets an agent check
before it opens a PR. Pre-registered as run 4 in PROTOCOL.md; ships as default-on only if
R4-H1..H3 pass.

## v0.3 — `assumptions` (agent-declared premises)
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

## Later
- Campaign dedup: N agents opening the same fix across repos → one decision, N applications.
- Agent-specific priors: per-agent calibration maps once run3 shows they differ.
- GitLab / Gitea webhooks.
