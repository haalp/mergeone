"""Run 6 part B on synthetic data: k-prefix consistency, todo exclusion, blind sample, labelling app, precision math, report."""
import json
import threading
import types
import urllib.request
from pathlib import Path

import pytest

from mergeone.eval import run6b as B

REPO = "acme/widget"


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _state(title, body, paths, diff="+x = 1"):
    return (f"PULL REQUEST TITLE: {title}\nMERGE MESSAGE BODY:\n{body}\n\nSUMMARY: s\nFILES CHANGED:\n" + "".join(f"  {p} +1 -0\n" for p in paths)
            + f"\nDIFF (truncated):\n{diff}\n")


def _pred(pid, p, model_cost=1000, qset="pair", err=None, split="test"):
    return {"pr_number": pid, "split": split, "qset": qset, "decision": {"probs": {"same_change": p}, "error": err, "input_tokens": model_cost,
                                                                         "output_tokens": 0, "cost_usd": 0.0}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = types.SimpleNamespace(root=tmp_path)
    for name, sub in (("DATASET", "data/run3/dataset.jsonl"), ("MEM_DIR", "data/run4/memory"), ("LABELS", "data/run4/labels.jsonl"),
                      ("PATCH_DIR", "data/run5/patches"), ("RUN3_DIR", "results/run3"), ("RUN4_DIR", "results/run4"),
                      ("RUN5_DIR", "results/run5"), ("RUN_DIR", "results/run6"), ("DATA_DIR", "data/run6"), ("PRICES", "results/prices.json")):
        monkeypatch.setattr(B, name, tmp_path / sub)
    # memory: 30 PRs touching overlapping files, all in window
    mem = [{"number": 100 + i, "title": f"widget parser change {i}", "body": f"refs #{7 if i < 3 else 50 + i} body text {i}", "author": f"u{i % 4}",
            "state": "MERGED" if i % 2 else "CLOSED", "created_at": f"2026-05-{1 + i % 28:02d}T00:00:00Z", "merged_at": None, "closed_at": None,
            "changed_files": 2, "files": [{"path": f"src/p{i % 5}.py"}, {"path": "src/common.py"}]} for i in range(30)]
    _w(tmp_path / "data/run4/memory/acme__widget.jsonl", mem)
    ds = []
    for j in range(12):
        n = 200 + j
        closed = j % 2 == 0
        ds.append({"pr_number": B._hpid(REPO, n), "repo": REPO, "number": n, "split": "test" if j < 8 else "val", "author": "bot",
                   "created_at": "2026-05-15T00:00:00Z", "state": _state(f"widget parser fix {j}", "fixes #7", ["src/p1.py", "src/common.py"]),
                   "labels": {"closed_unmerged": closed, "merged": not closed, "bad_outcome": closed, "rejected": closed}})
    _w(tmp_path / "data/run3/dataset.jsonl", ds)
    _w(tmp_path / "data/run4/labels.jsonl", [{"repo": REPO, "number": r["number"], "pr_number": r["pr_number"],
                                               "label": "superseded_by" if r["number"] in (200, 202) else "not_marked",
                                               "superseded_by": 101 if r["number"] in (200, 202) else None} for r in ds if r["labels"]["closed_unmerged"]])
    (tmp_path / "results").mkdir(exist_ok=True)
    (tmp_path / "results/prices.json").write_text(json.dumps({m: {"input_per_mtok": 0.01, "output_per_mtok": 0.0} for m in B.MODELS}))
    d.ds = ds
    monkeypatch.chdir(tmp_path)
    return d


def _retrieve(k):
    B.cmd_retrieve(types.SimpleNamespace(k=k))


# ----------------------------------------------------------------- retrieve: k=5/10 are prefixes of k=20; A1-only without patches
def test_retrieve_prefix_consistency_and_a1_only(env, capsys):
    _retrieve(20)
    _retrieve(10)
    _retrieve(5)
    k20 = B._rows(B.cand_path(20))
    assert len(k20) == len(env.ds) and all(len(r["candidates"]) == 20 for r in k20)
    assert all(len(r["candidate_bodies"]) == 20 and set(r) >= {"pr_number", "repo", "number", "split", "candidates", "candidates_top20"} for r in k20)
    for k in (5, 10):
        rows = {r["pr_number"]: r for r in B._rows(B.cand_path(k))}
        for r in k20:
            assert [c["number"] for c in rows[r["pr_number"]]["candidates"]] == [c["number"] for c in r["candidates"]][:k]
            assert set(rows[r["pr_number"]]["candidate_bodies"]) == {str(c["number"]) for c in r["candidates"][:k]}
    # no patches → A1 order, flagged
    assert all([c["a1_rank"] for c in r["candidates"]] == list(range(1, 21)) for r in k20)
    met = json.loads((B.RUN_DIR / "retrieval_k.json").read_text())
    assert met["a2_status"].startswith("not run") and met["prefix_check"]["checked"] == [5, 10, 20]
    assert met["recall"]["test"]["n_gold"] == 2 and met["recall"]["test"]["recall_at_20"] == 1.0
    assert "A1-only" in capsys.readouterr().err
    # break the prefix → loud failure
    bad = B._rows(B.cand_path(5))
    bad[0]["candidates"] = bad[0]["candidates"][::-1]
    _w(B.cand_path(5), bad)
    with pytest.raises(AssertionError, match="not the first 5"):
        B.check_prefix()


# ----------------------------------------------------------------- pairs_todo excludes pairs answered (error-free) in runs 4–5
def test_todo_excludes_answered_pairs(env):
    cands = [{"pr_number": 1, "repo": REPO, "number": 200, "split": "test", "candidates": [{"number": n} for n in range(100, 120)]}]
    ids = B.pair_ids(cands[0], 20)
    pair_rows = [{"pr_number": p, "split": "test", "kind": "retrieved", "state": "x" * 400} for p in ids]
    pair_rows.append({"pr_number": 999, "split": "test", "kind": "random_negative", "state": "y"})
    _w(B.RUN4_DIR / "predictions_jev_test_all.jsonl", [_pred(ids[0], 0.1), _pred(ids[1], 0.1, err="HTTP 500"), _pred(ids[2], float("nan"))])
    _w(B.RUN5_DIR / "predictions_jev_val_all.jsonl", [_pred(ids[3], 0.2, split="val"), _pred(ids[4], 0.9, qset="v3")])
    _w(B.RUN5_DIR / "predictions_pplx-flash_test_all.jsonl", [_pred(p, 0.3) for p in ids[:5]])
    rep = B.pairs_summary(pair_rows, cands, B.RUN_DIR)
    todo = {m: [json.loads(l)["pr_number"] for l in (B.RUN_DIR / f"pairs_todo_{m}.jsonl").open()] for m in B.MODELS}
    assert set(todo["jev"]) == set(ids) - {ids[0], ids[3]}           # error, NaN and v3 rows do not count as answered
    assert set(todo["pplx-flash"]) == set(ids[5:]) and set(todo["haiku"]) == set(ids)
    assert 999 not in todo["haiku"]                                    # only retrieved pairs are asked
    assert rep["retrieved_pairs_by_k"] == {"5": 5, "10": 10, "20": 20}
    assert rep["models"]["pplx-flash"]["new_pairs_by_k"] == {"5": 0, "10": 5, "20": 15}
    assert rep["models"]["jev"]["est_cost_usd_per_pair"] == pytest.approx(1000 * 0.01 / 1e6)


# ----------------------------------------------------------------- sample: refuses until complete; blind fields; strata; drawn once
def _cands_and_preds(env, scores):
    """k=20 candidates for the 8 test PRs + val; pplx-flash scores per PR index from `scores`; jev/haiku 0.5 everywhere."""
    _retrieve(20)
    cands = B._rows(B.cand_path(20))
    rows = {m: [] for m in B.MODELS}
    for i, cr in enumerate(c for c in cands if c["split"] == "test"):
        for r, c in enumerate(cr["candidates"]):
            pid = B._hpid(cr["repo"], cr["number"], c["number"])
            for m in B.MODELS:
                p = scores.get(i, 0.1) if (m == "pplx-flash" and r == 3) else (0.5 if m != "pplx-flash" else 0.1)
                rows[m].append(_pred(pid, p))
    return cands, rows


def test_sample_refuses_then_draws_blind_stratified(env):
    cands, rows = _cands_and_preds(env, {0: 0.9, 1: 0.95, 2: 0.8, 3: 0.75, 4: 0.71, 5: 0.2})
    for m in B.MODELS:
        _w(B.RUN_DIR / f"predictions_{m}_test_all.jsonl", rows[m][:-1] if m == "haiku" else rows[m])
    a = types.SimpleNamespace(n=3, seed=6, max_missing=0)
    with pytest.raises(SystemExit, match="REFUSING.*haiku"):
        B.cmd_sample(a)
    _w(B.RUN_DIR / "predictions_haiku_test_all.jsonl", rows["haiku"])
    B.cmd_sample(a)
    out = B._rows(B.DATA_DIR / "hold_sample.jsonl")
    assert len(out) == 3 and all(list(r) == list(B.SAMPLE_FIELDS) for r in out)
    test = [c for c in cands if c["split"] == "test"]
    for r in out:
        cr = next(c for c in test if c["pr_number"] == r["pr_number"])
        assert r["b_number"] == cr["candidates"][3]["number"] and r["a_number"] == cr["number"]   # B = top-scoring candidate
    meta = json.loads((B.RUN_DIR / "sample_meta.json").read_text())
    assert meta["n_held_test"] == 5 and sum(meta["strata"]["population"].values()) == 5 and sum(meta["strata"]["sampled"].values()) == 3
    text = (B.DATA_DIR / "hold_sample.jsonl").read_text()
    assert "score" not in text and "rank" not in text and "pplx" not in text
    B.cmd_sample(a)  # identical re-draw is a no-op
    _w(B.DATA_DIR / "hold_sample.jsonl", out[:2])
    with pytest.raises(SystemExit, match="drawn once"):
        B.cmd_sample(a)


def test_allocation_proportional():
    assert B.allocate({"superseded": 20, "closed_unmarked": 100, "merged": 80}, 100) == {"superseded": 10, "closed_unmarked": 50, "merged": 40}
    a = B.allocate({"superseded": 7, "closed_unmarked": 61, "merged": 45}, 100)
    assert sum(a.values()) == 100 and all(a[s] <= n for s, n in {"superseded": 7, "closed_unmarked": 61, "merged": 45}.items())
    assert B.allocate({"superseded": 1, "closed_unmarked": 2, "merged": 3}, 100) == {"superseded": 1, "closed_unmarked": 2, "merged": 3}
    p1, _ = B.draw_sample(list(range(50)), {i: B.STRATA[i % 3] for i in range(50)}, 10, 6)
    p2, _ = B.draw_sample(list(range(50)), {i: B.STRATA[i % 3] for i in range(50)}, 10, 6)
    assert p1 == p2 and len(set(p1)) == 10


# ----------------------------------------------------------------- precision math
def test_precision_and_label_metrics():
    p = B.precision(["duplicate"] * 3 + ["pointer"] * 3 + ["unrelated"] * 4)
    assert (p["union"], p["duplicate_share"], p["n"]) == (0.6, 0.3, 10)
    lo, hi = p["union_ci95"]
    assert lo < 0.6 < hi
    sample = [{"pr_number": i, "repo": REPO, "a_number": i, "b_number": 1000 + i} for i in range(10)]
    labs = ["duplicate"] * 3 + ["pointer"] * 3 + ["unrelated"] * 4
    labels = {(i, i, 1000 + i): {"label": l} for i, l in enumerate(labs)}
    dup = {m: {k: {} for k in B.KS} for m in B.MODELS}
    for i in range(10):
        dup["pplx-flash"][20][i] = {"dup": 0.705 + 0.025 * i, "b": 1000 + i}
        dup["pplx-flash"][5][i] = {"dup": 0.9 if i < 4 else 0.1, "b": 1000 + i if i < 2 else 5}
        dup["haiku"][20][i] = {"dup": 0.8 if i in (0, 9) else 0.2, "b": 1000 + i}
        dup["jev"][20][i] = {"dup": 0.0, "b": None}
    m = B.label_metrics(sample, labels, dup)
    h2 = m["R6B-H2"]
    assert h2["union"] == 0.6 and h2["duplicate_share"] == 0.3 and h2["pass"] and not h2["demote_to_annotate"]
    assert h2["sample_b_still_top"] == 10 and h2["sample_still_held"] == 10
    assert m["R6B-H4"]["held_at_k5_and_k20"]["n"] == 4 and m["R6B-H4"]["held_at_k5_and_k20"]["duplicate"] == 3
    assert m["R6B-H4"]["same_b_at_k5"]["n"] == 2
    sw = m["threshold_sweep"]
    assert sw["0.6"]["n"] == sw["0.7"]["n"] == 10 and sw["0.8"]["n"] == 6 and sw["0.9"]["n"] == 2
    assert sw["0.8"]["union"] == pytest.approx(2 / 6)                 # i = 4..9 → pointers 4, 5; rest unrelated
    assert m["other_models"]["haiku"]["k20_held_in_sample"]["n"] == 2 and m["other_models"]["jev"]["k20_held_in_sample"]["n"] == 0
    labels2 = {k: {"label": "unrelated"} for k in labels}
    assert B.label_metrics(sample, labels2, dup)["R6B-H2"]["demote_to_annotate"]


# ----------------------------------------------------------------- labelling app: blind, appends, validates
def test_label_server_blind_and_appends(env):
    sample = [{"pr_number": env.ds[0]["pr_number"], "repo": REPO, "a_number": 200, "b_number": 101},
              {"pr_number": env.ds[1]["pr_number"], "repo": REPO, "a_number": 201, "b_number": 105}]
    sp, lp = B.DATA_DIR / "hold_sample.jsonl", B.DATA_DIR / "hold_labels.jsonl"
    _w(sp, sample)
    srv = B.make_server(sp, lp, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        html = urllib.request.urlopen(base + "/").read().decode()
        item = urllib.request.urlopen(base + "/api/item?i=0").read().decode()
        it = json.loads(item)
        assert it["a"]["title"] == "widget parser fix 0" and it["a"]["files"] == ["src/p1.py", "src/common.py"] and "+x = 1" in it["a"]["diff"]
        assert it["b"]["title"] == "widget parser change 1" and it["b"]["url"] == f"https://github.com/{REPO}/pull/101" and it["b"]["files"]
        for s in (html.lower(), item.lower()):
            assert all(w not in s for w in ("score", "rank", "pplx", "jev", "haiku", "same_change", "duplicate_of", "prob"))
        req = lambda body: urllib.request.Request(base + "/api/label", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        st = json.loads(urllib.request.urlopen(req({"i": 1, "label": "pointer", "note": "same issue"})).read())
        assert st["labels"][1] == {"label": "pointer", "note": "same issue"} and st["labels"][0] is None
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(req({"i": 0, "label": "maybe"}))
        urllib.request.urlopen(req({"i": 1, "label": "duplicate"}))
    finally:
        srv.shutdown()
        srv.server_close()
    rows = B._rows(lp)
    assert len(rows) == 2 and set(rows[0]) == {"pr_number", "a_number", "b_number", "label", "note", "labelled_at"}
    assert rows[0]["b_number"] == 105 and rows[0]["a_number"] == 201
    c = B.label_counts(sp, lp)
    assert c["labelled"] == 1 and c["counts"]["duplicate"] == 1 and c["rows_in_file"] == 2   # last label wins


def test_label_server_rejects_non_blind_sample(env):
    sp = B.DATA_DIR / "hold_sample.jsonl"
    _w(sp, [{"pr_number": 1, "repo": REPO, "a_number": 200, "b_number": 101, "score": 0.9}])
    with pytest.raises(SystemExit, match="blind"):
        B.make_server(sp, B.DATA_DIR / "l.jsonl", port=0)


# ----------------------------------------------------------------- report end to end (verdict files absent → pending; labels → precision)
def test_report_synthetic(env):
    cands, rows = _cands_and_preds(env, {0: 0.9, 1: 0.95, 2: 0.8})
    for m in B.MODELS:
        _w(B.RUN_DIR / f"predictions_{m}_test_all.jsonl", rows[m])
    B.cmd_sample(types.SimpleNamespace(n=100, seed=6, max_missing=0))
    rep = B.run(B.RUN_DIR)
    assert "incomplete (0/3)" in rep["labels_status"] and rep["hypotheses"]["R6B-H2"]["pass"] is None
    assert rep["verdict_level"]["jev"]["20"]["status"].startswith("verdicts pending")
    c = rep["cost"]["pplx-flash"]["k"]
    assert c["20"]["calls_per_pr"] == 21 and c["20"]["pair_usd_per_pr"] == pytest.approx(20 * 1000 * 0.01 / 1e6)
    sample = B._rows(B.DATA_DIR / "hold_sample.jsonl")
    _w(B.DATA_DIR / "hold_labels.jsonl", [{**{k: r[k] for k in ("pr_number", "a_number", "b_number")}, "label": l, "note": "", "labelled_at": "t"}
                                          for r, l in zip(sample, ("duplicate", "pointer", "unrelated"))])
    rep = B.run(B.RUN_DIR)
    h2 = rep["hypotheses"]["R6B-H2"]
    assert rep["labels_status"] == "ok" and h2["union"] == pytest.approx(2 / 3) and h2["duplicate"] == pytest.approx(1 / 3) and h2["pass"]
    assert (B.RUN_DIR / "b_tables.md").read_text().startswith("# Run 6 part B")
