"use client";

import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  CHART_COLORS,
  clamp,
  cx,
  defaultFormat,
  defaultTick,
  focus,
  muted,
  Tooltip,
  useDismissOutside,
  useElementWidth,
} from "../../lib/chart-primitives";
import {
  buildFrame,
  pctText,
  valueAt,
  visibleExtent,
  type TimestampedPoint,
} from "../../lib/dashboard-chart-utils";

export { buildFrame, valueAt, visibleExtent } from "../../lib/dashboard-chart-utils";

/* -------------------------------------------------------------------------- */
/*  LiveLineChart                                                             */
/*                                                                            */
/*  A real-time line that scrolls right-to-left. Hover to pause and inspect.   */
/*  Draws straight into the DOM at 60fps without re-rendering React.           */
/* -------------------------------------------------------------------------- */

export interface LiveLineChartProps {
  /**
   * Your data feed. Called once; call `push(value)` whenever a new reading arrives
   * and return a cleanup function (close the socket, clear the timer…).
   */
  source?: (push: (value: number, time?: number) => void) => void | (() => void);
  /** Values already known, oldest → newest, spaced `intervalMs` apart and ending now */
  initial?: number[];
  /** How often readings arrive, roughly. Also the small delay that makes motion silky (default 1000) */
  intervalMs?: number;
  /** Seconds visible on screen (default 60) */
  windowSeconds?: number;
  height?: number;
  color?: string;
  unit?: string;
  formatValue?: (n: number) => string;
  /** Fix the y-range instead of auto-scaling */
  domain?: [number, number];
  /** Big current value + pause button above the chart (default true) */
  showReadout?: boolean;
  ariaLabel?: string;
  className?: string;
}

const LIVE_M = { l: 46, t: 8, r: 12, b: 22 };

