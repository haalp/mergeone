"""Run 6 part A: serve perplexity-ai/pplx-decider-flash-v1.2 behind the run 1–5 System One wire schema.

    POST /v1/systemone   {state, model, questions{name: {type: noul|choice|score, instructions, criteria?}}}
                      →  {model: "pplx-decider-flash-v1.2@<revision>", answers{name: {...}}, usage{input_tokens, output_tokens, ...}}
    GET  /healthz        {ok, model, batch_max, batch_ms, stats, fast_options}
    GET  /meta           serve_meta.json as written at startup (model, revision, GPU, fast options in effect, …)

PROTOCOL.md R6A.2: the vendor's own `pplx_decider` package does the inference, unmodified (`Decider.from_pretrained(...,
fast=True)`, BF16, no quantisation). This wrapper changes no prompt, question or temperature: the request's `state` and
`questions` are handed to `decider.decide` as they arrive (the `model` field is dropped; the vendor request has none), and
the vendor's answers are returned with only a `type` field added where missing. Concurrent requests are micro-batched
(≤ --batch-max requests or --batch-ms since the oldest queued request, whichever first) into one `decider.decide(list)`
call on a single worker thread, so the GPU runs one batch at a time.

Pass 2 (--batch-tokens N, off by default): requests are batched by *length*. A batch is padded to its longest request, so
a 500-token PR next to a 16k-token PR costs 16k; pass 1 (run 6A, 2026-10-18) formed batches in arrival order and spent
most of the L4 on padding (~5k tokens/s of a ~15k possible). With a budget, the batcher picks, from everything queued,
the requests closest in length to the oldest one such that `count × longest ≤ N` estimated tokens (chars / 4; no
tokenizer in the server); the rest wait for the next flush. The vendor call, prompts, questions and temperature are
unchanged; only which requests share a forward pass. /healthz reports padded vs useful token estimates.

Run 9 (--fast-options, off by default): the vendor's `FastOptions` (pplx_decider 0.3.0, `model.py`: a frozen dataclass with
compile, cuda_graphs, buckets, token_budget, prune_last, fp8, prefetch) reach the decider through
`Decider.from_pretrained(ckpt, device=…, fast=FastOptions(**options))`; absent = `fast=True` = `FastOptions()` defaults, i.e.
the run-6A behaviour. The options in effect (read back from the loaded decider) are written to serve_meta.json and served at
GET /meta, so every results directory can show which row it was. FP8 (`fp8: true`) is the R9-H3 row (§R9.4 exception).

    --fast-options '{"fp8": true}'                       JSON object
    --fast-options fp8=true buckets=2560,3072,4096,8192,16384,32768     key=value list (values parsed as JSON; a comma list → list)

Errors: the vendor's RequestError / LengthError (and malformed requests) → HTTP 400; anything else → 500. A failing batch
is retried request by request so one bad request cannot fail the others, and the batcher never stops on an exception.

Not a dependency of the mergeone package: `pip install -r serve/requirements.txt` plus the checkpoint's own
`pip install "<checkpoint_dir>/source[fast]"` (see scripts/run6_gpu.sh, docs/run6-gpu.md).

    python serve/systemone_server.py --download-only                 # print the checkpoint dir (HF_TOKEN; gated repo)
    python serve/systemone_server.py --port 8080 --vm-type g6.xlarge --usd-per-hour 0.8048
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import platform
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request  # module level: FastAPI resolves the handler's string annotations from module globals
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "perplexity-ai/pplx-decider-flash-v1.2"
DEFAULT_META = ROOT / "results" / "run6" / "serve_meta.json"
QUESTION_TYPES = ("noul", "choice", "score")
CLIENT_ERROR_NAMES = ("RequestError", "LengthError")  # pplx_decider's request-validation exceptions (matched by class name)

log = logging.getLogger("systemone_server")


# ----------------------------------------------------------------- request / response
class BadRequest(Exception):
    """Malformed request caught before it reaches the decider."""


def status_for(exc: BaseException) -> int:
    if isinstance(exc, BadRequest):
        return 400
    if any(c.__name__ in CLIENT_ERROR_NAMES for c in type(exc).__mro__):
        return 400
    return 500


def to_vendor_request(body: Any) -> dict:
    """Run 1–5 request → the vendor's request dict {state, questions}. Questions are passed through unchanged."""
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    state, questions = body.get("state"), body.get("questions")
    if not isinstance(state, str):
        raise BadRequest("`state` must be a string")
    if not isinstance(questions, dict) or not questions:
        raise BadRequest("`questions` must be a non-empty object")
    for name, q in questions.items():
        if not isinstance(q, dict):
            raise BadRequest(f"question {name!r} must be an object")
        if q.get("type") not in QUESTION_TYPES:
            raise BadRequest(f"question {name!r}: type must be one of {', '.join(QUESTION_TYPES)}")
        if not isinstance(q.get("instructions"), str):
            raise BadRequest(f"question {name!r}: `instructions` must be a string")
    return {"state": state, "questions": questions}


