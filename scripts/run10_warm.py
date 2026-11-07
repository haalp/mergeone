"""Run 10: warm a freshly started d3-flash server (d3_server.py) before the timed test pass.

Twelve val rows (never test) spread over the state-length quantiles, shortest first, the eight v4 questions as
`prompts/jev_v4.json` words them, one request at a time and no client timeout, through the same adapter the timed pass
uses (`d3-flash-local`). The first requests pay for kernel compilation / autotuning (`flash-linear-attention`); run 9 lost
a timed batch to a cold server (CHANGELOG 2026-11-02). Nothing is scored; `<out>` keeps the per-request wall time.

  python scripts/run10_warm.py --out results/run10/d3/warm.json [--n 12] [--model d3-flash-local]
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from mergeone.cli import build_model, load_dataset
from mergeone.models.base import DECISIONS_V4


def spread(rows: list[dict], key: str, n: int) -> list[dict]:
    """`n` rows spread over the length quantiles of `key` (shortest and longest included), shortest first."""
    rows = sorted(rows, key=lambda r: len(r[key]))
    if len(rows) <= n:
        return rows
    idx = sorted({round(i * (len(rows) - 1) / (n - 1)) for i in range(n)})
    return [rows[i] for i in idx]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="d3-flash-local")
    ap.add_argument("--dataset", default="data/run3/dataset.jsonl")
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.split == "test":
        raise SystemExit("warm on val, never on test")
    model = build_model(a.model, "v4")
    model.client.timeout = None  # the first requests compile kernels
    rec = {"model": a.model, "endpoint": model.endpoint, "split": a.split, "started_at": datetime.now(timezone.utc).isoformat(), "requests": []}
    for r in spread(load_dataset(a.split, None, Path(a.dataset)), "state", a.n):
        t0 = time.perf_counter()
        d = model.decide(r["state"], r["features"], DECISIONS_V4)
        dt = time.perf_counter() - t0
        rec["requests"].append({"chars": len(r["state"]), "s": round(dt, 2), "input_tokens": d.input_tokens, "error": d.error,
                                "model_version": d.model_version})
        print(f"warm v4 {len(r['state']):7d} chars {dt:7.1f}s {'ERROR ' + str(d.error)[:80] if d.error else ''}", flush=True)
    rec["finished_at"] = datetime.now(timezone.utc).isoformat()
    rec["errors"] = sum(1 for q in rec["requests"] if q["error"])
    rec["served_as"] = sorted({q["model_version"] for q in rec["requests"] if q["model_version"]})
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rec, indent=2) + "\n")
    print(f"warm: {len(rec['requests'])} requests, {rec['errors']} errors → {a.out}", flush=True)


if __name__ == "__main__":
    main()
