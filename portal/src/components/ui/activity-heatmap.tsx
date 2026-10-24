"use client";

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  Tooltip,
  clamp,
  cx,
  fullFmt,
  muted,
  useDismissOutside,
  useElementWidth,
} from "../../lib/chart-primitives";

export interface HeatDay {
  /** "YYYY-MM-DD" or a Date */
  date: string | Date;
  value: number;
}

export interface ActivityHeatmapProps {
  /** calendar: one cell per day (GitHub style) · hours: weekday × hour grid */
  mode?: "calendar" | "hours";
  /** calendar mode */
  data?: HeatDay[];
  /** hours mode: 7 rows × 24 columns */
  matrix?: number[][];
  /** hours mode row names (default Mon…Sun) */
  rowLabels?: string[];
  /** Most weeks to show. Narrow screens automatically show fewer (default 53) */
  weeks?: number;
  /** Last day shown (default: latest date in data) */
  endDate?: string | Date;
  /** 0 = Sunday, 1 = Monday */
  weekStart?: 0 | 1;
  color?: string;
  /** Plural noun used in the tooltip / summary */
  unit?: string;
  /** Smallest cell size in px before weeks are dropped (default 11) */
  minCell?: number;
  animated?: boolean;
  ariaLabel?: string;
  className?: string;
}

const DAY_MS = 86_400_000;
const toDay = (d: string | Date): number | null => {
  if (d instanceof Date) return isNaN(+d) ? null : Math.floor(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / DAY_MS);
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d);
  if (m) return Math.floor(Date.UTC(+m[1], +m[2] - 1, +m[3]) / DAY_MS);
  const p = new Date(d);
  return isNaN(+p) ? null : toDay(p);
};
const weekday = (day: number) => (((day + 4) % 7) + 7) % 7; // 0 = Sunday
const dayFmt = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const monthFmt = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const LEVEL_MIX = [0, 28, 52, 76, 100];

interface HeatCell {
  c: number;
  r: number;
  v: number;
  title: string;
}