def _jsonable(x: Any) -> Any:
    """numpy / torch scalars → float/int so the response serialises."""
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if x is None or isinstance(x, (str, bool, int, float)):
        return x
    if hasattr(x, "item"):
        return _jsonable(x.item())
    if hasattr(x, "__float__"):
        return float(x)
    return str(x)


def to_answer_schema(req: dict, out: Any, model_name: str) -> dict:
    """Vendor response → run 1–5 answer schema {model, answers, usage}."""
    if not isinstance(out, dict):
        raise RuntimeError(f"decider returned {type(out).__name__}, expected a dict")
    got = out.get("answers") if isinstance(out.get("answers"), dict) else out
    answers = {}
    for name, q in req["questions"].items():
        a = got.get(name)
        if not isinstance(a, dict):
            raise RuntimeError(f"decider returned no answer for question {name!r}")
        a = _jsonable(a)
        a.setdefault("type", q["type"])
        answers[name] = a
    usage = _jsonable(dict(out.get("usage") or {}))
    usage.setdefault("input_tokens", 0)
    usage.setdefault("output_tokens", 0)
    return {"model": model_name, "answers": answers, "usage": usage}


# ----------------------------------------------------------------- micro-batcher
CHARS_PER_TOKEN = 4.0  # rough estimate for the batch budget; usage in responses still comes from the vendor


def estimate_tokens(req: dict) -> int:
    n = len(req.get("state", ""))
    for q in (req.get("questions") or {}).values():
        n += len(q.get("instructions", "")) + sum(len(str(c)) for c in (q.get("criteria") or {}).values())
    return max(1, int(n / CHARS_PER_TOKEN))


@dataclass
class _Item:
    request: dict
    fut: asyncio.Future
    t_enq: float
    tokens: int = 1


def select_batch(pending: list["_Item"], batch_max: int, batch_tokens: int) -> list["_Item"]:
    """Pass-2 selection: the oldest request always goes; the rest are the queued requests nearest to it in length while
    `count × longest ≤ batch_tokens` and `count ≤ batch_max`. Deterministic; returns items in arrival order."""
    oldest = min(pending, key=lambda it: it.t_enq)
    if batch_tokens <= 0:
        return sorted(pending, key=lambda it: it.t_enq)[:batch_max]
    bylen = sorted(pending, key=lambda it: (it.tokens, it.t_enq))
    i = bylen.index(oldest)
    lo = hi = i
    longest = oldest.tokens
    grow_up = True
    while hi - lo + 1 < batch_max:
        down = lo - 1 if lo > 0 else None
        up = hi + 1 if grow_up and hi + 1 < len(bylen) else None
        if down is None and up is None:
            break
        take_up = up is not None and (down is None or bylen[up].tokens - oldest.tokens <= oldest.tokens - bylen[down].tokens)
        if take_up:
            if (hi - lo + 2) * bylen[up].tokens > batch_tokens:
                grow_up = False  # anything longer only pads more; shorter requests may still fit
                continue
            hi, longest = up, bylen[up].tokens
        else:
            if (hi - lo + 2) * longest > batch_tokens:
                break  # a shorter request does not reduce the padded width
            lo = down
    return sorted(bylen[lo:hi + 1], key=lambda it: it.t_enq)


