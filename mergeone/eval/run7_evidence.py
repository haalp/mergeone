"""Run 7 C3: structural evidence features (docs/run7-addendum-draft.md R7.2 C3, criterion R7-H3). No model calls.

  python -m mergeone.eval.run7_evidence features
        data/run3/dataset.jsonl → results/run7/evidence_features.jsonl
        one row per PR: {pr_number, split, features{...}, patterns_sha256}; no labels in the file
  python -m mergeone.eval.run7_evidence score --target iteration|rejection [--model M ...] [--probs path.jsonl]
        --i-have-frozen-the-protocol
        logistic regression on the val repositories only → weights frozen to results/run7/evidence_weights_<target>.json
        (an existing weights file is reused as is, never refit), ROC-AUC on the test repositories, plus "stacked" =
        logistic on [calibrated needs_human_review, evidence features], fitted on val, scored on test.
        → results/run7/evidence_metrics_<target>.json

Inputs are only the fields of a data/run3/dataset.jsonl row. Those rows carry the rendered `state`, not separate
title/body/files fields, so parse_record() reads them back from the state (render_state format): title, body (the
state holds its first 1,500 characters), the FILES CHANGED block (path +ins -del; at most 60 files are listed, the
"... and N more" remainder has no paths) and the diff (first 4,000 characters). Features are therefore computed on
what the models saw. A record that does carry `title` / `body` / `files` [{path, insertions, deletions}] /
`diff_head` (e.g. a raw agent_prs.jsonl row) is read from those fields instead.

Targets (labels of data/run3/dataset.jsonl): iteration = `iterated` (run-5 review-iteration target, R5-H5),
rejection = `rejected` (run-3 R3-H1 target).

Calibrated needs_human_review (stacking input), per model with run-5 v3 rows (results/run5/predictions_<model>_*.jsonl,
qset v3, error-free): isotonic on val raw answers vs the score's target, applied to val (in-sample) and test — the
run-5 R5-H5 `nhr_v3cal` procedure (run5._iso). `--probs` replaces this with a JSONL of {pr_number, prob} taken as
already calibrated (it must cover val and test PRs).

Logistic model (fixed before scoring): StandardScaler + LogisticRegression(C=1.0, lbfgs, max_iter=2000), as run 5's
stack. The frozen JSON holds the scaler and the coefficients, and predict() applies them with numpy, so scoring
needs nothing but the JSON. R7-H3: evidence-only test AUC on iteration ≥ 0.76, or stacked − needs_human_review-alone
≥ +0.02 on the same test PRs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

DATASET = Path("data/run3/dataset.jsonl")  # relative to cwd (repo root), as runs 3–6
RUN5_DIR = Path("results/run5")
RUN_DIR = Path("results/run7")
TARGETS = {"iteration": "iterated", "rejection": "rejected"}
H3_BAR_AUC, H3_BAR_STACK_GAIN = 0.76, 0.02
STACK_MODELS = ("jev", "pplx", "pplx-flash", "haiku")
FROZEN_FLAG = "--i-have-frozen-the-protocol"

# ----------------------------------------------------------------- patterns
# frozen before scoring (R7.4)
TEST_PATH_PATTERNS = (
    r"(^|/)(tests?|__tests__|__test__|spec|specs|testing|testdata|test_data|fixtures)/",
    r"(^|/)(e2e|integration[-_]?tests?|unit[-_]?tests?)/",
    r"_test\.(go|py|rs|exs?|dart|cc|cpp|c)$",
    r"(^|/)test_[^/]+\.py$",
    r"(^|/)conftest\.py$",
    r"\.(test|spec|e2e|cy)\.(c|m)?[jt]sx?$",
    r"(Test|Tests|Spec|IT)\.(cs|java|kt|kts|swift|scala|php)$",
    r"_spec\.rb$",
    r"(^|/)[^/]*\.test\.[^/]+$",
)
# frozen before scoring (R7.4)
E2E_PROPERTY_PATH_PATTERNS = (
    r"(^|/|[._-])(e2e|end[-_]?to[-_]?end|integration)([/._-]|$)",
    r"(^|/)(playwright|cypress)(/|\.config\.)",
    r"\.cy\.[jt]sx?$",
    r"(^|/|[._-])(fuzz|fuzzing|property|properties|proptest|proptests|hypothesis|quickcheck)([/._-]|$)",
)
# frozen before scoring (R7.4) — matched against added diff lines (lines starting with "+")
E2E_PROPERTY_CONTENT_PATTERNS = (
    r"@playwright/test|\bfrom ['\"]playwright|\bpage\.goto\(",
    r"\bcy\.(visit|get|request)\(|\bcypress\b",
    r"\bfrom hypothesis\b|\bimport hypothesis\b|@given\(|\bhypothesis\.strategies\b",
    r"\bfast-check\b|\bfc\.(assert|property|asyncProperty)\(",
    r"\bproptest!|\bproptest::|\bquickcheck\b",
    r"\bfunc Fuzz\w*\(|\*testing\.F\b",
    r"\bjqwik\b|@Property\b|\bFsCheck\b",
)
# frozen before scoring (R7.4) — "how tested" / verification sections and test-run output in the PR body
BODY_TEST_SECTION_PATTERNS = (
    r"(?im)^\s{0,3}(#{1,6}\s*|\*\*|__)?\s*(how (was this |it was |i |we )?(was )?test(ed)?|testing( done| performed| notes| instructions)?|"
    r"tests? (plan|run|results?)|verification|validation|how (was this |it was )?verified|verified (by|with|locally))\b",
)
BODY_TEST_OUTPUT_PATTERNS = (
    r"(?i)\b\d+\s+(tests?\s+)?(passed|passing)\b",
    r"(?im)^\s*(tests?|specs?|suites?|test files)\s*:\s*\d+",
    r"(?im)^\s*ok\s+\S+\s+\d+(\.\d+)?s\b",          # go test
    r"(?m)^\s*(---\s+)?(PASS|FAIL)\b",               # go test / generic runners (case-sensitive)
    r"(?i)\btest result: ok\.",                      # cargo test
    r"(?i)\bRan \d+ tests? in\b",                    # unittest
    r"(?i)=+ \d+ passed",                            # pytest summary
    r"(?i)\bcoverage\b[^\n]{0,40}?\d+(\.\d+)?\s?%",  # coverage line
    r"(?i)\ball (\d+ )?tests? pass(ed|ing)?\b",
)
# frozen before scoring (R7.4) — CI check / tool names listed in the PR body
BODY_CI_PATTERNS = (
    r"(?i)\b(CI|checks?|pipeline|workflows?|github actions)\b[^\n]{0,40}?\b(pass(es|ed|ing)?|green|succeed(s|ed)?)\b",
    r"(?i)\b(npm|pnpm|yarn|bun)\s+(run\s+)?(test|lint|typecheck|type-check|build|check|format)\b",
    r"(?i)\bmake\s+(test|lint|check|verify|ci)\b",
    r"(?i)\b(go (test|vet|build)|cargo (test|clippy|fmt|check)|pytest|tox|nox|ruff|mypy|pyright|flake8|black --check|eslint|"
    r"tsc( --noEmit)?|vitest|jest|golangci-lint|dotnet (test|build)|mvn (test|verify)|gradlew? (test|check|build)|"
    r"swift test|xcodebuild test|mix test|bundle exec rspec|rspec|phpunit|pre-commit run)\b",
)

FEATURES = ("touches_tests", "adds_e2e_or_property_tests", "body_has_test_log", "body_lists_ci_checks", "evidence_attached",
            "n_test_files", "test_line_share")


def patterns_sha256() -> str:
    """sha256 of every pattern list above; stored with the features and the frozen weights so a later edit is caught."""
    blob = json.dumps([TEST_PATH_PATTERNS, E2E_PROPERTY_PATH_PATTERNS, E2E_PROPERTY_CONTENT_PATTERNS, BODY_TEST_SECTION_PATTERNS,
                       BODY_TEST_OUTPUT_PATTERNS, BODY_CI_PATTERNS, FEATURES])
    return hashlib.sha256(blob.encode()).hexdigest()


_C = {name: [re.compile(p) for p in pats] for name, pats in (
    ("test_path", TEST_PATH_PATTERNS), ("e2e_path", E2E_PROPERTY_PATH_PATTERNS), ("e2e_content", E2E_PROPERTY_CONTENT_PATTERNS),
    ("body_section", BODY_TEST_SECTION_PATTERNS), ("body_output", BODY_TEST_OUTPUT_PATTERNS), ("body_ci", BODY_CI_PATTERNS))}


def _any(kind: str, text: str) -> bool:
    return any(p.search(text) for p in _C[kind])


# ----------------------------------------------------------------- record parsing
_FILE_LINE = re.compile(r"^  (\S.*?) \+(\d+) -(\d+)$")


def parse_record(rec: dict) -> dict:
    """{title, body, files: [(path, ins, del)], diff} from explicit fields when present, else from `state`."""
    if "files" in rec and isinstance(rec.get("files"), list):
        files = [(f["path"], int(f.get("insertions", 0) or 0), int(f.get("deletions", 0) or 0)) for f in rec["files"]]
        return {"title": rec.get("title") or "", "body": rec.get("body") or "", "files": files, "diff": rec.get("diff_head") or ""}
    from ..memory.retrieval_v2 import parse_state
    st = rec.get("state") or ""
    s = parse_state(st)
    files, on = [], False
    for line in st.split("\n"):
        if line.startswith("FILES CHANGED"):
            on = True
            continue
        if on:
            if line.startswith("DIFF (") or (line.strip() == "" and files):
                break
            m = _FILE_LINE.match(line)
            if m and not line.startswith("  ... and "):
                files.append((m.group(1), int(m.group(2)), int(m.group(3))))
    return {"title": s["title"], "body": s["body"], "files": files, "diff": s["diff"]}


def is_test_path(path: str) -> bool:
    return _any("test_path", path)


def evidence_features(pr_record: dict) -> dict[str, float]:
    """Deterministic evidence features of one PR (see the module docstring for which fields are read).

    touches_tests              any listed path matches TEST_PATH_PATTERNS
    adds_e2e_or_property_tests a listed test path matches E2E_PROPERTY_PATH_PATTERNS, or an added diff line ("+…",
                               not "+++") matches E2E_PROPERTY_CONTENT_PATTERNS
    body_has_test_log          body matches BODY_TEST_SECTION_PATTERNS or BODY_TEST_OUTPUT_PATTERNS
    body_lists_ci_checks       body matches BODY_CI_PATTERNS
    evidence_attached          any of the four above
    n_test_files               listed paths matching TEST_PATH_PATTERNS
    test_line_share            (ins + del of those files) / (ins + del of all listed files); 0.0 when no lines
    """
    r = parse_record(pr_record)
    test = [(p, i, d) for p, i, d in r["files"] if is_test_path(p)]
    added = "\n".join(l[1:] for l in r["diff"].split("\n") if l.startswith("+") and not l.startswith("+++"))
    e2e = any(_any("e2e_path", p) for p, _, _ in r["files"]) or bool(added and _any("e2e_content", added))
    body = r["body"] or ""
    log = _any("body_section", body) or _any("body_output", body)
    ci = _any("body_ci", body)
    churn = sum(i + d for _, i, d in r["files"])
    tchurn = sum(i + d for _, i, d in test)
    f = {"touches_tests": float(bool(test)), "adds_e2e_or_property_tests": float(e2e), "body_has_test_log": float(log),
         "body_lists_ci_checks": float(ci)}
    f["evidence_attached"] = float(any(f.values()))
    f["n_test_files"] = float(len(test))
    f["test_line_share"] = tchurn / churn if churn else 0.0
    return {k: f[k] for k in FEATURES}


# ----------------------------------------------------------------- logistic model (frozen JSON)
def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).open() if l.strip()]


def _write(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _matrix(rows: list[dict], cols: tuple[str, ...] | list[str]) -> np.ndarray:
    return np.array([[float(r["features"][c]) for c in cols] for r in rows], dtype=float).reshape(len(rows), len(cols))


def fit(rows: list[dict], cols, target_label: str) -> dict:
    """Fit StandardScaler + LogisticRegression(C=1, lbfgs, max_iter=2000) on `rows` ({features, labels}).
    Returns the frozen-weights dict (JSON-serialisable)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    cols = list(cols)
    X = _matrix(rows, cols)
    y = np.array([int(bool(r["labels"][target_label])) for r in rows])
    if len(y) == 0 or y.sum() in (0, len(y)):
        raise ValueError(f"cannot fit: {len(y)} rows, {int(y.sum())} positives")
    sc = StandardScaler().fit(X)
    lr = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(X), y)
    return {"cols": cols, "target": target_label, "scaler_mean": sc.mean_.tolist(), "scaler_scale": sc.scale_.tolist(),
            "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0]), "n_fit": int(len(y)), "positives_fit": int(y.sum()),
            "model": "StandardScaler + LogisticRegression(C=1.0, lbfgs, max_iter=2000)"}


