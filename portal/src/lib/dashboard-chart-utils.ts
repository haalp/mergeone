export const pctText = (x: number) => `${Math.abs(x) >= 10 ? Math.round(x) : x.toFixed(1)}%`;

/** A value sampled at a timestamp (UTC ms) */
export interface TimestampedPoint {
  t: number;
  v: number;
}

/** Smooth monotone cubic through points (never overshoots the data) */
export function monotonePath(p: [number, number][]): string {
  const n = p.length;
  if (n === 0) return "";
  if (n === 1) return `M${p[0][0].toFixed(2)} ${p[0][1].toFixed(2)}`;
  const dx: number[] = [], m: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    dx[i] = p[i + 1][0] - p[i][0];
    m[i] = (p[i + 1][1] - p[i][1]) / (dx[i] || 1e-9);
  }
  const t: number[] = new Array(n);
  t[0] = m[0];
  t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) {
      t[i] = 0;
      t[i + 1] = 0;
    } else {
      const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
      if (s > 9) {
        const k = 3 / Math.sqrt(s);
        t[i] = k * a * m[i];
        t[i + 1] = k * b * m[i];
      }
    }
  }
  let d = `M${p[0][0].toFixed(2)} ${p[0][1].toFixed(2)}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += `C${(p[i][0] + h).toFixed(2)} ${(p[i][1] + t[i] * h).toFixed(2)} ${(p[i + 1][0] - h).toFixed(2)} ${(p[i + 1][1] - t[i + 1] * h).toFixed(2)} ${p[i + 1][0].toFixed(2)} ${p[i + 1][1].toFixed(2)}`;
  }
  return d;
}

/** Linear interpolation of the stream at time t (clamped to the data) */
export function valueAt(buf: TimestampedPoint[], t: number): number | null {
  const n = buf.length;
  if (!n) return null;
  if (t <= buf[0].t) return buf[0].v;
  if (t >= buf[n - 1].t) return buf[n - 1].v;
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (buf[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const a = buf[lo], b = buf[hi];
  return a.v + ((t - a.t) / (b.t - a.t || 1)) * (b.v - a.v);
}

export function visibleExtent(buf: TimestampedPoint[], tEnd: number, win: number) {
  const tStart = tEnd - win;
  let min = Infinity, max = -Infinity;
  const edge = [valueAt(buf, tStart), valueAt(buf, tEnd)];
  for (const v of edge) if (v !== null) { min = Math.min(min, v); max = Math.max(max, v); }
  for (const p of buf) if (p.t > tStart && p.t <= tEnd) { min = Math.min(min, p.v); max = Math.max(max, p.v); }
  return { min, max };
}

export function buildFrame(
  buf: TimestampedPoint[],
  tEnd: number,
  win: number,
  dom: { lo: number; hi: number },
  g: { l: number; t: number; iw: number; ih: number },
) {
  const tStart = tEnd - win;
  const span = dom.hi - dom.lo || 1;
  const X = (t: number) => g.l + ((t - tStart) / win) * g.iw;
  const Y = (v: number) => g.t + g.ih - ((v - dom.lo) / span) * g.ih;
  const pts: [number, number][] = [];
  const v0 = valueAt(buf, tStart);
  if (v0 !== null && buf.length && buf[0].t <= tStart) pts.push([g.l, Y(v0)]);
  for (const p of buf) if (p.t > tStart && p.t < tEnd) pts.push([X(p.t), Y(p.v)]);
  const vh = valueAt(buf, tEnd);
  let head: { x: number; y: number } | null = null;
  if (vh !== null) {
    head = { x: g.l + g.iw, y: Y(vh) };
    pts.push([head.x, head.y]);
  }
  const line = pts.length > 1 ? monotonePath(pts) : "";
  const base = g.t + g.ih;
  const area = line ? `${line}L${pts[pts.length - 1][0].toFixed(2)} ${base}L${pts[0][0].toFixed(2)} ${base}Z` : "";
  return { line, area, head, tStart };
}
