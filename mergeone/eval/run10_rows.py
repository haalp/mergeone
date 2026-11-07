"""Run 10 rows (PROTOCOL.md §R10.2): one resumable command per row. Builds no input; every call is `mergeone predict`.

  python -m mergeone.eval.run10_rows list
        every row, its parts and the equivalent `mergeone predict` commands (no model call)
  python -m mergeone.eval.run10_rows run <row> [--part P …] [--concurrency N] [--dry-run] --i-have-frozen-the-protocol
        runs the row's parts in order into results/run10/<row>/ (`--run run10/<row>`, `--append --only-missing-from`
        the part's predictions file, so a re-run asks only what is missing or errored). Refuses without the flag.
        `--dry-run` prints what would be asked (rows missing per part) and calls nothing.
  python -m mergeone.eval.run10_rows set-aside <row> --part P
        moves the part's rows (its qset, its split) out of the shared predictions file into results/run10/<row>/passes/<UTC>/
        (with its meta file, and for v4_test the timed pass's throughput.json / server_before.json / server_after.json):
        an interrupted timed pass is kept, not resumed (run 9's rule); rows of other parts stay.
  python -m mergeone.eval.run10_rows snapshot --out <json> [--endpoint URL]
  python -m mergeone.eval.run10_rows serve-meta --out results/run10/d3/serve_meta.json [--endpoint URL] [--usd-per-hour X]
        [--vm-type S] [--revision SHA] [--snapshot-dir P]
        GET /health and /v1/models of the d3 server (no model call) + nvidia-smi → json

Rows and parts (inputs as they are; §R10.4: no new labels):
  control  pplx-flash      v4_test, relation2           hosted, the same-day comparison for every row
  full     pplx            v4_test, relation2           hosted
  haiku    haiku           relation2                    hosted (Anthropic), prompts/haiku_relation2.md (the model's frozen
                                                         relation2 prompt; its sha256 is recorded with --prompt-file)
  d3       d3-flash-local  v4_test, v4_val, relation2   self-hosted (scripts/run10_gpu.sh); v4_val is the calibration-map input

  v4_test   qset v4,        data/run3/dataset.jsonl, split test (705)  → predictions_<model>_test_all.jsonl
  v4_val    qset v4,        data/run3/dataset.jsonl, split val (1,491) → predictions_<model>_val_all.jsonl
  relation2 qset relation2, results/run8/relation_pairs.jsonl (100 adjudicated pairs, split test)
                                                                       → predictions_<model>_test_all.jsonl (rows kept apart by qset)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import types
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FROZEN_FLAG = "--i-have-frozen-the-protocol"
RUN = "run10"
DATASET = Path("data/run3/dataset.jsonl")
PAIRS = Path("results/run8/relation_pairs.jsonl")
HAIKU_PROMPT = Path("prompts/haiku_relation2.md")
D3_ENDPOINT = "http://127.0.0.1:8000/v1/systemone"
TIMED_PART = "v4_test"   # the d3 row's timed pass (scripts/run10_gpu.sh): its files sit in results/run10/d3/
TIMED_FILES = ("throughput.json", "server_before.json", "server_after.json")

ROWS: dict[str, dict] = {
    "control": {"model": "pplx-flash", "parts": ("v4_test", "relation2"), "hosted": True},
    "full": {"model": "pplx", "parts": ("v4_test", "relation2"), "hosted": True},
    "haiku": {"model": "haiku", "parts": ("relation2",), "hosted": True},
    "d3": {"model": "d3-flash-local", "parts": ("v4_test", "v4_val", "relation2"), "hosted": False},
}
PARTS: dict[str, dict] = {
    "v4_test": {"qset": "v4", "split": "test", "dataset": DATASET},
    "v4_val": {"qset": "v4", "split": "val", "dataset": DATASET},
    "relation2": {"qset": "relation2", "split": "test", "dataset": PAIRS},
}


def results_root() -> Path:
    return Path(__import__("mergeone.cli", fromlist=["x"]).RESULTS)


def row_dir(row: str, root: Path | None = None) -> Path:
    return Path(root or results_root()) / RUN / row


def preds_file(row: str, part: str, root: Path | None = None) -> Path:
    p = PARTS[part]
    return row_dir(row, root) / f"predictions_{ROWS[row]['model']}_{p['split']}_all.jsonl"


def meta_file(row: str, part: str, root: Path | None = None) -> Path:
    """The meta file cli.cmd_predict writes for this part (qset suffix: v4 and relation2 share a predictions file)."""
    p = PARTS[part]
    return row_dir(row, root) / f"meta_{ROWS[row]['model']}_{p['split']}_all_{p['qset']}.json"


def predict_args(row: str, part: str, concurrency: int = 8) -> types.SimpleNamespace:
    """The argparse namespace `mergeone predict` builds for this part (resumable: --append --only-missing-from)."""
    p = PARTS[part]
    return types.SimpleNamespace(
        model=ROWS[row]["model"], split=p["split"], run=f"{RUN}/{row}", limit=None, concurrency=concurrency, questions="all",
        words=False, append=True, only_errors_from=None, only_missing_from=str(preds_file(row, part)), qset=p["qset"], subset=None,
        dataset=str(p["dataset"]), single_question=False, compact=False, raw=None,
        prompt_file=str(HAIKU_PROMPT) if row == "haiku" else None)


def command(row: str, part: str, concurrency: int = 8) -> str:
    a = predict_args(row, part, concurrency)
    rel = preds_file(row, part).relative_to(results_root().parent)
    s = (f"mergeone predict --model {a.model} --qset {a.qset} --split {a.split} --dataset {a.dataset} --run {a.run} "
         f"--concurrency {a.concurrency} --append --only-missing-from {rel}")
    return s + (f" --prompt-file {a.prompt_file}" if a.prompt_file else "")


def missing(row: str, part: str) -> tuple[int, int]:
    """(rows of the part's input, rows not yet answered without error) — what a run would ask."""
    p = PARTS[part]
    path = Path(results_root().parent) / p["dataset"]
    if not path.exists():
        return 0, 0
    keys = []
    for l in path.open():
        if l.strip():
            r = json.loads(l)
            if r["split"] == p["split"]:
                keys.append(r["pr_number"])
    f = preds_file(row, part)
    done = set()
    if f.exists():
        for l in f.open():
            if l.strip():
                r = json.loads(l)
                if r.get("qset") == p["qset"] and r.get("mode", "joint") == "joint" and r["decision"].get("error") is None:
                    done.add(r["pr_number"])
    return len(keys), sum(1 for k in keys if k not in done)


# ----------------------------------------------------------------- commands
def cmd_list(a) -> None:
    for row, r in ROWS.items():
        print(f"# {row} ({r['model']}, {'hosted' if r['hosted'] else 'self-hosted'}) → results/{RUN}/{row}/")
        print(f"python -m mergeone.eval.run10_rows run {row} {FROZEN_FLAG}")
        for part in r["parts"]:
            print(f"   {part:9s} = {command(row, part)}")


def cmd_run(a) -> None:
    row = a.row
    parts = a.part or list(ROWS[row]["parts"])
    bad = [p for p in parts if p not in ROWS[row]["parts"]]
    if bad:
        raise SystemExit(f"row {row} has no part {', '.join(bad)} (parts: {', '.join(ROWS[row]['parts'])})")
    if a.dry_run:
        for part in parts:
            n, todo = missing(row, part)
            print(f"[dry run] {row}/{part}: {todo} of {n} rows to ask\n  {command(row, part, a.concurrency)}")
        return
    if not a.frozen:
        raise SystemExit(f"REFUSING to call any model: pass {FROZEN_FLAG} only after PROTOCOL.md §R10 is committed and tagged "
                         "(r10-frozen) and the run-10 tooling commit is pushed.")
    from .. import cli
    for part in parts:
        p = PARTS[part]
        if not (results_root().parent / p["dataset"]).exists():
            raise SystemExit(f"{p['dataset']} missing (data/run3/dataset.jsonl is gitignored: copy it over)")
        print(f"=== {row}/{part}: {command(row, part, a.concurrency)}", flush=True)
        cli.cmd_predict(predict_args(row, part, a.concurrency))


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def set_aside(row: str, part: str, root: Path | None = None, stamp: str | None = None) -> dict:
    """Move the part's rows (qset + split) and its meta file into <row>/passes/<UTC>/; other rows stay in place."""
    p = PARTS[part]
    f, m = preds_file(row, part, root), meta_file(row, part, root)
    dest = row_dir(row, root) / "passes" / (stamp or _utc())
    moved, kept = [], []
    if f.exists():
        for l in f.open():
            if not l.strip():
                continue
            r = json.loads(l)
            (moved if r.get("qset") == p["qset"] and r.get("split") == p["split"] else kept).append(l if l.endswith("\n") else l + "\n")
    if moved:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f.name).write_text("".join(moved))
        f.write_text("".join(kept))
    if m.exists():
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(m), str(dest / m.name))
    for extra in TIMED_FILES if part == TIMED_PART else ():
        e = row_dir(row, root) / extra
        if e.exists():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(e), str(dest / e.name))
    return {"moved_rows": len(moved), "kept_rows": len(kept), "to": str(dest) if (moved or m.exists() or dest.exists()) else None}


