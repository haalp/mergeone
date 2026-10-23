# Run 6 part B — candidate pool k and hold precision

A2: ran

## R6B-H1 retrieval recall@k (gold superseding PR)

| split | gold | @5 | @10 | @20 |
|---|---|---|---|---|
| test | 29 | 0.655 | 0.862 | 0.862 |
| val | 22 | 0.818 | 0.864 | 0.909 |

## Verdict level per k (test; base bad-outcome rate 0.468, R4-H3 bar ≤ 0.234)

| model | k | status | pass % | pass bad-rate | closed not passed | Δ vs no dedup | dedup holds |
|---|---|---|---|---|---|---|---|
| jev | 5 | ok | 30.6 | 0.352 | 76.1% | 8.5 pp | 137 |
| jev | 10 | ok | 30.5 | 0.349 | 76.4% | 8.9 pp | 140 |
| jev | 20 | ok | 30.4 | 0.346 | 76.7% | 9.2 pp | 144 |
| pplx-flash | 5 | ok | 37.4 | 0.383 | 67.9% | 18.4 pp | 184 |
| pplx-flash | 10 | ok | 37.0 | 0.379 | 68.5% | 19.0 pp | 192 |
| pplx-flash | 20 | ok | 36.7 | 0.375 | 69.2% | 19.7 pp | 195 |
| haiku | 5 | ok | 51.2 | 0.410 | 55.4% | 10.5 pp | 109 |
| haiku | 10 | ok | 51.1 | 0.411 | 55.4% | 10.5 pp | 113 |
| haiku | 20 | ok | 51.1 | 0.411 | 55.4% | 10.5 pp | 113 |

## R6B-H3 cost per PR (list price; calls = 1 + k)

| model | gate call $ | pair call $ | k=5 | k=10 | k=20 |
|---|---|---|---|---|---|
| jev | 0.000102 | 0.000103 | 0.00061 | 0.00111 | 0.00210 |
| pplx-flash | 0.000023 | 0.000021 | 0.00012 | 0.00023 | 0.00043 |
| haiku | 0.000472 | 0.000388 | 0.00235 | 0.00425 | 0.00801 |

## Hold precision (human labels, blind)

### R6B-H2 pplx-flash, k=20

| subset | n | duplicate | pointer | unrelated | dup ∪ pointer [95% CI] | duplicate share |
|---|---|---|---|---|---|---|
| all | 100 | 44 | 54 | 2 | 0.980 [0.930, 0.994] | 0.440 |
| stratum: superseded | 20 | 11 | 9 | 0 | 1.000 [0.839, 1.000] | 0.550 |
| stratum: closed_unmarked | 38 | 28 | 9 | 1 | 0.974 [0.865, 0.995] | 0.737 |
| stratum: merged | 42 | 5 | 36 | 1 | 0.976 [0.877, 0.996] | 0.119 |

sample B still the top candidate: 100/100; still held: 100/100

### R6B-H4 k=5 on the same labels

| subset | n | duplicate | pointer | unrelated | dup ∪ pointer [95% CI] | duplicate share |
|---|---|---|---|---|---|---|
| held at k=5 and k=20 | 93 | 40 | 52 | 1 | 0.989 [0.942, 0.998] | 0.430 |
| … same B at k=5 | 91 | 39 | 51 | 1 | 0.989 [0.940, 0.998] | 0.429 |

### Threshold sweep (reporting only)

| subset | n | duplicate | pointer | unrelated | dup ∪ pointer [95% CI] | duplicate share |
|---|---|---|---|---|---|---|
| duplicate_of ≥ 0.6 | 100 | 44 | 54 | 2 | 0.980 [0.930, 0.994] | 0.440 |
| duplicate_of ≥ 0.7 | 100 | 44 | 54 | 2 | 0.980 [0.930, 0.994] | 0.440 |
| duplicate_of ≥ 0.8 | 80 | 41 | 38 | 1 | 0.988 [0.933, 0.998] | 0.512 |
| duplicate_of ≥ 0.9 | 50 | 29 | 21 | 0 | 1.000 [0.929, 1.000] | 0.580 |

sample drawn from duplicate_of >= 0.7, so 0.6 equals 0.7 here (PRs in [0.6, 0.7) are not in the sample); reporting only, the threshold is not changed (R6B.2-B3)

