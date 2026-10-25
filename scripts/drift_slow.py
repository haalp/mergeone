"""Slow drift signal, Python reference (the Go path is not built yet — see docs/drift-monitor.md).

Flags a question whose ROC-AUC against the outcome label fell by more than SLOW_DROP between the reference
predictions and the current ones, on identical PRs. Writes results/run8/drift_slow.json for run8_report (R8-H5).
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from sklearn.metrics import roc_auc_score

SLOW_DROP = 0.05
LABEL = "iterated"
CASES = {  # label -> (reference files, current files, dataset with labels)
    "pplx-flash_val_run5_to_run7r0": (["results/run5/predictions_pplx-flash_val_all.jsonl"],
        ["results/run7/tune_r0/predictions_pplx-flash_val_all.jsonl", "results/run7/tune_r0_iter/predictions_pplx-flash_val_all.jsonl"]),
    "pplx-flash_test_run5_to_run7control": (["results/run5/predictions_pplx-flash_test_all.jsonl"], ["results/run7/control/predictions_pplx-flash_test_all.jsonl"]),
    "jev_test_run5_to_run7control": (["results/run5/predictions_jev_test_all.jsonl"], ["results/run7/control/predictions_jev_test_all.jsonl"]),
    "pplx-flash_test_run5hosted_to_run6Alocal": (["results/run5/predictions_pplx-flash_test_all.jsonl"], ["results/run6/predictions_pplx-flash-local_test_all.jsonl"]),
}

def load(paths):
    last = {}
    for p in paths:
        for line in open(p):
            r = json.loads(line)
            if r.get("qset", "v3") == "v3" and r["decision"].get("error") is None:
                last[r["pr_number"]] = r
    return last

def main():
    labels = {json.loads(l)["pr_number"]: int(json.loads(l)["labels"][LABEL]) for l in open("data/run3/dataset.jsonl")}
    out = {"slow_drop": SLOW_DROP, "label": LABEL, "source": "scripts/drift_slow.py (Python reference)", "comparisons": {}}
    for k, (ref, cur) in CASES.items():
        a, b = load(ref), load(cur)
        ids = [i for i in a if i in b and i in labels]
        y = [labels[i] for i in ids]
        qs = a[ids[0]]["decision"]["probs"].keys()
        deltas, flagged = {}, []
        for q in qs:
            ra = roc_auc_score(y, [a[i]["decision"]["probs"][q] for i in ids])
            rb = roc_auc_score(y, [b[i]["decision"]["probs"][q] for i in ids])
            deltas[q] = {"ref_auc": round(ra, 4), "cur_auc": round(rb, 4), "delta": round(rb - ra, 4)}
            if rb - ra < -SLOW_DROP:
                flagged.append(q)
        out["comparisons"][k] = {"n": len(ids), "flagged": sorted(flagged), "auc": deltas}
        print(k, "n", len(ids), "slow flags:", sorted(flagged))
    Path("results/run8/drift_slow.json").write_text(json.dumps(out, indent=1))

if __name__ == "__main__":
    sys.exit(main())
