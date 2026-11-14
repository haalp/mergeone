"""Holdout-2 tooling (PROTOCOL R12.0a) on synthetic rows: only test repositories, only PRs created after run 3's
cut-off, never a PR run 3 holds, same row schema as the run-3 dataset, and a manifest with the label counts."""
import json
from datetime import datetime, timezone
from pathlib import Path

from mergeone.data import prepare_holdout2 as H


def raw_row(repo, number, created, closed, fp=0, merged=True):
    return {"repo": repo, "number": number, "agent": ["copilot"], "title": f"t{number}", "body": "b", "author": "bot",
            "created_at": created, "merged_at": closed if merged else None, "closed_at": closed, "head_sha": "h", "head_ref": "r", "base": "main",
            "files": [{"path": "a.go", "insertions": 2, "deletions": 1}], "diff_head": "+x", "diff_truncated": False,
            "labels": {"merged": merged, "closed_unmerged": not merged, "human_review_comments": 0, "change_requested": False,
                       "approved_by_human": False, "force_pushes": fp, "reverted_within_30d": False, "reverted_by": None}}


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_build_filters_and_manifest(tmp_path):
    run3_raw, raw = tmp_path / "run3.jsonl", tmp_path / "raw.jsonl"
    _w(run3_raw, [raw_row("o/test", 1, "2026-10-01T00:00:00Z", "2026-10-16T19:30:52Z"), raw_row("o/val", 2, "2026-10-02T00:00:00Z", "2026-10-10T00:00:00Z")])
    (tmp_path / "m.json").write_text(json.dumps({"split_assignment": {"o/test": "test", "o/val": "val"}}))
    (tmp_path / "repos.json").write_text(json.dumps([{"repo": "o/test", "default_branch": "trunk", "by_agent": {"copilot": 9}}, {"repo": "o/val", "by_agent": {"copilot": 9}}]))
    _w(raw, [
        raw_row("o/test", 10, "2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z", fp=3),          # kept, iterated
        raw_row("o/test", 11, "2026-10-21T00:00:00Z", "2026-11-11T00:00:00Z", merged=False),  # kept, closed unmerged, recent
        raw_row("o/test", 10, "2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z"),                # duplicate
        raw_row("o/test", 1, "2026-10-01T00:00:00Z", "2026-10-16T19:30:52Z"),                 # already in run 3
        raw_row("o/test", 12, "2026-10-16T08:00:00Z", "2026-10-25T00:00:00Z"),                # created on the cut-off day
        raw_row("o/val", 13, "2026-10-25T00:00:00Z", "2026-10-26T00:00:00Z"),                 # a val repository
    ])
    out, man = tmp_path / "h2.jsonl", tmp_path / "man.json"
    m = H.build(raw, tmp_path / "repos.json", run3_raw, tmp_path / "m.json", out, man, now=datetime(2026, 11, 12, tzinfo=timezone.utc))
    assert m["created_after"] == "2026-10-16" and m["n_rows"] == 2 and m["repos"] == ["o/test"]
    assert m["iterated_positives"] == 1 and m["labels"]["closed_unmerged"] == 1 and m["revert_label_not_final"] == 2  # both closed inside the last 30 days
    assert m["dropped"] == {"duplicate": 1, "in_run3": 1, "created_on_or_before_cutoff": 1, "not_a_holdout_repo": 1}
    assert m["scored_by_any_model"] is False
    rows = [json.loads(l) for l in out.open()]
    assert {r["split"] for r in rows} == {"holdout2"} and rows[0]["default_branch"] == "trunk"
    assert rows[0]["labels"]["iterated"] is True and "state" in rows[0] and rows[0]["state"].startswith("PULL REQUEST TITLE")
    assert json.loads(man.read_text())["n_rows"] == 2


def test_cutoff_and_test_repos(tmp_path):
    _w(tmp_path / "r.jsonl", [raw_row("a/b", 1, "2026-01-01T00:00:00Z", "2026-03-04T05:06:07Z"), raw_row("a/b", 2, "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")])
    assert H.cutoff_date(tmp_path / "r.jsonl") == "2026-03-04"
    (tmp_path / "m.json").write_text(json.dumps({"split_assignment": {"z/z": "test", "a/a": "test", "v/v": "val"}}))
    assert H.test_repos(tmp_path / "m.json") == ["a/a", "z/z"]