@dataclass
class BatchStats:
    batches: int = 0
    requests: int = 0
    max_batch: int = 0
    batch_errors: int = 0
    request_errors: int = 0
    decide_s: float = 0.0
    sizes: dict = field(default_factory=dict)
    useful_tokens_est: int = 0  # sum of per-request estimates
    padded_tokens_est: int = 0  # sum over batches of count × longest

    def as_dict(self) -> dict:
        return {"batches": self.batches, "requests": self.requests, "max_batch": self.max_batch, "batch_errors": self.batch_errors,
                "request_errors": self.request_errors, "mean_batch": self.requests / self.batches if self.batches else None,
                "decide_s": round(self.decide_s, 3), "batch_size_histogram": dict(sorted(self.sizes.items())),
                "useful_tokens_est": self.useful_tokens_est, "padded_tokens_est": self.padded_tokens_est,
                "padding_efficiency": round(self.useful_tokens_est / self.padded_tokens_est, 3) if self.padded_tokens_est else None}


class MicroBatcher:
    """asyncio queue → one `decider.decide(list)` per flush on a single worker thread.

    A flush happens when `batch_max` requests are queued or `batch_ms` after the oldest queued request arrived,
    whichever first. While the GPU works, new requests queue; the next batch forms from them immediately."""

    def __init__(self, decider, batch_max: int = 32, batch_ms: float = 20.0, batch_tokens: int = 0):
        self.decider = decider
        self.batch_max = max(1, int(batch_max))
        self.batch_s = max(0.0, float(batch_ms)) / 1000.0
        self.batch_tokens = max(0, int(batch_tokens))  # 0 = pass-1 behaviour (arrival order, count only)
        self._pending: list[_Item] = []
        self.stats = BatchStats()
        self._q: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="decider")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._q = self._q or asyncio.Queue()
            self._task = asyncio.get_running_loop().create_task(self._run(), name="micro-batcher")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def submit(self, request: dict) -> Any:
        self.start()
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        await self._q.put(_Item(request, fut, loop.time(), estimate_tokens(request)))
        return await fut

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                pending = self._pending
                if not pending:
                    pending.append(await self._q.get())
                deadline = min(it.t_enq for it in pending) + self.batch_s
                while len(pending) < self.batch_max or self.batch_tokens > 0:  # with a budget, see everything queued before choosing
                    try:
                        pending.append(self._q.get_nowait())
                        continue
                    except asyncio.QueueEmpty:
                        pass
                    rem = deadline - loop.time()
                    if rem <= 0:
                        break
                    try:
                        pending.append(await asyncio.wait_for(self._q.get(), rem))
                    except asyncio.TimeoutError:
                        break
                batch = select_batch(pending, self.batch_max, self.batch_tokens)
                chosen = set(map(id, batch))
                self._pending = [it for it in pending if id(it) not in chosen]
                await self._flush(batch)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the batcher die
                log.exception("micro-batcher loop error")

    async def _decide(self, reqs: list[dict]) -> list:
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        try:
            outs = await loop.run_in_executor(self._executor, self.decider.decide, reqs)
        finally:
            self.stats.decide_s += time.perf_counter() - t0
        if not isinstance(outs, list) or len(outs) != len(reqs):
            raise RuntimeError(f"decider returned {type(outs).__name__} of length {len(outs) if isinstance(outs, list) else '-'} for {len(reqs)} requests")
        return outs

    async def _flush(self, batch: list[_Item]) -> None:
        live = [it for it in batch if not it.fut.done()]  # a client that went away cancels its future
        if not live:
            return
        reqs = [it.request for it in live]
        self.stats.batches += 1
        self.stats.requests += len(live)
        self.stats.max_batch = max(self.stats.max_batch, len(live))
        self.stats.sizes[len(live)] = self.stats.sizes.get(len(live), 0) + 1
        self.stats.useful_tokens_est += sum(it.tokens for it in live)
        self.stats.padded_tokens_est += len(live) * max(it.tokens for it in live)
        try:
            results = [(o, None) for o in await self._decide(reqs)]
        except Exception as e:
            self.stats.batch_errors += 1
            if len(live) == 1:
                results = [(None, e)]
            else:  # isolate the bad request(s): one by one, so the rest of the batch still gets answers
                log.warning("batch of %d failed (%s: %s); retrying one by one", len(live), type(e).__name__, e)
                results = []
                for r in reqs:
                    try:
                        results.append(((await self._decide([r]))[0], None))
                    except Exception as e2:
                        results.append((None, e2))
        for it, (out, err) in zip(live, results):
            if it.fut.done():
                continue
            if err is not None:
                self.stats.request_errors += 1
                it.fut.set_exception(err)
            else:
                it.fut.set_result(out)


