"""Run 8 tooling on synthetic data: relation2 (three binaries + metadata rule), v4 question set, overwrite-proof prediction
files. No model or API is called."""
import json
import types
from pathlib import Path

import numpy as np
import pytest

from mergeone import cli
from mergeone.eval import run6b, run7_report as RR, run8_relation as E
from mergeone.models import haiku as H, jev as J
from mergeone.models.base import (DECISIONS_V3, DECISIONS_V4, PRIMARY_V4, QUESTION_SETS, RELATION, RELATION2, Decision)

from test_run7 import REPO, _w, rel_env  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]


# ================================================================= question sets and prompts
def test_v4_is_v3_minus_two_and_prompts_match():
    assert DECISIONS_V4 == tuple(q for q in DECISIONS_V3 if q not in ("follows_conventions", "message_matches_diff"))
    assert len(DECISIONS_V4) == 8 and PRIMARY_V4 == "needs_human_review" in DECISIONS_V4
    assert QUESTION_SETS["v4"] == DECISIONS_V4 and QUESTION_SETS["relation2"] == RELATION2
    v3_raw = (ROOT / "prompts/jev_v3.json").read_text()
    v3 = json.loads(v3_raw)
    for k in ("follows_conventions", "message_matches_diff"):
        del v3[k]
    assert (ROOT / "prompts/jev_v4.json").read_text() == json.dumps(v3, indent=2, ensure_ascii=False) + ("\n" if v3_raw.endswith("\n") else "")
    assert tuple(json.loads(J.QUESTIONS["v4"].read_text())) == DECISIONS_V4
    h4 = H.PROMPTS["v4"].read_text()
    assert "follows_conventions" not in h4 and "message_matches_diff" not in h4
    assert all(f"{i}. {q}:" in h4 for i, q in enumerate(DECISIONS_V4, 1))
    assert H.SCHEMA_V4["required"] == list(DECISIONS_V4) and set(H.SCHEMA_V4["properties"]) == set(DECISIONS_V4)


def test_relation2_prompts_and_schema():
    assert RELATION2 == ("same_change", "builds_on", "same_goal") and "same_branch" not in RELATION2
    q = json.loads(J.QUESTIONS["relation2"].read_text())
    assert tuple(q) == RELATION2 and all(v["type"] == "noul" for v in q.values())
    md = H.PROMPTS["relation2"].read_text()
    assert all(f"- {k}:" in md for k in RELATION2) and "same_branch" not in md
    assert H.SCHEMA_RELATION2["required"] == list(RELATION2)


# ================================================================= class rule
@pytest.mark.parametrize("p,same_base,want", [
    ((.9, .9, .9), False, "backport"),
    ((.9, .1, .1), True, "duplicate"),
    ((.9, .1, .1), None, "duplicate"),       # unknown base counts as the same base
    ((.4, .8, .9), True, "follow_up"),       # builds_on before same_goal
    ((.4, .1, .7), False, "competing"),      # same_goal needs no branch fact
    ((.3, .1, .1), True, "same_area"),       # one P ≥ LOW
    ((.24, .1, .1), False, "unrelated"),
])
def test_relation2_rule(p, same_base, want):
    probs = dict(zip(RELATION2, p))
    assert E.relation2_class(probs, same_base) == want
    # same_author / a_newer are accepted and do not change the frozen rule
    assert E.relation2_class(probs, same_base, True, True) == E.relation2_class(probs, same_base, False, False) == want


# ================================================================= report
def _meta_env(rel_env):
    """Add base / author / created_at: A in the dataset, B in the PR memory. Pair 5 (gold backport) has B on another branch."""
    ds = []
    for i, r in enumerate(rel_env.ds):
        ds.append({**r, "base": "main", "author": "bot", "created_at": f"2026-06-{i + 1:02d}T00:00:00Z"})
    mem = [{**m, "base": "release-1" if i == 5 else "main", "author": "bot" if i % 2 else "u"} for i, m in enumerate(rel_env.mem)]
    _w(rel_env.root / "data/run3/dataset.jsonl", ds)
    _w(rel_env.root / f"data/run4/memory/{REPO.replace('/', '__')}.jsonl", mem)


def _pid(s):
    return run6b._hpid(REPO, s["a_number"], s["b_number"])


def test_pair_metadata(rel_env):
    _meta_env(rel_env)
    meta, src = E.pair_metadata(RR.Inputs(Path(".")), rel_env.sample)
    assert src["base"] == {"dataset": 10, "memory": 10, "missing": 0} and src["author"]["missing"] == 0
    m = [meta[_pid(s)] for s in rel_env.sample]
    assert [x["same_base"] for x in m] == [i != 5 for i in range(10)]
    assert [x["same_author"] for x in m] == [bool(i % 2) for i in range(10)]
    assert all(x["a_newer"] is True for x in m)  # A created in June, B in May
    assert RR.base_branches(RR.Inputs(Path(".")), rel_env.sample)[1] == src["base"]  # run 7's lookup unchanged