def predict(weights: dict, rows: list[dict]) -> np.ndarray:
    """P(target) from a frozen weights dict, numpy only."""
    X = _matrix(rows, weights["cols"])
    z = (X - np.array(weights["scaler_mean"])) / np.array(weights["scaler_scale"])
    return 1.0 / (1.0 + np.exp(-(z @ np.array(weights["coef"]) + weights["intercept"])))


def auc(y, p) -> float | None:
    from .metrics import roc_auc
    y, p = np.asarray(y, dtype=int), np.asarray(p, dtype=float)
    if len(y) == 0 or y.sum() in (0, len(y)):
        return None
    return float(roc_auc(y, p))


def fit_and_score(train_rows: list[dict], test_rows: list[dict], target: str, cols=FEATURES, weights: dict | None = None) -> dict:
    """Fit on train_rows (val repos) unless frozen `weights` are given, score on test_rows.
    `target` is a TARGETS key (iteration / rejection) or a label name. Rows: {pr_number, features{...}, labels{...}}.
    Returns {weights, val_auc (in-sample), test_auc, n_test, positives_test, test_probs}."""
    label = TARGETS.get(target, target)
    w = weights or fit(train_rows, cols, label)
    if list(w["cols"]) != list(cols):
        raise ValueError(f"frozen weights are for {w['cols']}, not {list(cols)}")
    yv = [int(bool(r["labels"][label])) for r in train_rows]
    yt = [int(bool(r["labels"][label])) for r in test_rows]
    pt = predict(w, test_rows) if test_rows else np.array([])
    return {"weights": w, "val_auc": auc(yv, predict(w, train_rows)) if train_rows else None, "test_auc": auc(yt, pt),
            "n_test": len(yt), "positives_test": int(sum(yt)), "test_probs": pt.tolist()}


