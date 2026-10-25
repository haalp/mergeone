# Run 8 — relation2 (three binaries + metadata) on the run-6B hold pairs

100 pairs · gold classes: duplicate 46, competing 7, backport 28, follow_up 7, same_area 10, unrelated 2 · gold source: notes rule 100 · HIGH 0.5, LOW 0.25

Rule: same_change & !same_base → backport; same_change & same_base → duplicate; builds_on → follow_up; same_goal → competing; any P ≥ low → same_area; else unrelated (same_base unknown → True).

| model | n | accuracy [95% CI] | same_change vs rest AUC (n duplicate) | same_change+backport vs rest AUC | related vs unrelated AUC (n unrelated) | base unknown |
|---|---|---|---|---|---|---|
| pplx-flash | 100 | 0.570 [0.472, 0.663] | 0.689 (46) | 0.838 | 0.898 (2) | 0 |
| jev | 100 | 0.740 [0.646, 0.816] | 0.728 (46) | 0.893 | 0.816 (2) | 0 |
| pplx-flash — baseline: run-7 relation answers, this rule | 97 | 0.722 [0.625, 0.801] | 0.728 (45) | 0.868 | 0.990 (1) | 0 |
| pplx-flash — run-7 post-hoc (run-7 rule, metadata branch; reference) | 97 | 0.722 | | | | |
| jev — baseline: run-7 relation answers, this rule | 100 | 0.740 [0.646, 0.816] | 0.685 (46) | 0.883 | 0.995 (2) | 0 |
| jev — run-7 post-hoc (run-7 rule, metadata branch; reference) | 100 | 0.720 | | | | |
| pplx-flash — same-day control: run-7 relation question asked today, this rule | 100 | 0.720 [0.625, 0.799] | 0.724 (46) | 0.857 | 0.607 (2) | 0 |
| jev — same-day control: run-7 relation question asked today, this rule | 99 | 0.747 [0.654, 0.823] | 0.679 (46) | 0.868 | 0.992 (2) | 0 |

## pplx-flash: accuracy per gold class (Wilson 95%)

| gold class | n | correct | accuracy [95% CI] |
|---|---|---|---|
| duplicate | 46 | 30 | 0.652 [0.508, 0.773] |
| competing | 7 | 0 | 0.000 [0.000, 0.354] |
| backport | 28 | 19 | 0.679 [0.493, 0.821] |
| follow_up | 7 | 6 | 0.857 [0.487, 0.974] |
| same_area | 10 | 1 | 0.100 [0.018, 0.404] |
| unrelated | 2 | 1 | 0.500 [0.095, 0.905] |

### pplx-flash: confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 30 | 0 | 3 | 9 | 4 | 0 |
| competing | 1 | 0 | 0 | 6 | 0 | 0 |
| backport | 0 | 0 | 19 | 4 | 5 | 0 |
| follow_up | 1 | 0 | 0 | 6 | 0 | 0 |
| same_area | 2 | 0 | 0 | 7 | 1 | 0 |
| unrelated | 0 | 0 | 1 | 0 | 0 | 1 |

## jev: accuracy per gold class (Wilson 95%)

| gold class | n | correct | accuracy [95% CI] |
|---|---|---|---|
| duplicate | 46 | 39 | 0.848 [0.718, 0.924] |
| competing | 7 | 2 | 0.286 [0.082, 0.641] |
| backport | 28 | 26 | 0.929 [0.774, 0.980] |
| follow_up | 7 | 5 | 0.714 [0.359, 0.918] |
| same_area | 10 | 1 | 0.100 [0.018, 0.404] |
| unrelated | 2 | 1 | 0.500 [0.095, 0.905] |

### jev: confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 39 | 1 | 3 | 0 | 3 | 0 |
| competing | 4 | 2 | 0 | 0 | 1 | 0 |
| backport | 1 | 0 | 26 | 1 | 0 | 0 |
| follow_up | 1 | 1 | 0 | 5 | 0 | 0 |
| same_area | 5 | 0 | 3 | 1 | 1 | 0 |
| unrelated | 0 | 0 | 1 | 0 | 0 | 1 |

