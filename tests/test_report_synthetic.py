"""Smoke test: synthetic predictions through the report pipeline."""
import json, math, random, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mergeone.eval.report import run

def make(run_dir, model, split, variant, n, signal, cost, lat, seed):
    rng = random.Random(seed)
    with (run_dir / f"predictions_{model}_{split}_{variant}.jsonl").open("w") as f:
        for i in range(n):
            y = rng.random() < 0.02
            p = min(max(rng.gauss(0.3 if y else 0.1, 0.15) * signal + rng.random() * (1 - signal) * 0.2, 0.001), 0.999)
            row = {"pr_number": i, "split": split, "labels": {"will_be_reverted": y, "touches_sensitive_area": rng.random() < 0.3, "large_blast_radius": rng.random() < 0.1},
                   "decision": {"probs": {"will_be_reverted": p, "touches_sensitive_area": rng.random(), "large_blast_radius": rng.random()}, "confidence": {},
                                "latency_ms": rng.gauss(lat, lat * 0.2), "input_tokens": 1500, "output_tokens": 20, "cost_usd": cost, "model_version": model, "error": None, "raw": {}}}
            f.write(json.dumps(row) + "\n")

def test_pipeline():
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d)
        for m, sig, cost, lat in (("rules", 0.5, 0, 0.1), ("gbm", 0.8, 0, 1), ("haiku", 0.7, 0.0002, 900), ("jev", 0.7, 0.00006, 200)):
            for v in ("primary", "all"):
                make(rd, m, "val", v, 1500, sig, cost, lat, 1)
                make(rd, m, "test", v, 1500, sig, cost, lat, 2)
        run(rd)
        mt = json.loads((rd / "metrics.json").read_text())
        assert "jev_then_haiku_primary" in mt["cascade"]
        assert mt["cascade"]["jev_then_haiku_primary"]["escalation_rate"] <= 0.26
        for m in ("rules", "gbm", "haiku", "jev"):
            t = mt["models"][m]["primary"]["primary"]["test"]
            assert not math.isnan(t["pr_auc"]) and t["threshold_source"] == "val"
        assert (rd / "reliability_primary.png").exists()
        print("OK")

if __name__ == "__main__":
    test_pipeline()