def stacked(train_rows: list[dict], test_rows: list[dict], target: str, probs: dict[int, float], weights: dict | None = None) -> dict:
    """Logistic on [nhr_cal] + FEATURES, fitted on val, scored on the test PRs that have a prob; compared with nhr_cal
    alone on exactly the same test PRs (gain = stacked − alone; R7-H3 bar +0.02)."""
    label = TARGETS.get(target, target)
    add = lambda rows: [{**r, "features": {**r["features"], "nhr_cal": float(probs[r["pr_number"]])}} for r in rows if r["pr_number"] in probs]
    tr, te = add(train_rows), add(test_rows)
    cols = ("nhr_cal",) + tuple(FEATURES)
    res = fit_and_score(tr, te, target, cols, weights)
    yt = [int(bool(r["labels"][label])) for r in te]
    alone = auc(yt, [r["features"]["nhr_cal"] for r in te])
    res.update(nhr_alone_test_auc=alone, n_val=len(tr), gain=None if res["test_auc"] is None or alone is None else res["test_auc"] - alone)
    return res


# ----------------------------------------------------------------- calibrated needs_human_review
def calibrated_nhr(model: str, target_label: str, ds: dict, run5_dir: Path | None = None) -> dict[int, float] | None:
    """{pr_number: isotonic(needs_human_review)} for val and test PRs of one model's run-5 v3 rows; None if absent.
    Isotonic fitted on val raw answers vs target_label (run5._iso: needs ≥ 30 rows and both classes)."""
    from ..models.base import PRIMARY_V3
    from .run5 import _iso
    run5_dir = Path(run5_dir or RUN5_DIR)
    raw = {}
    for sp in ("val", "test"):
        f = run5_dir / f"predictions_{model}_{sp}_all.jsonl"
        if not f.exists():
            continue
        for l in f.open():
            r = json.loads(l)
            if r.get("qset") != "v3" or r["decision"].get("error") is not None:
                continue
            v = (r["decision"].get("probs") or {}).get(PRIMARY_V3)
            if v is not None and v == v and r["pr_number"] in ds:
                raw[r["pr_number"]] = float(v)  # last row wins (as run5._load)
    vk = [k for k in raw if ds[k]["split"] == "val"]
    if not vk:
        return None
    iso = _iso(np.array([raw[k] for k in vk]), np.array([int(bool(ds[k]["labels"][target_label])) for k in vk]))
    if iso is None:
        return None
    keys = list(raw)
    return dict(zip(keys, (float(x) for x in iso.predict(np.array([raw[k] for k in keys])))))


