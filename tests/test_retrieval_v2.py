from datetime import datetime, timezone

from mergeone.memory import retrieval_v2 as R


def test_refs_forms_and_self_exclusion():
    t = "see #1, (#2) GH-3 gh-4 x/Y#5 https://GitHub.com/a/B/pull/6/files #1234567890 a#7 _#8 #42"
    got = R.refs(t, "Acme/W", 42)
    assert got == {("acme/w", 1), ("acme/w", 2), ("acme/w", 3), ("acme/w", 4), ("x/y", 5), ("a/b", 6)}


def test_dirs_depth2_and_author():
    assert R.dirs(["README.md", "src/a/b/c.py", "src/x.py"]) == {".", "src/a", "src"}
    assert R.norm_author("Claude[bot]") == R.norm_author("claude") == "claude"


def test_parse_state_and_ident():
    st = ("PULL REQUEST TITLE: t\nMERGE MESSAGE BODY:\nfixes #3\n\nSUMMARY: 1 file\n\nFILES CHANGED (path +ins -del):\n  a/b.py +1 -1\n\n"
          "DIFF (first 4000 chars, truncated):\ndiff --git a/a/b.py b/a/b.py\n--- a/a/b.py\n+++ b/a/b.py\n@@ -1 +1 @@\n-def old_name(self): return None\n+def new_name(self): return True\n ctx_only")
    s = R.parse_state(st)
    assert s["title"] == "t" and s["body"] == "fixes #3" and s["paths"] == ["a/b.py"]
    assert R.ident_of_diff(s["diff"]) == {"old_name", "new_name"}


def test_rerank_keeps_unfetched_position():
    top = [{"number": n, "score": s, "a1_rank": i + 1} for i, (n, s) in enumerate([(1, 1.0), (2, 0.9), (3, 0.8), (4, 0.7)])]
    ids = {1: frozenset(), 2: None, 3: frozenset({"foo", "bar"}), 4: frozenset({"foo"})}
    out = R.rerank_a2(top, frozenset({"foo", "bar"}), ids)
    assert [c["number"] for c in out] == [3, 2, 4, 1]
    assert out[1]["patch_fetched"] is False and [c["a2_rank"] for c in out] == [1, 2, 3, 4]


def test_a1_window_and_ranking():
    m = R.Memory("o/r")
    m.add({"number": 1, "title": "retry client", "body": "#9", "author": "bot", "state": "MERGED", "created_at": "2026-01-01T00:00:00Z",
           "merged_at": "2026-05-01T00:00:00Z", "files": [{"path": "src/http/client.py"}]})
    m.add({"number": 2, "title": "docs", "body": "", "author": "x", "state": "MERGED", "created_at": "2026-05-02T00:00:00Z", "files": [{"path": "README.md"}]})
    m.add({"number": 3, "title": "retry client", "body": "", "author": "x", "state": "MERGED", "created_at": "2025-01-01T00:00:00Z", "files": []})
    m.finalize()
    q = R.Query.make("o/r", 99, "retry client", "relates to #9", ["src/http/client.py"], "bot[bot]", datetime(2026, 5, 5, tzinfo=timezone.utc))
    res = m.a1(q)
    assert [c["number"] for c in res] == [1, 2]
    assert res[0]["issue_ref"] and res[0]["same_author"] and res[0]["jaccard"] == 1.0