def test_select_repos_and_widened_build(tmp_path):
    run3_repos, cands, out = tmp_path / "repos3.json", tmp_path / "cands.json", tmp_path / "repos12.json"
    (tmp_path / "m.json").write_text(json.dumps({"split_assignment": {"o/test": "test", "o/val": "val"}}))
    run3_repos.write_text(json.dumps([{"repo": "o/test", "default_branch": "trunk", "by_agent": {"copilot": 9}, "agent_prs_seen": 9},
                                      {"repo": "o/val", "by_agent": {"copilot": 9}, "agent_prs_seen": 9}]))
    cands.write_text(json.dumps([{"repo": "O/Val", "agent_prs_seen": 999, "by_agent": {"copilot": 999}},     # run 3 used it (any case): never
                                 {"repo": "n/small", "agent_prs_seen": 30, "by_agent": {"copilot": 30}},
                                 {"repo": "n/big", "agent_prs_seen": 80, "by_agent": {"copilot": 80}, "default_branch": "dev"},
                                 {"repo": "n/mid", "agent_prs_seen": 50, "by_agent": {"copilot": 50}}]))
    rows = H.select_repos(cands, run3_repos, tmp_path / "m.json", out, n_new=2)
    assert [(r["repo"], r["origin"]) for r in rows] == [("o/test", "run3_test"), ("n/big", "new"), ("n/mid", "new")]
    assert H.holdout_repos(out, tmp_path / "m.json") == {"o/test": "run3_test", "n/big": "new", "n/mid": "new"}
    assert H.holdout_repos(run3_repos, tmp_path / "m.json") == {"o/test": "run3_test"}

    run3_raw, raw = tmp_path / "run3.jsonl", tmp_path / "raw.jsonl"
    _w(run3_raw, [raw_row("o/test", 1, "2026-10-01T00:00:00Z", "2026-10-16T19:30:52Z")])
    _w(raw, [raw_row("o/test", 10, "2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z"),
             raw_row("n/big", 5, "2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z", fp=2),
             raw_row("n/small", 6, "2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z", fp=4),     # not among the first 2: dropped
             raw_row("o/val", 7, "2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z", fp=4)])
    m = H.build(raw, out, run3_raw, tmp_path / "m.json", tmp_path / "h2.jsonl", tmp_path / "manifest.json",
                now=datetime(2026, 12, 30, tzinfo=timezone.utc))
    assert m["n_rows"] == 2 and m["dropped"] == {"not_a_holdout_repo": 2} and m["iterated_positives"] == 1
    assert m["by_origin"] == {"new": {"rows": 1, "repos": 1, "iterated_positives": 1}, "run3_test": {"rows": 1, "repos": 1, "iterated_positives": 0}}
    got = [json.loads(l) for l in (tmp_path / "h2.jsonl").open()]
    assert {(r["repo"], r["origin"], r["default_branch"]) for r in got} == {("o/test", "run3_test", "trunk"), ("n/big", "new", "dev")}


def test_fetch_diffs_resumes_and_flags_changed_prs(tmp_path, monkeypatch):
    raw, out = tmp_path / "raw.jsonl", tmp_path / "diffs.jsonl"
    a, b = raw_row("o/a", 1, "2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z"), raw_row("o/a", 2, "2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z")
    a["diff_head"], b["diff_head"] = "x" * 4000, "old"
    _w(raw, [a, b, raw_row("o/a", 3, "2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z")])
    served = {1: "x" * 5000, 2: "new diff", 3: None}
    calls = []
    monkeypatch.setattr(H.agent_prs, "full_diff", lambda gh, repo, num: (calls.append(num), served[num])[1])
    assert H.fetch_diffs(raw, out, gh=object(), cap=4500) == {"fetched": 2, "prefix_mismatch": 1, "unavailable": 1}
    rows = [json.loads(l) for l in out.open()]
    assert [(r["number"], len(r["diff"]), r["capped"], r["chars"], r["prefix_matches"]) for r in rows] == [(1, 4500, True, 5000, True), (2, 8, False, 8, False)]
    assert H.fetch_diffs(raw, out, gh=object()) == {"unavailable": 1} and calls == [1, 2, 3, 3]
