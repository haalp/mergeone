import json, tempfile, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mergeone.gate.engine import Gate, Calibrator
from mergeone.models.mock import MockModel
from mergeone.server.store import Store
from mergeone.server.plan import landing_plan

POLICY = Path(__file__).resolve().parents[1] / "mergeone" / "gate" / "policy.yaml"


def _gate(tmp, **kw):
    import yaml
    pol = yaml.safe_load(POLICY.read_text()); pol["model"] = "mock"
    return Gate(pol, MockModel(latency_ms=0, **kw), Calibrator({}), log_path=Path(tmp) / "log.jsonl")


def test_policy_first_match_and_log():
    with tempfile.TemporaryDirectory() as d:
        g = _gate(d)
        r = g.decide("PULL REQUEST TITLE: x\nDIFF: y", ["pkg/a.go"])
        assert r.verdict in ("pass", "hold", "block") and r.rule
        rows = [json.loads(l) for l in (Path(d) / "log.jsonl").open()]
        assert rows[0]["input_sha256"] == r.input_sha256 and "state" in rows[0]


def test_always_human_paths_precede_model():
    with tempfile.TemporaryDirectory() as d:
        g = _gate(d)
        r = g.decide("anything", ["pkg/apis/core/types.go"])
        assert r.rule == "always_human_paths" and r.verdict == "hold" and r.probs == {}


def test_fail_closed_on_model_error():
    with tempfile.TemporaryDirectory() as d:
        g = _gate(d, error_rate=1.0)
        r = g.decide("anything", ["pkg/a.go"])
        assert r.rule == "on_error" and r.verdict == "hold" and r.error


def test_idempotent_jobs_and_decisions():
    with tempfile.TemporaryDirectory() as d:
        s = Store(Path(d) / "g.db")
        assert s.enqueue("r:1:a", {}) and not s.enqueue("r:1:a", {})
        g = _gate(d)
        r1 = g.decide("same", []); r2 = g.decide("same", [])
        assert r1.probs == r2.probs  # mock is deterministic on input


def test_landing_plan_waves():
    prs = [{"pr": 1, "head_sha": "a", "paths": ["x.go"], "updated": 1}, {"pr": 2, "head_sha": "b", "paths": ["x.go"], "updated": 2},
           {"pr": 3, "head_sha": "c", "paths": ["y.go"], "updated": 3}]
    p = landing_plan(prs)
    assert p["waves"] == [[1, 3], [2]] and p["conflict_edges"][0]["kind"] == "file"


def test_calibrator_interp():
    c = Calibrator({"q": {"x": [0, 1], "y": [0.2, 0.8]}})
    assert abs(c({"q": 0.5})["q"] - 0.5) < 1e-9 and abs(c({"q": 0.0})["q"] - 0.2) < 1e-9
