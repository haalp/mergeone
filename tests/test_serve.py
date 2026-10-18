"""Run 6A: serve/systemone_server.py HTTP layer with a fake decider (no GPU, no pplx_decider)."""
import asyncio
import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("systemone_server", ROOT / "serve" / "systemone_server.py")
srv = importlib.util.module_from_spec(_spec)
sys.modules["systemone_server"] = srv
_spec.loader.exec_module(srv)

MODEL = "pplx-decider-flash-v1.2@abc123"


class RequestError(Exception):  # same class names as pplx_decider's
    pass


class LengthError(Exception):
    pass


class FakeDecider:
    """noul → 0.5, choice/score → uniform over criteria. `state` triggers errors: BAD_REQUEST, TOO_LONG, BOOM."""

    def __init__(self, delay: float = 0.02):
        self.calls, self.sizes, self.delay = 0, [], delay
        self.threads = set()

    def _one(self, r):
        if r["state"] == "BAD_REQUEST":
            raise RequestError("bad question")
        if r["state"] == "TOO_LONG":
            raise LengthError("state too long")
        if r["state"] == "BOOM":
            raise RuntimeError("cuda oom")
        answers = {}
        for name, q in r["questions"].items():
            if q["type"] == "noul":
                answers[name] = {"noul": 0.5}
            else:
                opts = list(q.get("criteria") or {"a": "", "b": ""})
                answers[name] = {q["type"]: opts[0], "probabilities": {o: 1 / len(opts) for o in opts}, "confidence": 0.0}
        return {"answers": answers, "usage": {"input_tokens": len(r["state"]), "output_tokens": 0, "sequences": len(r["questions"])}}

    def decide(self, request):
        self.calls += 1
        self.threads.add(threading.get_ident())
        time.sleep(self.delay)
        if isinstance(request, list):
            self.sizes.append(len(request))
            return [self._one(r) for r in request]
        return self._one(request)


QS = {"needs_human_review": {"type": "noul", "instructions": "x"},
      "same_change": {"type": "choice", "instructions": "y", "criteria": {"same": "s", "subset": "u", "related": "r", "unrelated": "n"}}}


def _body(state="diff", questions=QS):
    return {"state": state, "model": "pplx-decider-flash-v1.2", "questions": questions}


async def _with_client(dec, fn, **kw):
    app = srv.create_app(dec, MODEL, **kw)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await fn(c, app)


def test_schema_and_healthz():
    async def go(c, app):
        r = await c.post("/v1/systemone", json=_body())
        h = await c.get("/healthz")
        return r, h
    r, h = asyncio.run(_with_client(FakeDecider(), go))
    assert r.status_code == 200
    d = r.json()
    assert d["model"] == MODEL
    assert d["answers"]["needs_human_review"] == {"noul": 0.5, "type": "noul"}
    sc = d["answers"]["same_change"]
    assert sc["type"] == "choice" and sc["probabilities"] == {"same": 0.25, "subset": 0.25, "related": 0.25, "unrelated": 0.25}
    assert d["usage"]["input_tokens"] == 4 and d["usage"]["output_tokens"] == 0
    assert h.status_code == 200 and h.json()["ok"] and h.json()["model"] == MODEL and h.json()["stats"]["requests"] == 1


def test_batching_ten_concurrent_requests():
    dec = FakeDecider(delay=0.05)

    async def go(c, app):
        return await asyncio.gather(*[c.post("/v1/systemone", json=_body(state=f"s{i}")) for i in range(10)])
    rs = asyncio.run(_with_client(dec, go))
    assert all(r.status_code == 200 for r in rs)
    assert [r.json()["usage"]["input_tokens"] for r in rs] == [2] * 10  # each caller gets its own answer
    assert dec.calls <= 2 and sum(dec.sizes) == 10
    assert len(dec.threads) == 1  # one worker thread


def test_batch_max_respected():
    dec = FakeDecider(delay=0.01)

    async def go(c, app):
        return await asyncio.gather(*[c.post("/v1/systemone", json=_body(state=f"s{i}")) for i in range(10)])
    rs = asyncio.run(_with_client(dec, go, batch_max=4))
    assert all(r.status_code == 200 for r in rs) and max(dec.sizes) <= 4


