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
    assert m["dropped"] == {"duplicate": 1, "in_run3": 1, "created_on_or_before_cutoff": 1, "not_a_test_repo": 1}
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
