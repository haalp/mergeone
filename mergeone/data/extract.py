"""Extract merged PRs and revert labels from git history only.

Design note (mirrors the Scion query pattern): we never materialize a working
copy. Every piece of state comes from history queries against the object
store (`git log`, `git show`, `git diff-tree`). The leakage rule from
PROTOCOL.md §2 is enforced structurally: the state for PR P is built from P's
own merge commit, and the label is built in a separate pass over *later*
commits that never feeds back into the state.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

MERGE_RE = re.compile(r"^Merge pull request #(\d+) from (\S+)")
REVERT_TITLE_RE = re.compile(r"^\s*Revert\b", re.IGNORECASE)
PR_REF_RE = re.compile(r"#(\d{4,7})\b")
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")

DIFF_CHAR_LIMIT = 4000
REVERT_HORIZON_DAYS = 30


@dataclass
class FileStat:
    path: str
    insertions: int
    deletions: int


@dataclass
class MergedPR:
    pr_number: int
    merge_sha: str
    merged_at: str  # ISO-8601 UTC
    title: str
    body: str
    source_ref: str
    files: list[FileStat]
    diff_head: str  # first DIFF_CHAR_LIMIT chars of unified diff
    diff_truncated: bool
    is_revert_pr: bool  # the PR itself is a revert (excluded from positives)
    commit_subjects: list[str] = field(default_factory=list)  # subjects of the PR's own commits
    # label fields, filled by label pass; None until then
    reverted: bool | None = None
    reverted_by_pr: int | None = None
    reverted_at: str | None = None
    days_to_revert: float | None = None
    label_complete: bool = True  # False if horizon not fully observed
    extra: dict = field(default_factory=dict)


def _norm_title(t: str) -> str:
    t = t.strip().lower()
    t = re.sub(r"[\"'`“”‘’]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.rstrip(".")


def _reverted_titles(revert_title: str) -> list[str]:
    """Candidate original titles from a revert title.

    Handles: Revert "X", Revert 'X', Revert X, Revert "Revert "X"" (nested;
    we only take the outermost, since revert-of-revert is excluded anyway).
    """
    t = revert_title.strip()
    m = re.match(r"^\s*revert\s*[:\-]?\s*(.*)$", t, re.IGNORECASE)
    if not m:
        return []
    inner = m.group(1).strip()
    out = {_norm_title(inner)}
    q = re.match(r'^["“\'`](.*)["”\'`]\s*(\(#\d+\))?\s*$', inner)
    if q:
        out.add(_norm_title(q.group(1)))
    return [x for x in out if x]


def _git(repo: Path, *args: str, text: bool = True) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=text,
        errors="replace" if text else None,
    )
    return out.stdout


def iter_merge_commits(repo: Path, branch: str = "HEAD") -> Iterator[tuple[str, str, str, str]]:
    """Yield (sha, iso_date, subject, body) for merge commits, newest first."""
    sep = "\x1e"
    rec = "\x1f"
    fmt = f"%H{sep}%cI{sep}%s{sep}%b{rec}"
    raw = _git(repo, "log", branch, "--merges", "--first-parent", f"--format={fmt}")
    for chunk in raw.split(rec):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split(sep)
        if len(parts) < 4:
            continue
        sha, date, subject, body = parts[0], parts[1], parts[2], sep.join(parts[3:])
        yield sha, date, subject, body


def _numstat(repo: Path, merge_sha: str) -> list[FileStat]:
    # Diff of the merge against its first parent = what the PR brought to master.
    raw = _git(repo, "diff-tree", "--numstat", "-r", "--no-commit-id", f"{merge_sha}^1", merge_sha)
    files: list[FileStat] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ins, dels, path = parts
        files.append(FileStat(path=path, insertions=int(ins) if ins != "-" else 0, deletions=int(dels) if dels != "-" else 0))
    return files


def _diff_head(repo: Path, merge_sha: str, limit: int = DIFF_CHAR_LIMIT) -> tuple[str, bool]:
    raw = _git(repo, "diff", f"{merge_sha}^1", merge_sha, "--no-color", "--unified=3")
    return raw[:limit], len(raw) > limit


def _commit_subjects(repo: Path, merge_sha: str, limit: int = 50) -> list[str]:
    raw = _git(repo, "log", "--format=%s", f"--max-count={limit}", f"{merge_sha}^1..{merge_sha}")
    return [l for l in raw.splitlines() if l.strip() and not l.startswith("Merge ")]


def _pr_title_from_body(body: str, subject: str) -> str:
    # GitHub merge commits put the PR title as the first non-empty body line.
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return subject


def extract(repo: Path, max_merges: int | None = None, with_diff: bool = True) -> list[MergedPR]:
    prs: list[MergedPR] = []
    for sha, date, subject, body in iter_merge_commits(repo):
        m = MERGE_RE.match(subject)
        if not m:
            continue
        pr_number = int(m.group(1))
        title = _pr_title_from_body(body, subject)
        files = _numstat(repo, sha)
        diff_head, truncated = _diff_head(repo, sha) if with_diff else ("", False)
        prs.append(
            MergedPR(
                pr_number=pr_number,
                merge_sha=sha,
                merged_at=datetime.fromisoformat(date).astimezone(timezone.utc).isoformat(),
                title=title,
                body=body,
                source_ref=m.group(2),
                files=files,
                diff_head=diff_head,
                diff_truncated=truncated,
                is_revert_pr=bool(REVERT_TITLE_RE.match(title)),
                commit_subjects=_commit_subjects(repo, sha),
            )
        )
        if max_merges and len(prs) >= max_merges:
            break
    prs.sort(key=lambda p: p.merged_at)
    return prs


def label_reverts(prs: list[MergedPR], horizon_days: int = REVERT_HORIZON_DAYS, observed_until: str | None = None) -> None:
    """Fill revert labels in place. Separate pass; only uses later commits.

    A PR P is positive if a later merged PR R (within horizon) is a revert PR
    whose title/body references P's number or P's merge SHA.
    """
    by_number = {p.pr_number: p for p in prs}
    by_sha_prefix: dict[str, MergedPR] = {}
    by_title: dict[str, list[MergedPR]] = {}
    for p in prs:
        for k in range(7, 13):
            by_sha_prefix.setdefault(p.merge_sha[:k], p)
        by_title.setdefault(_norm_title(p.title), []).append(p)
        for subj in p.commit_subjects:
            by_title.setdefault(_norm_title(subj), []).append(p)
    last_ts = datetime.fromisoformat(observed_until) if observed_until else max(datetime.fromisoformat(p.merged_at) for p in prs)

    for p in prs:
        p.reverted = False
        p.reverted_by_pr = None
        p.reverted_at = None
        p.days_to_revert = None
        p_ts = datetime.fromisoformat(p.merged_at)
        p.label_complete = (last_ts - p_ts).total_seconds() >= horizon_days * 86400

    for r in prs:
        if not r.is_revert_pr:
            continue
        r_ts = datetime.fromisoformat(r.merged_at)
        text = f"{r.title}\n{r.body}"
        targets: set[int] = set()
        for num in PR_REF_RE.findall(text):
            n = int(num)
            if n != r.pr_number and n in by_number:
                targets.add(n)
        for sha in SHA_RE.findall(text):
            hit = by_sha_prefix.get(sha[:12]) or by_sha_prefix.get(sha[:7])
            if hit and hit.pr_number != r.pr_number:
                targets.add(hit.pr_number)
        # Kubernetes convention: Revert "<original PR title>". Match by title,
        # taking the most recent earlier PR with that title.
        for quoted in _reverted_titles(r.title):
            cands = [c for c in by_title.get(quoted, []) if c.merged_at < r.merged_at and c.pr_number != r.pr_number]
            if cands:
                targets.add(max(cands, key=lambda c: c.merged_at).pr_number)
        for n in targets:
            p = by_number[n]
            if p.is_revert_pr:
                continue  # revert of a revert: excluded from positives
            dt = (r_ts - datetime.fromisoformat(p.merged_at)).total_seconds() / 86400
            if 0 < dt <= horizon_days and not p.reverted:
                p.reverted = True
                p.reverted_by_pr = r.pr_number
                p.reverted_at = r.merged_at
                p.days_to_revert = round(dt, 2)


def write_jsonl(prs: list[MergedPR], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for p in prs:
            f.write(json.dumps(asdict(p)) + "\n")


def read_jsonl(path: Path) -> list[MergedPR]:
    out = []
    with path.open() as f:
        for line in f:
            d = json.loads(line)
            d["files"] = [FileStat(**x) for x in d["files"]]
            out.append(MergedPR(**d))
    return out
