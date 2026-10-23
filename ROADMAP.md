# Roadmap

Ordered. Each item ships only with a measurement in `results/`, per [PROTOCOL.md](PROTOCOL.md).

## Now — run 6B: is a `hold` for duplication right?
100 PRs the gate holds as duplicates (pplx-flash, k = 20), labelled blind by one human (pre-registered
number) and by a second labeller (agreement and Cohen κ reported). Interim at n = 50: dup ∪ pointer 1.00,
duplicate share 0.54 under the final taxonomy, κ 0.80 after the taxonomy was written down. The result decides
the product default for the dedup rule — `hold → route: dedup` stays, or it is demoted to `annotate` (link,
no hold) — and which pointer classes (backport, promote, competing fix) get "link both, route to a human".
Then run 7 is frozen from [`docs/run7-addendum-draft.md`](docs/run7-addendum-draft.md).

Run 3 (real agent PRs) is done: negative, 67% of closures are coordination; see docs/EVIDENCE.md.

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
- Run 6A measured the first self-hosted row (pplx-flash on one L4 and one H100: `pplx-flash-local`). The other
  open-weight rows (clef, JEV-9B/27B, H2O Lightning 4B, Laya) are still claims until they get a row; run 7's C4
  takes one that fits a single L4.

## Verdict actions (done, gateway v0.2)
- ✅ `actions:` in the policy writes the verdict back to the PR in webhook/CI mode: commit status for branch
  protection / auto-merge, exclusive `mergeone:*` labels, one comment per head sha, reviewers on hold/block
  routed to `human`. Off by default, deterministic, never merges, never changes a verdict.

## Convergence cap (done, gateway v0.2.1)
- ✅ `max_rounds: N` in the policy (default 0 = off). Agent author + automated review can loop (one reported PR
  went 50+ rounds before a three-round cap). After N non-pass verdicts on one PR — distinct head SHAs in the
  gatelog — the next non-pass verdict is routed to a human under rule `max-rounds`, the matched rule kept in
  `capped_rule`; `pass` is never overridden. Webhook, `/decide`, MCP (with a PR number) and `mergeone pr --pr`.

## Run 7 candidates (draft addendum in docs/run7-addendum-draft.md; nothing is a hypothesis until it is in PROTOCOL.md)
- **Base-branch-aware dedup.** Backports/cherry-picks of one change to release branches look like duplicates and
  are held; they are legitimate. Read the target branch; same change on a different branch → `annotate`, not
  `hold`. Run 6B's labels say how much of the hold set this is.
- **Diff budget ablation, 16k → 4k tokens.** Run 6A pass 2: throughput is per-request compute, so tokens per PR
  is the only single-box lever (~3× on a GPU, ~3× cheaper hosted). Quality cost unknown; measure on the run-5
  v3 questions.
- **Test evidence as a structural feature (free floor).** Run 5's `rules` floor (size, test touch, file mix)
  sat at AUC 0.73 vs 0.76 for the models; `tests_cover_change` alone scored 0.62–0.68 raw. A modelless feature
  set — PR touches test files, adds E2E/property tests, has a passing test check or a test log in its body,
  "evidence attached" vs "diff only" — may lift the floor and is what reviewers increasingly ask for ("review
  evidence, not diffs"). Cheap to compute, zero latency; worth one pre-registered sub-hypothesis against the
  rules floor before spending model budget on it.
- **Prompt tuning on val, frozen before test.** Published results show +0.3 to +13 AP from a few rounds on a
  small labelled set, the smallest models gaining most. Run 5's val-chosen pruning dropped the best test
  question, so the risk is real; pre-register the tuning set size and the freeze commit. Acceptance rule,
  borrowed from how the Claude Code team scores their reviewer against an incident set: a prompt change is kept
  only if its score on the frozen revert/iteration eval (run 1 reverts, run 5 ≥2-round PRs) does not drop.
- **Typed relation + `related` annotations (C5 in the draft).** 6B says a hold is rarely a false alarm but often a
  pointer, not a duplicate. A relation question (`same_change / follow_up / competing_fix / backport / same_area /
  unrelated`) scored on the 6B pairs; policy gains `on_related` (cross-link both PRs, no hold) and `on_competing`
  (route to a human). Ships opt-in only if it clears its bar.
- **More registry rows:** a 4B model that runs on one L4, a 26B MoE with 4B active parameters (27B quality at
  4B compute, the interesting self-host candidate), a 9B on an A10G; two further hosted decision APIs.

## Later
- Relation graph: once C5 lands, every `(A, B, relation, p)` goes to the gatelog, which gives a per-repository PR
  graph (node = PR, edge = duplicate / follow-up / competing / backport / same-area). `GET /graph?repo=` serves it;
  a small web view draws it as agent PRs pass the gate (competing edges red, follow-up chains, cross-branch
  promotes); `mergeone plan` uses the edges for landing waves. UI work starts only after R7-H5 passes.
- Campaign dedup: N agents opening the same fix across repos → one decision, N applications.
- Agent-specific priors: per-agent calibration maps once run3 shows they differ.
- GitLab / Gitea webhooks.