def test_relation2_report_with_run7_baseline(rel_env, monkeypatch):
    _meta_env(rel_env)
    monkeypatch.setattr(E, "RUN_DIR", rel_env.root / "results/run8")
    monkeypatch.setattr(E, "RUN7_REL_DIR", rel_env.root / "results/run7/relation")
    gold = E.R.load_gold()
    want = {"duplicate": (.9, .1, .1), "competing": (.2, .1, .8), "backport": (.8, .1, .1), "follow_up": (.1, .9, .3),
            "same_area": (.3, .1, .2), "unrelated": (.1, .1, .1)}
    rows = []
    for i, s in enumerate(rel_env.sample):
        c = gold[_pid(s)]["relation"] if i != 0 else "follow_up"   # one duplicate wrong
        rows.append({"pr_number": _pid(s), "split": "test", "qset": "relation2", "labels": {},
                     "decision": {"probs": dict(zip(RELATION2, want[c])), "error": None}})
    rows.append({**rows[1], "qset": "v3"})  # other question sets ignored
    _w(rel_env.root / "results/run8/relation/predictions_pplx-flash_test_all.jsonl", rows)
    # run 7 relation rows (four questions) for jev: everything says same_change, same_branch high → the old rule says
    # duplicate everywhere; the run-8 rule with metadata turns pair 5 into backport
    r7 = [{"pr_number": _pid(s), "split": "test", "qset": "relation", "labels": {},
           "decision": {"probs": dict(zip(RELATION, (.9, .1, .9, .1))), "error": None}} for s in rel_env.sample]
    _w(rel_env.root / "results/run7/relation/predictions_jev_test_all.jsonl", r7)
    rep = E.run(("pplx-flash", "jev"))
    e = rep["models"]["pplx-flash"]
    assert e["n"] == 10 and e["accuracy"] == pytest.approx(0.9) and e["n_base_unknown"] == 0
    assert e["confusion"]["duplicate"]["follow_up"] == 1 and e["per_class"]["duplicate"]["ci95"] == run6b.wilson(3, 4)
    assert e["per_class"]["backport"]["correct"] == 1 and e["per_class"]["unrelated"]["correct"] == 2
    assert rep["models"]["jev"]["status"].startswith("no relation2 predictions")
    b = rep["baseline_run7"]["jev"]
    assert b["predicted"] == {"duplicate": 9, "backport": 1} and b["accuracy"] == pytest.approx(0.5)
    assert "pplx-flash" not in rep["baseline_run7"] and rep["control_same_day"] == {}
    t = (rel_env.root / "results/run8/relation_tables.md").read_text()
    assert "baseline: run-7 relation answers" in t and "| duplicate | 4 | 3 |" in t
    assert json.loads((rel_env.root / "results/run8/relation_metrics.json").read_text())["low"] == E.LOW
    assert any(i["path"].endswith("predictions_jev_test_all.jsonl") for i in rep["inputs"])
    _w(rel_env.root / "results/run8/relation/predictions_pplx-flash_test_all.jsonl", rows + r7)   # same-day relation control, same file
    rep = E.run(("pplx-flash",))
    assert rep["models"]["pplx-flash"]["accuracy"] == pytest.approx(0.9)
    assert rep["control_same_day"]["pplx-flash"]["predicted"] == {"duplicate": 9, "backport": 1}


def test_relation2_predict_refuses_without_flag(rel_env, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "cmd_predict", lambda a: called.append(a))
    monkeypatch.setattr(E, "RUN_DIR", rel_env.root / "results/run8")
    with pytest.raises(SystemExit, match="REFUSING"):
        E.main(["predict", "--model", "jev"])
    assert called == []
    E.main(["predict", "--model", "jev", "--compact", "--i-have-frozen-the-protocol"])
    assert len(called) == 1 and called[0].qset == "relation2" and called[0].run == "run8/relation" and called[0].compact is True


# ================================================================= v4 scoring
def test_compare_v4_means_over_eight():
    rng = np.random.default_rng(1)
    n = 90
    y = {i: int(i % 3 == 0) for i in range(n)}
    mk = lambda qs, noise: {i: {"decision": {"probs": {q: float(1 / (1 + np.exp(-(y[i] + rng.normal(0, noise))))) for q in qs}}} for i in range(n)}
    v4, v3 = mk(DECISIONS_V4, 0.5), mk(DECISIONS_V3, 0.8)
    base = {"questions": {q: {"test_raw_vs_iterated": 0.6} for q in DECISIONS_V3}}
    c = RR.compare_v4(v4, v3, y, base)
    assert set(c["questions"]) == set(DECISIONS_V4) and c["mean"]["n_questions"] == 8
    assert c["mean"]["delta"] == pytest.approx(np.mean([c["questions"][q]["auc"] for q in DECISIONS_V4]) - 0.6)
    assert c["mean"]["n_paired"] == n and c["mean"]["delta_same_keys"] > 0
    assert RR.compare_v3(v4, v3, y, base)["mean"]["delta"] is None   # v4 rows lack two v3 questions