# ----------------------------------------------------------------- app
def create_app(decider, model_name: str, batch_max: int = 32, batch_ms: float = 20.0, meta: dict | None = None, batch_tokens: int = 0):
    batcher = MicroBatcher(decider, batch_max, batch_ms, batch_tokens)

    @asynccontextmanager
    async def lifespan(app):
        batcher.start()
        yield
        await batcher.stop()

    app = FastAPI(title="systemone (pplx-decider)", lifespan=lifespan)
    app.state.batcher = batcher
    app.state.model_name = model_name

    def _err(status: int, exc: BaseException) -> JSONResponse:
        return JSONResponse({"error": {"type": type(exc).__name__, "message": str(exc)[:1000], "status": status}}, status_code=status)

    @app.post("/v1/systemone")
    async def systemone(request: Request):
        t0 = time.perf_counter()
        try:
            body = json.loads(await request.body())
        except Exception as e:
            return _err(400, BadRequest(f"invalid JSON: {e}"))
        try:
            req = to_vendor_request(body)
            out = await batcher.submit(req)
            resp = to_answer_schema(req, out, model_name)
        except Exception as e:
            st = status_for(e)
            if st >= 500:
                log.error("decide failed: %s: %s", type(e).__name__, e)
            return _err(st, e)
        return JSONResponse(resp, headers={"X-Server-Ms": f"{(time.perf_counter() - t0) * 1000:.1f}"})

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "model": model_name, "batch_max": batcher.batch_max, "batch_ms": batcher.batch_s * 1000, "batch_tokens": batcher.batch_tokens,
                "stats": batcher.stats.as_dict(), **({"gpu": meta.get("gpu_name"), "fast_options": meta.get("fast_options_effective"),
                                                     "dtype": meta.get("dtype")} if meta else {})}

    @app.get("/meta")
    async def meta_endpoint():
        return {**(meta or {}), "batch_max": batcher.batch_max, "batch_ms": batcher.batch_s * 1000, "batch_tokens": batcher.batch_tokens}

    return app


# ----------------------------------------------------------------- run 9: vendor FastOptions
# Fields of pplx_decider.FastOptions as published in 0.3.0 (source/src/pplx_decider/model.py). Used to validate the flag before
# the vendor package is imported (and in tests, where it is not installed); at load time the installed dataclass's own fields win.
FAST_OPTION_FIELDS = ("compile", "cuda_graphs", "buckets", "token_budget", "prune_last", "fp8", "prefetch")
FAST_OPTIONS_SIGNATURE = "pplx_decider 0.3.0: Decider.from_pretrained(path, *, device, fast: bool | FastOptions = False, …)"


