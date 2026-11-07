"""§R10 pre-freeze check: does d3-flash take the v4 questions unchanged through /v1/systemone?

Runs on the pod next to a `d3_server.py` (vllm-sr/d3-flash). Sends the eight v4 questions, word for word from
`prompts/jev_v4.json`, with a few real test-split states: once as the raw HTTP body our adapter sends, once through
the adapter itself (`pplx-flash-local`, endpoint overridden), and reports the reply shape, whether every question got
a probability, the agreement between the two paths, and timing. No bar, no scoring; a tooling commit for the freeze.

  MERGEONE_LOCAL_ENDPOINT=http://127.0.0.1:8000/v1/systemone python scripts/r10_d3_check.py --n 12
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import httpx

from mergeone.cli import build_model, load_dataset
from mergeone.models.base import DECISIONS_V4


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="data/run3/dataset.jsonl")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--out", default="results/run10prep/d3_check.json")
    a = ap.parse_args()
    endpoint = os.environ.get("MERGEONE_LOCAL_ENDPOINT", "http://127.0.0.1:8000/v1/systemone")
    questions = json.loads((Path("prompts") / "jev_v4.json").read_text())
    rows = load_dataset("test", None, Path(a.dataset))[: a.n]
    client = httpx.Client(timeout=600)
    health = client.get(endpoint.replace("/v1/systemone", "/health")).json()
    print("health:", health)

    raw, timings, shapes = [], [], set()
    for r in rows:
        body = {"state": r["state"], "model": "vllm-sr/d3-flash", "questions": {q: questions[q] for q in DECISIONS_V4}}
        t0 = time.perf_counter()
        resp = client.post(endpoint, json=body)
        timings.append(time.perf_counter() - t0)
        if resp.status_code != 200:
            print("HTTP", resp.status_code, resp.text[:300])
            raw.append({"pr": r["pr_number"], "error": resp.text[:300]})
            continue
        d = resp.json()
        ans = d.get("answers", {})
        shapes.add(json.dumps(sorted(next(iter(ans.values())).keys())) if ans else "no answers")
        raw.append({"pr": r["pr_number"], "answers": ans, "usage": d.get("usage"), "model": d.get("model")})
    print("answer shape(s):", shapes)
    print("usage sample:", raw[0].get("usage") if raw else None)
    print(f"raw: {len(raw)} requests, median {sorted(timings)[len(timings)//2]:.2f}s, max {max(timings):.2f}s")

    model = build_model("pplx-flash-local", "v4")
    model.endpoint = endpoint
    adapter, agree, n_cmp = [], 0, 0
    for r, rr in zip(rows, raw):
        d = model.decide(r["state"], r["features"], DECISIONS_V4)
        adapter.append({"pr": r["pr_number"], "probs": d.probs, "error": d.error, "model_version": d.model_version})
        if d.error or "answers" not in rr:
            continue
        for q in DECISIONS_V4:
            a = rr["answers"].get(q, {})
            p_raw = a.get("noul", a.get("probability"))
            if p_raw is None:
                continue
            n_cmp += 1
            agree += abs(float(p_raw) - d.probs[q]) < 1e-6
    errs = sum(1 for x in adapter if x["error"])
    print(f"adapter: {len(adapter)} decisions, {errs} errors; raw-vs-adapter identical on {agree}/{n_cmp} answers")
    print("adapter sample:", {k: round(v, 3) for k, v in adapter[0]["probs"].items()} if adapter else None, adapter[0]["error"] if adapter else None)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"endpoint": endpoint, "health": health, "raw": raw, "adapter": adapter,
                                       "timings_s": timings, "shapes": sorted(shapes)}, indent=2))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
