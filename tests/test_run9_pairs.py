"""Run 9 R9-H4: shared-A request rendering, the two pair sets, predict --qset pair_shared against a fake server, scoring.
No model or API is called."""
import argparse
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from mergeone import cli
from mergeone.eval import run4
from mergeone.eval import run9_pairs as P


def _w(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_frozen_prompt_loads_and_renders():
    spec = P.load_spec()
    g = {"group_id": 1, "a_state": "A" * 7000, "candidates": [{"b_state": f"B{i}" * 2000} for i in range(1, 4)]}
    state, qs, names = P.render_shared(g, spec)
    assert names == ["same_change_1", "same_change_2", "same_change_3"]
    assert state.startswith("=== PULL REQUEST A ===\n" + "A" * 6000 + "\n\n=== CANDIDATE B1 ===\n")
    assert "A" * 6001 not in state
    assert state.endswith("\n\nHow does A relate to each candidate, B1 to B3?")
    assert state.count("=== CANDIDATE B") == 3 and "=== CANDIDATE B3 ===\n" in state
    seg = state.split("=== CANDIDATE B2 ===\n")[1].split("\n\n=== CANDIDATE B3")[0]
    assert len(seg) == 3000
    q2 = qs["same_change_2"]
    assert q2["type"] == "choice" and set(q2["criteria"]) == {"same", "subset", "related", "unrelated"}
    assert "3 candidate pull requests B1 to B3" in q2["instructions"] and "candidate B2 only" in q2["instructions"]
    assert "{i}" not in q2["instructions"] and "{k}" not in q2["instructions"]
    with pytest.raises(ValueError):
        P.render_shared({"a_state": "a", "candidates": [{"b_state": "b"}] * 11}, spec)


def test_split_pair_state_roundtrip():
    a, b = "PULL REQUEST TITLE: x\n" + "a" * 7000, "PULL REQUEST TITLE: y\nFILES CHANGED:\n  f.go"
    s = run4.pair_state(a, b)
    assert P.split_pair_state(s) == (a[:6000], b)
    with pytest.raises(ValueError):
        P.split_pair_state("nope")


def test_duplicate_of_rule():
    p, c, pr = P.duplicate_of({"probabilities": {"same": 0.5, "subset": 0.2, "related": 0.2, "unrelated": 0.1}, "confidence": 0.3})
    assert p == pytest.approx(0.6) and c == 0.3 and pr["same"] == 0.5
    assert np.isnan(P.duplicate_of(None)[0])


def _pair(repo, a, b, positive=False, kind="retrieved", split="test"):
    return {"pr_number": P.pair_id(repo, a, b), "split": split, "repo": repo, "a": a, "b": b, "kind": kind,
            "labels": {"positive": positive, "a_label": "x"}, "features": {}, "state": run4.pair_state(f"A{a} text", f"B{b} text")}


def test_set_a_groups_by_a_in_file_order_and_chunks_of_ten():
    pairs = [_pair("o/r", 1, b, positive=(b == 103)) for b in range(100, 113)] + [_pair("o/r", 2, 7), _pair("o/r", 1, 999)]
    keys = {p["pr_number"] for p in pairs if p["b"] != 999}
    g = P.build_set_a(pairs, keys)
    assert [x["group_size"] for x in g] == [10, 3, 1]
    assert [c["b"] for c in g[0]["candidates"]] == list(range(100, 110)) and [c["b"] for c in g[1]["candidates"]] == [110, 111, 112]
    assert g[0]["a_state"] == "A1 text" and g[0]["candidates"][0]["b_state"] == "B100 text"
    assert g[0]["chunk"] == 0 and g[1]["chunk"] == 1 and g[0]["group_id"] != g[1]["group_id"] and g[0]["n_chunks"] == 2
    assert g[0]["candidates"][3]["labels"]["positive"] is True
    assert all(c["pr_number"] in keys for x in g for c in x["candidates"])


def _cand(n, rank):
    return {"number": n, "title": f"t{n}", "state": "OPEN", "created_at": "2026-01-01T00:00:00", "merged_at": None, "author": "u", "score": 1.0 / rank}


def _k20(repo, a, numbers):
    return {"pr_number": a, "repo": repo, "number": a, "split": "test", "candidates": [_cand(n, i) for i, n in enumerate(numbers, 1)],
            "candidate_bodies": {str(n): {"body": f"body {n}", "files": [f"f{n}.go"]} for n in numbers}}


def test_set_b_forces_adjudicated_b_into_slot_ten():
    ds = {("o/r", 1): {"repo": "o/r", "number": 1, "split": "test", "state": "A one"},
          ("o/r", 2): {"repo": "o/r", "number": 2, "split": "test", "state": "A two"},
          ("o/r", 3): {"repo": "o/r", "number": 3, "split": "test", "state": "A three"}}
    k20 = [_k20("o/r", 1, list(range(100, 120))), _k20("o/r", 2, list(range(200, 220))), _k20("o/r", 3, list(range(300, 320)))]
    adj = [{"repo": "o/r", "a_number": 1, "b_number": 104, "label": "duplicate"},
           {"repo": "o/r", "a_number": 2, "b_number": 215, "label": "pointer"},
           {"repo": "o/r", "a_number": 3, "b_number": 999, "label": "unrelated"}]
    mem = lambda repo, n: ({"number": n, "title": "m", "state": "CLOSED", "created_at": "2026-01-01T00:00:00", "merged_at": None, "author": "v", "score": None},
                           "mem body", ["m.go"]) if n == 999 else None
    groups, rows, summ = P.build_set_b(adj, k20, ds, mem)
    assert [g["group_size"] for g in groups] == [10, 10, 10] and len(rows) == 30
    g1, g2, g3 = groups
    assert [c["b"] for c in g1["candidates"]] == list(range(100, 110))
    assert [c["b"] for c in g2["candidates"]] == list(range(200, 209)) + [215]
    assert g2["candidates"][-1]["kind"] == "adjudicated_b_forced" and g2["candidates"][-1]["labels"]["retrieval_rank"] == 16
    assert g3["candidates"][-1]["b"] == 999 and g3["candidates"][-1]["labels"]["adjudicated"] == "unrelated"
    assert summ["b_forced_into_slot_10"] == 2 and summ["b_from_memory"] == ["o/r#3~999"]
    lab = {r["pr_number"]: r["labels"]["adjudicated"] for r in rows if r["labels"]["adjudicated"]}
    assert sorted(lab.values()) == ["duplicate", "pointer", "unrelated"]
    # per-candidate rows carry the same A and B text as the group (run-4 renderer)
    r0 = next(r for r in rows if r["b"] == 104)
    a_txt, b_txt = P.split_pair_state(r0["state"])
    c0 = next(c for c in g1["candidates"] if c["b"] == 104)
    assert a_txt == g1["a_state"] and b_txt == c0["b_state"] and r0["pr_number"] == c0["pr_number"] == P.pair_id("o/r", 1, 104)
    with pytest.raises(ValueError):
        P.build_set_b([{"repo": "o/r", "a_number": 3, "b_number": 999, "label": "x"}], k20, ds, None)


def test_cmd_groups_degrades_without_inputs(tmp_path):
    out = P.cmd_groups(argparse.Namespace(root=str(tmp_path)))
    assert out["a"]["status"].startswith("not built") and out["b"]["status"].startswith("not built")
    assert (tmp_path / P.GROUPS_SUMMARY).exists()


class FakeModel:
    def __init__(self, handler):
        self.client = httpx.Client(transport=httpx.MockTransport(handler))
        self.endpoint = "http://fake/v1/systemone"
        self.max_retries_429 = 0
        self.pinned, self.model, self.version = None, "pplx-decider-flash-v1.2", "pair"
        self.in_price = self.out_price = 0.0


def _handler(seen):
    def h(req):
        body = json.loads(req.content)
        seen.append(body)
        answers = {}
        for i, name in enumerate(body["questions"], 1):
            same = 0.1 * i
            answers[name] = {"type": "choice", "choice": "same", "confidence": 0.5,
                             "probabilities": {"same": same, "subset": 0.2, "related": 0.1, "unrelated": 0.7 - same}}
        if "FAIL" in body["state"]:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"model": "pplx-decider-flash-v1.2@abc", "answers": answers, "usage": {"input_tokens": 1000, "output_tokens": 0}})
    return h