def cmd_set_aside(a) -> None:
    print(json.dumps(set_aside(a.row, a.part)))


def _get(url: str, timeout: float = 10.0) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # recorded, never fatal
        return {"error": f"{type(e).__name__}: {e}"}


def base_url(endpoint: str) -> str:
    return endpoint.split("/v1/")[0]


def snapshot(endpoint: str = D3_ENDPOINT) -> dict:
    b = base_url(endpoint)
    return {"at": datetime.now(timezone.utc).isoformat(), "health": _get(b + "/health"), "models": _get(b + "/v1/models")}


def _gpu() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
        name, drv, tot, used = [x.strip() for x in out[0].split(",")]
        return {"gpu_name": name, "driver": drv, "memory_total": tot, "memory_used_at_start": used}
    except Exception as e:
        return {"gpu_name": None, "gpu_error": f"{type(e).__name__}: {e}"}


def serve_meta(endpoint: str, usd_per_hour: float | None, vm_type: str | None, revision: str | None, snapshot_dir: str | None,
               batch_size: int = 8) -> dict:
    s = snapshot(endpoint)
    return {"row": "d3", "weights": "vllm-sr/d3-flash", "revision": revision, "snapshot_dir": snapshot_dir, "endpoint": endpoint,
            "server": "d3_server.py (package's own)", "batch_size": batch_size, "dtype": "bf16 (vendor default; §R10.4)",
            "served_as": (s["health"] or {}).get("model"), "usd_per_hour": usd_per_hour, "vm_type": vm_type,
            "started_at": s["at"], "health": s["health"], "v1_models": s["models"], **_gpu()}


