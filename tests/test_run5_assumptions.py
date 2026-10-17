import numpy as np

from mergeone.eval.run5_assumptions import _floor_threshold, assumption_block, dirs2, jaccard, pair_state, title_body
from mergeone.models.base import QUESTION_SETS, questions_for
from mergeone.models.haiku import SCHEMA_PAIR, SCHEMA_PAIR_ASSUME

STATE = ("PULL REQUEST TITLE: fix(x): thing\nMERGE MESSAGE BODY:\nDepends on the parser.\n\nMore text.\n\n"
         "SUMMARY: 2 files changed, +3 / -1 lines, 1 top-level areas touched.\n\nFILES CHANGED (path +ins -del):\n"
         "  src/a/b.py +2 -1\n  src/a/c.py +1 -0\n\nDIFF (first 10 chars):\ndiff --git")


def test_title_body_never_includes_files_or_diff():
    t, b = title_body(STATE)
    assert t == "fix(x): thing"
    assert b == "Depends on the parser.\n\nMore text."
    assert "FILES CHANGED" not in b and "diff --git" not in b and "src/a" not in b


def test_dirs2_and_jaccard():
    assert dirs2(["a/b/c/x.py", "a/y.py", "z.py"]) == {"a/b", "a", "."}
    assert jaccard({"a"}, {"a", "b"}) == 0.5 and jaccard(set(), set()) == 0.0


def test_assumption_block_survives_truncation():
    blk = assumption_block({"assumptions": [{"kind": "api", "target": "f", "claim": "f exists."}]})
    assert blk == "DECLARED ASSUMPTIONS:\n- [api] f: f exists."
    assert assumption_block(None) == "DECLARED ASSUMPTIONS: none"
    s = pair_state("A" * 10000, blk, "B" * 10000, "DECLARED ASSUMPTIONS: none")
    assert blk in s and s.endswith("DECLARED ASSUMPTIONS: none\n\nHow does A relate to B?")


def test_floor_threshold_matches_fpr():
    rand = np.array([0.0, 0.0, 0.0, 0.2, 0.5])
    t, fpr = _floor_threshold(rand, 0.2)
    assert fpr <= 0.2 and t == 0.5
    t, fpr = _floor_threshold(rand, 0.0)
    assert fpr == 0.0 and t > 0.5


def test_pair_assume_question_set():
    assert QUESTION_SETS["pair_assume"] == ("assumption_conflict",)
    assert questions_for("pair_assume", ("will_be_reverted",)) == ("assumption_conflict",)
    assert questions_for("pair", ("same_change",)) == ("same_change",)
    assert set(SCHEMA_PAIR_ASSUME["properties"]["assumption_conflict"]["required"]) == {"conflict", "depends", "independent"}
    assert SCHEMA_PAIR["required"] == ["same_change"]