export function LiveLineChart({
  source,
  initial,
  intervalMs = 1000,
  windowSeconds = 60,
  height = 220,
  color = CHART_COLORS[0],
  unit = "",
  formatValue = defaultFormat,
  domain,
  showReadout = true,
  ariaLabel,
  className = "",
}: LiveLineChartProps) {
  const reduce = useReducedMotion();
  const uid = useId().replace(/:/g, "");
  const [rootRef, width] = useElementWidth<HTMLDivElement>(640);
  const wrapRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const lineEl = useRef<SVGPathElement>(null);
  const areaEl = useRef<SVGPathElement>(null);
  const headEl = useRef<SVGCircleElement>(null);
  const haloEl = useRef<SVGCircleElement>(null);
  const crossLine = useRef<SVGLineElement>(null);
  const crossDot = useRef<SVGCircleElement>(null);
  const tickEls = useRef<(SVGTextElement | null)[]>([]);

  const buf = useRef<TimestampedPoint[]>([]);
  const size = useRef({ w: width, h: height });
  const live = useRef({ delay: intervalMs, win: windowSeconds * 1000, domain, reduce: !!reduce, fmt: formatValue });
  const flags = useRef({ paused: false, hoverX: null as number | null, dirty: true });
  const fmtTick = useRef(defaultTick);

  const [latest, setLatest] = useState<number | null>(initial?.length ? initial[initial.length - 1] : null);
  const [first, setFirst] = useState<number | null>(initial?.length ? initial[0] : null);
  const [manualPause, setManualPause] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [scrub, setScrub] = useState<{ x: number; y: number; v: number; ago: number } | null>(null);
  const paused = manualPause || hovering;

  useDismissOutside(wrapRef, hovering, useCallback(() => {
    flags.current.hoverX = null;
    setHovering(false);
    setScrub(null);
  }, []));

  // keep the animation loop's view of props / state fresh without restarting it
  useEffect(() => {
    live.current = { delay: reduce ? 0 : intervalMs, win: windowSeconds * 1000, domain, reduce: !!reduce, fmt: formatValue };
    size.current = { w: width, h: height };
    flags.current.paused = paused;
    flags.current.dirty = true;
  }, [intervalMs, windowSeconds, domain, reduce, formatValue, width, height, paused]);

  const push = useCallback((value: number, time?: number) => {
    if (!Number.isFinite(value)) return;
    const b = buf.current;
    let t = time ?? Date.now();
    if (b.length && t <= b[b.length - 1].t) t = b[b.length - 1].t + 1;
    b.push({ t, v: value });
    if (b.length > 6000) b.splice(0, b.length - 6000);
    flags.current.dirty = true;
    setLatest(value);
    setFirst((f) => f ?? value);
  }, []);

  // seed history + subscribe to the feed
  useEffect(() => {
    if (initial?.length) {
      const end = Date.now();
      buf.current = initial.map((v, i) => ({ t: end - (initial.length - 1 - i) * intervalMs, v }));
      flags.current.dirty = true;
    }
    const stop = source?.(push);
    return () => {
      if (typeof stop === "function") stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // the render loop: draws straight into the DOM (no React re-renders at 60 fps)
  useEffect(() => {
    let raf = 0;
    let last = 0;
    let dc = Date.now() - live.current.delay; // the moment currently on screen
    let visible = true;
    let lastLabels = 0;
    let lastScrub = "";
    const dom = { lo: 0, hi: 1, init: false };

    const geom = () => {
      const { w, h } = size.current;
      return { l: LIVE_M.l, t: LIVE_M.t, iw: Math.max(10, w - LIVE_M.l - LIVE_M.r), ih: Math.max(10, h - LIVE_M.t - LIVE_M.b) };
    };

    const draw = (dt: number, now: number) => {
      const b = buf.current;
      const { win, domain: fixed } = live.current;
      const g = geom();
      if (b.length < 2 || !lineEl.current || !areaEl.current) return;

      // y-range eases toward what's on screen
      let lo: number, hi: number;
      if (fixed) {
        [lo, hi] = fixed;
        dom.lo = lo; dom.hi = hi; dom.init = true;
      } else {
        const ext = visibleExtent(b, dc, win);
        const pad = (ext.max - ext.min) * 0.16 || Math.max(1, Math.abs(ext.max) * 0.05);
        lo = ext.min - pad;
        hi = ext.max + pad;
        if (!dom.init) { dom.lo = lo; dom.hi = hi; dom.init = true; }
        else {
          const k = live.current.reduce ? 1 : 1 - Math.exp((-dt / 1000) * 5);
          dom.lo += (lo - dom.lo) * k;
          dom.hi += (hi - dom.hi) * k;
        }
      }

      const f = buildFrame(b, dc, win, dom, g);
      lineEl.current.setAttribute("d", f.line);
      areaEl.current.setAttribute("d", f.area);
      if (f.head && headEl.current && haloEl.current) {
        headEl.current.setAttribute("cx", String(f.head.x));
        headEl.current.setAttribute("cy", String(f.head.y));
        haloEl.current.setAttribute("cx", String(f.head.x));
        haloEl.current.setAttribute("cy", String(f.head.y));
      }

      // y-axis labels (updated ~5×/s so the numbers stay readable while the range eases)
      if (now - lastLabels > 200 || flags.current.dirty) {
        lastLabels = now;
        for (let i = 0; i < 3; i++) {
          const el = tickEls.current[i];
          if (el) el.textContent = fmtTick.current(dom.hi - ((dom.hi - dom.lo) * i) / 2);
        }
      }

      // scrubbing (hover / touch): crosshair follows the pointer on the frozen frame
      const hx = flags.current.hoverX;
      if (hx !== null && crossLine.current && crossDot.current) {
        const x = clamp(hx, g.l, g.l + g.iw);
        const t = f.tStart + ((x - g.l) / g.iw) * win;
        const v = valueAt(b, t);
        if (v !== null) {
          const y = g.t + g.ih - ((v - dom.lo) / (dom.hi - dom.lo || 1)) * g.ih;
          crossLine.current.setAttribute("x1", String(x));
          crossLine.current.setAttribute("x2", String(x));
          crossDot.current.setAttribute("cx", String(x));
          crossDot.current.setAttribute("cy", String(y));
          const ago = Math.max(0, (dc - t) / 1000);
          const key = `${Math.round(x)}|${v.toFixed(2)}|${ago.toFixed(1)}`;
          if (key !== lastScrub) {
            lastScrub = key;
            setScrub({ x, y, v, ago });
          }
        }
      }
    };

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const dt = Math.min(now - last, 100);
      last = now;
      const b = buf.current;
      const { delay, win, reduce: rm } = live.current;
      const f = flags.current;
      if (rm) {
        if (!f.dirty) return;
        if (!f.paused && b.length) dc = b[b.length - 1].t;
      } else if (!f.paused) {
        const target = Date.now() - delay;
        // resume smoothly: run slightly faster until we've caught up with live
        dc += dt * (1 + clamp((target - dc) / 2000, 0, 1.5));
        if (dc > target) dc = target;
        if (target - dc > win) dc = target;
      }
      // forget what has scrolled far out of view
      const keep = dc - win - 5000;
      while (b.length > 2 && b[1].t < keep) b.shift();
      draw(dt, now);
      f.dirty = false;
    };

    const shouldRun = () => visible && !document.hidden;
    const start = () => {
      if (raf || !shouldRun()) return;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    };
    const stop = () => {
      cancelAnimationFrame(raf);
      raf = 0;
    };
    const sync = () => (shouldRun() ? start() : stop());

    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      sync();
    });
    if (wrapRef.current) io.observe(wrapRef.current);
    document.addEventListener("visibilitychange", sync);
    start();
    return () => {
      stop();
      io.disconnect();
      document.removeEventListener("visibilitychange", sync);
    };
  }, []);

  /* pointer: hovering pauses the stream and lets you scrub it */
  const pointer = (e: React.PointerEvent<SVGRectElement>) => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return;
    flags.current.hoverX = e.clientX - box.left;
    flags.current.paused = true;
    flags.current.dirty = true;
    setHovering(true);
  };
  const leave = (e: React.PointerEvent) => {
    if (e.pointerType !== "mouse") return;
    flags.current.hoverX = null;
    flags.current.dirty = true;
    setHovering(false);
    setScrub(null);
  };

  const g = { l: LIVE_M.l, t: LIVE_M.t, iw: Math.max(10, width - LIVE_M.l - LIVE_M.r), ih: Math.max(10, height - LIVE_M.t - LIVE_M.b) };
  const delta = latest !== null && first !== null && first !== 0 ? ((latest - first) / Math.abs(first)) * 100 : null;
  const winS = windowSeconds;

  return (
    <div ref={rootRef} className={cx("w-full", className)}>
      {showReadout && (
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[28px] font-semibold leading-none tracking-[-0.035em] tabular-nums text-neutral-900 dark:text-neutral-50 sm:text-[32px]">
              {latest === null ? "—" : formatValue(latest)}
              {unit && <span className={`ml-1.5 text-[13px] font-normal tracking-normal ${muted}`}>{unit}</span>}
            </p>
            {delta !== null && (
              <p className={`mt-1.5 text-[12px] tabular-nums ${muted}`}>
                <span className={delta >= 0 ? "text-emerald-500" : "text-rose-500"}>
                  {delta >= 0 ? "▲" : "▼"} {pctText(Math.abs(delta))}
                </span>{" "}
                over the last {winS >= 120 ? `${Math.round(winS / 60)} min` : `${winS}s`}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => setManualPause((p) => !p)}
            aria-pressed={manualPause}
            aria-label={manualPause ? "Resume live updates" : "Pause live updates"}
            className={`inline-flex h-9 items-center gap-2 rounded-full bg-black/[0.04] pl-3 pr-3.5 text-[12px] font-medium text-neutral-600 transition-[background-color,transform] duration-150 active:scale-[0.97] dark:bg-white/[0.05] dark:text-neutral-300 sm:h-8 [@media(hover:hover)]:hover:bg-black/[0.07] dark:[@media(hover:hover)]:hover:bg-white/[0.09] ${focus}`}
          >
            <span className="relative flex h-2 w-2">
              {!paused && !reduce && <span className="absolute inset-0 animate-ping rounded-full bg-emerald-400/70" />}
              <span className={cx("relative h-2 w-2 rounded-full", paused ? "bg-amber-400" : "bg-emerald-500")} />
            </span>
            {paused ? "Paused" : "Live"}
          </button>
        </div>
      )}

      <div ref={wrapRef} className="relative" style={{ height }}>
        <svg
          ref={svgRef}
          width={width}
          height={height}
          role="img"
          aria-label={ariaLabel ?? `Live chart of the last ${winS} seconds${latest !== null ? `, currently ${formatValue(latest)} ${unit}` : ""}`}
          className="block touch-pan-y overflow-visible"
        >
          <defs>
            <linearGradient id={`${uid}-a`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={color} stopOpacity="0.26" />
              <stop offset="1" stopColor={color} stopOpacity="0" />
            </linearGradient>
            <clipPath id={`${uid}-c`}>
              <rect x={g.l} y={0} width={g.iw + LIVE_M.r} height={height} />
            </clipPath>
          </defs>

          {[0, 1, 2].map((i) => {
            const gy = g.t + (g.ih / 2) * i;
            return (
              <g key={i}>
                <line x1={g.l} x2={width - LIVE_M.r} y1={gy} y2={gy} className="stroke-black/[0.06] dark:stroke-white/[0.07]" />
                <text
                  ref={(el) => {
                    tickEls.current[i] = el;
                  }}
                  x={g.l - 8}
                  y={gy}
                  textAnchor="end"
                  dominantBaseline="middle"
                  className="fill-neutral-500 text-[11px] tabular-nums"
                />
              </g>
            );
          })}
          {[0, 0.5, 1].map((p, i) => (
            <text
              key={i}
              x={g.l + g.iw * p}
              y={height - 6}
              textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}
              className="fill-neutral-500 text-[11px]"
            >
              {i === 2 ? "now" : `−${Math.round(winS * (1 - p))}s`}
            </text>
          ))}

          <g clipPath={`url(#${uid}-c)`}>
            <path ref={areaEl} fill={`url(#${uid}-a)`} />
            <path ref={lineEl} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          </g>

          {/* head of the line */}
          <motion.circle
            ref={haloEl}
            r={4}
            fill={color}
            style={{ transformBox: "fill-box", transformOrigin: "center", opacity: hovering ? 0 : 1 }}
            animate={paused || reduce ? { scale: 1, opacity: 0 } : { scale: [1, 3], opacity: [0.4, 0] }}
            transition={paused || reduce ? { duration: 0 } : { repeat: Infinity, duration: 1.6, ease: "easeOut" }}
          />
          <circle ref={headEl} r={4} fill={color} className="stroke-white dark:stroke-[#0b0b0c]" strokeWidth={2} />

          {/* scrubber */}
          <g style={{ opacity: hovering ? 1 : 0, transition: "opacity .12s" }} pointerEvents="none">
            <line ref={crossLine} y1={g.t} y2={g.t + g.ih} strokeDasharray="2 3" className="stroke-black/20 dark:stroke-white/25" />
            <circle ref={crossDot} r={5} fill={color} className="stroke-white dark:stroke-[#0b0b0c]" strokeWidth={2} />
          </g>

          <rect
            x={g.l}
            y={g.t}
            width={g.iw}
            height={g.ih}
            fill="transparent"
            onPointerMove={pointer}
            onPointerDown={pointer}
            onPointerLeave={leave}
          />
        </svg>

        <Tooltip pos={scrub && hovering ? { x: scrub.x, y: scrub.y } : null} boundsWidth={width}>
          {scrub && (
            <>
              <p className="font-medium tabular-nums text-neutral-900 dark:text-neutral-100">
                {formatValue(scrub.v)}
                {unit && <span className="ml-1 font-normal text-neutral-500">{unit}</span>}
              </p>
              <p className="mt-0.5 tabular-nums text-neutral-500">{scrub.ago < 0.5 ? "now" : `${scrub.ago.toFixed(1)}s ago`}</p>
            </>
          )}
        </Tooltip>
      </div>
      <p className="sr-only" role="status" aria-live="polite">
        {paused ? "Live updates paused" : ""}
      </p>
    </div>
  );
}