export function ActivityHeatmap({
  mode = "calendar",
  data = [],
  matrix,
  rowLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
  weeks = 53,
  endDate,
  weekStart = 0,
  color = "#34d399",
  unit = "contributions",
  minCell = 11,
  animated = true,
  ariaLabel,
  className = "",
}: ActivityHeatmapProps) {
  const reduce = useReducedMotion();
  const anim = animated && !reduce;
  const [rootRef, width] = useElementWidth<HTMLDivElement>(720);
  const [active, setActive] = useState<{ c: number; r: number } | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  useDismissOutside(wrapRef, active !== null, useCallback(() => setActive(null), []));

  const hours = mode === "hours";
  const narrow = width < 440;
  const LW = hours ? (narrow ? 30 : 36) : narrow ? 18 : 30;
  const TOP = 18;
  const gap = hours ? (narrow ? 2 : 3) : 3;
  const innerW = Math.max(40, width - LW);

  // columns that fit
  const cols = hours ? 24 : clamp(Math.min(weeks, Math.floor((innerW + gap) / (minCell + gap))), 4, weeks);
  const cw = (innerW - (cols - 1) * gap) / cols;
  const cell = hours ? cw : Math.min(cw, 16);
  const ch = hours ? clamp(cw, 10, 22) : cell;
  const cwEff = hours ? cw : cell;

  // build cells
  const model = useMemo(() => {
    const out: HeatCell[] = [];
    let first = 0;
    let endDay = 0;
    if (hours) {
      const m = matrix ?? [];
      for (let r = 0; r < 7; r++)
        for (let c = 0; c < 24; c++) {
          const v = Math.max(0, Number(m[r]?.[c]) || 0);
          out.push({ c, r, v, title: `${rowLabels[r] ?? ""} · ${String(c).padStart(2, "0")}:00` });
        }
    } else {
      const map = new Map<number, number>();
      let maxDay = -Infinity;
      for (const d of data) {
        const day = toDay(d.date);
        if (day === null) continue;
        map.set(day, (map.get(day) ?? 0) + (Number.isFinite(d.value) ? Math.max(0, d.value) : 0));
        maxDay = Math.max(maxDay, day);
      }
      endDay = (endDate ? toDay(endDate) : null) ?? (isFinite(maxDay) ? maxDay : (toDay(new Date()) as number));
      const lastColStart = endDay - ((weekday(endDay) - weekStart + 7) % 7);
      first = lastColStart - (cols - 1) * 7;
      for (let c = 0; c < cols; c++)
        for (let r = 0; r < 7; r++) {
          const day = first + c * 7 + r;
          if (day > endDay) continue;
          out.push({ c, r, v: map.get(day) ?? 0, title: dayFmt.format(new Date(day * DAY_MS)) });
        }
    }
    const nz = out.filter((x) => x.v > 0).map((x) => x.v).sort((a, b) => a - b);
    const q = (p: number) => nz[Math.min(nz.length - 1, Math.floor(p * nz.length))] ?? 0;
    const t1 = q(0.25), t2 = q(0.5), t3 = q(0.75);
    const level = (v: number) => (v <= 0 ? 0 : v <= t1 ? 1 : v <= t2 ? 2 : v <= t3 ? 3 : 4);
    const total = out.reduce((s, x) => s + x.v, 0);
    const best = out.reduce<HeatCell | null>((b, x) => (!b || x.v > b.v ? x : b), null);
    return { cells: out, level, total, best, first, endDay, active: nz.length };
  }, [hours, matrix, rowLabels, data, endDate, weekStart, cols]);

  const byKey = useMemo(() => new Map(model.cells.map((x) => [`${x.c}:${x.r}`, x])), [model.cells]);
  const gx = (c: number) => LW + c * (cwEff + gap);
  const gy = (r: number) => TOP + r * (ch + gap);
  const svgH = TOP + 7 * ch + 6 * gap + 2;

  // month / hour labels
  const topLabels = useMemo(() => {
    const out: { c: number; text: string }[] = [];
    if (hours) {
      const every = cwEff < 14 ? 6 : 3;
      for (let c = 0; c < 24; c += every) out.push({ c, text: String(c) });
    } else {
      let prev = -1;
      let lastC = -9;
      for (let c = 0; c < cols; c++) {
        const m = new Date((model.first + c * 7) * DAY_MS).getUTCMonth();
        if (m !== prev) {
          if (prev !== -1 && c - lastC >= 3 && c <= cols - 2) {
            out.push({ c, text: monthFmt.format(new Date((model.first + c * 7) * DAY_MS)) });
            lastC = c;
          } else if (prev === -1 && cols > 6) {
            out.push({ c, text: monthFmt.format(new Date((model.first + c * 7) * DAY_MS)) });
            lastC = c;
          }
          prev = m;
        }
      }
    }
    return out;
  }, [hours, cols, cwEff, model.first]);

  const rowNames = hours
    ? rowLabels.map((l, r) => ({ r, text: narrow ? l.slice(0, 1) : l })).filter((x) => !narrow || x.r % 2 === 0)
    : [1, 3, 5].map((k) => {
        const r = (k - weekStart + 7) % 7;
        return { r, text: narrow ? WEEKDAYS[k].slice(0, 1) : WEEKDAYS[k] };
      });

  const locate = (e: ReactPointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const c = Math.floor((e.clientX - box.left - LW) / (cwEff + gap));
    const r = Math.floor((e.clientY - box.top - TOP) / (ch + gap));
    if (c < 0 || r < 0 || c >= cols || r > 6 || !byKey.has(`${c}:${r}`)) return setActive(null);
    setActive({ c, r });
  };
  const onKey = (e: ReactKeyboardEvent) => {
    const dc = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    const dr = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
    if (e.key === "Escape") return setActive(null);
    if (!dc && !dr) return;
    e.preventDefault();
    setActive((a) => {
      const from = a ?? { c: cols - 1, r: 0 };
      const next = { c: clamp(from.c + dc, 0, cols - 1), r: clamp(from.r + dr, 0, 6) };
      return byKey.has(`${next.c}:${next.r}`) ? next : from;
    });
  };

  const cur = active ? byKey.get(`${active.c}:${active.r}`) : null;
  const total = model.total;
  const summary = hours
    ? `${fullFmt.format(total)} ${unit} per week`
    : `${fullFmt.format(total)} ${unit} in the last ${cols >= 52 ? "year" : cols >= 8 ? `${Math.round(cols / 4.345)} months` : `${cols} weeks`}`;
  const lvlFill = (l: number): CSSProperties =>
    l === 0 ? {} : { fill: `color-mix(in srgb, ${color} ${LEVEL_MIX[l]}%, transparent)` };

  return (
    <div ref={rootRef} className={cx("w-full", className)}>
      <style
        dangerouslySetInnerHTML={{
          __html:
            "@keyframes cj-cell{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}" +
            ".cj-cell{transform-box:fill-box;transform-origin:center;animation:cj-cell .55s cubic-bezier(.23,1,.32,1) both;animation-delay:var(--d,0s)}" +
            "@media (prefers-reduced-motion: reduce){.cj-cell{animation:none}}",
        }}
      />

      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-[13px] font-medium tabular-nums text-neutral-900 dark:text-neutral-100">{summary}</p>
        {model.best && model.best.v > 0 && (
          <p className={`text-[12px] ${muted}`}>
            Peak {model.best.title} · <span className="tabular-nums">{fullFmt.format(model.best.v)}</span>
          </p>
        )}
      </div>

      <div ref={wrapRef} className="relative" style={{ height: svgH }}>
        <svg
          width={width}
          height={svgH}
          role="img"
          aria-label={ariaLabel ?? `Activity heatmap. ${summary}`}
          tabIndex={0}
          onKeyDown={onKey}
          onBlur={() => setActive(null)}
          className="block touch-pan-y overflow-visible rounded-md outline-none focus-visible:ring-2 focus-visible:ring-neutral-400/60 dark:focus-visible:ring-white/30"
        >
          {topLabels.map((l) => (
            <text key={l.c} x={gx(l.c)} y={11} className="fill-neutral-500 text-[11px]">
              {l.text}
            </text>
          ))}
          {rowNames.map((l) => (
            <text key={l.r} x={0} y={gy(l.r) + ch / 2} dominantBaseline="middle" className="fill-neutral-500 text-[10.5px]">
              {l.text}
            </text>
          ))}

          {/* crosshair bands (hours mode) */}
          {hours && (
            <>
              <motion.rect
                x={LW - 2}
                width={24 * (cwEff + gap)}
                height={ch + 4}
                rx={5}
                className="fill-black/[0.035] dark:fill-white/[0.04]"
                initial={false}
                animate={{ y: gy(active?.r ?? 0) - 2, opacity: active ? 1 : 0 }}
                transition={reduce ? { duration: 0 } : { duration: 0.15 }}
              />
              <motion.rect
                y={TOP - 2}
                width={cwEff + 4}
                height={7 * (ch + gap)}
                rx={5}
                className="fill-black/[0.035] dark:fill-white/[0.04]"
                initial={false}
                animate={{ x: gx(active?.c ?? 0) - 2, opacity: active ? 1 : 0 }}
                transition={reduce ? { duration: 0 } : { duration: 0.15 }}
              />
            </>
          )}

          {model.cells.map((x) => {
            const l = model.level(x.v);
            return (
              <rect
                key={`${x.c}:${x.r}`}
                x={gx(x.c)}
                y={gy(x.r)}
                width={cwEff}
                height={ch}
                rx={Math.min(3, cwEff / 4)}
                className={cx(anim && "cj-cell", l === 0 && "fill-black/[0.05] dark:fill-white/[0.06]")}
                style={{ ...lvlFill(l), ["--d" as string]: anim ? `${(x.c * 0.012 + x.r * 0.02).toFixed(3)}s` : undefined } as CSSProperties}
              />
            );
          })}

          {/* active ring */}
          <motion.rect
            width={cwEff + 2}
            height={ch + 2}
            rx={Math.min(4, cwEff / 3)}
            fill="none"
            strokeWidth={1.5}
            className="stroke-neutral-900 dark:stroke-white"
            initial={false}
            animate={{ x: gx(active?.c ?? 0) - 1, y: gy(active?.r ?? 0) - 1, opacity: active ? 1 : 0 }}
            transition={reduce ? { duration: 0 } : { x: { duration: 0.12 }, y: { duration: 0.12 }, opacity: { duration: 0.12 } }}
          />

          <rect
            x={LW}
            y={TOP}
            width={cols * (cwEff + gap)}
            height={7 * (ch + gap)}
            fill="transparent"
            onPointerMove={locate}
            onPointerDown={locate}
            onPointerLeave={(e) => e.pointerType === "mouse" && setActive(null)}
          />
        </svg>

        <Tooltip pos={active ? { x: gx(active.c) + cwEff / 2, y: gy(active.r) } : null} boundsWidth={width}>
          {cur && (
            <>
              <p className="font-medium tabular-nums text-neutral-900 dark:text-neutral-100">
                {fullFmt.format(cur.v)} <span className="font-normal text-neutral-500">{unit}</span>
              </p>
              <p className="mt-0.5 text-neutral-500">{cur.title}</p>
            </>
          )}
        </Tooltip>
      </div>

      <p className="sr-only" role="status" aria-live="polite">
        {cur ? `${cur.title}: ${fullFmt.format(cur.v)} ${unit}` : ""}
      </p>

      <div className="mt-3 flex items-center justify-end gap-2 text-[11.5px] text-neutral-500">
        Less
        <span className="flex gap-[3px]" aria-hidden>
          {LEVEL_MIX.map((m, i) => (
            <span
              key={i}
              className={cx("h-[11px] w-[11px] rounded-[3px]", i === 0 && "bg-black/[0.05] dark:bg-white/[0.06]")}
              style={i === 0 ? undefined : { background: `color-mix(in srgb, ${color} ${m}%, transparent)` }}
            />
          ))}
        </span>
        More
      </div>
    </div>
  );
}
