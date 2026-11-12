# Demo script (≈ 2 min 45 s, one take, terminal + browser)

For the YC application's demo slot and for the README. Record at 1080p, terminal font ≥ 18 pt, no music. Say the
lines; do not read the screen aloud. Every command is real and runs as written on v0.7+.

| time | screen | say |
|---|---|---|
| 0:00 – 0:15 | A GitHub PR list full of agent-authored PRs (any busy public repo) | "Coding agents open pull requests faster than anyone can review them. The question per PR is not 'what does a reviewer think' — it's 'can this merge without a human, yes or no'. mergeone answers that." |
| 0:15 – 0:35 | Terminal: `brew install haalp/tap/mergeone` (already installed: show `mergeone version`), then `mergeone init` in a repository | "One binary. `init` writes a policy file the team owns — rules over typed questions, not prompts." |
| 0:35 – 0:55 | `mergeone doctor --repo <owner/name>` | "`doctor` checks the whole setup: policy, calibration, one real model call, token access. That call took a third of a second and cost four thousandths of a cent." |
| 0:55 – 1:30 | `mergeone pr <owner/name> <N>` on a PR that holds | "Here's a real pull request. Verdict: hold, route to a human. It says which rule fired and by how much — and the nearest rule that didn't, so you know what a threshold change would do. It never merges or closes anything." |
| 1:30 – 1:50 | `mergeone explain` | "Every decision is recorded. `explain` replays it rule by rule, without calling the model again." |
| 1:50 – 2:15 | Browser: app.mergeone.dev → Needs you → The braid | "The portal shows what needs you, and how related PRs braid together — duplicates and competing fixes of the same issue, which agents produce constantly." |
| 2:15 – 2:45 | Browser: mergeone.dev → the run list, scroll through the red tags | "Every threshold in the product comes from a pre-registered benchmark: eleven runs, twenty-four results, sixteen of them failures — published the same way as the passes. That's how we know where the model can be trusted and where a human has to look." |

Before recording: `set -a; source .env; set +a`; pick the PR for 0:55 in advance with `mergeone pr … --json | jq .verdict`
so the take shows a `hold`; clear the terminal; hide bookmarks; sign in to the portal.