def test_error_mapping_and_isolation():
    dec = FakeDecider()

    async def go(c, app):
        rs = await asyncio.gather(c.post("/v1/systemone", json=_body("ok1")), c.post("/v1/systemone", json=_body("BAD_REQUEST")),
                                  c.post("/v1/systemone", json=_body("TOO_LONG")), c.post("/v1/systemone", json=_body("BOOM")),
                                  c.post("/v1/systemone", json=_body("ok2")))
        after = await c.post("/v1/systemone", json=_body("ok3"))  # the batcher survived
        bad = [await c.post("/v1/systemone", content=b"{not json"),
               await c.post("/v1/systemone", json={"state": "x"}),
               await c.post("/v1/systemone", json=_body(questions={"q": {"type": "maybe", "instructions": "x"}})),
               await c.post("/v1/systemone", json={"state": 3, "questions": QS})]
        return rs, after, bad
    rs, after, bad = asyncio.run(_with_client(dec, go))
    assert [r.status_code for r in rs] == [200, 400, 400, 500, 200]
    assert rs[1].json()["error"]["type"] == "RequestError" and rs[3].json()["error"]["type"] == "RuntimeError"
    assert after.status_code == 200
    assert [r.status_code for r in bad] == [400, 400, 400, 400]


def test_bad_decider_output_is_500():
    class Short(FakeDecider):
        def decide(self, request):
            return [] if isinstance(request, list) else {}

    async def go(c, app):
        return await c.post("/v1/systemone", json=_body())
    r = asyncio.run(_with_client(Short(), go))
    assert r.status_code == 500


def test_numpy_scalars_serialise():
    np = pytest.importorskip("numpy")

    class Np(FakeDecider):
        def _one(self, r):
            return {"answers": {k: {"noul": np.float32(0.25)} for k in r["questions"]}, "usage": {"input_tokens": np.int64(7)}}

    async def go(c, app):
        return await c.post("/v1/systemone", json=_body(questions={"q": {"type": "noul", "instructions": "x"}}))
    r = asyncio.run(_with_client(Np(), go))
    assert r.status_code == 200 and r.json()["answers"]["q"]["noul"] == 0.25 and r.json()["usage"] == {"input_tokens": 7, "output_tokens": 0}


def test_revision_from_snapshot_path(tmp_path):
    p = tmp_path / "models--x" / "snapshots" / "deadbeef"
    p.mkdir(parents=True)
    assert srv.resolved_revision(p, "main") == "deadbeef"
    assert srv.resolved_revision(tmp_path, "v1.2") == "v1.2"


# ----------------------------------------------------------------- pass 2: length-aware batching
def _items(tokens, t0=0.0):
    loop = asyncio.new_event_loop()
    try:
        return [srv._Item({"state": "x" * (t * 4)}, loop.create_future(), t0 + i * 1e-3, t) for i, t in enumerate(tokens)]
    finally:
        loop.close()


def test_select_batch_budget_off_is_arrival_order():
    its = _items([16000, 500, 600, 700])
    assert [it.tokens for it in srv.select_batch(its, 3, 0)] == [16000, 500, 600]


def test_select_batch_groups_by_length_under_budget():
    # oldest is a 500-token PR; a 16k PR is queued too. Budget 4k padded tokens → the 16k one waits, short ones go together.
    its = _items([500, 16000, 600, 450, 700, 8000])
    got = srv.select_batch(its, 32, 4000)
    assert sorted(it.tokens for it in got) == [450, 500, 600, 700]  # 4 × 700 = 2,800 ≤ 4,000; adding 8k would be 5 × 8k
    assert [it.tokens for it in got] == [500, 600, 450, 700]  # arrival order preserved inside the batch


def test_select_batch_oldest_always_goes_even_over_budget():
    its = _items([16000, 500])
    got = srv.select_batch(its, 32, 1000)
    assert [it.tokens for it in got] == [16000]


def test_select_batch_respects_batch_max():
    its = _items([100] * 50)
    assert len(srv.select_batch(its, 8, 10_000)) == 8


def test_batch_tokens_end_to_end_padding_stats():
    dec = FakeDecider(delay=0.03)

    async def go(c, app):
        rs = await asyncio.gather(*[c.post("/v1/systemone", json=_body(state="a" * n)) for n in [40, 64000, 48, 56, 64000, 52]])
        h = (await c.get("/healthz")).json()
        return rs, h
    rs, h = asyncio.run(_with_client(dec, go, batch_tokens=4000, batch_ms=30.0))
    assert all(r.status_code == 200 for r in rs)
    assert h["batch_tokens"] == 4000
    assert h["stats"]["padding_efficiency"] is not None and h["stats"]["padding_efficiency"] > 0.5  # long ones never padded against short ones
    assert sum(dec.sizes) == 6 and max(dec.sizes) <= 4
