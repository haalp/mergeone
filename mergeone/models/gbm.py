"""LightGBM on structural features. Tuned on the validation split only."""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

from .base import Decision, DECISIONS, Timer

FEATURE_ORDER = [
    "n_files", "insertions", "deletions", "churn", "n_topdirs", "n_exts", "go_ratio", "test_ratio",
    "vendor_ratio", "generated_ratio", "sensitive_files", "touches_sensitive", "title_len", "body_len",
    "title_risk_words", "title_lowrisk_words", "body_mentions_issue", "is_cherry_pick", "diff_truncated",
]


def _vec(features: dict[str, float]) -> np.ndarray:
    return np.array([[features[k] for k in FEATURE_ORDER]], dtype=float)


class GBMModel:
    name = "gbm"

    def __init__(self, path: Path | None = None):
        self.boosters: dict[str, object] = {}
        self.version = "gbm_untrained"
        if path and path.exists():
            with path.open("rb") as f:
                d = pickle.load(f)
            self.boosters = d["boosters"]
            self.version = d["version"]

    def fit(self, train_feats: list[dict], train_labels: dict[str, list[int]], val_feats: list[dict], val_labels: dict[str, list[int]], seed: int = 7) -> dict:
        import lightgbm as lgb
        from sklearn.metrics import average_precision_score

        X_tr = np.array([[f[k] for k in FEATURE_ORDER] for f in train_feats], dtype=float)
        X_va = np.array([[f[k] for k in FEATURE_ORDER] for f in val_feats], dtype=float)
        report = {}
        for name in DECISIONS:
            y_tr = np.array(train_labels[name], dtype=int)
            y_va = np.array(val_labels[name], dtype=int)
            if y_tr.sum() == 0 or y_va.sum() == 0:
                report[name] = {"skipped": "no positives"}
                continue
            pos_w = (len(y_tr) - y_tr.sum()) / max(y_tr.sum(), 1)
            best = None
            # Small grid; selection on validation PR-AUC. Deliberately modest.
            for leaves in (7, 15, 31):
                for lr in (0.03, 0.1):
                    params = dict(objective="binary", num_leaves=leaves, learning_rate=lr, min_data_in_leaf=20,
                                  feature_fraction=0.9, bagging_fraction=0.9, bagging_freq=1, scale_pos_weight=pos_w,
                                  verbose=-1, seed=seed, deterministic=True)
                    dtr = lgb.Dataset(X_tr, y_tr)
                    dva = lgb.Dataset(X_va, y_va, reference=dtr)
                    b = lgb.train(params, dtr, num_boost_round=600, valid_sets=[dva],
                                  callbacks=[lgb.early_stopping(50, verbose=False)])
                    ap = average_precision_score(y_va, b.predict(X_va, num_iteration=b.best_iteration))
                    if best is None or ap > best[0]:
                        best = (ap, b, params, b.best_iteration)
            ap, b, params, it = best
            self.boosters[name] = b
            report[name] = {"val_pr_auc": float(ap), "best_iter": int(it), "num_leaves": params["num_leaves"], "lr": params["learning_rate"]}
        self.version = f"gbm_v1_seed{seed}"
        return report

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump({"boosters": self.boosters, "version": self.version}, f)

    def decide(self, state: str, features: dict[str, float], questions=DECISIONS) -> Decision:
        with Timer() as t:
            x = _vec(features)
            probs = {}
            for q in questions:
                b = self.boosters.get(q)
                probs[q] = float(b.predict(x)[0]) if b is not None else 0.0
        return Decision(probs=probs, latency_ms=t.ms, model_version=self.version)
