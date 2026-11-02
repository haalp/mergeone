"""Run 9: warm every length bucket of a freshly started self-hosted server before a timed step.

pplx_decider 0.3.0's FastOptions defaults (compile="layer", cuda_graphs=True) compile each length bucket on its first
request; `Decider.warmup()` covers one. With 32 concurrent timed requests arriving on a cold server, the first request of
every bucket takes longer than the client's 30 s read timeout and its neighbours queue behind it — the bf16 control's
first `equiv` pass on 2026-11-02 lost 96 of 705 rows to `ReadTimeout` that way (kept under `bf16/equiv/passes/`, §R9.4).

This sends one request at a time, with no client timeout, on rows chosen to span the state-length quantiles of the
split (so every bucket up to the longest state is hit at least once), plus the pair rendering when a pair file is given.
Nothing is written to results/ except `<out>` (the per-request wall time, for the record); nothing here is scored.

  python scripts/run9_warm.py --model pplx-flash-local --dataset data/run3/dataset.jsonl --split val --n 24 \
      --pairs results/run4/pairs.jsonl --out results/run9/<arm>/warm.json
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from mergeone.cli import build_model, load_dataset
from mergeone.models.base import DECISIONS_V3, QUESTION_SETS


def spread(rows: list[dict], key: str, n: int) -> list[dict]:
    """`n` rows spread over the length quantiles of `key` (longest included), one per quantile."""
    rows = sorted(rows, key=lambda r: len(r[key]))
    if len(rows) <= n:
        return rows
    idx = sorted({round(i * (len(rows) - 1) / (n - 1)) for i in range(n)})
    return [rows[i] for i in idx]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--split", default="val")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--pairs", help="pair jsonl (results/run4/pairs.jsonl); warms the pair rendering too")
    ap.add_argument("--n-pairs", type=int, default=8)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    model = build_model(a.model, "v3")
    model.client.timeout = None  # type: ignore[attr-defined]  # compile may take minutes on the first request of a bucket
    record = {"model": a.model, "started_at": datetime.now(timezone.utc).isoformat(), "requests": []}

    def hit(kind: str, state: str, features: dict, questions) -> None:
        t0 = time.perf_counter()
        d = model.decide(state, features, questions)
        dt = time.perf_counter() - t0
        record["requests"].append({"kind": kind, "chars": len(state), "s": round(dt, 2), "error": d.error})
        print(f"warm {kind:5s} {len(state):7d} chars {dt:7.1f}s {'ERROR ' + str(d.error)[:80] if d.error else ''}", flush=True)

    rows = load_dataset(a.split, None, Path(a.dataset))
    for r in spread(rows, "state", a.n):  # shortest → longest: each bucket compiles once, alone
        hit("v3", r["state"], r["features"], DECISIONS_V3)
    if a.pairs and Path(a.pairs).exists():
        prow = [json.loads(l) for l in Path(a.pairs).open()]
        key = "state" if prow and "state" in prow[0] else "pair_state"
        for r in spread(prow, key, a.n_pairs):
            hit("pair", r[key], r.get("features", {}), QUESTION_SETS["pair"])
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    record["errors"] = sum(1 for q in record["requests"] if q["error"])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(record, indent=2))
    print(f"warm: {len(record['requests'])} requests, {record['errors']} errors → {a.out}", flush=True)


if __name__ == "__main__":
    main()
