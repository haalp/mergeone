"""python -m scmbench.data.extract_cli --repo PATH [--max-merges N]"""
import argparse, time
from pathlib import Path
from .extract import extract, label_reverts, write_jsonl

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True)
ap.add_argument("--max-merges", type=int, default=15000)
ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "data" / "raw" / "k8s_merges.jsonl"))
a = ap.parse_args()
t = time.time()
prs = extract(Path(a.repo), max_merges=a.max_merges)
label_reverts(prs)
write_jsonl(prs, Path(a.out))
print("merged PRs", len(prs), "positives", sum(1 for p in prs if p.reverted), "window", prs[0].merged_at, "->", prs[-1].merged_at, "secs", round(time.time() - t))
