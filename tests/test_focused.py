"""The `focused` rendering (PROTOCOL R12.1, R12-H1): path classes, diff splitting, the budget, and that nothing but the
order of the diff's files differs from `full`. No model."""
import pytest

from mergeone.data import focused as F
from mergeone.data.extract import FileStat, MergedPR
from mergeone.data.features import render_state


@pytest.mark.parametrize("path,cls", [
    ("src/app/main.go", F.SOURCE), ("lib/util.py", F.SOURCE), ("Sources/App/View.swift", F.SOURCE),
    ("src/app/main_test.go", F.TEST), ("tests/test_util.py", F.TEST), ("pkg/x/test_helpers.py", F.TEST),
    ("web/src/Button.test.tsx", F.TEST), ("web/src/Button.spec.ts", F.TEST), ("src/FooTest.java", F.TEST),
    ("src/Foo.Tests/BarTests.cs", F.TEST), ("e2e/login.ts", F.TEST), ("conftest.py", F.TEST), ("spec/models/user_spec.rb", F.TEST),
    ("package.json", F.CONFIG), (".github/workflows/ci.yml", F.CONFIG), ("Dockerfile", F.CONFIG), ("README.md", F.CONFIG),
    ("docs/guide/intro.md", F.CONFIG), ("pyproject.toml", F.CONFIG), ("src/App.csproj", F.CONFIG),
    ("package-lock.json", F.LOW), ("go.sum", F.LOW), ("pnpm-lock.yaml", F.LOW), ("Cargo.lock", F.LOW),
    ("vendor/github.com/x/y.go", F.LOW), ("web/dist/app.js", F.LOW), ("api/v1/types.pb.go", F.LOW),
    ("pkg/apis/zz_generated.deepcopy.go", F.LOW), ("assets/logo.svg", F.LOW),
    ("web/src/__tests__/__snapshots__/Button.test.tsx.snap", F.LOW),   # LOW is checked before TEST
    ("tests/fixtures/data.json", F.TEST),                              # TEST is checked before CONFIG
    ("src/latest/contest.py", F.SOURCE), ("src/attestation.go", F.SOURCE),   # "test" inside a word is not a test
])
def test_classify_path(path, cls):
    assert F.classify_path(path) == cls


def sec(path, n):
    head = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    return head + "+" + "x" * (n - len(head) - 2) + "\n"


def test_split_diff_roundtrip_and_paths():
    d = sec("a.lock", 100) + sec("src/a.go", 120) + sec("src/a_test.go", 90)
    parts = F.split_diff(d)
    assert [p for p, _ in parts] == ["a.lock", "src/a.go", "src/a_test.go"]
    assert "".join(s for _, s in parts) == d and [len(s) for _, s in parts] == [100, 120, 90]
    assert F.split_diff("") == [] and F.split_diff("no header") == [("", "no header")]
    assert F.split_diff("pre\n" + sec("x.py", 60))[0] == ("", "pre\n")


def test_focused_order_budget_and_truncation():
    lock, src, test, cfg = sec("a-lock/yarn.lock", 3000), sec("src/a.go", 1500), sec("src/a_test.go", 1000), sec("ci.yml", 800)
    d = lock + cfg + src + test                      # the order GitHub would give is not the order shown
    text, truncated, plan = F.focused_diff(d)
    assert len(text) == F.DIFF_BUDGET and truncated
    assert text == test + src + cfg + lock[:F.DIFF_BUDGET - 3300]
    assert plan == [("src/a_test.go", "test", 1000), ("src/a.go", "source", 1500), ("ci.yml", "config", 800), ("a-lock/yarn.lock", "low", 700)]
    # `full` on the same diff shows the lockfile only
    full, ftrunc = F.full_from_diff(d)
    assert full == d[:4000] and ftrunc and "a_test.go" not in full
    # a diff inside the budget is the same characters, reordered, not truncated
    small = sec("z.lock", 300) + sec("t/test_a.py", 200)
    text, truncated, _ = F.focused_diff(small)
    assert not truncated and sorted(text) == sorted(small) and text.startswith("diff --git a/t/test_a.py")
    assert F.focused_diff(small, capped=True)[1] is True
    # two files of one class keep their order; a section that does not fit is cut, later ones show nothing
    two = sec("b.go", 3000) + sec("a.go", 3000)
    text, truncated, plan = F.focused_diff(two)
    assert text == two[:4000] and truncated and [n for _, _, n in plan] == [3000, 1000]
    assert F.focused_diff("") == ("", False, [])


def test_render_focused_differs_from_full_only_in_the_diff_block():
    d = sec("yarn.lock", 5000) + sec("src/a.go", 500) + sec("src/a.test.ts", 400)
    full_head, full_trunc = F.full_from_diff(d)
    p = MergedPR(pr_number=1, merge_sha="s", merged_at="2026-11-01T00:00:00Z", title="T", body="B", source_ref="r",
                 files=[FileStat("yarn.lock", 90, 0), FileStat("src/a.go", 5, 1), FileStat("src/a.test.ts", 4, 0)],
                 diff_head=full_head, diff_truncated=full_trunc, is_revert_pr=False)
    full, foc = render_state(p), F.render_focused(p, d)
    cut_full, cut_foc = full.index("\nDIFF (first "), foc.index("\nDIFF (first ")
    assert full[:cut_full] == foc[:cut_foc]                                   # title, body, SUMMARY, FILES CHANGED identical
    assert "DIFF (first 4000 chars, truncated):" in full and "DIFF (first 4000 chars, truncated):" in foc
    assert len(full) == len(foc)                                              # same budget
    assert "a.test.ts" not in full[cut_full:] and foc[cut_foc:].index("a.test.ts") < foc[cut_foc:].index("src/a.go b/") < foc[cut_foc:].index("yarn.lock")
    assert F.render_focused(p, d, numbers_as_words=True).count("IN WORDS:") == 1
