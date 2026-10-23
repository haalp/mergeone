"""Run 7 tooling on synthetic data: C1 compact state, C3 evidence features, C5 relation question, C2 tuning harness.
No model or API is called."""
import json
import types
from pathlib import Path

import numpy as np
import pytest

from mergeone import cli
from mergeone.data.extract import FileStat, MergedPR
from mergeone.data.features import (COMPACT_BODY_CHARS, COMPACT_DIFF_CHARS, COMPACT_MAX_FILES, compact_pair_state, compact_state,
                                    render_state)
from mergeone.eval import run4, run6b, run7_evidence as E, run7_relation as R, run7_tune as T


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _pr(n_files=30, body_len=2000, diff_len=3000, truncated=False, pid=1):
    files = [FileStat(f"src/dir{i % 4}/file_{i}.py", i, i % 3) for i in range(n_files)]
    body = "  " + "".join(chr(97 + i % 26) for i in range(body_len)) + "\n\n"
    diff = "".join("+line %d\n" % i for i in range(diff_len // 8 + 1))[:diff_len]
    return MergedPR(pr_number=pid, merge_sha="x", merged_at="2026-01-01T00:00:00Z", title="fix the parser", body=body, source_ref="",
                    files=files, diff_head=diff, diff_truncated=truncated, is_revert_pr=False)


# ================================================================= C1
@pytest.mark.parametrize("n_files,body_len,diff_len,truncated", [(30, 2000, 3000, False), (5, 100, 500, False), (5, 100, 500, True),
                                                                 (80, 900, 4000, True), (20, 600, 2000, False), (0, 0, 0, False)])
def test_compact_state_matches_raw_rendering(n_files, body_len, diff_len, truncated):
    p = _pr(n_files, body_len, diff_len, truncated)
    for words in (False, True):
        full = render_state(p, numbers_as_words=words)
        assert compact_state(full) == render_state(p, numbers_as_words=words, compact=True)
    c = compact_state(render_state(p))
    assert c.count("\n  src/") == min(n_files, COMPACT_MAX_FILES)
    assert ("more files" in c) == (n_files > COMPACT_MAX_FILES)
    if n_files > COMPACT_MAX_FILES:
        assert f"... and {n_files - COMPACT_MAX_FILES} more files" in c
    shown = min(diff_len, COMPACT_DIFF_CHARS)
    assert f"DIFF (first {shown} chars{', truncated' if truncated or diff_len > COMPACT_DIFF_CHARS else ''}):" in c
    body = c.split("MERGE MESSAGE BODY:\n", 1)[1].split("\n\nSUMMARY:", 1)[0]
    assert body == p.body.strip()[:COMPACT_BODY_CHARS]
    assert "SUMMARY: %d files changed" % n_files in c   # SUMMARY still counts every file
    assert compact_state(c) == c                         # idempotent


def test_compact_state_ignores_markers_inside_body_and_rejects_foreign_text():
    p = _pr(25, 50, 2500)
    p.body = "FILES CHANGED (path +ins -del):\n  fake +1 -1\nDIFF (first 3 chars):\nabc"
    assert compact_state(render_state(p)) == render_state(p, compact=True)
    with pytest.raises(ValueError):
        compact_state("hello world")


def test_compact_pair_state_uses_full_a_and_keeps_wrapper():
    p = _pr(30, 2000, 4000, False)
    a_full = render_state(p)
    b = run4._render_b({"title": "other", "state": "MERGED", "merged_at": "2026-01-02T00:00:00", "created_at": "2026-01-01", "author": "u"},
                       "b" * 1300, [f"lib/f{i}.go" for i in range(45)])
    ps = run4.pair_state(a_full, b)
    c = compact_pair_state(ps, a_full)
    assert c.startswith("=== PULL REQUEST A ===\n" + render_state(p, compact=True))
    assert c.endswith("How does A relate to B?")
    b_part = c.split("=== PULL REQUEST B ===\n", 1)[1]
    assert "... and 25 more files" in b_part and b_part.count("\n  lib/") == COMPACT_MAX_FILES
    assert "b" * COMPACT_BODY_CHARS + "\n\nSTATE:" in b_part
    # without the full A state: A is compacted from the 6,000-char prefix the pair state holds
    assert compact_pair_state(ps).startswith("=== PULL REQUEST A ===\nPULL REQUEST TITLE: fix the parser")


def _ds_row(p: MergedPR, split="val", **labels):
    from mergeone.data.features import structural_features
    return {"pr_number": p.pr_number, "split": split, "labels": {"will_be_reverted": False, "touches_sensitive_area": False,
                                                                 "large_blast_radius": False, **labels},
            "features": structural_features(p), "state": render_state(p), "state_words": render_state(p, numbers_as_words=True)}


def test_predict_compact_records_render_and_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    prs = [_pr(30, 2000, 3000, pid=i) for i in (1, 2, 3)]
    ds = tmp_path / "ds.jsonl"
    _w(ds, [_ds_row(p) for p in prs])
    base = dict(model="rules", split="val", run="r", limit=None, concurrency=1, questions="all", words=False, append=False,
                only_errors_from=None, only_missing_from=None, qset="v1", subset=None, dataset=str(ds), single_question=False,
                raw=None, prompt_file=None)
    cli.cmd_predict(types.SimpleNamespace(**base, compact=False))
    cli.cmd_predict(types.SimpleNamespace(**base, compact=True))
    full = [json.loads(l) for l in (tmp_path / "results/r/predictions_rules_val_all.jsonl").open()]
    comp = [json.loads(l) for l in (tmp_path / "results/r/predictions_rules_val_all_compact.jsonl").open()]
    assert len(full) == len(comp) == 3 and all("render" not in r for r in full)
    assert all(r["render"] == "compact" and r["render_source"] == "state" for r in comp)  # no raw file behind ds.jsonl
    meta = json.loads((tmp_path / "results/r/meta_rules_val_all_compact.json").read_text())
    assert meta["render"] == "compact" and meta["render_source"] == "state"


def test_compact_rows_raw_path_equals_state_path(tmp_path):
    from mergeone.data.prepare_run3 import pr_id
    p = _pr(30, 2000, 3000, pid=pr_id("a/b", 5))
    raw = tmp_path / "agent_prs.jsonl"
    _w(raw, [{"repo": "a/b", "number": 5, "title": p.title, "body": p.body, "diff_head": p.diff_head, "diff_truncated": False, "labels": {},
              "files": [{"path": f.path, "insertions": f.insertions, "deletions": f.deletions} for f in p.files]}])
    row = _ds_row(p)
    via_raw, src = cli.compact_rows([row], "state", "v3", tmp_path / "ds.jsonl", str(raw))
    via_state, src2 = cli.compact_rows([row], "state", "v3", tmp_path / "ds.jsonl", str(tmp_path / "missing.jsonl"))
    assert (src, src2) == ("raw", "state")
    assert via_raw[0]["state"] == via_state[0]["state"] == render_state(p, compact=True)


def test_apply_prompt_file_replaces_questions_and_returns_sha(tmp_path):
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"needs_human_review": {"type": "noul", "instructions": "x"}}))
    m = types.SimpleNamespace(questions={"old": 1})
    sha = cli.apply_prompt_file(m, str(f))
    assert m.questions == {"needs_human_review": {"type": "noul", "instructions": "x"}} and sha == T.sha256_file(f)