### Other models, on sample PRs they also hold at k=20

| subset | n | duplicate | pointer | unrelated | dup ∪ pointer [95% CI] | duplicate share |
|---|---|---|---|---|---|---|
| jev | 68 | 37 | 31 | 0 | 1.000 [0.947, 1.000] | 0.544 | same B: 57
| haiku | 54 | 26 | 27 | 1 | 0.981 [0.902, 0.997] | 0.481 | same B: 42

### Second labeller (fable-5.1; not pre-registered, agreement reported per R6B.2-B2)

| subset | n | duplicate | pointer | unrelated | dup ∪ pointer [95% CI] | duplicate share |
|---|---|---|---|---|---|---|
| human, final labels (pre-registered number) | 100 | 44 | 54 | 2 | 0.980 [0.930, 0.994] | 0.440 |
| human, first-pass labels (before the taxonomy was written down) | 100 | 35 | 63 | 2 | 0.980 [0.930, 0.994] | 0.350 |
| second labeller, promote pairs → pointer | 100 | 56 | 42 | 2 | 0.980 [0.930, 0.994] | 0.560 |
| second labeller, as written (promote pairs = duplicate) | 100 | 68 | 30 | 2 | 0.980 [0.930, 0.994] | 0.680 |

Agreement 0.880, Cohen κ **0.772** (human final vs second, promote→pointer); as written 0.760 / κ 0.554; human first-pass vs second 0.770 / κ 0.573. 12 human rows are pass-2 re-reads under the final rule (note prefix `pass2`; first-pass rows kept in the file).

Confusion (human/second): duplicate/duplicate 44, pointer/duplicate 12, pointer/pointer 42, unrelated/unrelated 2.

Disagreements (for the adjudication round, post-hoc):

- 9 · VocaHQ/vocalinux #882 / #907: human **pointer** (related, maybe conflıct wıth b. Check b first) · second **duplicate** (competing implementations of transcript persistence)
- 17 · mattermost/mattermost #37013 / #37014: human **pointer** (same ticket need to check b ) · second **duplicate** (same ticket)
- 29 · Azure/azure-sdk-for-net #63514 / #63533: human **pointer** (competing fix) · second **duplicate** (same pagination fix)
- 36 · compdemocracy/polis #2913 / #2688: human **pointer** (check b fırst b already ıncludes decommıssıon of clojure math service) · second **duplicate** (both decommission Clojure math; A broader)
- 37 · mattermost/mattermost #36516 / #36483: human **pointer** (competing fix) · second **duplicate** (—)
- 51 · mattermost/mattermost #37098 / #37095: human **pointer** (same issue different tasks same area not duplicate need 2 check B) · second **duplicate** (—)
- 52 · Azure/azure-sdk-for-net #60520 / #60904: human **pointer** (same ıssue dıfferent solutıon ? check b ) · second **duplicate** (same regen task)
- 53 · Comfy-Org/ComfyUI_frontend #17215 / #17216: human **pointer** (need to check B ? ) · second **duplicate** (—)
- 84 · Azure/azure-sdk-for-net #62018 / #61491: human **pointer** (same-area (follow-up)) · second **duplicate** (same CODEOWNERS update)
- 87 · mattermost/mattermost #36483 / #36516: human **pointer** (competing (item 37'nin aynası: parallel’i kapat vs Eventually)) · second **duplicate** (—)
- 97 · microsoft/vscode #326188 / #328586: human **pointer** (same issue sam approach but b added test ) · second **duplicate** (—)
- 100 · Comfy-Org/ComfyUI_frontend #14960 / #14961: human **pointer** (B = re-push of the core fix only; A also carries customWidgets combo-promotion c) · second **duplicate** (—)

## Pre-registered criteria

