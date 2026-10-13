from __future__ import annotations

import math

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve, brier_score_loss


def _clean(y, p):
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    m = ~np.isnan(p)
    return y[m], p[m], float(1 - m.mean())


def pr_auc(y, p) -> float:
    y, p, _ = _clean(y, p)
    return float(average_precision_score(y, p)) if y.sum() > 0 else float("nan")


def roc_auc(y, p) -> float:
    y, p, _ = _clean(y, p)
    return float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else float("nan")


def recall_at_fpr(y, p, fpr_target: float = 0.05, threshold: float | None = None) -> tuple[float, float]:
    """Recall at a fixed FPR. If threshold given (from validation), use it; else find it."""
    y, p, _ = _clean(y, p)
    if y.sum() == 0:
        return float("nan"), float("nan")
    if threshold is None:
        fpr, tpr, thr = roc_curve(y, p)
        ok = np.where(fpr <= fpr_target)[0]
        i = ok[-1] if len(ok) else 0
        return float(tpr[i]), float(thr[i])
    pred = p >= threshold
    tp = int((pred & (y == 1)).sum())
    return float(tp / y.sum()), float(threshold)


def threshold_for_fpr(y, p, fpr_target: float = 0.05) -> float:
    y, p, _ = _clean(y, p)
    fpr, tpr, thr = roc_curve(y, p)
    ok = np.where(fpr <= fpr_target)[0]
    i = ok[-1] if len(ok) else 0
    return float(thr[i])


def fpr_at_threshold(y, p, threshold: float) -> float:
    y, p, _ = _clean(y, p)
    neg = y == 0
    return float(((p >= threshold) & neg).sum() / max(neg.sum(), 1))


def ece(y, p, n_bins: int = 15) -> float:
    """Expected calibration error, equal-mass bins."""
    y, p, _ = _clean(y, p)
    if len(p) == 0:
        return float("nan")
    order = np.argsort(p)
    y, p = y[order], p[order]
    bins = np.array_split(np.arange(len(p)), n_bins)
    total = 0.0
    for b in bins:
        if len(b) == 0:
            continue
        total += len(b) / len(p) * abs(y[b].mean() - p[b].mean())
    return float(total)


def brier(y, p) -> float:
    y, p, _ = _clean(y, p)
    return float(brier_score_loss(y, p)) if len(p) else float("nan")


def reliability(y, p, n_bins: int = 10) -> list[dict]:
    y, p, _ = _clean(y, p)
    order = np.argsort(p)
    y, p = y[order], p[order]
    out = []
    for b in np.array_split(np.arange(len(p)), n_bins):
        if len(b) == 0:
            continue
        out.append({"n": int(len(b)), "mean_pred": float(p[b].mean()), "frac_pos": float(y[b].mean())})
    return out


def accuracy(y, p, thr: float = 0.5) -> float:
    y, p, _ = _clean(y, p)
    return float(((p >= thr).astype(int) == y).mean()) if len(p) else float("nan")


def summarize(y, p, fpr_threshold: float | None = None) -> dict:
    yy, pp, nan_rate = _clean(y, p)
    r, thr = recall_at_fpr(y, p, 0.05, fpr_threshold)
    return {
        "n": int(len(yy)),
        "positives": int(yy.sum()),
        "base_rate": float(yy.mean()) if len(yy) else float("nan"),
        "nan_rate": nan_rate,
        "pr_auc": pr_auc(y, p),
        "roc_auc": roc_auc(y, p),
        "recall_at_fpr5": r,
        "fpr5_threshold": thr,
        "actual_fpr_at_threshold": fpr_at_threshold(y, p, thr) if not math.isnan(thr) else float("nan"),
        "ece": ece(y, p),
        "brier": brier(y, p),
    }


def bootstrap_ci(y, p, fn, n: int = 500, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    y = np.asarray(y)
    p = np.asarray(p, dtype=float)
    vals = []
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        try:
            vals.append(fn(y[idx], p[idx]))
        except Exception:
            pass
    vals = [v for v in vals if not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return float("nan"), float("nan")
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))
