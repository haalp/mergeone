"use client";

import React, {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "framer-motion";
import {
  CHART_COLORS,
  EASE_OUT,
  clamp,
  cx,
  defaultFormat,
  focus,
  muted,
  ring,
  useDismissOutside,
  useElementWidth,
} from "../../lib/chart-primitives";
import { monotonePath, pctText } from "../../lib/dashboard-chart-utils";

/* -------------------------------------------------------------------------- */
/*  StatCard / StatGrid                                                       */
/*                                                                            */
/*  KPI tiles with a count-up value, a delta pill, and a scrubable sparkline. */
/*  Hand-written SVG · light and dark · measures its own container.            */
/* -------------------------------------------------------------------------- */

export interface StatCardProps {
  label: string;
  /** The headline number (usually the latest point of `trend`) */
  value: number;
  /** Points for the sparkline, oldest → newest */
  trend: number[];
  /** Names for each trend point, shown while scrubbing (e.g. "Oct 12") */
  trendLabels?: string[];
  /** Previous-period value → the delta is computed from it */
  previous?: number;
  /** …or give the delta in percent directly */
  delta?: number;
  /** Which direction is good news. Use "down" for churn, latency, costs */
  goodWhen?: "up" | "down";
  /** Text under the number, e.g. "vs last 30 days" */
  period?: string;
  format?: (n: number) => string;
  color?: string;
  animated?: boolean;
  className?: string;
}

export function StatCard({
  label,
  value,
  trend,
  trendLabels,
  previous,
  delta,
  goodWhen = "up",
  period = "vs previous period",
  format = defaultFormat,
  color = CHART_COLORS[0],
  animated = true,
  className = "",
}: StatCardProps) {
  const reduce = useReducedMotion();
  const anim = animated && !reduce;
  const uid = useId().replace(/:/g, "");
  const [boxRef, width] = useElementWidth<HTMLDivElement>(220);
  const svgRef = useRef<SVGSVGElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  useDismissOutside(cardRef, active !== null, useCallback(() => setActive(null), []));

  const n = trend.length;
  const H = 52, padX = 0, padT = 8, padB = 6;
  const min = n ? Math.min(...trend) : 0;
  const span = (n ? Math.max(...trend) : 1) - min || 1;
  const stepX = n > 1 ? (width - padX * 2) / (n - 1) : 0;
  const pts = trend.map((v, i) => [padX + i * stepX, padT + (1 - (v - min) / span) * (H - padT - padB)] as [number, number]);
  const line = monotonePath(pts);
  const area = n > 1 ? `${line}L${pts[n - 1][0].toFixed(2)} ${H}L${pts[0][0].toFixed(2)} ${H}Z` : "";

  const pct =
    delta ??
    (previous !== undefined && previous !== 0
      ? ((value - previous) / Math.abs(previous)) * 100
      : n > 1 && trend[0] !== 0
        ? ((trend[n - 1] - trend[0]) / Math.abs(trend[0])) * 100
        : 0);
  const rounded = Math.round(pct * 10) / 10;
  const good = rounded === 0 ? null : goodWhen === "up" ? rounded > 0 : rounded < 0;

  /* number: counts up on mount, follows the scrubber afterwards */
  const fmt = useRef(format);
  useEffect(() => {
    fmt.current = format;
  }, [format]);
  const shown = useMotionValue(anim ? 0 : value);
  const text = useTransform(shown, (v) => fmt.current(v));
  const mounted = useRef(false);
  useEffect(() => {
    const target = active === null ? value : trend[active];
    const intro = !mounted.current;
    mounted.current = true;
    const c = animate(shown, target, { duration: reduce ? 0 : intro ? 1.1 : 0.22, ease: EASE_OUT });
    return () => c.stop();
  }, [active, value, trend, shown, reduce]);

  /* scrub cursor */
  const curX = useSpring(0, { stiffness: 600, damping: 44, mass: 0.5 });
  const curY = useSpring(0, { stiffness: 600, damping: 44, mass: 0.5 });
  const seen = useRef(false);
  useEffect(() => {
    if (active === null || !pts[active]) {
      seen.current = false;
      return;
    }
    const [px, py] = pts[active];
    if (!seen.current) {
      curX.jump(px);
      curY.jump(py);
      seen.current = true;
    } else {
      curX.set(px);
      curY.set(py);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, width, curX, curY]);

  const move = (e: React.PointerEvent) => {
    if (n < 2 || !svgRef.current) return;
    const box = svgRef.current.getBoundingClientRect();
    setActive(clamp(Math.round((e.clientX - box.left - padX) / stepX), 0, n - 1));
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowRight") setActive((a) => clamp((a ?? n - 2) + 1, 0, n - 1));
    else if (e.key === "ArrowLeft") setActive((a) => clamp((a ?? n) - 1, 0, n - 1));
    else if (e.key === "Escape") setActive(null);
    else return;
    e.preventDefault();
  };

  const sub = active === null ? period : (trendLabels?.[active] ?? `Point ${active + 1}`);

  return (
    <div
      ref={cardRef}
      tabIndex={0}
      role="group"
      aria-label={`${label}: ${format(value)}, ${rounded > 0 ? "up" : rounded < 0 ? "down" : "unchanged"} ${pctText(Math.abs(rounded))}`}
      onPointerMove={move}
      onPointerDown={move}
      onPointerLeave={(e) => e.pointerType === "mouse" && setActive(null)}
      onKeyDown={onKey}
      onBlur={() => setActive(null)}
      className={`group relative min-w-0 select-none overflow-hidden rounded-2xl bg-white pt-4 text-neutral-700 transition-[box-shadow] duration-200 dark:bg-[#0b0b0c] dark:text-neutral-300 ${ring} ${focus} [@media(hover:hover)]:hover:ring-black/15 dark:[@media(hover:hover)]:hover:ring-white/15 ${className}`}
      style={{ boxShadow: "0 1px 2px rgba(0,0,0,0.04), 0 18px 36px -28px rgba(0,0,0,0.2)" }}
    >
      <div className="flex items-start justify-between gap-3 px-4">
        <p className={`truncate text-[12.5px] ${muted}`}>{label}</p>
        <span
          className={cx(
            "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-medium tabular-nums",
            good === null && "bg-black/[0.05] text-neutral-500 dark:bg-white/[0.06]",
            good === true && "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
            good === false && "bg-rose-500/10 text-rose-600 dark:text-rose-400",
          )}
        >
          {rounded !== 0 && (
            <svg viewBox="0 0 10 10" className={cx("h-2 w-2", rounded < 0 && "rotate-180")} fill="currentColor" aria-hidden>
              <path d="M5 1.5 9 7.5H1Z" />
            </svg>
          )}
          {pctText(Math.abs(rounded))}
        </span>
      </div>

      <p className="mt-1.5 px-4 text-[28px] font-semibold leading-none tracking-[-0.035em] tabular-nums text-neutral-900 dark:text-neutral-50 sm:text-[30px]">
        <motion.span>{text}</motion.span>
      </p>
      <p className={`mt-1.5 truncate px-4 text-[12px] ${muted}`}>{sub}</p>

      <div ref={boxRef} className="mt-3" style={{ height: H }}>
        {n > 1 && (
          <svg
            ref={svgRef}
            width={width}
            height={H}
            role="img"
            aria-label={`${label} trend from ${format(trend[0])} to ${format(trend[n - 1])}`}
            className="block overflow-visible"
          >
            <defs>
              <linearGradient id={`${uid}-g`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor={color} stopOpacity="0.28" />
                <stop offset="1" stopColor={color} stopOpacity="0" />
              </linearGradient>
            </defs>
            <motion.path
              d={area}
              fill={`url(#${uid}-g)`}
              initial={anim ? { opacity: 0 } : false}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.8, delay: 0.5 }}
            />
            <motion.path
              d={line}
              fill="none"
              stroke={color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              initial={anim ? { pathLength: 0 } : false}
              animate={{ pathLength: 1 }}
              transition={{ duration: 1.1, ease: EASE_OUT, delay: 0.15 }}
            />
            {/* latest point */}
            <g style={{ opacity: active === null ? 1 : 0, transition: "opacity .15s" }}>
              {anim && (
                <motion.circle
                  cx={pts[n - 1][0]}
                  cy={pts[n - 1][1]}
                  r={3.5}
                  fill={color}
                  style={{ transformBox: "fill-box", transformOrigin: "center" }}
                  animate={{ scale: [1, 2.8], opacity: [0.4, 0] }}
                  transition={{ repeat: Infinity, duration: 1.8, ease: "easeOut" }}
                />
              )}
              <circle cx={pts[n - 1][0]} cy={pts[n - 1][1]} r={3.5} fill={color} />
            </g>
            {/* scrubber */}
            <motion.g initial={false} animate={{ opacity: active === null ? 0 : 1 }} transition={{ duration: 0.12 }}>
              <motion.line y1={0} y2={H} x1={curX} x2={curX} className="stroke-black/15 dark:stroke-white/20" strokeDasharray="2 3" />
              <motion.circle cx={curX} cy={curY} r={4.5} fill={color} className="stroke-white dark:stroke-[#0b0b0c]" strokeWidth={2} />
            </motion.g>
          </svg>
        )}
      </div>
      <p className="sr-only" role="status" aria-live="polite">
        {active !== null ? `${trendLabels?.[active] ?? `Point ${active + 1}`}: ${format(trend[active])}` : ""}
      </p>
    </div>
  );
}

export function StatGrid({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`grid gap-3 sm:gap-4 ${className}`}
      style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 210px), 1fr))" }}
    >
      {children}
    </div>
  );
}