| hypothesis | result | bar | pass |
|---|---|---|---|
| R4-H3[jev,k=5] | {"pass_bad_rate": 0.3519, "base": 0.4681, "dedup_holds": 137, "status": "ok"} | pass bad-rate <= 0.5 x base | ❌ |
| R4-H4[jev,k=5] | {"delta_pp": 8.5246, "status": "ok"} | >= +15 pp | ❌ |
| R4-H3[jev,k=10] | {"pass_bad_rate": 0.3488, "base": 0.4681, "dedup_holds": 140, "status": "ok"} | pass bad-rate <= 0.5 x base | ❌ |
| R4-H4[jev,k=10] | {"delta_pp": 8.8525, "status": "ok"} | >= +15 pp | ❌ |
| R4-H3[jev,k=20] | {"pass_bad_rate": 0.3458, "base": 0.4681, "dedup_holds": 144, "status": "ok"} | pass bad-rate <= 0.5 x base | ❌ |
| R4-H4[jev,k=20] | {"delta_pp": 9.1803, "status": "ok"} | >= +15 pp | ❌ |
| R4-H3[pplx-flash,k=5] | {"pass_bad_rate": 0.3826, "base": 0.4681, "dedup_holds": 184, "status": "ok"} | pass bad-rate <= 0.5 x base | ❌ |
| R4-H4[pplx-flash,k=5] | {"delta_pp": 18.3607, "status": "ok"} | >= +15 pp | ✅ |
| R4-H3[pplx-flash,k=10] | {"pass_bad_rate": 0.3793, "base": 0.4681, "dedup_holds": 192, "status": "ok"} | pass bad-rate <= 0.5 x base | ❌ |
| R4-H4[pplx-flash,k=10] | {"delta_pp": 19.0164, "status": "ok"} | >= +15 pp | ✅ |
| R4-H3[pplx-flash,k=20] | {"pass_bad_rate": 0.3745, "base": 0.4681, "dedup_holds": 195, "status": "ok"} | pass bad-rate <= 0.5 x base | ❌ |
| R4-H4[pplx-flash,k=20] | {"delta_pp": 19.6721, "status": "ok"} | >= +15 pp | ✅ |
| R4-H3[haiku,k=5] | {"pass_bad_rate": 0.41, "base": 0.4681, "dedup_holds": 109, "status": "ok"} | pass bad-rate <= 0.5 x base | ❌ |
| R4-H4[haiku,k=5] | {"delta_pp": 10.4918, "status": "ok"} | >= +15 pp | ❌ |
| R4-H3[haiku,k=10] | {"pass_bad_rate": 0.4111, "base": 0.4681, "dedup_holds": 113, "status": "ok"} | pass bad-rate <= 0.5 x base | ❌ |
| R4-H4[haiku,k=10] | {"delta_pp": 10.4918, "status": "ok"} | >= +15 pp | ❌ |
| R4-H3[haiku,k=20] | {"pass_bad_rate": 0.4111, "base": 0.4681, "dedup_holds": 113, "status": "ok"} | pass bad-rate <= 0.5 x base | ❌ |
| R4-H4[haiku,k=20] | {"delta_pp": 10.4918, "status": "ok"} | >= +15 pp | ❌ |
| R6B-H1 | {"recall_at_20_test": 0.8621, "recall_pass": true, "r4h3_k20_pass": {"jev": false, "pplx-flash": false}, "pool_not_the_lever": true} | recall@20 >= 0.80 and R4-H3 at k=20 for >= 1 of jev, pplx-flash | ❌ |
| R6B-H3 | {"pplx_flash_usd_per_pr_k20": 0.0004, "calls_per_pr": {"5": 6, "10": 11, "20": 21}, "consequence_if_fail": "k = 10 is the product default regardless of H1"} | pplx-flash k=20 <= $0.002 per PR at list price | ✅ |
| R6B-H2 | {"union": 0.98, "union_ci95": [0.9299868370580063, 0.9944981477650318], "duplicate": 0.44, "n": 100, "demote_to_annotate": false} | union >= 0.60 and duplicate >= 0.30 (pplx-flash, k=20); union < 0.40 → demote dedup to annotate | ✅ |
| R6B-H4 | {"n_held_both": 93, "union": 0.9892, "duplicate": 0.4301} | reported, not a bar | pending |

## Notes

- Pair predictions: union by pair id, run 4 first, then run 5, then run 6 (error-free same_change rows).
- Verdicts: jev/haiku replay their run-3 v2 predictions, pplx-flash its run-5 v3 answers (run-3 rule), as R5-H3.
- Precision is the unweighted share on the sample (proportional stratified allocation ⇒ self-weighting up to rounding).
- A2: ran
