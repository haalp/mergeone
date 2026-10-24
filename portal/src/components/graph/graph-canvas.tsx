"use client";

import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { Maximize, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { HEX } from "@/lib/format";
import type { EdgeType, GraphEdge, GraphNode } from "@/lib/types";

export type SimNode = GraphNode & SimulationNodeDatum & { r: number; deg: number; cluster: number };
type SimLink = SimulationLinkDatum<SimNode> & { edge: GraphEdge };

interface View {
  x: number;
  y: number;
  k: number;
}

export const EDGE_STYLE: Record<EdgeType | "duplicate_of", { color: string; dash: number[]; label: string }> = {
  duplicate_of: { color: HEX.brand, dash: [], label: "duplicate of" },
  same_change: { color: HEX.brand, dash: [], label: "same change" },
  backport: { color: HEX.subtle, dash: [6, 4], label: "backport" },
  competing: { color: HEX.hold, dash: [], label: "competing" },
  builds_on: { color: HEX.pass, dash: [], label: "builds on" },
};

const VERDICT_HEX = { pass: HEX.pass, hold: HEX.hold, block: HEX.block } as const;

export function edgeLabel(e: GraphEdge) {
  const s = EDGE_STYLE[e.type ?? "duplicate_of"];
  return e.type === "backport" && e.note ? `${s.label} · ${e.note}` : `${s.label} ${e.p.toFixed(2)}`;
}

/** Connected components over the edges: node id → cluster index (singletons get their own). */
export function clusters(nodes: GraphNode[], edges: GraphEdge[]): Map<string, number> {
  const parent = new Map(nodes.map((n) => [n.id, n.id]));
  const find = (x: string): string => {
    const p = parent.get(x) ?? x;
    if (p === x) return x;
    const root = find(p);
    parent.set(x, root);
    return root;
  };
  for (const e of edges) if (parent.has(e.source) && parent.has(e.target)) parent.set(find(e.source), find(e.target));
  const ids = new Map<string, number>();
  const out = new Map<string, number>();
  for (const n of nodes) {
    const root = find(n.id);
    if (!ids.has(root)) ids.set(root, ids.size);
    out.set(n.id, ids.get(root)!);
  }
  return out;
}

export function GraphCanvas({
  nodes,
  edges,
  selectedCluster,
  selectedEdge,
  onSelectNode,
  onSelectEdge,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedCluster: number | null;
  selectedEdge: GraphEdge | null;
  onSelectNode: (n: SimNode | null) => void;
  onSelectEdge: (e: GraphEdge) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 800, h: 640 });
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [hover, setHover] = useState<{ node: SimNode; x: number; y: number } | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);
  const [dragging, setDragging] = useState(false);

  // Layout: a static d3-force run (deterministic initial positions → the same picture every load).
  const sim = useMemo(() => {
    const cl = clusters(nodes, edges);
    const deg = new Map<string, number>();
    for (const e of edges) {
      deg.set(e.source, (deg.get(e.source) ?? 0) + 1);
      deg.set(e.target, (deg.get(e.target) ?? 0) + 1);
    }
    const simNodes: SimNode[] = nodes.map((n) => {
      const d = deg.get(n.id) ?? 0;
      const base = Math.sqrt(n.additions + n.deletions);
      return { ...n, deg: d, cluster: cl.get(n.id) ?? -1, r: d ? Math.min(46, 22 + base * 1.4) : Math.min(24, 14 + base * 0.5) };
    });
    const byId = new Map(simNodes.map((n) => [n.id, n]));
    // Each related cluster gets its own cell (grouped by repository), so clusters never interleave
    // and their edges don't cross; unrelated PRs float loosely around them.
    const related = [...new Set(simNodes.filter((n) => n.deg).map((n) => n.cluster))].sort((a, b) => {
      const ra = simNodes.find((n) => n.cluster === a)!.repo, rb = simNodes.find((n) => n.cluster === b)!.repo;
      return ra.localeCompare(rb) || a - b;
    });
    const cols = Math.max(1, Math.ceil(Math.sqrt(related.length)));
    const rows = Math.ceil(related.length / cols);
    const CELL = 330;
    const anchor = new Map(
      related.map((c, i) => [c, { x: ((i % cols) - (cols - 1) / 2) * CELL, y: (Math.floor(i / cols) - (rows - 1) / 2) * CELL }]),
    );
    // Pulls each related cluster's centroid (not each node) toward its cell: the cluster keeps its shape.
    const clusterPull = (alpha: number) => {
      const sum = new Map<number, { x: number; y: number; n: number }>();
      for (const n of simNodes) {
        if (!n.deg) continue;
        const a = sum.get(n.cluster) ?? { x: 0, y: 0, n: 0 };
        sum.set(n.cluster, { x: a.x + n.x!, y: a.y + n.y!, n: a.n + 1 });
      }
      for (const n of simNodes) {
        const c = n.deg ? sum.get(n.cluster) : undefined;
        if (!c) continue;
        const t = anchor.get(n.cluster)!;
        n.vx! += (t.x - c.x / c.n) * alpha * 0.3;
        n.vy! += (t.y - c.y / c.n) * alpha * 0.3;
      }
    };
    const links: SimLink[] = edges
      .filter((e) => byId.has(e.source) && byId.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, edge: e }));
    forceSimulation(simNodes)
      .force("charge", forceManyBody<SimNode>().strength((n) => (n.deg ? -420 : -60)))
      .force(
        "link",
        forceLink<SimNode, SimLink>(links)
          .id((n) => n.id)
          .distance((l) => (l.source as SimNode).r + (l.target as SimNode).r + 120 + (1 - l.edge.p) * 60)
          .strength(0.7),
      )
      .force("collide", forceCollide<SimNode>().radius((n) => n.r + 14))
      .force("cluster", clusterPull)
      .force("x", forceX<SimNode>(0).strength((n) => (n.deg ? 0 : 0.06)))
      .force("y", forceY<SimNode>(0).strength((n) => (n.deg ? 0 : 0.08)))
      .stop()
      .tick(400);
    return { nodes: simNodes, links };
  }, [nodes, edges]);

  const fit = useCallback(() => {
    if (!sim.nodes.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of sim.nodes) {
      x0 = Math.min(x0, n.x! - n.r);
      y0 = Math.min(y0, n.y! - n.r);
      x1 = Math.max(x1, n.x! + n.r);
      y1 = Math.max(y1, n.y! + n.r);
    }
    const pad = 48;
    const k = Math.min(2, (size.w - pad * 2) / (x1 - x0 || 1), (size.h - pad * 2) / (y1 - y0 || 1));
    setView({ k, x: size.w / 2 - ((x0 + x1) / 2) * k, y: size.h / 2 - ((y0 + y1) / 2) * k });
  }, [sim, size]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Fit when the data or the box changes.
  useEffect(() => {
    const id = requestAnimationFrame(fit);
    return () => cancelAnimationFrame(id);
  }, [fit]);

  const zoom = (f: number) =>
    setView((v) => {
      const k = Math.max(0.3, Math.min(4, v.k * f));
      const cx = size.w / 2, cy = size.h / 2;
      return { k, x: cx - ((cx - v.x) / v.k) * k, y: cy - ((cy - v.y) / v.k) * k };
    });

  // Draw.
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr;
    c.height = size.h * dpr;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const mono = getComputedStyle(document.documentElement).getPropertyValue("--font-geist-mono").trim() || "ui-monospace, monospace";
    ctx.clearRect(0, 0, size.w, size.h);
    ctx.save();
    ctx.translate(view.x, view.y);
    ctx.scale(view.k, view.k);

    const dimmed = (n: SimNode) => (selectedCluster !== null ? n.cluster !== selectedCluster : n.deg === 0);

    for (const l of sim.links) {
      const s = l.source as SimNode, t = l.target as SimNode;
      const st = EDGE_STYLE[l.edge.type ?? "duplicate_of"];
      const isSel = selectedEdge === l.edge;
      ctx.globalAlpha = selectedCluster !== null && s.cluster !== selectedCluster ? 0.2 : l.edge.type ? 1 : 0.85;
      ctx.strokeStyle = st.color;
      ctx.lineWidth = (1 + l.edge.p * 2.5 + (isSel ? 2 : 0)) / Math.sqrt(view.k);
      ctx.setLineDash(st.dash);
      ctx.beginPath();
      ctx.moveTo(s.x!, s.y!);
      ctx.lineTo(t.x!, t.y!);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    for (const n of sim.nodes) {
      const dim = dimmed(n);
      ctx.globalAlpha = dim ? 0.35 : 1;
      const col = VERDICT_HEX[n.verdict];
      if (!dim && n.verdict !== "pass") {
        ctx.beginPath();
        ctx.arc(n.x!, n.y!, n.r + 6, 0, Math.PI * 2);
        ctx.fillStyle = n.verdict === "hold" ? "rgba(210,153,34,0.15)" : "rgba(248,81,73,0.15)";
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(n.x!, n.y!, n.r, 0, Math.PI * 2);
      ctx.fillStyle = n.deg ? HEX.raised : HEX.card;
      ctx.fill();
      ctx.lineWidth = n.deg ? 3 : 2;
      ctx.strokeStyle = col;
      ctx.stroke();
      if (hover?.node === n) {
        ctx.lineWidth = 1;
        ctx.strokeStyle = HEX.text;
        ctx.beginPath();
        ctx.arc(n.x!, n.y!, n.r + 3, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (n.deg || view.k > 1.3) {
        ctx.fillStyle = HEX.text;
        ctx.font = `${n.deg ? 13 : 10}px ${mono}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(`#${n.pr}`, n.x!, n.y!);
      }
    }
    // Edge labels last, so a node never hides one; placed at the midpoint of the visible segment.
    for (const l of sim.links) {
      const s = l.source as SimNode, t = l.target as SimNode;
      const st = EDGE_STYLE[l.edge.type ?? "duplicate_of"];
      ctx.globalAlpha = selectedCluster !== null && s.cluster !== selectedCluster ? 0.25 : 1;
      const len = Math.hypot(t.x! - s.x!, t.y! - s.y!) || 1;
      const u = (s.r + (len - s.r - t.r) / 2) / len;
      const mx = s.x! + (t.x! - s.x!) * u, my = s.y! + (t.y! - s.y!) * u;
      const text = edgeLabel(l.edge);
      ctx.font = `${11 / view.k}px ${mono}`;
      const w = ctx.measureText(text).width;
      const pad = 5 / view.k;
      ctx.fillStyle = HEX.panel;
      ctx.fillRect(mx - w / 2 - pad, my - 8 / view.k, w + pad * 2, 16 / view.k);
      ctx.fillStyle = st.color;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(text, mx, my);
    }
    ctx.restore();
    ctx.globalAlpha = 1;
  }, [sim, size, view, selectedCluster, selectedEdge, hover]);

  const toWorld = (e: React.PointerEvent) => {
    const box = canvasRef.current!.getBoundingClientRect();
    const sx = e.clientX - box.left, sy = e.clientY - box.top;
    return { sx, sy, x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
  };
  const nodeAt = (x: number, y: number) =>
    [...sim.nodes].reverse().find((n) => (n.x! - x) ** 2 + (n.y! - y) ** 2 <= n.r * n.r) ?? null;
  const edgeAt = (x: number, y: number) => {
    const tol = 8 / view.k;
    for (const l of sim.links) {
      const s = l.source as SimNode, t = l.target as SimNode;
      const dx = t.x! - s.x!, dy = t.y! - s.y!;
      const len2 = dx * dx + dy * dy || 1;
      const u = Math.max(0, Math.min(1, ((x - s.x!) * dx + (y - s.y!) * dy) / len2));
      const px = s.x! + u * dx, py = s.y! + u * dy;
      if ((x - px) ** 2 + (y - py) ** 2 <= tol * tol) return l.edge;
    }
    return null;
  };

  const unrelated = sim.nodes.filter((n) => n.deg === 0).length;

  return (
    <Card className="relative min-h-[480px] flex-1 overflow-hidden rounded-xl bg-panel p-0 shadow-none lg:min-h-[640px]">
      <div ref={wrapRef} className="absolute inset-0">
        <canvas
          ref={canvasRef}
          style={{ width: size.w, height: size.h, cursor: hover ? "pointer" : dragging ? "grabbing" : "grab", touchAction: "none" }}
          aria-label="Graph of open pull requests and the relations the gate found between them"
          role="img"
          onPointerDown={(e) => {
            (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
            drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
          }}
          onPointerMove={(e) => {
            const d = drag.current;
            if (d) {
              const dx = e.clientX - d.x, dy = e.clientY - d.y;
              if (!d.moved && Math.abs(dx) + Math.abs(dy) > 3) {
                d.moved = true;
                setDragging(true);
              }
              if (d.moved) setView((v) => ({ ...v, x: d.vx + dx, y: d.vy + dy }));
              return;
            }
            const p = toWorld(e);
            const n = nodeAt(p.x, p.y);
            setHover(n ? { node: n, x: p.sx, y: p.sy } : null);
          }}
          onPointerUp={(e) => {
            const d = drag.current;
            drag.current = null;
            setDragging(false);
            if (d?.moved) return;
            const p = toWorld(e);
            const n = nodeAt(p.x, p.y);
            if (n) return onSelectNode(n);
            const ed = edgeAt(p.x, p.y);
            if (ed) return onSelectEdge(ed);
            onSelectNode(null);
          }}
          onPointerLeave={() => setHover(null)}
        />
      </div>

      {hover && (
        <div
          className="pointer-events-none absolute z-10 max-w-72 rounded-md border bg-popover px-3 py-2 text-xs shadow-lg"
          style={{ left: Math.min(hover.x + 14, size.w - 290), top: hover.y + 14 }}
        >
          <div className="truncate font-medium text-foreground">
            #{hover.node.pr} {hover.node.title}
          </div>
          <div className="mt-0.5 text-muted-foreground">
            <span style={{ color: VERDICT_HEX[hover.node.verdict] }}>{hover.node.verdict}</span> · {hover.node.route} ·{" "}
            {hover.node.files} files · +{hover.node.additions} −{hover.node.deletions}
          </div>
        </div>
      )}

      <div className="absolute bottom-4 left-4 font-mono text-[11px] text-muted-foreground">
        {unrelated} unrelated {unrelated === 1 ? "PR" : "PRs"} dimmed · drag to pan · click a node or an edge
      </div>
      <div className="absolute right-4 bottom-4 flex gap-1.5" role="group" aria-label="Zoom">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button size="icon-sm" variant="outline" className="border-border-strong bg-card text-subtle" onClick={() => zoom(1.25)} aria-label="Zoom in">
              <Plus />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Zoom in</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button size="icon-sm" variant="outline" className="border-border-strong bg-card text-subtle" onClick={() => zoom(0.8)} aria-label="Zoom out">
              <Minus />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Zoom out</TooltipContent>
        </Tooltip>
        <Button size="sm" variant="outline" className="border-border-strong bg-card text-subtle" onClick={fit}>
          <Maximize /> Fit
        </Button>
      </div>
    </Card>
  );
}