def _write_json(path: str, d: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2) + "\n")
    print(p)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run10_rows")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("run", help="ask one row's parts (guarded, resumable)")
    s.add_argument("row", choices=list(ROWS))
    s.add_argument("--part", action="append", choices=list(PARTS), help="repeatable; default: every part of the row, in order")
    s.add_argument("--concurrency", type=int, default=8)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument(FROZEN_FLAG, dest="frozen", action="store_true")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("set-aside")
    s.add_argument("row", choices=list(ROWS))
    s.add_argument("--part", required=True, choices=list(PARTS))
    s.set_defaults(fn=cmd_set_aside)
    s = sub.add_parser("snapshot")
    s.add_argument("--out", required=True)
    s.add_argument("--endpoint", default=os.environ.get("MERGEONE_D3_ENDPOINT", D3_ENDPOINT))
    s.set_defaults(fn=lambda a: _write_json(a.out, snapshot(a.endpoint)))
    s = sub.add_parser("serve-meta")
    s.add_argument("--out", required=True)
    s.add_argument("--endpoint", default=os.environ.get("MERGEONE_D3_ENDPOINT", D3_ENDPOINT))
    s.add_argument("--usd-per-hour", type=float)
    s.add_argument("--vm-type")
    s.add_argument("--revision")
    s.add_argument("--snapshot-dir")
    s.add_argument("--batch-size", type=int, default=8)
    s.set_defaults(fn=lambda a: _write_json(a.out, serve_meta(a.endpoint, a.usd_per_hour, a.vm_type, a.revision, a.snapshot_dir, a.batch_size)))
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
