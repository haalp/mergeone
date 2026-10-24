"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  CHART_COLORS,
  Dot,
  EASE_OUT,
  SPRING,
  Tooltip,
  clamp,
  cx,
  defaultFormat,
  defaultTick,
  focus,
  muted,
  niceStep,
  useDismissOutside,
  useElementWidth,
} from "../../lib/chart-primitives";

export interface BarSeries {
  key: string;
  label: string;
  color?: string;
}
export type BarDatum = { label: string } & Record<string, string | number>;

export interface BarChartProps {
  data: BarDatum[];
  series: BarSeries[];
  /** grouped: bars side by side · stacked: bars on top of each other (changing it morphs) */
  variant?: "grouped" | "stacked";
  height?: number;
  formatValue?: (n: number) => string;
  formatTick?: (n: number) => string;
  showLegend?: boolean;
  /** Entrance animation (also disabled by prefers-reduced-motion) */
  animated?: boolean;
  ariaLabel?: string;
  className?: string;
}

// Rounded-top bar. Always the same command structure, so any two bars can morph into each other
function barPath(x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return `M${x} ${y + h}V${y + rr}Q${x} ${y} ${x + rr} ${y}H${x + w - rr}Q${x + w} ${y} ${x + w} ${y + rr}V${y + h}Z`;
}

function LegendChip({
  color,
  label,
  on,
  onClick,
}: {
  color: string;
  label: string;
  on: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`inline-flex h-8 items-center gap-2 rounded-full px-3 text-[12px] transition-[opacity,background-color] duration-150 [-webkit-tap-highlight-color:transparent] ${
        on ? "text-neutral-700 dark:text-neutral-300" : "text-neutral-400 line-through opacity-60"
      } [@media(hover:hover)]:hover:bg-black/[0.04] dark:[@media(hover:hover)]:hover:bg-white/[0.05] ${focus}`}
    >
      <Dot color={on ? color : "currentColor"} />
      {label}
    </button>
  );
}