def _parse_value(v: str) -> Any:
    v = v.strip()
    try:
        return json.loads(v)
    except ValueError:
        pass
    if "," in v:
        return [_parse_value(x) for x in v.split(",") if x.strip()]
    low = v.lower()
    if low in ("none", "null"):
        return None
    return v


def parse_fast_options(tokens: list[str] | str | None) -> dict | None:
    """`--fast-options` → {field: value}. Accepts one JSON object, or `key=value` items (separated by spaces, `;` or given as
    several arguments). Values are JSON when they parse (true, 2560, [1, 2], "layer"); `a,b,c` is a list. `buckets` becomes a
    tuple of ints (the dataclass default is a tuple). Unknown keys are refused here against the 0.3.0 field list."""
    if tokens is None:
        return None
    text = " ".join(tokens) if isinstance(tokens, (list, tuple)) else str(tokens)
    text = text.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            opts = json.loads(text)
        except ValueError as e:
            raise ValueError(f"--fast-options: invalid JSON: {e}") from e
        if not isinstance(opts, dict):
            raise ValueError("--fast-options: JSON must be an object")
    else:
        opts = {}
        for item in text.replace(";", " ").split():
            if "=" not in item:
                raise ValueError(f"--fast-options: expected key=value, got {item!r}")
            k, v = item.split("=", 1)
            opts[k.strip()] = _parse_value(v)
    unknown = sorted(set(opts) - set(FAST_OPTION_FIELDS))
    if unknown:
        raise ValueError(f"--fast-options: unknown field(s) {', '.join(unknown)}; FastOptions (pplx_decider 0.3.0) has {', '.join(FAST_OPTION_FIELDS)}")
    if "buckets" in opts:
        b = opts["buckets"]
        b = [b] if isinstance(b, int) else b
        if not isinstance(b, (list, tuple)) or not b or not all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in b):
            raise ValueError("--fast-options: buckets must be a non-empty list of positive integers")
        opts["buckets"] = tuple(b)
    for k in ("cuda_graphs", "prune_last", "fp8", "prefetch"):
        if k in opts and not isinstance(opts[k], bool):
            raise ValueError(f"--fast-options: {k} must be true or false")
    if "token_budget" in opts and (not isinstance(opts["token_budget"], int) or isinstance(opts["token_budget"], bool) or opts["token_budget"] < 1):
        raise ValueError("--fast-options: token_budget must be a positive integer")
    if "compile" in opts and opts["compile"] not in (None, "sub", "layer"):
        raise ValueError("--fast-options: compile must be null, \"sub\" or \"layer\"")
    return opts


def options_dict(obj: Any) -> dict | None:
    """A FastOptions instance (or any dataclass) → JSON-able dict; None stays None."""
    if obj is None:
        return None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return _jsonable(obj)
    return {"repr": repr(obj)}


def make_fast_options(opts: dict, fast_options_cls: Any) -> Any:
    """{field: value} → the installed FastOptions. Fails with the documented signature in the message if the installed vendor
    package differs (no such class, or a field it does not have)."""
    if fast_options_cls is None:
        raise RuntimeError("--fast-options: the installed pplx_decider has no FastOptions (written against " + FAST_OPTIONS_SIGNATURE +
                           "); read the installed model.py / fast.py and record the difference in CHANGELOG before any run-9 call")
    if dataclasses.is_dataclass(fast_options_cls):
        have = {f.name for f in dataclasses.fields(fast_options_cls)}
        missing = sorted(set(opts) - have)
        if missing:
            raise RuntimeError(f"--fast-options: installed FastOptions has no field(s) {', '.join(missing)} (has {', '.join(sorted(have))}); "
                               f"written against {FAST_OPTIONS_SIGNATURE}")
    return fast_options_cls(**opts)