# ================================================================= C3
def _rec(title="t", body="", files=(), diff=""):
    return {"title": title, "body": body, "files": [{"path": p, "insertions": i, "deletions": d} for p, i, d in files], "diff_head": diff}


def test_evidence_features_synthetic():
    f = E.evidence_features(_rec(files=[("src/a.py", 10, 0)]))
    assert f == {"touches_tests": 0.0, "adds_e2e_or_property_tests": 0.0, "body_has_test_log": 0.0, "body_lists_ci_checks": 0.0,
                 "evidence_attached": 0.0, "n_test_files": 0.0, "test_line_share": 0.0}
    f = E.evidence_features(_rec(body="## How was this tested?\nRan it.\n\n```\n12 passed in 0.4s\n```\nCI is green; `npm run lint` ok",
                                 files=[("src/a.py", 10, 0), ("tests/test_a.py", 30, 0), ("e2e/login.spec.ts", 0, 0)],
                                 diff="+++ b/e2e/login.spec.ts\n+import { test } from '@playwright/test'\n"))
    assert f["touches_tests"] == f["adds_e2e_or_property_tests"] == f["body_has_test_log"] == f["body_lists_ci_checks"] == f["evidence_attached"] == 1.0
    assert f["n_test_files"] == 2.0 and f["test_line_share"] == pytest.approx(30 / 40)
    assert E.evidence_features(_rec(files=[("src/a.rs", 1, 0)], diff="+proptest! {\n"))["adds_e2e_or_property_tests"] == 1.0
    assert E.evidence_features(_rec(body="Pass the config through"))["body_has_test_log"] == 0.0  # PASS lines are case-sensitive
    assert E.evidence_features(_rec(body="coverage: 87.5% of statements"))["body_has_test_log"] == 1.0
    assert E.evidence_features(_rec(files=[("pkg/x/handler_test.go", 5, 5)]))["touches_tests"] == 1.0


