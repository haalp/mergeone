"""Figure: decision models under load — p99 latency vs. load multiplier, one line per model row.

Reads the frozen loadtest_*.json summaries (runs 1, 5, 6 incl. pass 2) and draws nothing that is not in them.
Error rates are drawn on the point (hollow marker + label); steps where every call failed are marked ✕ at the
top of the plot. Output: results/figures/decision_models_under_load.{png,svg} (light) and _dark.{png,svg}.

    python scripts/fig_decision_models_under_load.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator, NullFormatter

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "results"
OUT = R / "figures"

# series in fixed order (categorical slots 1..6 of the validated palette); (label, run dir, model key)
SERIES = [
    ("Jev · hosted",                       "run1",            "jev"),
    ("pplx-decider-flash 4B · hosted",     "run5",            "pplx-flash"),
    ("pplx-decider 27B · hosted",          "run5",            "pplx"),
    ("Claude Haiku 5.5 · LLM judge",       "run1",            "haiku"),
    ("pplx-flash 4B · self-hosted, 1× L4",   "run6/pass2-l4",   "pplx-flash-local"),
    ("pplx-flash 4B · self-hosted, 1× H100", "run6/pass2-h100", "pplx-flash-local"),
]
LIGHT = dict(surface="#fcfcfb", text="#0b0b0b", text2="#52514e", grid="#e6e5e1",
             series=["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"])
DARK = dict(surface="#0E0F12", text="#ECECEA", text2="#B7B9C0", grid="#23252B",
            series=["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#2e9e3a"])
MULTS = [1, 10, 30, 100, 300]
DAY = {1: "30k", 10: "300k", 30: "900k", 100: "3M", 300: "9M"}
CEIL = 60_000  # ms; "all calls failed" marks sit here


def load(run: str, model: str):
    pts = []
    for m in MULTS:
        p = R / run / f"loadtest_{model}_x{m}.json"
        if not p.exists():
            continue
        s = json.loads(p.read_text())["summary"]
        pts.append((m, s.get("p99_ms"), float(s.get("error_rate") or 0.0)))
    return pts


def draw(theme: dict, stem: str):
    plt.rcParams.update({"font.family": ["Geist", "Inter", "DejaVu Sans"], "font.size": 11})
    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=150)
    fig.patch.set_facecolor(theme["surface"]); ax.set_facecolor(theme["surface"])
    ends = []  # (y, label, color) for the right-hand direct labels, de-collided below
    for (label, run, model), color in zip(SERIES, theme["series"]):
        pts = load(run, model)
        ok = []
        broke = None
        for m, p, e in pts:  # the line runs through the multipliers the model carried with 0 errors; it stops where it breaks
            if e == 0 and p is not None:
                ok.append((m, p))
            else:
                broke = (m, p, e)
                break
        ax.plot([m for m, _ in ok], [p for _, p in ok], color=color, lw=2.2, zorder=3)
        ax.scatter([m for m, _ in ok], [p for _, p in ok], s=42, color=color, zorder=4)
        if broke:
            m, p, e = broke
            y = ok[-1][1] if ok else CEIL
            ax.plot([ok[-1][0], m], [y, y], color=color, lw=1.2, ls=(0, (2, 3)), zorder=2)
            ax.scatter([m], [y], marker="x", s=90, color=color, lw=2.4, zorder=5)
        if ok:
            ends.append((ok[-1][1], label, color))
            if not broke or ok[-1][0] == 100:
                ax.annotate("0 errors at ×100", (ok[-1][0], ok[-1][1]), xytext=(0, 11), textcoords="offset points", ha="center", color=theme["text2"], fontsize=9)
    ax.set_xscale("log"); ax.set_yscale("log")
    # right-hand labels: sorted by y, pushed apart so none overlap (log space, ~0.09 decades ≈ one text line)
    import math
    ends.sort(key=lambda t: t[0])
    ys = [math.log10(y) for y, _, _ in ends]
    gap = 0.11
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + gap)
    for i in range(len(ys) - 2, -1, -1):
        ys[i] = min(ys[i], ys[i + 1] - gap)
    for (y0, label, color), y in zip(ends, ys):
        ax.annotate(label, (150, 10 ** y), xycoords="data", xytext=(0, 0), textcoords="offset points",
                    va="center", color=theme["text"], fontsize=10, annotation_clip=False)
        ax.plot([138, 146], [y0, 10 ** y], color=color, lw=1, alpha=0.6, clip_on=False)
    ax.set_xlim(0.8, 330); ax.set_ylim(80, 20_000)
    ax.xaxis.set_major_locator(FixedLocator([1, 10, 30, 100])); ax.xaxis.set_minor_locator(FixedLocator([])); ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_xticklabels([f"×{m}\n{DAY[m]}/day" for m in [1, 10, 30, 100]])
    ax.yaxis.set_major_locator(FixedLocator([100, 300, 1000, 3000, 10000])); ax.yaxis.set_minor_locator(FixedLocator([]))
    ax.set_yticklabels(["100 ms", "300 ms", "1 s", "3 s", "10 s"])
    ax.grid(True, axis="y", color=theme["grid"], lw=0.8); ax.grid(False, axis="x")
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(theme["grid"])
    ax.tick_params(colors=theme["text2"], length=0)
    ax.set_xlabel("open-loop load, multiples of 30,000 merge decisions per day", color=theme["text2"], labelpad=10)
    ax.set_ylabel("p99 latency per decision (slowest 1%)", color=theme["text2"], labelpad=10)
    fig.text(0.07, 0.95, "Decision models under agent-scale load", color=theme["text"], fontsize=17, weight="semibold")
    fig.text(0.07, 0.915, "One line per model, behind one API key or one GPU. The line runs as far as the model answered every call; "
             "✕ is the first load where calls failed.", color=theme["text2"], fontsize=10)
    fig.text(0.07, 0.888, "gatebench runs 1, 5 and 6 · 600 decisions per step, sent on schedule · mergeone.dev/updates", color=theme["text2"], fontsize=10)
    fig.subplots_adjust(left=0.07, right=0.76, top=0.86, bottom=0.12)
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "svg"):
        fig.savefig(OUT / f"{stem}.{ext}", facecolor=theme["surface"])
    plt.close(fig)


if __name__ == "__main__":
    draw(LIGHT, "decision_models_under_load")
    draw(DARK, "decision_models_under_load_dark")
    print(OUT)