export function BarChart({
  data,
  series,
  variant = "grouped",
  height = 260,
  formatValue = defaultFormat,
  formatTick = defaultTick,
  showLegend = true,
  animated = true,
  ariaLabel,
  className = "",
}: BarChartProps) {
  const reduce = useReducedMotion();
  const anim = animated && !reduce;
  const [rootRef, width] = useElementWidth<HTMLDivElement>(640);
  const [hidden, setHidden] = useState<string[]>([]);
  const [active, setActive] = useState<number | null>(null);
  const [entered, setEntered] = useState(!anim);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!anim) return;
    const t = setTimeout(() => setEntered(true), 1200);
    return () => clearTimeout(t);
  }, [anim]);
  useDismissOutside(wrapRef, active !== null, useCallback(() => setActive(null), []));

  const colors = series.map((s, i) => s.color ?? CHART_COLORS[i % CHART_COLORS.length]);
  const visible = series.map((s, i) => ({ ...s, color: colors[i], i })).filter((s) => !hidden.includes(s.key));
  const stacked = variant === "stacked";
  const n = data.length;
  const val = (d: BarDatum, k: string) => {
    const v = d[k];
    return typeof v === "number" && isFinite(v) ? Math.max(0, v) : 0;
  };
  const maxV = Math.max(
    1e-9,
    ...data.map((d) =>
      stacked ? visible.reduce((s, x) => s + val(d, x.key), 0) : Math.max(0, ...visible.map((x) => val(d, x.key))),
    ),
  );
  const step = niceStep(maxV / 4);
  const yMax = step * 4;
  const ticks = [0, 1, 2, 3, 4].map((i) => i * step);
  const tickText = ticks.map(formatTick);

  const M = { top: 8, right: 6, bottom: 26, left: Math.ceil(Math.max(...tickText.map((s) => s.length)) * 6.4) + 10 };
  const iw = Math.max(10, width - M.left - M.right);
  const ih = height - M.top - M.bottom;
  const base = M.top + ih;
  const y = (v: number) => base - (v / yMax) * ih;
  const stepX = iw / Math.max(1, n);
  const k = Math.max(1, visible.length);
  const gapIn = 3;
  const barW = stacked ? clamp(stepX * 0.62, 4, 46) : clamp((stepX * 0.74 - gapIn * (k - 1)) / k, 3, 34);
  const groupW = stacked ? barW : barW * k + gapIn * (k - 1);
  const labelEvery = Math.max(1, Math.ceil((Math.max(...data.map((d) => d.label.length), 1) * 6.4 + 14) / stepX));

  // Geometry of every bar (hidden series collapse onto their group's centre)
  const groupTop = (i: number) => {
    const d = data[i];
    return y(stacked ? visible.reduce((s, x) => s + val(d, x.key), 0) : Math.max(0, ...visible.map((x) => val(d, x.key))));
  };
  const bars = data.map((d, i) => {
    const centre = M.left + stepX * i + stepX / 2;
    let cum = 0;
    const topSeries = [...visible].reverse().find((x) => val(d, x.key) > 0);
    return series.map((s, si) => {
      const vi = visible.findIndex((x) => x.key === s.key);
      const v = val(d, s.key);
      if (vi < 0) return { s, si, d: barPath(centre, base, 0, 0, 0), opacity: 0 };
      if (stacked) {
        const y1 = y(cum + v);
        const y0 = y(cum);
        const h = Math.max(0, y0 - y1 - (cum > 0 && v > 0 ? 1.5 : 0));
        cum += v;
        return { s, si, d: barPath(centre - barW / 2, y1, barW, h, topSeries?.key === s.key ? Math.min(6, barW / 2) : 0), opacity: 1 };
      }
      const bx = centre - groupW / 2 + vi * (barW + gapIn);
      const h = (v / yMax) * ih;
      return { s, si, d: barPath(bx, base - h, barW, h, Math.min(6, barW / 2)), opacity: 1 };
    });
  });

  const move = (e: ReactPointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    setActive(clamp(Math.floor((e.clientX - box.left) / stepX), 0, n - 1));
  };
  const onKey = (e: ReactKeyboardEvent) => {
    if (e.key === "ArrowRight") setActive((a) => clamp((a ?? -1) + 1, 0, n - 1));
    else if (e.key === "ArrowLeft") setActive((a) => clamp((a ?? n) - 1, 0, n - 1));
    else if (e.key === "Home") setActive(0);
    else if (e.key === "End") setActive(n - 1);
    else if (e.key === "Escape") setActive(null);
    else return;
    e.preventDefault();
  };
  const toggle = (key: string) =>
    setHidden((h) => (h.includes(key) ? h.filter((x) => x !== key) : visible.length > 1 ? [...h, key] : h));

  if (!n) return <p className={`py-10 text-center text-[13px] ${muted}`}>No data</p>;

  const tr = (i: number, si: number) =>
    reduce ? { duration: 0 } : entered ? SPRING : { duration: 0.8, ease: EASE_OUT, delay: i * 0.04 + si * 0.025 };

  return (
    <div ref={rootRef} className={cx("w-full", className)}>
      <div ref={wrapRef} className="relative" style={{ height }}>
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={ariaLabel ?? `Bar chart of ${series.map((s) => s.label).join(", ")} across ${n} categories`}
          tabIndex={0}
          onKeyDown={onKey}
          onBlur={() => setActive(null)}
          className="block touch-pan-y overflow-visible rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-neutral-400/60 dark:focus-visible:ring-white/30"
        >
          {/* grid + y labels */}
          {ticks.map((_, t) => {
            const gy = base - (t / 4) * ih;
            return (
              <g key={t}>
                <line x1={M.left} x2={width - M.right} y1={gy} y2={gy} className={t === 0 ? "stroke-black/15 dark:stroke-white/15" : "stroke-black/[0.06] dark:stroke-white/[0.07]"} />
                <motion.text
                  key={tickText[t]}
                  x={M.left - 8}
                  y={gy}
                  textAnchor="end"
                  dominantBaseline="middle"
                  className="fill-neutral-500 text-[11px] tabular-nums"
                  initial={anim ? { opacity: 0 } : false}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.25 }}
                >
                  {tickText[t]}
                </motion.text>
              </g>
            );
          })}

          {/* hover band */}
          <motion.rect
            y={M.top}
            height={ih}
            width={stepX}
            rx={8}
            className="fill-black/[0.04] dark:fill-white/[0.05]"
            initial={false}
            animate={{ x: M.left + stepX * (active ?? 0), opacity: active === null ? 0 : 1 }}
            transition={reduce ? { duration: 0 } : { x: { type: "spring", duration: 0.3, bounce: 0.1 }, opacity: { duration: 0.15 } }}
          />

          {/* bars */}
          {bars.map((group, i) => (
            <g key={i} style={{ opacity: active === null || active === i ? 1 : 0.38, transition: "opacity .2s" }}>
              {group.map(({ s, si, d, opacity }) => (
                <motion.path
                  key={s.key}
                  fill={colors[si]}
                  initial={anim ? { d: barPath(M.left + stepX * i + stepX / 2 - barW / 2, base, barW, 0, 0), opacity: 0 } : false}
                  animate={{ d, opacity }}
                  transition={tr(i, si)}
                />
              ))}
            </g>
          ))}

          {/* x labels */}
          {data.map((d, i) =>
            i % labelEvery === 0 ? (
              <text
                key={i}
                x={M.left + stepX * i + stepX / 2}
                y={height - 8}
                textAnchor="middle"
                className={cx("text-[11px]", active === i ? "fill-neutral-900 dark:fill-neutral-100" : "fill-neutral-500")}
              >
                {d.label}
              </text>
            ) : null,
          )}

          {/* pointer surface */}
          <rect
            x={M.left}
            y={M.top}
            width={iw}
            height={ih}
            fill="transparent"
            onPointerMove={move}
            onPointerDown={move}
            onPointerLeave={(e) => e.pointerType === "mouse" && setActive(null)}
          />
        </svg>

        <Tooltip pos={active === null ? null : { x: M.left + stepX * (active + 0.5), y: groupTop(active) }} boundsWidth={width}>
          {active !== null && (
            <>
              <p className="mb-1.5 font-medium text-neutral-900 dark:text-neutral-100">{data[active].label}</p>
              <ul className="space-y-1">
                {visible.map((s) => (
                  <li key={s.key} className="flex items-center justify-between gap-4">
                    <span className="flex items-center gap-2 text-neutral-500">
                      <Dot color={s.color} />
                      {s.label}
                    </span>
                    <span className="tabular-nums text-neutral-900 dark:text-neutral-100">{formatValue(val(data[active], s.key))}</span>
                  </li>
                ))}
              </ul>
              {stacked && visible.length > 1 && (
                <p className="mt-1.5 flex justify-between gap-4 border-t border-black/[0.06] pt-1.5 dark:border-white/[0.08]">
                  <span className="text-neutral-500">Total</span>
                  <span className="tabular-nums text-neutral-900 dark:text-neutral-100">
                    {formatValue(visible.reduce((s, x) => s + val(data[active], x.key), 0))}
                  </span>
                </p>
              )}
            </>
          )}
        </Tooltip>
      </div>

      {showLegend && series.length > 1 && (
        <div className="mt-3 flex flex-wrap gap-x-1 gap-y-0.5" role="group" aria-label="Series">
          {series.map((s, i) => (
            <LegendChip key={s.key} color={colors[i]} label={s.label} on={!hidden.includes(s.key)} onClick={() => toggle(s.key)} />
          ))}
        </div>
      )}

      <table className="sr-only">
        <caption>{ariaLabel ?? "Chart data"}</caption>
        <thead>
          <tr>
            <th scope="col">Category</th>
            {series.map((s) => (
              <th key={s.key} scope="col">{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((d, i) => (
            <tr key={i}>
              <th scope="row">{d.label}</th>
              {series.map((s) => (
                <td key={s.key}>{formatValue(val(d, s.key))}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