def effective_fast_options(decider: Any) -> dict | None:
    """The options the loaded decider actually runs with: `decider.fast.options` (0.3.0: FastPath keeps them), None on the
    reference path."""
    fp = getattr(decider, "fast", None)
    if fp is None:
        return None
    opts = getattr(fp, "options", None)
    return options_dict(opts) if opts is not None else {"unknown": "decider.fast has no `options` attribute"}


def dtype_label(effective: dict | None) -> str:
    if effective and effective.get("fp8"):
        return "fp8 (vendor FastOptions(fp8=True): rowwise-scaled float8_e4m3fn decoder matmuls; bf16 elsewhere)"
    return "bf16 (vendor default; no quantisation)"


# ----------------------------------------------------------------- startup: checkpoint, decider, meta
def download(model: str, revision: str | None) -> Path:
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(model, revision=revision or None, token=os.environ.get("HF_TOKEN") or None))


def resolved_revision(ckpt: Path, requested: str | None) -> str:
    """snapshot_download returns …/snapshots/<commit>; that commit is the pinned revision."""
    if ckpt.parent.name == "snapshots":
        return ckpt.name
    return requested or "unknown"


def load_decider(ckpt: Path, fast: bool, device: str = "cuda", fast_options: dict | None = None):
    import pplx_decider  # lazy: only on the GPU VM, after `pip install "<ckpt>/source[fast]"`
    Decider = pplx_decider.Decider
    if fast_options is None:  # runs 6A–8 and every run-9 control row: unchanged call
        return Decider.from_pretrained(str(ckpt), device=device, fast=fast)
    if not fast:
        raise ValueError("--fast-options needs --fast (the options configure the vendor fast path)")
    fo = make_fast_options(fast_options, getattr(pplx_decider, "FastOptions", None))
    return Decider.from_pretrained(str(ckpt), device=device, fast=fo)


def _version(*dists: str) -> str | None:
    from importlib import metadata
    for d in dists:
        try:
            return metadata.version(d)
        except metadata.PackageNotFoundError:
            continue
    return None


def ec2_metadata(timeout: float = 0.5) -> dict:
    """Instance type / region / AZ from EC2 IMDSv2, or {} when not on EC2 (RunPod, laptop)."""
    base = "http://169.254.169.254/latest"
    try:
        tok = urllib.request.urlopen(urllib.request.Request(f"{base}/api/token", method="PUT",
                                     headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}), timeout=timeout).read().decode()
        get = lambda p: urllib.request.urlopen(urllib.request.Request(f"{base}/meta-data/{p}", headers={"X-aws-ec2-metadata-token": tok}),
                                               timeout=timeout).read().decode()
        return {"instance_type": get("instance-type"), "region": get("placement/region"), "availability_zone": get("placement/availability-zone")}
    except Exception:
        return {}