# ----------------------------------------------------------------- commands
def features_rows(ds_rows: list[dict]) -> list[dict]:
    sha = patterns_sha256()
    return [{"pr_number": r["pr_number"], "split": r["split"], "features": evidence_features(r), "patterns_sha256": sha} for r in ds_rows]


def cmd_features(a) -> None:
    rows = features_rows(_rows(DATASET))
    out = RUN_DIR / "evidence_features.jsonl"
    _write(out, rows)
    share = {k: float(np.mean([r["features"][k] for r in rows])) if rows else None for k in FEATURES}
    print(json.dumps({"wrote": str(out), "n": len(rows), "patterns_sha256": patterns_sha256(), "mean": share}, indent=2))


def _load_or_freeze(path: Path, fit_fn) -> tuple[dict, bool]:
    """Frozen weights: reuse `path` if it exists (patterns hash must match), else fit and write it once."""
    if path.exists():
        w = json.loads(path.read_text())
        if w.get("patterns_sha256") != patterns_sha256():
            raise SystemExit(f"REFUSING: {path} was frozen with patterns {w.get('patterns_sha256')}, the code now has "
                             f"{patterns_sha256()} (R7.4: pattern lists are frozen before scoring)")
        return w, False
    w = fit_fn()
    w.update(patterns_sha256=patterns_sha256(), fitted_on="val", frozen_at=_now())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(w, indent=2))
    return w, True


