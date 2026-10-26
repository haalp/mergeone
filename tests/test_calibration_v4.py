import json, sys, tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mergeone.gate.engine import Calibrator
from mergeone.models.base import DECISIONS_V4, QUESTION_SETS

GATE = ROOT / "mergeone" / "gate"
MODELS = ("jev", "pplx-flash", "haiku")


@pytest.mark.parametrize("model", MODELS)
def test_v4_maps_cover_exactly_the_eight_questions(model):
    path = GATE / f"calibration_{model}_v4.json"
    raw = json.loads(path.read_text())
    assert len(DECISIONS_V4) == 8 and QUESTION_SETS["v4"] == DECISIONS_V4
    assert set(raw) - {"fit_stats"} == set(DECISIONS_V4)
    assert set(raw["fit_stats"]) == set(DECISIONS_V4)
    for q in DECISIONS_V4:
        fs = raw["fit_stats"][q]
        assert fs["n"] > 0 and 0 <= fs["mean"] <= 1 and 0 <= fs["yes_rate"] <= 1 and fs["spread"] >= 0
        assert raw[q]["x"][0] == 0 and raw[q]["x"][-1] == 1 and raw[q]["y"] == sorted(raw[q]["y"])
    cal = Calibrator.load(path)
    out = cal({q: 0.5 for q in DECISIONS_V4})
    assert set(out) == set(DECISIONS_V4) and all(0 <= v <= 1 for v in out.values())


def test_v4_prompt_matches_question_set():
    assert set(json.loads((ROOT / "prompts" / "jev_v4.json").read_text())) == set(DECISIONS_V4)


@pytest.mark.skipif(not (ROOT / "data" / "run3" / "dataset.jsonl").exists(), reason="data/run3/dataset.jsonl not present")
@pytest.mark.parametrize("model", MODELS)
def test_v4_maps_reproduce(model, monkeypatch):
    from mergeone.cli import main
    monkeypatch.chdir(ROOT)
    with tempfile.TemporaryDirectory() as d:
        main(["fit-calibration", "--run", "results/run5", "--model", model, "--qset", "v4", "--from-qset", "v3",
              "--label", "iterated", "--dataset", "data/run3/dataset.jsonl", "--out", d])
        assert (Path(d) / f"calibration_{model}_v4.json").read_text() == (GATE / f"calibration_{model}_v4.json").read_text()
