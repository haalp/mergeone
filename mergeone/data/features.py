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


# Run 7 C1 (docs/run7-addendum-draft.md R7.2): the compact rendering. Frozen with the draft; do not tune.
COMPACT_DIFF_CHARS = 2000
COMPACT_BODY_CHARS = 600
COMPACT_MAX_FILES = 20
FULL_BODY_CHARS = 1500


def render_state(p: MergedPR, numbers_as_words: bool = False, max_files_listed: int = 60, compact: bool = False) -> str:
    """The single rendered state every model sees. Identical across models.

    numbers_as_words adds a short verbal summary (ablation for Jev, which the
    vendor says handles words better than numbers). The raw numbers stay.

    compact (run 7, C1): body cut to COMPACT_BODY_CHARS (600) after strip, at most COMPACT_MAX_FILES (20) files
    listed (overrides max_files_listed; the "... and N more files" line counts the rest), diff cut to
    COMPACT_DIFF_CHARS (2,000). The DIFF header always states the number of characters shown and carries
    ", truncated" when the stored diff was already truncated OR the compact cut removed characters. The SUMMARY line
    still counts every file and line. compact_state() applies the same rules to an already rendered string and
    produces the identical text (tests/test_run7.py checks this).
    """
    f = structural_features(p)
    body_cap = COMPACT_BODY_CHARS if compact else FULL_BODY_CHARS
    if compact:
        max_files_listed = COMPACT_MAX_FILES
    lines = [
        f"PULL REQUEST TITLE: {p.title}",
        f"MERGE MESSAGE BODY:\n{p.body.strip()[:body_cap]}",
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
    diff, truncated = p.diff_head, p.diff_truncated
    if compact and len(diff) > COMPACT_DIFF_CHARS:
        diff, truncated = diff[:COMPACT_DIFF_CHARS], True
    lines.append(f"DIFF (first {len(diff)} chars{', truncated' if truncated else ''}):")
    lines.append(diff)
    return "\n".join(lines)


_BODY_HDR = "PULL REQUEST TITLE: "
_BODY_MARK = "\nMERGE MESSAGE BODY:\n"
_SUMMARY_RE = re.compile(r"\n\nSUMMARY: \d+ files changed, \+\d+ / -\d+ lines, \d+ top-level areas touched\.(?:\n|$)")
_FILES_MARK = "\nFILES CHANGED (path +ins -del):\n"
_MORE_RE = re.compile(r"^  \.\.\. and (\d+) more files$")
_DIFF_RE = re.compile(r"\nDIFF \(first (\d+) chars(, truncated)?\):\n")


def compact_state(state: str) -> str:
    """Apply the C1 compact rules to a state string produced by render_state (full or --words rendering).

    Used when the raw PRs are not loaded. Rules, identical to render_state(compact=True):
      * body: the stored body (already strip()[:1500]) is cut to its first 600 characters — equal to
        body.strip()[:600] of the raw PR;
      * files: the first 20 listed lines are kept; the "... and N more files" line is recomputed from the listed
        count plus the stored N, so it equals the raw rendering's;
      * diff: cut to 2,000 characters; the header is rewritten to "first <shown> chars" and carries ", truncated"
        if the stored header had it or characters were removed.
    Every other line (title, SUMMARY, IN WORDS) is kept verbatim. Parsing is anchored on the exact render_state
    markers (the SUMMARY regex, the FILES CHANGED header, the first DIFF header after it), so text inside the body
    cannot be mistaken for a section. A string without these markers raises ValueError (never silently passed on).
    """
    if not state.startswith(_BODY_HDR):
        raise ValueError("not a render_state string (no title line)")
    b0 = state.find(_BODY_MARK)
    m_sum = _SUMMARY_RE.search(state, b0 + 1 if b0 >= 0 else 0)
    f0 = state.find(_FILES_MARK, m_sum.end() - 1 if m_sum else 0)
    m_diff = _DIFF_RE.search(state, f0 + 1 if f0 >= 0 else 0)
    if b0 < 0 or m_sum is None or f0 < 0 or m_diff is None:
        raise ValueError("not a render_state string (missing body / SUMMARY / FILES CHANGED / DIFF markers)")
    head = state[: b0 + len(_BODY_MARK)]
    body = state[b0 + len(_BODY_MARK): m_sum.start()]
    middle = state[m_sum.start(): f0 + len(_FILES_MARK)]  # SUMMARY (+ IN WORDS) + blank + FILES header
    file_lines = state[f0 + len(_FILES_MARK): m_diff.start()].split("\n")
    if file_lines and file_lines[-1] == "":
        file_lines = file_lines[:-1]  # the blank line before DIFF
    more = 0
    listed = []
    for ln in file_lines:
        mm = _MORE_RE.match(ln)
        if mm:
            more += int(mm.group(1))
        else:
            listed.append(ln)
    total = len(listed) + more
    kept = listed[:COMPACT_MAX_FILES]
    if total > COMPACT_MAX_FILES:
        kept.append(f"  ... and {total - COMPACT_MAX_FILES} more files")
    diff = state[m_diff.end():]
    truncated = bool(m_diff.group(2))
    if len(diff) > COMPACT_DIFF_CHARS:
        diff, truncated = diff[:COMPACT_DIFF_CHARS], True
    return (head + body[:COMPACT_BODY_CHARS] + middle + "\n".join(kept + [""])
            + f"\nDIFF (first {len(diff)} chars{', truncated' if truncated else ''}):\n" + diff)


_PAIR_A = "=== PULL REQUEST A ===\n"
_PAIR_B = "\n\n=== PULL REQUEST B ===\n"
_PAIR_Q = "\n\nHow does A relate to B?"


def compact_b_state(b_state: str) -> str:
    """C1 rules on the run-4 candidate rendering (mergeone.eval.run4._render_b: title, body ≤ 1,200, STATE, AUTHOR,
    FILES CHANGED path-only, no diff): body cut to 600 characters, at most 20 files listed plus an
    "... and N more files" line. B never carries a diff, so there is no diff rule."""
    i = b_state.find(_BODY_MARK)
    j = b_state.find("\n\nSTATE: ", i + 1 if i >= 0 else 0)
    k = b_state.find("\nFILES CHANGED:\n", j + 1 if j >= 0 else 0)
    if i < 0 or j < 0 or k < 0:
        raise ValueError("not a run-4 candidate rendering")
    files = b_state[k + len("\nFILES CHANGED:\n"):].split("\n")
    kept = files[:COMPACT_MAX_FILES]
    if len(files) > COMPACT_MAX_FILES:
        kept.append(f"  ... and {len(files) - COMPACT_MAX_FILES} more files")
    return (b_state[: i + len(_BODY_MARK)] + b_state[i + len(_BODY_MARK): j][:COMPACT_BODY_CHARS] + b_state[j: k]
            + "\nFILES CHANGED:\n" + "\n".join(kept))


def compact_pair_state(pair_state: str, a_full_state: str | None = None) -> str:
    """C1 on a run-4 pair state ("=== PULL REQUEST A ===" … "How does A relate to B?").

    A: compact_state(a_full_state) when the full A state is given (the dataset row's `state`; the pair state holds
    only its first 6,000 characters, which can cut the diff without a "truncated" mark), else compact_state of the
    A part as stored. B: compact_b_state. The 6,000 / 3,000 character caps of run4.pair_state are re-applied, so
    the wrapper is byte-for-byte the run-4 one."""
    if not pair_state.startswith(_PAIR_A) or _PAIR_B not in pair_state or not pair_state.endswith(_PAIR_Q):
        raise ValueError("not a run-4 pair state")
    a_part, rest = pair_state[len(_PAIR_A):].split(_PAIR_B, 1)
    b_part = rest[: -len(_PAIR_Q)]
    a = compact_state(a_full_state if a_full_state is not None else a_part)
    return _PAIR_A + a[:6000] + _PAIR_B + compact_b_state(b_part)[:3000] + _PAIR_Q


def split_chronological(prs: list[MergedPR], train: float = 0.6, val: float = 0.2) -> dict[str, list[MergedPR]]:
    prs = sorted(prs, key=lambda p: p.merged_at)
    n = len(prs)
    a, b = int(n * train), int(n * (train + val))
    return {"train": prs[:a], "val": prs[a:b], "test": prs[b:]}