def test_evidence_features_from_rendered_state_equals_fields():
    p = _pr(8, 300, 1000)
    p.files.append(FileStat("tests/test_parser.py", 40, 2))
    p.body = "Fixes #3.\n\n## Testing\n- `pytest -q` → 31 passed"
    rec = _rec(p.title, p.body, [(f.path, f.insertions, f.deletions) for f in p.files], p.diff_head)
    assert E.evidence_features({"state": render_state(p)}) == E.evidence_features(rec)


def _synthetic_rows(n=300, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        ev = rng.random() < 0.5
        y = rng.random() < (0.6 if ev else 0.15)
        f = {k: 0.0 for k in E.FEATURES}
        f.update(touches_tests=float(ev), evidence_attached=float(ev), n_test_files=float(ev) * 2, test_line_share=float(ev) * rng.random())
        rows.append({"pr_number": i, "split": "val" if i < n * 2 // 3 else "test", "features": f, "labels": {"iterated": y, "rejected": not y}})
    return rows


def test_fit_and_score_frozen_weights_roundtrip(tmp_path):
    rows = _synthetic_rows()
    tr, te = [r for r in rows if r["split"] == "val"], [r for r in rows if r["split"] == "test"]
    res = E.fit_and_score(tr, te, "iteration")
    assert res["test_auc"] > 0.6 and res["weights"]["n_fit"] == len(tr)
    w = json.loads(json.dumps(res["weights"]))  # survives JSON
    again = E.fit_and_score(tr, te, "iteration", weights=w)
    assert again["test_probs"] == pytest.approx(res["test_probs"])
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    X = E._matrix(tr, E.FEATURES); y = [r["labels"]["iterated"] for r in tr]
    sk = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)).fit(X, y)
    assert E.predict(w, te) == pytest.approx(sk.predict_proba(E._matrix(te, E.FEATURES))[:, 1])
    probs = {r["pr_number"]: 0.5 for r in rows}
    s = E.stacked(tr, te, "iteration", probs)
    assert s["weights"]["cols"][0] == "nhr_cal" and s["nhr_alone_test_auc"] == pytest.approx(0.5)  # constant prob → chance
    assert s["gain"] == pytest.approx(s["test_auc"] - 0.5) and s["n_val"] == len(tr)


def test_score_end_to_end_and_guard(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(E, "DATASET", tmp_path / "ds.jsonl")
    monkeypatch.setattr(E, "RUN_DIR", tmp_path / "results/run7")
    monkeypatch.setattr(E, "RUN5_DIR", tmp_path / "results/run5")
    rows = []
    for r in _synthetic_rows(240, 1):
        tested = r["features"]["touches_tests"] == 1.0
        p = _pr(3, 50, 100, pid=r["pr_number"] + 1)
        if tested:
            p.files.append(FileStat("tests/test_x.py", 5, 0))
        rows.append({**_ds_row(p, r["split"]), "labels": r["labels"]})
    _w(tmp_path / "ds.jsonl", rows)
    _w(tmp_path / "probs.jsonl", [{"pr_number": r["pr_number"], "prob": 0.3 + 0.4 * r["labels"]["iterated"] * (r["pr_number"] % 2)} for r in rows])
    with pytest.raises(SystemExit):
        E.main(["score", "--target", "iteration"])
    E.main(["features"])
    rep = E.score("iteration", probs_path=tmp_path / "probs.jsonl")
    assert rep["evidence_only"]["test_auc"] is not None and rep["R7-H3"]["pass"] in (True, False)
    assert (tmp_path / "results/run7/evidence_weights_iteration.json").exists()
    assert list(rep["stacked"]) == ["probs:probs.jsonl"]
    frozen = (tmp_path / "results/run7/evidence_weights_iteration.json").read_text()
    E.score("iteration", probs_path=tmp_path / "probs.jsonl")  # second score reuses, never refits
    assert (tmp_path / "results/run7/evidence_weights_iteration.json").read_text() == frozen
    w = json.loads(frozen); w["patterns_sha256"] = "edited"
    (tmp_path / "results/run7/evidence_weights_iteration.json").write_text(json.dumps(w))
    with pytest.raises(SystemExit, match="R7.4"):
        E.score("iteration", probs_path=tmp_path / "probs.jsonl")


# ================================================================= C5
@pytest.mark.parametrize("p,cls", [
    ({"same_change": .9, "same_goal": .9, "same_branch": .9, "builds_on": .1}, "duplicate"),
    ({"same_change": .9, "same_goal": .9, "same_branch": .1, "builds_on": .1}, "backport"),
    ({"same_change": .2, "same_goal": .8, "same_branch": .9, "builds_on": .9}, "competing"),
    ({"same_change": .2, "same_goal": .8, "same_branch": .2, "builds_on": .9}, "follow_up"),
    ({"same_change": .2, "same_goal": .2, "same_branch": .9, "builds_on": .7}, "follow_up"),
    ({"same_change": .2, "same_goal": .2, "same_branch": .9, "builds_on": .1}, "same_area"),
    ({"same_change": .2, "same_goal": .6, "same_branch": .2, "builds_on": .1}, "same_area"),
    ({"same_change": .1, "same_goal": .1, "same_branch": .1, "builds_on": .1}, "unrelated"),
])
def test_relation_class_rule_table(p, cls):
    assert R.relation_class(p) == cls


def test_gold_class_from_notes():
    assert R.gold_class("duplicate", ["competing", "", ""]) == "duplicate"
    assert R.gold_class("unrelated", ["", "", ""]) == "unrelated"
    assert R.gold_class("pointer", ["", "promote (edge→stable, carries B)", "same-area"]) == "backport"
    assert R.gold_class("pointer", ["competing: two designs", "backport", ""]) == "competing"
    assert R.gold_class("pointer", ["", "backport (release-11.11 cherry-pick) --> competing", ""]) == "backport"  # earliest keyword
    assert R.gold_class("pointer", ["", "need to check b", "follow-up: A fixes regression"]) == "follow_up"   # next source
    assert R.gold_class("pointer", ["", "REVERT OF B", ""]) == "follow_up"
    assert R.gold_class("pointer", ["", "need to check b", ""]) == "same_area"                               # default


REPO = "acme/widget"


@pytest.fixture
def rel_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for mod, name, sub in ((R, "DATASET", "data/run3/dataset.jsonl"), (R, "MEM_DIR", "data/run4/memory"), (R, "RUN6_DIR", "results/run6"),
                           (R, "DATA6", "data/run6"), (R, "RUN_DIR", "results/run7"), (R, "DATA7", "data/run7"),
                           (run6b, "RUN4_DIR", "results/run4"), (run6b, "RUN5_DIR", "results/run5"), (run6b, "RUN_DIR", "results/run6"),
                           (run6b, "PRICES", "results/prices.json")):
        monkeypatch.setattr(mod, name, tmp_path / sub)
    ds, sample, adj, human, mem = [], [], [], [], []
    labels = ["duplicate"] * 4 + ["pointer"] * 4 + ["unrelated"] * 2
    notes = ["", "", "", "", "competing fix", "backport", "follow up", "same area", "", ""]
    for i in range(10):
        a, b = 300 + i, 100 + i
        p = _pr(3, 40, 200, pid=run6b._hpid(REPO, a))
        ds.append({**_ds_row(p, "test"), "repo": REPO, "number": a})
        mem.append({"number": b, "title": f"B {i}", "body": f"body {i}", "author": "u", "state": "MERGED", "created_at": "2026-05-01T00:00:00Z",
                    "merged_at": "2026-05-02T00:00:00Z", "closed_at": None, "files": [{"path": "src/z.py"}, {"path": "src/a.py"}]})
        k = {"pr_number": p.pr_number, "repo": REPO, "a_number": a, "b_number": b}
        sample.append(k)
        adj.append({**k, "item": i + 1, "label": labels[i], "rationale": "", "source": "agreed"})
        human.append({**{x: k[x] for x in ("pr_number", "a_number", "b_number")}, "label": labels[i], "note": notes[i], "labelled_at": "t"})
    _w(tmp_path / "data/run3/dataset.jsonl", ds)
    _w(tmp_path / f"data/run4/memory/{REPO.replace('/', '__')}.jsonl", mem)
    _w(tmp_path / "data/run6/hold_sample.jsonl", sample)
    _w(tmp_path / "data/run6/hold_labels_adjudicated.jsonl", adj)
    _w(tmp_path / "data/run6/hold_labels.jsonl", human)
    return types.SimpleNamespace(root=tmp_path, ds=ds, sample=sample, mem=mem)


def test_relation_pairs_identical_to_run4_builder(rel_env):
    rows = R.cmd_pairs(None)
    assert [r["pr_number"] for r in rows] == [run6b._hpid(REPO, s["a_number"], s["b_number"]) for s in rel_env.sample]
    m = rel_env.mem[0]
    meta = {"title": m["title"], "state": m["state"], "created_at": "2026-05-01T00:00:00+00:00", "merged_at": "2026-05-02T00:00:00+00:00", "author": "u"}
    assert rows[0]["state"] == run4.pair_state(rel_env.ds[0]["state"], run4._render_b(meta, m["body"], ["src/a.py", "src/z.py"]))
    # a results/run6/pairs.jsonl row wins verbatim
    _w(rel_env.root / "results/run6/pairs.jsonl", [{"pr_number": rows[1]["pr_number"], "state": "VERBATIM"}])
    again = R.build_pairs(rel_env.sample)
    assert again[1]["state"] == "VERBATIM" and again[1]["state_source"] == "results/run6/pairs.jsonl" and again[0]["state"] == rows[0]["state"]


def test_relation_predict_refuses_without_flag(rel_env, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "cmd_predict", lambda a: called.append(a))
    with pytest.raises(SystemExit, match="REFUSING"):
        R.main(["predict", "--model", "pplx-flash"])
    assert called == []
    R.main(["predict", "--model", "pplx-flash", "--i-have-frozen-the-protocol"])
    assert len(called) == 1 and called[0].qset == "relation" and called[0].run == R.REL_RUN and called[0].split == "test"


def test_relation_report_synthetic(rel_env):
    gold = R.load_gold()
    assert [gold[run6b._hpid(REPO, s["a_number"], s["b_number"])]["relation"] for s in rel_env.sample] == \
           ["duplicate"] * 4 + ["competing", "backport", "follow_up", "same_area", "unrelated", "unrelated"]
    probs = {"duplicate": (.9, .9, .9, .1), "competing": (.2, .8, .9, .1), "backport": (.8, .8, .1, .1), "follow_up": (.1, .3, .2, .9),
             "same_area": (.1, .2, .9, .1), "unrelated": (.1, .1, .1, .1)}
    rows = []
    for i, s in enumerate(rel_env.sample):
        pid = run6b._hpid(REPO, s["a_number"], s["b_number"])
        c = gold[pid]["relation"] if i != 0 else "same_area"  # one duplicate predicted wrong
        rows.append({"pr_number": pid, "split": "test", "qset": "relation", "labels": {},
                     "decision": {"probs": dict(zip(R.RELATION, probs[c])), "error": None, "input_tokens": 1000, "output_tokens": 0, "cost_usd": 0.0}})
    rows.append({**rows[1], "qset": "pair"})  # other question sets are ignored
    _w(rel_env.root / "results/run7/relation/predictions_pplx-flash_test_all.jsonl", rows)
    rep = R.run(("pplx-flash", "jev"))
    e = rep["models"]["pplx-flash"]
    assert e["n"] == 10 and e["accuracy"] == pytest.approx(0.9)
    assert e["per_class"]["duplicate"]["correct"] == 3 and e["per_class"]["duplicate"]["ci95"] == run6b.wilson(3, 4)
    assert e["confusion"]["duplicate"]["same_area"] == 1
    assert e["related_vs_unrelated_auc"] == pytest.approx(1.0) and e["same_change_vs_rest_auc"] > 0.8
    assert rep["models"]["jev"]["status"].startswith("no relation predictions")
    assert rep["hypotheses"]["R7-H5"]["pass"] is True
    t = (rel_env.root / "results/run7/relation_tables.md").read_text()
    assert "| duplicate | 4 | 3 |" in t and "R7-H5" in t
    _w(rel_env.root / "data/run7/relation_gold.jsonl", [{**rel_env.sample[7], "relation": "follow_up"}])  # override wins
    assert R.load_gold()[run6b._hpid(REPO, 307, 107)] == {"item": 8, "label": "pointer", "relation": "follow_up", "source": "override"}


# ================================================================= C2
def _tune_ds(n_val=900, n_test=50):
    rows = []
    for i in range(n_val + n_test):
        rows.append({"pr_number": 10_000 + i, "repo": f"r/{i % 7}", "number": i, "split": "val" if i < n_val else "test",
                     "labels": {"iterated": i % 25 == 0, "rejected": False}})
    return rows


def test_draw_deterministic_stratified_val_only():
    ds = _tune_ds()
    a, b = T.draw_tuning(ds), T.draw_tuning(list(reversed(ds)))
    assert a == b and len(a) == 400
    assert {r["pr_number"] for r in a} <= {r["pr_number"] for r in ds if r["split"] == "val"}
    assert sum(r["stratum"] == "iterated" for r in a) == round(400 * 36 / 900)
    assert T.draw_tuning(ds, seed=8) != a
    assert len(T.draw_tuning(_tune_ds(100, 5))) == 100  # population below 400 → all of val


def test_cmd_draw_once(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DATASET", tmp_path / "ds.jsonl"); monkeypatch.setattr(T, "DATA_DIR", tmp_path / "data/run7")
    _w(tmp_path / "ds.jsonl", _tune_ds())
    T.main(["draw"])
    first = (tmp_path / "data/run7/tuning_set.jsonl").read_text()
    assert all("drawn_at" in json.loads(l) for l in first.splitlines())
    T.main(["draw"])  # identical → unchanged
    assert (tmp_path / "data/run7/tuning_set.jsonl").read_text() == first
    with pytest.raises(SystemExit, match="drawn once"):
        T.main(["draw", "--seed", "8"])


def _preds(ids, sha, auc_good=True, split="val", label="iterated", ds=None):
    out = []
    for i, p in enumerate(sorted(ids)):
        y = (ds or {}).get(p, i % 4 == 0)
        v = (0.8 if y else 0.2) if auc_good else 0.5 + 0.01 * (i % 7)
        out.append({"pr_number": p, "split": split, "qset": "v3", "prompt_sha256": sha, "labels": {label: y},
                    "decision": {"probs": {q: v for q in T.DECISIONS_V3}, "error": None}})
    return out


@pytest.fixture
def tune_env(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "DATA_DIR", tmp_path / "data/run7")
    ids = list(range(1, 41))
    _w(tmp_path / "data/run7/tuning_set.jsonl", [{"pr_number": i, "repo": "r", "number": i, "stratum": "x", "drawn_at": "t"} for i in ids])
    def prompt(name, text):
        p = tmp_path / name; p.write_text(text); return p
    return types.SimpleNamespace(root=tmp_path, ids=ids, prompt=prompt)


def test_round_refuses_duplicate_prompt_and_out_of_order(tune_env):
    p0 = tune_env.prompt("p0.json", '{"v": 0}')
    s0 = T.sha256_file(p0)
    e0 = T.record_round(0, p0, _preds(tune_env.ids, s0, auc_good=False))
    assert e0["kept"] and e0["round"] == 0
    with pytest.raises(SystemExit, match="already recorded"):
        T.record_round(1, p0, _preds(tune_env.ids, s0))                     # same prompt sha again
    p1 = tune_env.prompt("p1.json", '{"v": 1}')
    s1 = T.sha256_file(p1)
    with pytest.raises(SystemExit, match="next round is 1"):
        T.record_round(2, p1, _preds(tune_env.ids, s1))
    with pytest.raises(SystemExit, match="prompt_sha256"):
        T.record_round(1, p1, _preds(tune_env.ids, s0))                     # rows made with another prompt
    with pytest.raises(SystemExit, match="val only"):
        T.record_round(1, p1, _preds(tune_env.ids, s1, split="test"))
    with pytest.raises(SystemExit, match="not in the tuning set"):
        T.record_round(1, p1, _preds(tune_env.ids + [999], s1))
    assert len(T.read_log()) == 1                                           # nothing recorded by the refusals
    e1 = T.record_round(1, p1, _preds(tune_env.ids, s1, auc_good=True))
    assert e1["kept"] and e1["primary_val_auc"] == pytest.approx(1.0) and T.in_force(T.read_log())["round"] == 1


def test_rounds_keep_rule_stop_and_freeze(tune_env):
    sha = lambda p: T.sha256_file(p)
    p0 = tune_env.prompt("p0.json", "0")
    rv = lambda s, good: _preds(range(500, 540), s, auc_good=good, label="will_be_reverted")
    T.record_round(0, p0, _preds(tune_env.ids, sha(p0), auc_good=False), revert_rows=rv(sha(p0), True))
    p1 = tune_env.prompt("p1.json", "1")  # val up but the revert eval drops → rejected
    e1 = T.record_round(1, p1, _preds(tune_env.ids, sha(p1)), revert_rows=rv(sha(p1), False))
    assert not e1["kept"] and "drops" in e1["decision"]
    p2 = tune_env.prompt("p2.json", "2")  # val does not rise → rejected; second in a row → stop
    e2 = T.record_round(2, p2, _preds(tune_env.ids, sha(p2), auc_good=False), revert_rows=rv(sha(p2), True))
    assert not e2["kept"] and T.stopped(T.read_log()) == "two consecutive rounds rejected"
    p3 = tune_env.prompt("p3.json", "3")
    with pytest.raises(SystemExit, match="stopped"):
        T.record_round(3, p3, _preds(tune_env.ids, sha(p3)))
    fz = T.freeze()
    assert fz["round"] == 0 and fz["prompt_sha256"] == sha(p0) and fz["tag"] == "r7-tuned"
    assert T.freeze()["frozen_at"] == fz["frozen_at"]  # idempotent
    with pytest.raises(SystemExit, match="frozen"):
        T.record_round(3, p3, _preds(tune_env.ids, sha(p3)))


def test_iteration_eval_drops_tuning_prs(tune_env):
    p0 = tune_env.prompt("p0.json", "0")
    it = _preds(list(range(1, 81)), T.sha256_file(p0))
    e = T.record_round(0, p0, _preds(tune_env.ids, T.sha256_file(p0)), iteration_rows=it)
    assert e["n_iteration_eval"] == 40 and e["eval_score"] == e["iteration_auc"]


def test_relation_prompts_match_question_set():
    from mergeone.models import haiku, jev
    from mergeone.models.base import QUESTION_SETS, RELATION
    q = json.loads(jev.QUESTIONS["relation"].read_text())
    assert tuple(q) == RELATION == QUESTION_SETS["relation"] and all(v["type"] == "noul" and v["instructions"] for v in q.values())
    assert haiku.SCHEMA_RELATION["required"] == list(RELATION)
    text = haiku.PROMPTS["relation"].read_text()
    assert all(f"- {k}:" in text for k in RELATION)
