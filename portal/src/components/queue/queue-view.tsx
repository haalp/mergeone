"use client";

import { ArrowRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { usePortal } from "@/components/portal-context";
import { HoldSheet } from "@/components/queue/hold-sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { useNow } from "@/lib/use-now";
import { age, p2, parseState, prKey, RISK_QUESTIONS, riskText, short } from "@/lib/format";
import type { GateRecord, Resolution } from "@/lib/types";
import { cn } from "@/lib/utils";

type Filter = "all" | "dedup" | "human" | "blocked";

const matches = (r: GateRecord, f: Filter) =>
  f === "all" ||
  (f === "blocked" ? r.result.verdict === "block" : r.result.verdict === "hold" && r.result.route === f);

export function QueueView() {
  const { queue, repo, reloadQueue } = usePortal();
  const [filter, setFilter] = useState<Filter>("all");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [resolved, setResolved] = useState<Set<string>>(new Set());
  const now = useNow();

  // Deep link from the graph: /queue#acme/platform#4123
  useEffect(() => {
    if (!queue) return;
    const h = decodeURIComponent(window.location.hash.slice(1));
    if (h && queue.holds.some((r) => prKey(r.repo, r.pr) === h)) {
      const id = requestAnimationFrame(() => setOpenKey(h));
      return () => cancelAnimationFrame(id);
    }
  }, [queue]);

  const holds = useMemo(
    () =>
      (queue?.holds ?? [])
        .filter((r) => (!repo || r.repo === repo) && !resolved.has(prKey(r.repo, r.pr)))
        .sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts)),
    [queue, repo, resolved],
  );
  const counts = useMemo(
    () => ({
      all: holds.length,
      dedup: holds.filter((r) => matches(r, "dedup")).length,
      human: holds.filter((r) => matches(r, "human")).length,
      blocked: holds.filter((r) => matches(r, "blocked")).length,
    }),
    [holds],
  );
  const shown = holds.filter((r) => matches(r, filter));
  const open = holds.find((r) => prKey(r.repo, r.pr) === openKey) ?? null;
  const matchOf = (r: GateRecord) => {
    const n = r.result.extra?.duplicate_candidate;
    return n ? (queue?.matches?.[prKey(r.repo, n)] ?? null) : null;
  };

  async function resolve(r: GateRecord, action: Resolution) {
    const target = r.result.extra?.duplicate_candidate;
    try {
      await api.resolve({ repo: r.repo, pr: r.pr ?? 0, head_sha: r.head_sha, action, target_pr: target });
      const what =
        action === "approve"
          ? `#${r.pr} approved as is`
          : action === "link_duplicate"
            ? `#${r.pr} linked as duplicate of #${target}`
            : `rebase of #${r.pr} onto #${target} requested`;
      toast.success("Logged, nothing merged or closed", {
        description: `${what} · head ${short(r.head_sha)}`,
        action: { label: "Reload", onClick: reloadQueue },
      });
      setResolved((s) => new Set(s).add(prKey(r.repo, r.pr)));
      setOpenKey(null);
    } catch (e) {
      toast.error("Not logged", { description: e instanceof Error ? e.message : String(e) });
    }
  }

  const oldest = holds[0];

  return (
    <main className="flex flex-col gap-5 p-4 sm:p-7">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.02em]">Held for a human</h1>
          <p className="mt-1 text-muted-foreground">
            {queue ? (
              <>
                {holds.length} open {holds.length === 1 ? "hold" : "holds"}
                {oldest && <> · oldest {age(oldest.ts, now)}</>} · a hold never closes a PR — you do.
              </>
            ) : (
              "Loading the queue…"
            )}
          </p>
        </div>
        <div className="flex-1" />
        <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)}>
          <TabsList className="h-auto gap-1.5 bg-transparent p-0" aria-label="Route filter">
            {(["all", "dedup", "human", "blocked"] as const).map((f) => (
              <TabsTrigger
                key={f}
                value={f}
                className="h-8 flex-none rounded-full border-border-strong px-3 text-[13px] font-normal text-subtle data-[state=active]:border-border-strong data-[state=active]:bg-secondary data-[state=active]:text-foreground dark:data-[state=active]:border-border-strong dark:data-[state=active]:bg-secondary"
              >
                {f === "all" ? "All" : f} <span className="font-mono text-xs opacity-80">{counts[f]}</span>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <div className="flex flex-col gap-2.5">
        {shown.map((r) => (
          <QueueRow
            key={prKey(r.repo, r.pr)}
            r={r}
            match={matchOf(r)}
            now={now}
            selected={openKey === prKey(r.repo, r.pr)}
            onOpen={() => setOpenKey(prKey(r.repo, r.pr))}
            onResolve={(a) => resolve(r, a)}
          />
        ))}
        {queue && shown.length === 0 && (
          <Card className="items-center gap-1 py-10 text-center text-muted-foreground">
            <span className="text-foreground">Nothing held here.</span>
            <span className="text-[13px]">Every PR in this filter passed, or a human already resolved it.</span>
          </Card>
        )}
        {shown.length > 0 && (
          <div className="flex items-center gap-3 px-1 py-2 text-[13px] text-muted-foreground">
            <span className="h-px flex-1 bg-border" />
            {shown.length} shown · sorted oldest first
            <span className="h-px flex-1 bg-border" />
          </div>
        )}
      </div>

      <HoldSheet
        record={open}
        match={open ? matchOf(open) : null}
        onOpenChange={(o) => !o && setOpenKey(null)}
        onResolve={(a) => open && resolve(open, a)}
      />
    </main>
  );
}

function VerdictBadge({ r }: { r: GateRecord }) {
  const v = r.result.verdict;
  const label = v === "block" ? `block · ${r.result.rule}` : `${v} · ${r.result.route}`;
  return (
    <Badge
      variant="outline"
      className={cn(
        "rounded-md border-transparent bg-secondary px-1.5 font-mono text-[11px] font-normal tracking-[0.08em] uppercase",
        v === "block" ? "text-block" : v === "hold" ? "text-hold" : "text-pass",
      )}
    >
      {label}
    </Badge>
  );
}

function QueueRow({
  r,
  match,
  now,
  selected,
  onOpen,
  onResolve,
}: {
  r: GateRecord;
  match: GateRecord | null;
  now: number | null;
  selected: boolean;
  onOpen: () => void;
  onResolve: (a: Resolution) => void;
}) {
  const st = parseState(r.state);
  // The match is the row's story only when the duplicate rule is what held it.
  const cand = r.result.rule === "duplicate-of" ? r.result.extra?.duplicate_candidate : undefined;
  const candInfo = r.result.extra?.candidates?.find((c) => c.number === cand);
  const matchTitle = match ? parseState(match.state).title : candInfo?.title;
  const otherBase = match?.context?.base && r.context?.base && match.context.base !== r.context.base;
  // Risk questions first (highest answer first), then one neutral question for context.
  const chips = Object.entries(r.result.probs)
    .filter(([q]) => q !== "duplicate_of" && q !== "silent_days")
    .sort((a, b) => Number(RISK_QUESTIONS.has(b[0])) - Number(RISK_QUESTIONS.has(a[0])) || b[1] - a[1])
    .slice(0, 3);

  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen())}
      className={cn(
        "grid cursor-pointer grid-cols-1 items-center gap-4 rounded-xl px-[18px] py-4 shadow-none transition-colors hover:border-border-strong md:grid-cols-12",
        selected && "border-brand hover:border-brand",
      )}
    >
      <div className="min-w-0 md:col-span-5">
        <div className="mb-1 flex items-center gap-2">
          <VerdictBadge r={r} />
          <span className="font-mono text-[11px] text-muted-foreground">{age(r.ts, now)}</span>
        </div>
        <div className="truncate font-medium">
          #{r.pr} {st.title}
        </div>
        <div className="mt-0.5 text-[13px] text-muted-foreground">
          {r.context?.author && (
            <>
              agent <span className="font-mono">{r.context.author}</span> ·{" "}
            </>
          )}
          {st.files.length} {st.files.length === 1 ? "file" : "files"} · +{st.ins} −{st.del} · head{" "}
          <span className="font-mono">{short(r.head_sha)}</span>
        </div>
      </div>

      <div className="flex min-w-0 items-center gap-2.5 md:col-span-4">
        {cand ? (
          <>
            <ArrowRight className="size-4 flex-none text-muted-foreground" aria-hidden />
            <div className="min-w-0">
              <div className={cn("font-mono text-xs", otherBase ? "text-subtle" : "text-brand")}>
                same change{otherBase ? " · other base" : ""} · p {p2(r.result.probs.duplicate_of)}
              </div>
              <div className="truncate">
                #{cand} {matchTitle}
              </div>
              <div className="text-[13px] text-muted-foreground">
                {candInfo?.state === "merged" ? (
                  <>merged to {match?.context?.base ?? "main"}{match && <> {age(match.ts, now)} ago</>}</>
                ) : (
                  <>
                    open{match?.context?.created_at && <> {age(match.context.created_at, now)}</>}
                    {match?.context?.author && (
                      <>
                        {" "}
                        · <span className="font-mono">{match.context.author}</span>
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {chips.map(([q, p]) => (
              <span key={q} className={cn("rounded-md bg-secondary px-2 py-1 font-mono text-xs", riskText(q, p))}>
                {q} {p2(p)}
              </span>
            ))}
          </div>
        )}
      </div>

      <div
        className="flex flex-wrap justify-start gap-1.5 md:col-span-3 md:justify-end"
        role="group"
        aria-label={`Resolve #${r.pr}`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <Button size="sm" variant="secondary" className="border border-border-strong" onClick={() => onResolve("approve")}>
          {r.result.verdict === "block" ? "Override" : "Approve"}
        </Button>
        {cand ? (
          <>
            <Button size="sm" variant="outline" className="border-border-strong bg-transparent text-subtle" onClick={() => onResolve("link_duplicate")}>
              Link
            </Button>
            <Button size="sm" variant="outline" className="border-border-strong bg-transparent text-subtle" onClick={() => onResolve("request_rebase")}>
              Rebase
            </Button>
          </>
        ) : (
          <Button size="sm" variant="outline" className="border-border-strong bg-transparent text-subtle" onClick={onOpen}>
            Review
          </Button>
        )}
      </div>
    </Card>
  );
}
