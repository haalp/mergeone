"""Structural features, mechanical secondary labels, and state rendering.

Everything here uses only the MergedPR state (diff, files, title, body),
never the revert label.
"""
from __future__ import annotations

import re
from collections import Counter

from .extract import MergedPR

# Pre-declared (PROTOCOL.md §3.2). Do not edit after results exist.
SENSITIVE_PREFIXES = (
    "pkg/apis/",
    "staging/src/k8s.io/api/",
    "staging/src/k8s.io/apiserver/",
    "staging/src/k8s.io/apimachinery/",
    "pkg/kubelet/",
    "pkg/controlplane/",
    "pkg/registry/",
    "plugin/pkg/auth/",
    "pkg/auth/",
    "staging/src/k8s.io/client-go/",
)

BLAST_FILES = 10
BLAST_TOPDIRS = 3

RISK_WORDS = ("fix", "bug", "regression", "flake", "race", "panic", "crash", "revert", "hotfix", "workaround")
LOWRISK_WORDS = ("doc", "docs", "typo", "comment", "readme", "owners", "test", "e2e", "cleanup", "refactor", "rename", "log")


def top_dir(path: str) -> str:
    parts = path.split("/")
    if parts[0] == "staging" and len(parts) > 3:
        return "/".join(parts[:3])
    if parts[0] in ("pkg", "cmd", "plugin", "test") and len(parts) > 1:
        return "/".join(parts[:2])
    return parts[0]


def structural_features(p: MergedPR) -> dict[str, float]:
    n_files = len(p.files)
    ins = sum(f.insertions for f in p.files)
    dels = sum(f.deletions for f in p.files)
    topdirs = Counter(top_dir(f.path) for f in p.files)
    exts = Counter((f.path.rsplit(".", 1)[-1] if "." in f.path.rsplit("/", 1)[-1] else "") for f in p.files)
    go_files = sum(1 for f in p.files if f.path.endswith(".go"))
    test_files = sum(1 for f in p.files if f.path.endswith("_test.go") or "/test/" in f.path or f.path.startswith("test/"))
    vendor_files = sum(1 for f in p.files if "/vendor/" in f.path or f.path.startswith("vendor/"))
    generated = sum(1 for f in p.files if "zz_generated" in f.path or f.path.endswith(".pb.go") or "/generated/" in f.path)
    sensitive = sum(1 for f in p.files if f.path.startswith(SENSITIVE_PREFIXES))
    title_l = p.title.lower()
    body_l = p.body.lower()
    return {
        "n_files": n_files,
        "insertions": ins,
        "deletions": dels,
        "churn": ins + dels,
        "n_topdirs": len(topdirs),
        "n_exts": len(exts),
        "go_ratio": go_files / n_files if n_files else 0.0,
        "test_ratio": test_files / n_files if n_files else 0.0,
        "vendor_ratio": vendor_files / n_files if n_files else 0.0,
        "generated_ratio": generated / n_files if n_files else 0.0,
        "sensitive_files": sensitive,
        "touches_sensitive": float(sensitive > 0),
        "title_len": len(p.title),
        "body_len": len(p.body),
        "title_risk_words": sum(w in title_l for w in RISK_WORDS),
        "title_lowrisk_words": sum(w in title_l for w in LOWRISK_WORDS),
        "body_mentions_issue": float("fixes #" in body_l or "closes #" in body_l or "kind/bug" in body_l),
        "is_cherry_pick": float("cherry pick" in title_l or "cherry-pick" in title_l or "automated-cherry-pick" in p.source_ref),
        "diff_truncated": float(p.diff_truncated),
    }


def secondary_labels(p: MergedPR) -> dict[str, bool]:
    n_files = len(p.files)
    n_top = len({top_dir(f.path) for f in p.files})
    return {
        "touches_sensitive_area": any(f.path.startswith(SENSITIVE_PREFIXES) for f in p.files),
        "large_blast_radius": n_files > BLAST_FILES and n_top > BLAST_TOPDIRS,
    }


def _words(n: int) -> str:
    if n == 0:
        return "none"
    if n <= 2:
        return "very few"
    if n <= 5:
        return "a few"
    if n <= 15:
        return "several"
    if n <= 50:
        return "many"
    return "a very large number"


def render_state(p: MergedPR, numbers_as_words: bool = False, max_files_listed: int = 60) -> str:
    """The single rendered state every model sees. Identical across models.

    numbers_as_words adds a short verbal summary (ablation for Jev, which the
    vendor says handles words better than numbers). The raw numbers stay.
    """
    f = structural_features(p)
    lines = [
        f"PULL REQUEST TITLE: {p.title}",
        f"MERGE MESSAGE BODY:\n{p.body.strip()[:1500]}",
        "",
        f"SUMMARY: {int(f['n_files'])} files changed, +{int(f['insertions'])} / -{int(f['deletions'])} lines, "
        f"{int(f['n_topdirs'])} top-level areas touched.",
    ]
    if numbers_as_words:
        lines.append(
            f"IN WORDS: {_words(int(f['n_files']))} files; {_words(int(f['churn']))} lines of churn; "
            f"{_words(int(f['n_topdirs']))} areas; "
            f"{'touches' if f['touches_sensitive'] else 'does not touch'} security- or API-critical directories."
        )
    lines.append("")
    lines.append("FILES CHANGED (path +ins -del):")
    for fs in p.files[:max_files_listed]:
        lines.append(f"  {fs.path} +{fs.insertions} -{fs.deletions}")
    if len(p.files) > max_files_listed:
        lines.append(f"  ... and {len(p.files) - max_files_listed} more files")
    lines.append("")
    lines.append(f"DIFF (first {len(p.diff_head)} chars{', truncated' if p.diff_truncated else ''}):")
    lines.append(p.diff_head)
    return "\n".join(lines)


def split_chronological(prs: list[MergedPR], train: float = 0.6, val: float = 0.2) -> dict[str, list[MergedPR]]:
    prs = sorted(prs, key=lambda p: p.merged_at)
    n = len(prs)
    a, b = int(n * train), int(n * (train + val))
    return {"train": prs[:a], "val": prs[a:b], "test": prs[b:]}
