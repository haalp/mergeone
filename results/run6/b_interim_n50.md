# Run 6 part B — interim look at n = 50 of 100 (2026-10-20)

**Reporting only.** R6B.2-B2 says precision is computed once all 100 labels exist; this file is a dated look at the first 50 so the
labelling could be sanity-checked mid-way. No threshold, rule or default changed on it. The pre-registered number is the human
labeller's at n = 100; it will replace this file.

Labels are blind (labeller sees A, B, files, diff excerpt; no model output). Taxonomy as fixed on 2026-10-19: **duplicate** = same
change / B makes A unnecessary (incl. revision superseded by B); **pointer** = A's author should have looked at B first (backport,
promote edge→stable, competing fix, follow-up, revert, same area); **unrelated** = file overlap only.

| labeller | n | duplicate | pointer | unrelated | dup ∪ pointer [95% CI] | duplicate share [95% CI] |
|---|---|---|---|---|---|---|
| human (pre-registered) | 50 | 16 | 34 | 0 | 1.00 [0.93, 1.00] | 0.32 [0.21, 0.46] |
| second labeller (fable-5.1, blind, same 50; promote pairs mapped to pointer) | 50 | 32 | 18 | 0 | 1.00 [0.93, 1.00] | 0.64 [0.50, 0.76] |

Agreement 0.64, Cohen κ 0.35 (3 classes). Confusion (human, second): duplicate/duplicate 15, duplicate/pointer 1, pointer/duplicate 17, pointer/pointer 17.

Against the R6B-H2 bar (union ≥ 0.60 and duplicate ≥ 0.30, pplx-flash k=20): union clears it with room; duplicate share 0.32 clears it
with the interval reaching below the bar, so n = 100 decides. Nearly all disagreement is one-directional: the human reads 'same
ticket / same files, B must be checked first' as pointer where the second labeller reads it as duplicate. An adjudication round on
the disagreements (post-hoc, separate file, both readings kept) follows the 100th label.

## Disagreements (item, repo, A, B, human → second)

- 9 · VocaHQ/vocalinux #882 / #907: pointer (related, maybe conflıct wıth b. Check b first) → duplicate (competing implementations of transcript persistence)
- 10 · microsoft/vscode #333984 / #333980: pointer (touches same files should labeled as pointer) → duplicate (—)
- 11 · mattermost/mattermost #36863 / #36862: pointer (same area) → duplicate (—)
- 12 · Comfy-Org/ComfyUI_frontend #14820 / #14822: pointer (need to check b first) → duplicate (—)
- 13 · microsoft/vscode #326187 / #326186: pointer (need to check B) → duplicate (—)
- 15 · Azure/azure-sdk-for-net #63593 / #63451: pointer (need to check B) → duplicate (same dependency bump)
- 17 · mattermost/mattermost #37013 / #37014: pointer (same ticket need to check b) → duplicate (same ticket)
- 21 · compdemocracy/polis #2704 / #2742: pointer (same area but not totally duplicated. Thus check b first) → duplicate (supersedes)
- 22 · mattermost/mattermost #38704 / #38033: pointer (check b first same area) → duplicate (same bug, same files)
- 28 · Azure/azure-sdk-for-net #60025 / #60014: pointer (need to check b) → duplicate (same codegen)
- 29 · Azure/azure-sdk-for-net #63514 / #63533: pointer (competing fix) → duplicate (same pagination fix)
- 32 · microsoft/vscode #334913 / #334664: pointer (related , check b) → duplicate (same pty buffering fix)
- 35 · compdemocracy/polis #3034 / #3037: pointer (need to check b whıch comments changes ın b) → duplicate (—)
- 36 · compdemocracy/polis #2913 / #2688: pointer (check b fırst b already ıncludes decommıssıon of clojure math service) → duplicate (both decommission Clojure math; A broader)
- 37 · mattermost/mattermost #36516 / #36483: pointer (competing fix) → duplicate (—)
- 47 · compdemocracy/polis #3030 / #3031: duplicate (—) → pointer (promote carries A's change)
- 48 · mattermost/mattermost #38575 / #38375: pointer (superseded by B (fix replaces skip)) → duplicate (same ticket)
- 49 · mattermost/mattermost #37097 / #37096: pointer (Competing same ıssue dıfferent fixes) → duplicate (—)