def score(target: str, models=STACK_MODELS, probs_path: Path | None = None, run_dir: Path | None = None) -> dict:
    run_dir = Path(run_dir or RUN_DIR)
    label = TARGETS[target]
    ds_rows = _rows(DATASET)
    ds = {r["pr_number"]: r for r in ds_rows}
    fpath = run_dir / "evidence_features.jsonl"
    feats = {r["pr_number"]: r for r in _rows(fpath)} if fpath.exists() else {r["pr_number"]: r for r in features_rows(ds_rows)}
    bad = {r["patterns_sha256"] for r in feats.values()} - {patterns_sha256()}
    if bad:
        raise SystemExit(f"REFUSING: {fpath} was computed with other patterns ({sorted(bad)}); re-run `features`")
    rows = [{"pr_number": p, "split": ds[p]["split"], "labels": ds[p]["labels"], "features": feats[p]["features"]} for p in ds if p in feats]
    val = [r for r in rows if r["split"] == "val"]
    test = [r for r in rows if r["split"] == "test"]
    w, new = _load_or_freeze(run_dir / f"evidence_weights_{target}.json", lambda: fit(val, FEATURES, label))
    ev = fit_and_score(val, test, target, FEATURES, w)
    rep = {"computed_at": _now(), "target": target, "label": label, "patterns_sha256": patterns_sha256(), "n_val": len(val), "n_test": len(test),
           "base_rate_test": float(np.mean([r["labels"][label] for r in test])) if test else None,
           "evidence_only": {k: v for k, v in ev.items() if k not in ("test_probs", "weights")}, "weights_newly_frozen": new,
           "per_feature_test_auc": {c: auc([r["labels"][label] for r in test], [r["features"][c] for r in test]) for c in FEATURES},
           "stacked": {}}
    sources = {}
    if probs_path:
        sources["probs:" + Path(probs_path).name] = {int(r["pr_number"]): float(r["prob"]) for r in _rows(probs_path)}
    else:
        for m in models:
            p = calibrated_nhr(m, label, ds)
            if p is not None:
                sources[m] = p
    for name, p in sources.items():
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        sw, snew = _load_or_freeze(run_dir / f"evidence_weights_{target}_stacked_{safe}.json",
                                   lambda p=p: stacked(val, test, target, p)["weights"])
        s = stacked(val, test, target, p, sw)
        rep["stacked"][name] = {k: v for k, v in s.items() if k not in ("test_probs", "weights")} | {"weights_newly_frozen": snew}
    a_only = rep["evidence_only"]["test_auc"]
    gains = {m: s["gain"] for m, s in rep["stacked"].items()}
    rep["R7-H3"] = {"bar": f"evidence-only test AUC on iteration >= {H3_BAR_AUC} or stacked gain >= +{H3_BAR_STACK_GAIN}",
                    "evidence_only_auc": a_only, "stack_gain": gains,
                    "pass": None if target != "iteration" else bool((a_only is not None and a_only >= H3_BAR_AUC)
                                                                     or any(g is not None and g >= H3_BAR_STACK_GAIN for g in gains.values())),
                    "note": "the bar is defined on the iteration target; rejection is reported only"}
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"evidence_metrics_{target}.json").write_text(json.dumps(rep, indent=2))
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m mergeone.eval.run7_evidence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("features", help="evidence features for every run-3 PR → results/run7/evidence_features.jsonl").set_defaults(fn=cmd_features)
    s = sub.add_parser("score", help="fit on val (frozen weights), AUC on test, stacked on calibrated needs_human_review")
    s.add_argument("--target", choices=sorted(TARGETS), required=True)
    s.add_argument("--model", action="append", help=f"stack on this model's run-5 needs_human_review (repeatable; default {', '.join(STACK_MODELS)})")
    s.add_argument("--probs", help="JSONL {pr_number, prob}: calibrated needs_human_review to stack on instead of run-5 predictions")
    s.add_argument(FROZEN_FLAG, dest="frozen", action="store_true", help="required: PROTOCOL.md §R7 is committed and these patterns with it")
    s.set_defaults(fn=lambda a: print(json.dumps(score(a.target, tuple(a.model or STACK_MODELS), Path(a.probs) if a.probs else None), indent=2))
                   if a.frozen else sys.exit(f"REFUSING to score: pass {FROZEN_FLAG} once PROTOCOL.md §R7 and the C3 pattern lists "
                                             "are committed (R7.4: pattern lists are committed before scoring)"))
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