def _predict_args(ds, run, **kw):
    a = dict(model="pplx-flash-local", split="test", run=run, limit=None, concurrency=4, questions="all", words=False, append=False,
             only_errors_from=None, only_missing_from=None, qset="pair_shared", subset=None, dataset=str(ds), single_question=False,
             compact=False, raw=None, prompt_file=None)
    a.update(kw)
    return argparse.Namespace(**a)


def test_predict_pair_shared_writes_one_row_per_candidate(tmp_path, monkeypatch):
    pairs = [_pair("o/r", 1, b, positive=(b == 101)) for b in range(100, 104)] + [_pair("o/r", 2, 7)]
    groups = P.build_set_a(pairs, {p["pr_number"] for p in pairs})
    groups.append({**P.build_set_a([_pair("o/r", 3, 8)], {P.pair_id("o/r", 3, 8)})[0], "a_state": "FAIL"})
    ds = tmp_path / "groups_a.jsonl"
    _w(ds, groups)
    seen = []
    monkeypatch.setattr(cli, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(cli, "build_model", lambda name, version="v1": FakeModel(_handler(seen)))
    # an earlier per-candidate pair row in the same file must survive
    out = tmp_path / "results/run9/shared/pairs_a/predictions_pplx-flash-local_test_all.jsonl"
    _w(out, [{"pr_number": 5, "split": "test", "labels": {}, "qset": "pair", "mode": "joint", "decision": {"probs": {"same_change": 0.1}, "error": None}}])
    meta = cli.cmd_predict(_predict_args(ds, "run9/shared/pairs_a"))
    rows = [json.loads(l) for l in out.open()]
    shared = [r for r in rows if r["qset"] == "pair_shared"]
    assert len(seen) == 3 and len(shared) == 6 and sum(r["qset"] == "pair" for r in rows) == 1
    g1 = sorted((r for r in shared if r["a"] == 1), key=lambda r: r["slot"])
    assert [r["b"] for r in g1] == [100, 101, 102, 103] and all(r["group_size"] == 4 and r["mode"] == "shared" for r in g1)
    assert g1[1]["pr_number"] == P.pair_id("o/r", 1, 101) and g1[1]["labels"]["positive"] is True
    assert g1[1]["decision"]["probs"]["same_change"] == pytest.approx(0.2 + 0.1)  # P(same) + 0.5·P(subset), slot 2
    assert g1[0]["decision"]["input_tokens"] == 250 and g1[0]["decision"]["raw"]["usage_group"]["input_tokens"] == 1000
    assert all(r["decision"]["error"] for r in shared if r["a"] == 3)
    body = next(b for b in seen if "A1 text" in b["state"])
    assert list(body["questions"]) == [f"same_change_{i}" for i in range(1, 5)] and body["state"].count("=== CANDIDATE B") == 4
    assert meta["n"] == 6 and meta["n_requests"] == 3 and meta["errors"] == 1 and meta["request_errors"] == 1 and meta["wall_s"] > 0
    assert (tmp_path / "results/run9/shared/pairs_a/meta_pplx-flash-local_test_all_pair_shared.json").exists()
    # resume: only the failed group is sent again
    seen.clear()
    cli.cmd_predict(_predict_args(ds, "run9/shared/pairs_a", append=True, only_missing_from=str(out)))
    assert len(seen) == 1 and "FAIL" in seen[0]["state"]


def _prow(pid, p, labels, qset="pair", mode="joint", **kw):
    return {"pr_number": pid, "split": "test", "labels": labels, "qset": qset, "mode": mode,
            "decision": {"probs": {"same_change": p}, "error": None, "input_tokens": 100}, **kw}


def test_score_and_h4_verdict(tmp_path):
    rng = np.random.default_rng(1)
    n = 60
    y = (np.arange(n) % 4 == 0).astype(int)
    pc = (y * 0.6 + rng.uniform(0, 0.4, n)).round(4)
    sh = (y * 0.55 + rng.uniform(0, 0.45, n)).round(4)
    keys = [{"pr_number": i} for i in range(n)]
    _w(tmp_path / P.PAIR_KEYS, keys)
    d = {k: tmp_path / v for k, v in P.ARM_DIRS.items()}
    _w(d[("per_candidate", "a")] / "predictions_pplx-flash-local_test_all.jsonl", [_prow(i, float(pc[i]), {"positive": bool(y[i])}) for i in range(n)])
    _w(d[("shared", "a")] / "predictions_pplx-flash-local_test_all.jsonl",
       [_prow(i, float(sh[i]), {"positive": bool(y[i])}, qset="pair_shared", mode="shared", group_size=10) for i in range(n)])
    lab = lambda i: "duplicate" if y[i] else ("pointer" if i % 2 else "unrelated")
    _w(d[("per_candidate", "b")] / "predictions_pplx-flash-local_test_all.jsonl",
       [_prow(1000 + i, float(pc[i]), {"adjudicated": lab(i)}) for i in range(n)] + [_prow(5000, 0.9, {"adjudicated": None})])
    _w(d[("shared", "b")] / "predictions_pplx-flash-local_test_all.jsonl",
       [_prow(1000 + i, float(sh[i]), {"adjudicated": lab(i)}, qset="pair_shared", mode="shared", group_size=10) for i in range(n)])
    (d[("per_candidate", "b")] / "throughput.json").write_text(json.dumps({"items_per_s": 10.0}))
    (d[("shared", "b")] / "throughput.json").write_text(json.dumps({"items_per_s": 30.0}))
    with pytest.raises(SystemExit):
        P.cmd_score(argparse.Namespace(root=str(tmp_path), i_have_frozen_the_protocol=False))
    rep = P.cmd_score(argparse.Namespace(root=str(tmp_path), i_have_frozen_the_protocol=True))
    from sklearn.metrics import roc_auc_score
    assert rep["auc_set_a"]["per_candidate"]["auc"] == pytest.approx(roc_auc_score(y, pc))
    assert rep["auc_set_a"]["shared"]["auc"] == pytest.approx(roc_auc_score(y, sh))
    assert rep["dup_vs_rest_set_b"]["per_candidate"]["n"] == n  # the unlabelled candidate is not scored
    assert rep["speed_set_b"]["ratio"] == pytest.approx(3.0)
    assert rep["agreement"]["a"]["n"] == n and rep["shared_group_sizes"]["a"] == {10: n}
    h = rep["R9-H4"]
    assert h["clauses"]["speed"] is True and h["pass"] in (True, False)
    assert (tmp_path / P.PAIRS_METRICS).exists()
    v = P.h4_verdict({"ratio": 2.4}, {"shared": {"auc": 0.9}, "per_candidate": {"auc": 0.9}}, {"shared": {"auc": 0.8}, "per_candidate": {"auc": 0.81}})
    assert v["pass"] is False and v["clauses"] == {"speed": False, "auc_set_a": True, "dup_vs_rest_set_b": True}
    v = P.h4_verdict({"ratio": None}, {}, {})
    assert v["pass"] is None


# ----------------------------------------------------------------- loadtest: errors per step and the server's options
def test_loadtest_reports_errors_per_step_and_server_meta(tmp_path, monkeypatch):
    from mergeone.models.base import Decision
    rows = [{"pr_number": i, "split": "test", "labels": {}, "features": {}, "state": f"s{i}"} for i in range(40)]
    ds = tmp_path / "ds.jsonl"
    _w(ds, rows)

    class M:
        endpoint = "http://127.0.0.1:8080/v1/systemone"
        max_retries_429 = 5

        def decide(self, state, features, questions):
            i = int(state[1:])
            return Decision(probs={}, latency_ms=float(i), error=("HTTP 500: boom" if i % 10 == 3 else ("ReadTimeout: slow" if i == 7 else None)))

    monkeypatch.setattr(cli, "RESULTS", tmp_path)
    monkeypatch.setattr(cli, "build_model", lambda name, version="v1": M())
    monkeypatch.setattr(cli, "_server_meta", lambda ep: {"fast_options_effective": {"fp8": True}, "dtype": "fp8 (…)"} if ep else None)
    cli.main(["loadtest", "--model", "pplx-flash-local", "--multiplier", "1000", "--limit", "40", "--run", "run9/fp8/load", "--dataset", str(ds),
              "--qset", "v3", "--concurrency", "512", "--no-retry"])
    s = json.loads((tmp_path / "run9/fp8/load/loadtest_pplx-flash-local_x1000.json").read_text())["summary"]
    assert s["n"] == 40 and s["n_errors"] == 5 and s["n_ok"] == 35 and s["error_rate"] == 5 / 40
    assert s["errors_by_kind"] == {"HTTP 500": 4, "ReadTimeout": 1}
    assert s["retry_429"] == 0 and s["p99_ms"] is not None and s["server_meta"]["fast_options_effective"] == {"fp8": True}


def test_server_meta_only_for_local_systemone_urls():
    assert cli._server_meta(None) is None and cli._server_meta("https://api.perplexity.ai/v1/decide") is None
    assert cli._server_meta("http://127.0.0.1:9/v1/systemone", timeout=0.2) is None  # nothing listening