# ================================================================= overwrite-proof prediction files
class _Fake:
    name = "fake"

    def decide(self, state, features, questions):
        return Decision(probs={q: 0.5 for q in questions}, model_version="fake-1")


def _pair_ds(tmp_path):
    from test_run7 import _pr
    from mergeone.data.features import render_state, structural_features
    from mergeone.eval import run4
    rows = []
    for i in (1, 2):
        p = _pr(5, 40, 300, pid=i)
        b = run4._render_b({"title": "b", "state": "MERGED", "merged_at": "2026-01-02", "created_at": "2026-01-01", "author": "u"}, "x", ["a.py"])
        rows.append({"pr_number": 1000 + i, "split": "test", "repo": "o/r", "a": i, "labels": {}, "features": structural_features(p),
                     "state": run4.pair_state(render_state(p), b)})
    v = []
    for i in (1, 2):
        p = _pr(5, 40, 300, pid=i)
        v.append({"pr_number": i, "split": "test", "labels": {}, "features": structural_features(p), "state": render_state(p)})
    _w(tmp_path / "pairs.jsonl", rows)
    _w(tmp_path / "ds.jsonl", v)


def test_v3_then_pair_into_one_dir_keeps_both(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(cli, "RUN3_DATASET", tmp_path / "none.jsonl")
    monkeypatch.setattr(cli, "build_model", lambda name, version="v1": _Fake())
    _pair_ds(tmp_path)
    base = dict(model="fake", split="test", run="run8/c", limit=None, concurrency=2, questions="all", words=False, append=False,
                only_errors_from=None, only_missing_from=None, subset=None, single_question=False, compact=True, raw=None, prompt_file=None)
    run = lambda qset, ds, **kw: cli.cmd_predict(types.SimpleNamespace(**{**base, **kw}, qset=qset, dataset=str(tmp_path / ds)))
    run("v3", "ds.jsonl")
    run("v4", "ds.jsonl")
    run("pair", "pairs.jsonl")
    run("relation2", "pairs.jsonl")
    f = tmp_path / "results/run8/c/predictions_fake_test_all_compact.jsonl"
    rows = [json.loads(l) for l in f.open()]
    assert sorted((r["qset"], r["pr_number"]) for r in rows) == sorted([(q, i) for q in ("v3", "v4") for i in (1, 2)]
                                                                       + [(q, 1000 + i) for q in ("pair", "relation2") for i in (1, 2)])
    assert set(rows[0]["decision"]["probs"]) == set(DECISIONS_V3)
    # a re-run of one question set without --append replaces only that question set's rows
    run("v3", "ds.jsonl")
    rows = [json.loads(l) for l in f.open()]
    assert sum(r["qset"] == "v3" for r in rows) == 2 and len(rows) == 8
    # --append keeps this question set's earlier rows too
    run("pair", "pairs.jsonl", append=True)
    assert len(f.read_text().splitlines()) == 10
    # every meta file is distinct
    metas = sorted(p.name for p in f.parent.glob("meta_*.json"))
    assert metas == ["meta_fake_test_all_compact.json", "meta_fake_test_all_compact_relation2.json",
                     "meta_fake_test_all_compact_v3.json", "meta_fake_test_all_compact_v4.json"]
    assert json.loads((f.parent / "meta_fake_test_all_compact_v3.json").read_text())["other_rows_kept"] == 6
    # the readers still find their rows in the shared file
    assert len(RR.latest([json.loads(l) for l in f.open()], "v3")) == 2 and len(E.load_preds(f)) == 2


def test_open_predictions_keeps_legacy_rows_and_fixes_missing_newline(tmp_path):
    f = tmp_path / "p.jsonl"
    f.write_text(json.dumps({"pr_number": 1, "decision": {}}) + "\n" + json.dumps({"pr_number": 2, "qset": "pair", "decision": {}}))
    fh, other = cli.open_predictions(f, "v1", "joint", append=False)   # row without qset = this qset (runs 1–4) → replaced
    fh.write(json.dumps({"pr_number": 3, "qset": "v1"}) + "\n")
    fh.close()
    assert other == 1 and [json.loads(l)["pr_number"] for l in f.read_text().splitlines()] == [2, 3]
