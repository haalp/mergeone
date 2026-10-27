"""Run 9 step bookkeeping for scripts/run9_gpu.sh (no model call; reads the local server and files only).

  python -m mergeone.eval.run9_step snapshot --out <step>/server_before.json [--port 8080]
        GET /meta and /healthz of serve/systemone_server.py → one json (the fast options in effect when the step ran)
  python -m mergeone.eval.run9_step throughput --step-dir results/run9/<arm>/<step> --qset v3 --split test \
        --before <step>/server_before.json --after <step>/server_after.json --outer-wall-s <s> [--model pplx-flash-local]
        → <step>/throughput.json {n_items, n_requests, errors, wall_s, items_per_s, requests_per_s, concurrency, decide_s, server}

Throughput (§R9.2): items completed without error ÷ wall-clock seconds of one closed-loop pass. wall_s is the pass's own
clock from the predict meta (first submission → last answer; `wall_s` since run 9); the script's outer wall (process start
to exit, includes Python start-up and model construction) is recorded beside it as `outer_wall_s`. Items = requests for
the equivalence set (one v3 request per PR), candidates for the pair sets (per-candidate: one request each; shared: k per
request). decide_s = the server's cumulative time inside `decider.decide`, after − before.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _get(url: str, timeout: float = 5.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # recorded, never fatal: the step's own output is the measurement
        return {"error": f"{type(e).__name__}: {e}"}


def snapshot(port: int = 8080, host: str = "127.0.0.1") -> dict:
    base = f"http://{host}:{port}"
    return {"at": datetime.now(timezone.utc).isoformat(), "meta": _get(base + "/meta"), "healthz": _get(base + "/healthz")}


def meta_file(step_dir: Path, model: str, split: str, qset: str, single: bool = False) -> Path:
    """The predict meta file name cli.cmd_predict / run9_pairs.predict_shared write for this pass."""
    if qset == "pair_shared":
        return Path(step_dir) / f"meta_{model}_{split}_all_pair_shared.json"
    sfx = "" if qset in ("v1", "v2", "pair") else f"_{qset}"
    return Path(step_dir) / f"meta_{model}_{split}_all{sfx}{'_single' if single else ''}.json"


def _decide_s(snap: dict | None) -> float | None:
    try:
        return float(snap["healthz"]["stats"]["decide_s"])
    except Exception:
        return None


def throughput(step_dir: Path, qset: str, split: str = "test", model: str = "pplx-flash-local", before: dict | None = None,
               after: dict | None = None, outer_wall_s: float | None = None, single: bool = False) -> dict:
    mf = meta_file(step_dir, model, split, qset, single)
    if not mf.exists():
        raise SystemExit(f"{mf} not found: the predict pass did not finish")
    m = json.loads(mf.read_text())
    n, err = int(m.get("n", 0)), int(m.get("errors", 0))
    ok = n - err
    wall = m.get("wall_s") or outer_wall_s
    n_req = m.get("n_requests", n)  # pair_shared: groups; every other qset: one request per row (joint)
    d0, d1 = _decide_s(before), _decide_s(after)
    server = ((after or before or {}).get("meta") or {})
    return {"step": Path(step_dir).name, "arm": Path(step_dir).parent.name, "qset": qset, "split": split, "mode": m.get("mode"),
            "n_items": ok, "n_rows": n, "errors": err, "n_requests": n_req, "request_errors": m.get("request_errors", err),
            "wall_s": wall, "wall_source": "predict meta" if m.get("wall_s") else "outer", "outer_wall_s": outer_wall_s,
            "items_per_s": (ok / wall) if wall else None, "requests_per_s": (n_req / wall) if wall else None,
            "concurrency": m.get("concurrency"), "decide_s": (d1 - d0) if (d0 is not None and d1 is not None) else None,
            "server": {k: server.get(k) for k in ("served_as", "dtype", "fast_options_effective", "batch_tokens", "batch_max", "batch_ms", "gpu_name")},
            "predict_meta": mf.name, "started_at": m.get("started_at"), "finished_at": m.get("finished_at")}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run9_step")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--out", required=True)
    s.add_argument("--port", type=int, default=8080)
    t = sub.add_parser("throughput")
    t.add_argument("--step-dir", required=True)
    t.add_argument("--qset", required=True)
    t.add_argument("--split", default="test")
    t.add_argument("--model", default="pplx-flash-local")
    t.add_argument("--single-question", action="store_true")
    t.add_argument("--before")
    t.add_argument("--after")
    t.add_argument("--outer-wall-s", type=float)
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot(a.port), indent=2) + "\n")
        print(out)
        return
    rd = lambda p: json.loads(Path(p).read_text()) if p and Path(p).exists() else None
    r = throughput(Path(a.step_dir), a.qset, a.split, a.model, rd(a.before), rd(a.after), a.outer_wall_s, a.single_question)
    (Path(a.step_dir) / "throughput.json").write_text(json.dumps(r, indent=2) + "\n")
    print(json.dumps({k: r[k] for k in ("arm", "step", "n_items", "errors", "wall_s", "items_per_s", "decide_s")}))


if __name__ == "__main__":
    main()
