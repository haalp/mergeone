"""§R11-H2 tooling on synthetic set-(b) data: group building from the four inputs, the three fixed scores, recall and
duplicate-vs-rest AUC at k' ∈ {3, 5} against the k' = 10 control, the pass rule, and the freeze guard. No model call."""
import json
import random
from pathlib import Path

import pytest

from mergeone.eval import run11_prefilter as P


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def synthetic(root: Path, n_a=40, dup_in_top3=True, seed=1):
    """n_a adjudicated A's (half duplicate, half pointer), ten candidates each. The duplicate B has the highest retrieval
    score and probability when dup_in_top3; otherwise it sits at rank 7 with a low retrieval score (so retrieval@3 misses)."""
    rng = random.Random(seed)
    preds, pairs, cands, adj = [], [], [], []
    for i in range(n_a):
        repo, a = "o/r", 1000 + i
        label = "duplicate" if i % 2 == 0 else "pointer"
        b_adj = 5000 + i
        clist, bodies = [], {}
        dup_rank = 1 if dup_in_top3 else 7
        for rank in range(1, 11):
            b = b_adj if rank == dup_rank else 9000 + i * 10 + rank
            is_dup_b = b == b_adj
            score = (0.9 if is_dup_b and dup_in_top3 else 0.2 if is_dup_b else 0.8 - 0.05 * rank) + rng.random() * 0.01
            clist.append({"number": b, "score": round(score, 4), "jaccard": 0.6 if is_dup_b else 0.05, "dir_jaccard": 0.5 if is_dup_b else 0.1,
                          "ident_jaccard": 0.3 if is_dup_b else 0.02, "bm25": 1.0, "created_at": "2026-10-01T00:00:00+00:00", "title": "t"})
            pid = P_pair_id(repo, a, b)
            p_same = (0.85 if label == "duplicate" else 0.3) if is_dup_b else 0.05 + rng.random() * 0.1
            preds.append({"pr_number": pid, "split": "test", "labels": {"hold_b": is_dup_b, "retrieval_rank": rank},
                          "decision": {"probs": {"same_change": p_same}, "error": None}})
            pairs.append({"pr_number": pid, "repo": repo, "a": a, "b": b, "kind": "retrieved", "labels": {"hold_b": is_dup_b, "retrieval_rank": rank}})
        cands.append({"pr_number": a, "repo": repo, "number": a, "split": "test", "candidates": clist, "candidate_bodies": bodies})
        adj.append({"item": i + 1, "repo": repo, "a_number": a, "b_number": b_adj, "label": label})
    _w(root / P.PREDICTIONS, preds)
    _w(root / P.PAIRS_B, pairs)
    _w(root / P.CANDIDATES, cands)
    _w(root / P.ADJUDICATED, adj)


def P_pair_id(repo, a, b):
    return hash((repo, a, b)) & 0xFFFFFFFFFFFF


def test_groups_and_pass(tmp_path):
    synthetic(tmp_path)
    groups = P.build_groups(P._rows(tmp_path / P.PREDICTIONS), P._rows(tmp_path / P.PAIRS_B), P._rows(tmp_path / P.CANDIDATES), P._rows(tmp_path / P.ADJUDICATED))
    assert len(groups) == 40 and all(len(g["candidates"]) == 10 for g in groups)
    assert sum(1 for g in groups if g["label"] == "duplicate") == 20
    rep = P.evaluate(groups)
    assert rep["control"]["k"] == 10 and rep["control"]["positives"] == 20 and rep["control"]["calls_per_a"] == 10
    r3 = rep["scores"]["retrieval"]["3"]
    assert r3["recall_dup"] == 1.0 and r3["pass"] and r3["calls_per_a"] == 3 and r3["kept"] == 120
    assert rep["verdict"]["pass"] and rep["verdict"]["ships_k"] == 3 and rep["verdict"]["primary"] == "retrieval"
    # overlap and blend also see the duplicate first here
    assert rep["scores"]["overlap"]["3"]["recall_dup"] == 1.0 and rep["scores"]["blend"]["5"]["pass"]


def test_retrieval_misses_when_duplicate_ranks_low(tmp_path):
    synthetic(tmp_path, dup_in_top3=False)
    groups = P.build_groups(P._rows(tmp_path / P.PREDICTIONS), P._rows(tmp_path / P.PAIRS_B), P._rows(tmp_path / P.CANDIDATES), P._rows(tmp_path / P.ADJUDICATED))
    rep = P.evaluate(groups)
    # retrieval keeps ranks 1–3 / 1–5: the duplicate at rank 7 is never kept → recall 0, fail, nothing ships
    assert rep["scores"]["retrieval"]["3"]["recall_dup"] == 0.0 and rep["scores"]["retrieval"]["5"]["recall_dup"] == 0.0
    assert not rep["verdict"]["pass"] and rep["verdict"]["ships_k"] is None
    # the file-overlap score still finds it (reported beside, does not ship: primary is retrieval)
    assert rep["scores"]["overlap"]["3"]["recall_dup"] == 1.0 and rep["scores"]["overlap"]["3"]["pass"]


def test_error_rows_and_unknown_pairs_are_dropped(tmp_path):
    synthetic(tmp_path, n_a=4)
    preds = P._rows(tmp_path / P.PREDICTIONS)
    preds[0]["decision"]["error"] = "HTTP 503"
    pairs = P._rows(tmp_path / P.PAIRS_B) + [{"pr_number": 1, "repo": "o/r", "a": 77, "b": 78, "labels": {}}]
    groups = P.build_groups(preds, pairs, P._rows(tmp_path / P.CANDIDATES), P._rows(tmp_path / P.ADJUDICATED))
    assert len(groups) == 4 and sum(len(g["candidates"]) for g in groups) == 39


def test_scores_are_the_fixed_weights():
    c = {"score": 0.4, "jaccard": 1.0, "dir_jaccard": 1.0, "ident_jaccard": 1.0}
    s = P.scores_for(c, 1)
    assert s["retrieval"] == 0.4 and s["overlap"] == pytest.approx(1.0) and s["blend"] == pytest.approx(1.0)
    assert P.scores_for(c, 10)["blend"] == pytest.approx(0.5)
    assert P.scores_for({}, None)["blend"] == 0.0


def test_freeze_guard_and_missing_inputs(tmp_path, capsys):
    with pytest.raises(SystemExit):
        P.main(["--root", str(tmp_path)])
    with pytest.raises(SystemExit, match="missing inputs"):
        P.main(["--root", str(tmp_path), "--i-have-frozen-the-protocol"])
    synthetic(tmp_path, n_a=6)
    assert P.main(["--root", str(tmp_path), "--i-have-frozen-the-protocol", "--out", "results/run11/prefilter.json"]) == 0
    rep = json.loads((tmp_path / "results/run11/prefilter.json").read_text())
    assert rep["verdict"]["pass"] and rep["weights"] == P.WEIGHTS and "R11-H2 pass" in capsys.readouterr().out