## pplx-flash — baseline (run-7 answers): accuracy per gold class (Wilson 95%)

| gold class | n | correct | accuracy [95% CI] |
|---|---|---|---|
| duplicate | 45 | 42 | 0.933 [0.821, 0.977] |
| competing | 7 | 0 | 0.000 [0.000, 0.354] |
| backport | 27 | 26 | 0.963 [0.817, 0.993] |
| follow_up | 7 | 2 | 0.286 [0.082, 0.641] |
| same_area | 10 | 0 | 0.000 [0.000, 0.278] |
| unrelated | 1 | 0 | 0.000 [0.000, 0.793] |

### pplx-flash — baseline (run-7 answers): confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 42 | 0 | 3 | 0 | 0 | 0 |
| competing | 6 | 0 | 1 | 0 | 0 | 0 |
| backport | 1 | 0 | 26 | 0 | 0 | 0 |
| follow_up | 5 | 0 | 0 | 2 | 0 | 0 |
| same_area | 7 | 0 | 3 | 0 | 0 | 0 |
| unrelated | 1 | 0 | 0 | 0 | 0 | 0 |

## jev — baseline (run-7 answers): accuracy per gold class (Wilson 95%)

| gold class | n | correct | accuracy [95% CI] |
|---|---|---|---|
| duplicate | 46 | 40 | 0.870 [0.743, 0.939] |
| competing | 7 | 2 | 0.286 [0.082, 0.641] |
| backport | 28 | 27 | 0.964 [0.823, 0.994] |
| follow_up | 7 | 5 | 0.714 [0.359, 0.918] |
| same_area | 10 | 0 | 0.000 [0.000, 0.278] |
| unrelated | 2 | 0 | 0.000 [0.000, 0.658] |

### jev — baseline (run-7 answers): confusion (rows gold, columns predicted)

| gold \ pred | duplicate | competing | backport | follow_up | same_area | unrelated |
|---|---|---|---|---|---|---|
| duplicate | 40 | 1 | 3 | 2 | 0 | 0 |
| competing | 4 | 2 | 1 | 0 | 0 | 0 |
| backport | 1 | 0 | 27 | 0 | 0 | 0 |
| follow_up | 2 | 0 | 0 | 5 | 0 | 0 |
| same_area | 6 | 0 | 3 | 1 | 0 | 0 |
| unrelated | 0 | 0 | 1 | 1 | 0 | 0 |

## Metadata per gold class

| gold class | n | same_base | same_author | a_newer | unknown (base / author / created) |
|---|---|---|---|---|---|
| duplicate | 46 | 43 | 16 | 19 | 0 / 0 / 0 |
| competing | 7 | 6 | 5 | 2 | 0 / 0 / 0 |
| backport | 28 | 1 | 17 | 10 | 0 / 0 / 0 |
| follow_up | 7 | 7 | 4 | 3 | 0 / 0 / 0 |
| same_area | 10 | 7 | 3 | 6 | 0 / 0 / 0 |
| unrelated | 2 | 1 | 0 | 2 | 0 / 0 / 0 |

## Notes

- Metadata source (dataset → PR memory): base {'dataset': 119, 'memory': 62, 'missing': 0}; author {'dataset': 119, 'memory': 62, 'missing': 0}; created_at {'dataset': 119, 'memory': 62, 'missing': 0}.
- same_author and a_newer are inputs of the rule's signature but not consulted by the frozen rule; tabulated above.
- Baseline row: run 7's relation answers (four questions) with same_branch dropped, re-scored through the run-8 rule and metadata — the pre-registered comparison point for run 8's relation2 number.
- Gold sub-classes: run7_relation.GOLD_KEYWORDS rule; data/run7/relation_gold.jsonl overrides per pair when present.
