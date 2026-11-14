"""The `focused` rendering (PROTOCOL.md R12.1, R12-H1): the same diff budget as `full`, spent in priority order.

`full` shows the first DIFF_BUDGET characters of the unified diff in the order GitHub returns files (alphabetical by
path), so a lockfile or a generated file that sorts early can use the whole budget. `focused` keeps every other part
of the state identical and fills the same budget file by file: tests, then source, then configuration and docs, then
lockfiles / generated / vendored / binary paths. Inside a class the original order is kept.

The classifier reads the path only. No model reads or tunes it; it is committed before the freeze and not edited
after results exist. It needs the PR's whole diff (the stored `diff_head` is already the first 4,000 characters), so
rows are rendered from `fetch_full_diffs` output, never from a dataset row alone.
"""
from __future__ import annotations

import re
from dataclasses import replace

from .extract import MergedPR
from .features import render_state

DIFF_BUDGET = 4000            # the collector's diff_head length (agent_prs._diff_head): `full` and `focused` show the same amount
FULL_DIFF_CAP = 400_000       # characters of one PR's diff kept by fetch_full_diffs; a longer diff is marked capped

TEST, SOURCE, CONFIG, LOW = 1, 2, 3, 4
CLASS_NAMES = {TEST: "test", SOURCE: "source", CONFIG: "config", LOW: "low"}

_LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock", "cargo.lock", "go.sum",
    "poetry.lock", "uv.lock", "pipfile.lock", "gemfile.lock", "composer.lock", "packages.lock.json", "flake.lock", "mix.lock",
    "podfile.lock", "pubspec.lock", "gradle.lockfile",
}
_LOW_DIRS = {"vendor", "vendored", "node_modules", "third_party", "third-party", "dist", "build", "out", "__snapshots__", "__generated__", "generated", ".yarn"}
_LOW_SUFFIXES = (
    ".lock", ".min.js", ".min.css", ".map", ".snap", ".pb.go", "_pb2.py", "_pb2_grpc.py", ".pb.cc", ".pb.h", ".g.dart", ".g.cs",
    ".designer.cs", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".woff", ".woff2", ".ttf", ".eot", ".zip", ".gz", ".bin",
)
_LOW_RE = re.compile(r"(^|[._-])generated([._-]|$)|^zz_generated")

_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "testing", "testdata", "test-data", "fixtures", "__mocks__", "integration-tests", "integration_tests", "unittests", "cypress", "playwright"}
_TEST_RE = re.compile(
    r"^test_.*\.py$|.*_test\.(py|go|rb|cc|cpp|c|rs|dart|exs?)$|.*\.(test|spec|e2e)\.[cm]?[jt]sx?$|.*(Test|Tests|IT|Spec)\.(java|kt|cs|scala|groovy|swift)$"
    r"|^conftest\.py$|.*_spec\.rb$|.*Tests?\.(m|mm)$"
)

_CONFIG_NAMES = {"dockerfile", "makefile", "justfile", "procfile", "license", "notice", "codeowners", "owners", ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore", ".npmrc", ".nvmrc"}
_CONFIG_DIRS = {".github", ".circleci", ".vscode", ".devcontainer", ".husky", "docs", "doc"}
_CONFIG_SUFFIXES = (
    ".json", ".jsonc", ".json5", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".properties", ".xml", ".md", ".mdx", ".rst",
    ".txt", ".adoc", ".csproj", ".props", ".targets", ".sln", ".gradle", ".tf", ".tfvars", ".resx", ".plist",
)


def classify_path(path: str) -> int:
    """TEST | SOURCE | CONFIG | LOW for one changed path. Order of the checks is part of the rule: LOW first (a snapshot
    under __tests__ is generated output, not a test), then TEST, then CONFIG, else SOURCE."""
    p = path.strip().replace("\\", "/").lower()
    parts = [x for x in p.split("/") if x]
    if not parts:
        return SOURCE
    base, dirs = parts[-1], set(parts[:-1])
    if base in _LOCKFILES or dirs & _LOW_DIRS or base.endswith(_LOW_SUFFIXES) or _LOW_RE.search(base):
        return LOW
    orig_base = path.strip().replace("\\", "/").split("/")[-1]
    if dirs & _TEST_DIRS or _TEST_RE.match(orig_base) or _TEST_RE.match(base):
        return TEST
    if base in _CONFIG_NAMES or base.startswith(("dockerfile.", ".env")) or dirs & _CONFIG_DIRS or base.endswith(_CONFIG_SUFFIXES):
        return CONFIG
    return SOURCE


_FILE_HDR = re.compile(r"^diff --git a/(.*?) b/(.*)$", re.M)


def split_diff(diff: str) -> list[tuple[str, str]]:
    """[(path, section)] for a unified diff, in the diff's own order. Text before the first `diff --git` header (none
    in a GitHub PR diff) is returned with path "" so that joining the sections gives the input back."""
    heads = list(_FILE_HDR.finditer(diff))
    if not heads:
        return [("", diff)] if diff else []
    out = [("", diff[:heads[0].start()])] if heads[0].start() > 0 else []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(diff)
        out.append((m.group(2), diff[m.start():end]))
    return out


def focused_diff(full_diff: str, capped: bool = False, budget: int = DIFF_BUDGET) -> tuple[str, bool, list[tuple[str, str, int]]]:
    """(text, truncated, plan): the first `budget` characters of the diff's file sections reordered by class.

    truncated is True when characters were left out (by the budget, or because the fetched diff was itself capped).
    plan lists (path, class name, characters shown) for every section, shown or not; it is for the run's tables and
    is never part of what a model reads.
    """
    sections = split_diff(full_diff)
    order = sorted(range(len(sections)), key=lambda i: (classify_path(sections[i][0]) if sections[i][0] else TEST - 1, i))
    text, shown = [], {}
    left = budget
    for i in order:
        take = sections[i][1][:left] if left > 0 else ""
        shown[i] = len(take)
        text.append(take)
        left -= len(take)
    out = "".join(text)
    plan = [(sections[i][0], CLASS_NAMES.get(classify_path(sections[i][0]), "source") if sections[i][0] else "preamble", shown[i]) for i in order]
    return out, capped or len(out) < len(full_diff), plan


def render_focused(p: MergedPR, full_diff: str, capped: bool = False, numbers_as_words: bool = False) -> str:
    """render_state(p) with the DIFF block filled by focused_diff. Title, body, SUMMARY, FILES CHANGED and the DIFF
    header's wording are those of `full`, so the order of the diff's files is the only thing that differs."""
    diff, truncated, _ = focused_diff(full_diff, capped)
    return render_state(replace(p, diff_head=diff, diff_truncated=truncated), numbers_as_words=numbers_as_words)


def full_from_diff(full_diff: str, capped: bool = False, budget: int = DIFF_BUDGET) -> tuple[str, bool]:
    """What the collector stores as diff_head for this diff: the control's diff block, from the same fetched text."""
    return full_diff[:budget], capped or len(full_diff) > budget
