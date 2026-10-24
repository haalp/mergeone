"use client";

import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { clusters, EDGE_STYLE, edgeLabel, GraphCanvas, type SimNode } from "@/components/graph/graph-canvas";
import { usePortal } from "@/components/portal-context";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { useNow } from "@/lib/use-now";
import { age, prKey, VERDICT_COLOR } from "@/lib/format";
import type { EdgeType, GraphEdge, GraphNode, GraphResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const MEANING: { type: EdgeType | "duplicate_of"; text: string }[] = [
  { type: "same_change", text: "B makes A unnecessary (run 6B: right 98 times in 100 when held)" },
  { type: "backport", text: "same change, other base branch; both should land" },
  { type: "competing", text: "same goal, different method; a human picks" },
  { type: "builds_on", text: "A depends on B; land B first" },
];

export function GraphView() {
  const { repo } = usePortal();
  const [data, setData] = useState<GraphResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cluster, setCluster] = useState<number | null>(null);
  const [edge, setEdge] = useState<GraphEdge | null>(null);
  const now = useNow();

  useEffect(() => {
    let alive = true;
    api
      .getGraph(repo || undefined)
      .then((g) => {
        if (!alive) return;
        setData(g);
        setError(null);
        setCluster(null);
        setEdge(null);
      })
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [repo]);

  const cl = useMemo(() => (data ? clusters(data.nodes, data.edges) : new Map<string, number>()), [data]);
  const related = useMemo(() => new Set(data?.edges.flatMap((e) => [e.source, e.target]) ?? []), [data]);
  const typed = useMemo(() => new Set(data?.edges.map((e) => e.type).filter(Boolean) as EdgeType[]), [data]);

  // Default panel: the biggest related cluster that still has something held.
  const defaultCluster = useMemo(() => {
    if (!data) return null;
    const sizes = new Map<number, { n: number; held: boolean }>();
    for (const n of data.nodes) {
      if (!related.has(n.id)) continue;
      const c = cl.get(n.id)!;
      const s = sizes.get(c) ?? { n: 0, held: false };
      sizes.set(c, { n: s.n + 1, held: s.held || n.verdict !== "pass" });
    }
    return [...sizes.entries()].sort((a, b) => Number(b[1].held) - Number(a[1].held) || b[1].n - a[1].n)[0]?.[0] ?? null;
  }, [data, cl, related]);

  const active = cluster ?? defaultCluster;
  const members = data?.nodes.filter((n) => cl.get(n.id) === active) ?? [];
  const byId = new Map(data?.nodes.map((n) => [n.id, n]) ?? []);
  const clusterEdges = data?.edges.filter((e) => cl.get(e.source) === active) ?? [];

  return (
    <div className="flex flex-1 flex-wrap">
      <main className="flex min-w-0 flex-[999_1_560px] flex-col">
        <div className="flex flex-wrap items-center gap-4 px-4 pt-[18px] sm:px-7">
          <div>
            <h1 className="text-2xl font-semibold tracking-[-0.02em]">
              {data ? `${data.nodes.length} open PRs · ${related.size} related` : "Loading the graph…"}
            </h1>
            <p className="mt-1 text-muted-foreground">
              Node = PR, size = diff, colour = verdict. Edge = what the gate found between two PRs; thickness = calibrated
              probability.
            </p>
          </div>
          <div className="flex-1" />
          <div className="flex flex-wrap gap-1.5" aria-label="Legend">
            {(["pass", "hold", "block"] as const).map((v) => (
              <Badge key={v} variant="outline" className="gap-1.5 border-border bg-card font-normal text-subtle">
                <span className="size-2.5 rounded-full" style={{ background: VERDICT_COLOR[v] }} />
                {v}
              </Badge>
            ))}
            <LegendEdge type={typed.size ? "same_change" : "duplicate_of"} />
            {(["backport", "competing", "builds_on"] as const).filter((t) => typed.has(t)).map((t) => (
              <LegendEdge key={t} type={t} />
            ))}
          </div>
        </div>

        <div className="mx-4 mt-4 mb-7 flex flex-1 flex-col sm:mx-7">
          {error ? (
            <Card className="flex-1 items-center justify-center text-muted-foreground">Could not load the graph: {error}</Card>
          ) : data ? (
            <GraphCanvas
              nodes={data.nodes}
              edges={data.edges}
              selectedCluster={cluster}
              selectedEdge={edge}
              onSelectNode={(n: SimNode | null) => {
                setEdge(null);
                setCluster(n && n.deg ? n.cluster : null);
              }}
              onSelectEdge={(e) => {
                setEdge(e);
                setCluster(cl.get(e.source) ?? null);
              }}
            />
          ) : (
            <Card className="flex-1 animate-pulse bg-panel" />
          )}
        </div>
      </main>

      <aside className="flex min-w-0 flex-[1_1_360px] flex-col gap-4 border-l bg-panel p-6 lg:max-w-[420px]">
        {edge ? (
          <EdgePanel edge={edge} a={byId.get(edge.source)} b={byId.get(edge.target)} onClose={() => setEdge(null)} />
        ) : (
          <>
            <div className="eyebrow">
              cluster · {members.length} PRs{members[0] ? ` · ${members[0].repo}` : ""}
            </div>
            <p className="text-[13px] text-subtle">{summary(members)}</p>
          </>
        )}

        <div className="flex flex-col gap-2">
          {members
            .slice()
            .sort((x, y) => x.pr - y.pr)
            .map((n) => (
              <ClusterRow key={n.id} n={n} edges={clusterEdges} now={now} />
            ))}
          {data && members.length === 0 && (
            <Card className="p-4 text-[13px] text-muted-foreground shadow-none">No related PRs in this repository.</Card>
          )}
        </div>

        <Card className="gap-0 rounded-[10px] p-3 text-[13px] text-subtle shadow-none">
          <div className="mb-1.5 font-mono text-[11px] text-muted-foreground">what the edges mean</div>
          {MEANING.map((m) => (
            <div key={m.type} className="mt-1 first:mt-0">
              <span style={{ color: EDGE_STYLE[m.type].color }}>{EDGE_STYLE[m.type].label}</span> — {m.text}
            </div>
          ))}
          <div className="mt-2 text-xs text-muted-foreground">
            Typed edges ship when run 8 clears its bar (class accuracy ≥ 0.80). Until then an edge without a type is drawn
            as a purple <span className="font-mono">duplicate_of</span>.
          </div>
        </Card>

        <div className="mt-auto flex flex-wrap gap-2">
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="flex-[1_1_140px]" tabIndex={0}>
                <Button variant="secondary" disabled className="w-full border border-border-strong">
                  Plan a landing wave
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>CLI only for now: mergeone plan owner/repo</TooltipContent>
          </Tooltip>
          <Button
            variant="ghost"
            className="flex-[1_1_140px] border border-border-strong text-subtle"
            onClick={() => {
              setCluster(null);
              setEdge(null);
            }}
          >
            Clear selection
          </Button>
        </div>
      </aside>
    </div>
  );
}

function LegendEdge({ type }: { type: EdgeType | "duplicate_of" }) {
  const s = EDGE_STYLE[type];
  return (
    <Badge variant="outline" className="gap-1.5 border-border bg-card font-normal text-subtle">
      <span
        className="inline-block w-5"
        style={{ borderTop: `${type === "same_change" || type === "duplicate_of" ? 3 : 2}px ${s.dash.length ? "dashed" : "solid"} ${s.color}` }}
      />
      {s.label}
    </Badge>
  );
}

function summary(members: GraphNode[]) {
  if (!members.length) return "Click a node to see its cluster.";
  const agents = new Set(members.map((m) => m.author).filter(Boolean)).size;
  const c = { pass: 0, hold: 0, block: 0 };
  members.forEach((m) => c[m.verdict]++);
  const parts = [];
  if (c.hold) parts.push(`held ${c.hold}`);
  if (c.block) parts.push(`blocked ${c.block}`);
  if (c.pass) parts.push(`let ${c.pass} pass`);
  return `${members.length} PRs${agents ? `, ${agents} ${agents === 1 ? "agent" : "agents"}` : ""}. The gate ${parts.join(", ")}.`;
}

function ClusterRow({ n, edges, now }: { n: GraphNode; edges: GraphEdge[]; now: number | null }) {
  const out = edges.find((e) => e.source === n.id);
  const rel = out ? `${EDGE_STYLE[out.type ?? "duplicate_of"].label} → #${out.target.split("#")[1]}` : null;
  return (
    <div
      className={cn(
        "flex items-center gap-2.5 rounded-[10px] border bg-card px-3 py-2.5",
        n.verdict === "hold" && "border-hold",
        n.verdict === "block" && "border-block",
      )}
    >
      <span className="size-2.5 flex-none rounded-full" style={{ background: VERDICT_COLOR[n.verdict] }} />
      <div className="min-w-0 flex-1">
        <div className="truncate">
          #{n.pr} {n.title}
        </div>
        <div className="text-xs text-muted-foreground">
          {n.verdict} · {n.verdict === "pass" ? n.route : rel ?? n.route}
          {n.author && ` · ${n.author}`}
          {n.updated && ` · ${age(n.updated, now)}`}
        </div>
      </div>
      {n.verdict !== "pass" ? (
        <Link href={`/queue#${prKey(n.repo, n.pr)}`} className="flex-none text-xs text-brand hover:underline">
          resolve →
        </Link>
      ) : (
        <a
          href={`https://github.com/${n.repo}/pull/${n.pr}`}
          target="_blank"
          rel="noreferrer"
          className="flex-none text-muted-foreground hover:text-foreground"
          aria-label={`Open #${n.pr} on GitHub`}
        >
          <ExternalLink className="size-3.5" />
        </a>
      )}
    </div>
  );
}

function EdgePanel({ edge, a, b, onClose }: { edge: GraphEdge; a?: GraphNode; b?: GraphNode; onClose: () => void }) {
  const st = EDGE_STYLE[edge.type ?? "duplicate_of"];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="eyebrow">pair</span>
        <span className="font-semibold">
          #{a?.pr} ↔ #{b?.pr}
        </span>
        <div className="flex-1" />
        <Button size="xs" variant="ghost" className="text-muted-foreground" onClick={onClose}>
          back to cluster
        </Button>
      </div>
      <div className="font-mono text-xs" style={{ color: st.color }}>
        {edgeLabel(edge)} · relation {edge.relation}
        {edge.type ? ` · type ${edge.type}` : " · untyped"}
      </div>
      <p className="text-[13px] text-subtle">
        The pair question the gate asked about #{a?.pr} and #{b?.pr} answered{" "}
        <span className="font-mono text-foreground">{edge.p.toFixed(2)}</span> after calibration.
        {a && a.verdict !== "pass" && <> #{a.pr} is held on it; resolve it from the queue.</>}
      </p>
    </div>
  );
}