def build_meta(args, ckpt: Path, revision: str, model_name: str, warmup_s: float | None, fast_effective: dict | None = None) -> dict:
    gpu = cuda = torch_v = None
    try:
        import torch
        torch_v, cuda = torch.__version__, torch.version.cuda
        gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:
        pass
    ec2 = ec2_metadata()
    dc = ckpt / "decision_config.json"
    return {
        "model": args.model, "revision": revision, "served_as": model_name, "checkpoint_dir": str(ckpt),
        "decision_config": json.loads(dc.read_text()) if dc.exists() else None,
        "fast": args.fast, "device": args.device, "dtype": dtype_label(fast_effective),
        "fast_options_flag": getattr(args, "fast_options_raw", None), "fast_options_requested": options_dict(getattr(args, "fast_options", None)),
        "fast_options_effective": fast_effective,
        "batch_max": args.batch_max, "batch_ms": args.batch_ms, "batch_tokens": args.batch_tokens,
        "batching": "length-aware (pass 2)" if args.batch_tokens else "arrival order (pass 1)",
        "gpu_name": gpu, "cuda": cuda,
        "versions": {"torch": torch_v, "transformers": _version("transformers"), "pplx_decider": _version("pplx_decider", "pplx-decider"),
                     "fastapi": _version("fastapi"), "uvicorn": _version("uvicorn"), "python": sys.version.split()[0]},
        "vm_type": args.vm_type or ec2.get("instance_type"), "region": args.region or ec2.get("region"),
        "availability_zone": ec2.get("availability_zone"), "vm_type_source": "flag" if args.vm_type else ("ec2-imds" if ec2 else None),
        "usd_per_hour": args.usd_per_hour, "host": platform.node(), "warmup_s": warmup_s,
        "started_at": datetime.now(timezone.utc).isoformat(), "port": args.port,
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="systemone_server", description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Hugging Face repo id")
    ap.add_argument("--revision", default="main", help="branch, tag or commit; the resolved commit is recorded and served as <name>@<commit>")
    ap.add_argument("--checkpoint-dir", help="use this local checkpoint instead of snapshot_download")
    ap.add_argument("--download-only", action="store_true", help="download the checkpoint, print its directory, exit")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--fast", action=argparse.BooleanOptionalAction, default=True, help="vendor fast path (flash-linear-attention); R6A.2 uses --fast")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--batch-max", type=int, default=32)
    ap.add_argument("--batch-ms", type=float, default=20.0)
    ap.add_argument("--batch-tokens", type=int, default=0, help="pass 2: padded-token budget per batch (count × longest, chars/4); 0 = arrival-order batching as pass 1")
    ap.add_argument("--fast-options", nargs="+", metavar="OPT",
                    help="run 9: vendor FastOptions as a JSON object or key=value list (fp8, buckets, token_budget, compile, cuda_graphs, "
                         "prune_last, prefetch), e.g. '{\"fp8\": true}' or buckets=2560,3072,4096; absent = FastOptions() defaults")
    ap.add_argument("--vm-type", help="VM type for serve_meta.json (default: EC2 metadata if reachable)")
    ap.add_argument("--region", help="region for serve_meta.json (default: EC2 metadata if reachable)")
    ap.add_argument("--usd-per-hour", type=float, help="VM on-demand price, for the R6A cost row")
    ap.add_argument("--meta-out", default=str(DEFAULT_META))
    ap.add_argument("--access-log", action="store_true")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args(argv)
    args.fast_options_raw = " ".join(args.fast_options) if args.fast_options else None
    try:
        args.fast_options = parse_fast_options(args.fast_options)
    except ValueError as e:
        ap.error(str(e))
    if args.fast_options is not None and not args.fast:
        ap.error("--fast-options needs --fast")
    return args


def main(argv=None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ckpt = Path(args.checkpoint_dir) if args.checkpoint_dir else download(args.model, args.revision)
    if args.download_only:
        print(ckpt)
        return
    revision = resolved_revision(ckpt, args.revision)
    model_name = f"{args.model.split('/')[-1]}@{revision}"
    log.info("loading %s from %s (fast=%s, fast_options=%s)", model_name, ckpt, args.fast, args.fast_options)
    decider = load_decider(ckpt, args.fast, args.device, args.fast_options)
    fast_effective = effective_fast_options(decider)
    log.info("fast options in effect: %s", fast_effective)
    t0 = time.perf_counter()
    decider.warmup()
    warmup_s = time.perf_counter() - t0
    meta = build_meta(args, ckpt, revision, model_name, warmup_s, fast_effective)
    out = Path(args.meta_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(meta, indent=2) + "\n")
    log.info("warm in %.1fs; meta → %s; gpu=%s", warmup_s, out, meta["gpu_name"])
    import uvicorn
    uvicorn.run(create_app(decider, model_name, args.batch_max, args.batch_ms, meta, args.batch_tokens), host=args.host, port=args.port,
                log_level=args.log_level, access_log=args.access_log, workers=1)


if __name__ == "__main__":
    main()
