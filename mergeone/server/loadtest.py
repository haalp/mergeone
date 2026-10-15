"""Open-loop HTTP load test against a running gateway.

    python -m mergeone.server.loadtest --url http://localhost:8787 --rate 35 --n 1200 [--async]

--rate is decisions per second (30k/day = 0.35/s; 3M/day = 35/s). Requests are
fired on schedule regardless of completion (open loop) so queueing shows in p99.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8787")
    ap.add_argument("--rate", type=float, required=True)
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--async", dest="async_", action="store_true", help="enqueue (202) instead of waiting; then poll until drained")
    ap.add_argument("--concurrency", type=int, default=256)
    a = ap.parse_args(argv)
    rng = random.Random(1)
    run_id = f"{int(time.time())}"
    client = httpx.Client(base_url=a.url, timeout=60)
    t0 = time.perf_counter()
    results = []

    def one(i):
        wait = i / a.rate - (time.perf_counter() - t0)
        if wait > 0:
            time.sleep(wait)
        state = f"PULL REQUEST TITLE: change {i} {rng.random()}\nFILES CHANGED:\n  pkg/x/{i%50}.go +{i%40} -{i%7}\nDIFF: ..." + "x" * 3000
        body = {"repo": "load/test", "pr": i, "head_sha": f"{run_id}-{i:06d}", "state": state, "paths": [f"pkg/x/{i%50}.go"], "wait": not a.async_}
        s = time.perf_counter()
        try:
            r = client.post("/decide", json=body)
            return {"i": i, "status": r.status_code, "latency_ms": (time.perf_counter() - s) * 1000, "verdict": r.json().get("verdict") if r.status_code == 200 else None}
        except Exception as e:
            return {"i": i, "status": 0, "latency_ms": (time.perf_counter() - s) * 1000, "error": type(e).__name__}

    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        for f in as_completed([ex.submit(one, i) for i in range(a.n)]):
            results.append(f.result())
    wall = time.perf_counter() - t0
    if a.async_:
        # wait for the queue to drain
        while True:
            q = client.get("/healthz").json()["queue"]
            if not q.get("queued") and not q.get("running"):
                break
            time.sleep(0.2)
        drain = time.perf_counter() - t0
    ok = [r for r in results if r["status"] in (200, 202)]
    lat = sorted(r["latency_ms"] for r in ok)
    out = {"rate_per_s": a.rate, "n": a.n, "wall_s": round(wall, 1), "ok": len(ok), "error_rate": 1 - len(ok) / a.n,
           "p50_ms": round(lat[len(lat) // 2], 1) if lat else None, "p99_ms": round(lat[min(int(len(lat) * .99), len(lat) - 1)], 1) if lat else None,
           "achieved_rate": round(a.n / wall, 2), "mode": "async" if a.async_ else "sync"}
    if a.async_:
        out["drain_s"] = round(drain, 1)
        out["effective_decisions_per_s"] = round(a.n / drain, 2)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
