# mergeone beside Claude Ship

Claude Ship (Anthropic, beta since 2026-11-23) reviews a pull request, fixes it, and applies an approval policy the
team writes in `.claude/approvals.yml`. Its classifiers judge **one pull request**: its diff, its description, its
review threads. Nothing in its documentation compares a pull request with the other open ones.

That comparison is what mergeone's PR memory does. This page is how to run the two side by side: Claude Ship decides
who has to approve a change, and mergeone answers one more question before it merges — *does another open pull
request look like the same change?*

What is and is not established here:

- **Works today, no Claude Ship access needed:** mergeone posts its own commit status for that one question, and you
  require it in a branch rule next to Claude Ship's check. GitHub does the combining. This is what the rest of the
  page sets up.
- **Not tested by us:** anything inside Claude Ship. It is offered on Team and Enterprise plans; this project has
  no such organization. The two tighter integrations at the end are read off Anthropic's documentation and are
  marked as untested.
- **What mergeone has measured:** the duplicate relation only. Run 6B: of 100 blind-labelled pairs the gate called
  the same change, 98 were a duplicate or a pointer to one (0.98, CI [0.93, 0.99]). Run 11: the memory's retrieval
  kept the true duplicate among its first five candidates for 42 of 46 pull requests (0.913; the bar was 0.95, a
  fail), and among its first ten for 45. So a duplicate can be missed when it is not retrieved. Backport, competing
  and builds-on relations are not validated and are not part of this check (`docs/EVIDENCE.md`).

## 1. The related check

In `.mergeone.yaml`:

```yaml
memory:
  enabled: true            # PR memory; backfill once with: mergeone index owner/name
  top_k: 10
actions:
  enabled: true
  related_context: mergeone/related   # the second status; the verdict keeps its own (status_context, default "mergeone")
  # related_threshold: 0.7            # calibrated duplicate_of from which the check fails (default: the shipped rule's)
```

On every decided head the gateway then writes two statuses. `mergeone/related` is:

| state | when |
|---|---|
| `success` | no open pull request looks like the same change |
| `failure` | the duplicate answer is at or above the threshold, or the decision's route is `dedup`; the description names the other pull request |
| `error` | the comparison could not be made: the decision failed, a pair call did not answer, or the repository is not indexed |

It fails closed: anything short of a completed comparison is not `success`. A new push is a new head and a new
answer. The gateway never merges and never closes either pull request.

**A wrong call has a way out, and it is a person's.** A maintainer answers the held decision "Not a duplicate — let
it through" (an `approve` resolution, recorded with their login). Through the gateway's own `POST /api/resolve` the
gateway then sets `mergeone/related` to `success` for that head, with "kept by <login>" in the description. "Mark
as duplicate" and "Ask to rebase" leave the check failed, and no resolution touches the verdict's own status.
The hosted portal (app.mergeone.dev) records the same resolution and mirrors its label and comment, but does not
write this status yet; until it does, answer through the gateway's API, or push a new head.

If you want only this question from mergeone and nothing else, reduce the policy's `rules` to the `duplicate-of`
rule and leave `default` at `pass`; the decision model is still called once per head (a fraction of a cent).

## 2. Require it next to Claude Ship's check

In the repository's ruleset for the default branch (GitHub → Settings → Rules), under "Require status checks to
pass", add `mergeone/related`. If the team enforces a Claude Approvals policy, its check (`Claude Approvals`) is
already in that list. Both must pass; neither knows about the other.

Two things to know before requiring it:

- A required check that never reports blocks every merge. Run the gateway first (`mergeone doctor --url …`), open a
  test pull request, and see the status arrive before adding the rule.
- `mode: shadow` writes nothing to GitHub, so the status does not appear in shadow mode. Shadow is for watching what
  the gate would say (`mergeone log --verdict hold`); switch to `enforce` when you require the check.

## 3. Before a pull request is opened

`/ship` in Claude Code opens the pull request when the branch has none. The cheapest duplicate is the one that is
never opened: the mergeone plugin's `before-you-open-a-pr` skill asks the repository's `gate_similar` tool first and
links the existing pull request instead. Install the plugin and write the repository's MCP server once
(`mergeone init --mcp --write`); the skill then runs before `/ship` or any other way of opening a pull request.

## Tighter integrations (from Anthropic's documentation; untested)

Both would let a Claude Approvals policy read mergeone's answer directly. Neither has been run by this project.

- **A seat.** A policy's `seats` can name a bot (`bot:<name>`), and a set can require one approval from it. A GitHub
  App of mergeone that approves when nothing related is found, and does not when something is, would make the
  related check one of the policy's approvers. It would be the first thing mergeone ever approves, and its approval
  would mean exactly "not the same change as another open pull request", nothing about the code.
- **A classifier that reads a label.** A custom classifier (`.claude/classifiers/<name>/`) reads the pull
  request's labels. mergeone already labels a held duplicate; a rule could then refuse Claude's approval while that
  label is present. Each classifier run is billed, a label can be removed by anyone with triage access, and a label
  that arrives after the classifier ran needs a rerun.

If you run Claude Ship and try either, an issue with what happened is welcome.
