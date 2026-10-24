"use client";

import { Button } from "@/components/ui/button";
import { MagneticButton } from "@/components/ui/magnetic-button";
import { Progress } from "@/components/ui/progress";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { p2, parseState, QUESTION_ORDER, RISK_QUESTIONS, short } from "@/lib/format";
import type { GateRecord, Resolution } from "@/lib/types";
import { cn } from "@/lib/utils";

export function HoldSheet({
  record,
  match,
  onOpenChange,
  onResolve,
}: {
  record: GateRecord | null;
  match: GateRecord | null;
  onOpenChange: (open: boolean) => void;
  onResolve: (a: Resolution) => void;
}) {
  return (
    <Sheet open={!!record} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto border-l bg-panel sm:max-w-[460px]">
        {record && <Body r={record} match={match} onResolve={onResolve} />}
      </SheetContent>
    </Sheet>
  );
}

function Body({ r, match, onResolve }: { r: GateRecord; match: GateRecord | null; onResolve: (a: Resolution) => void }) {
  const a = parseState(r.state);
  const cand = r.result.extra?.duplicate_candidate;
  const b = match ? parseState(match.state) : null;
  const probs = Object.entries(r.result.probs).sort(
    ([x], [y]) => (QUESTION_ORDER.indexOf(x) + 1 || 99) - (QUESTION_ORDER.indexOf(y) + 1 || 99),
  );
  const sameBase = match?.context?.base && r.context?.base ? match.context.base === r.context.base : null;

  return (
    <>
      <SheetHeader className="gap-3 px-6 pt-7 pb-4">
        <div className="flex items-baseline gap-2.5">
          <span className="eyebrow">selected</span>
          <SheetTitle className="text-base">
            #{r.pr}
            {cand ? ` → #${cand}` : ` · ${r.repo}`}
          </SheetTitle>
        </div>
        <SheetDescription className="text-[13px] text-subtle">
          The exact input the model saw, and what it answered. Verdict is keyed on head{" "}
          <span className="font-mono">{short(r.head_sha)}</span>; a new push re-runs it.
        </SheetDescription>
      </SheetHeader>

      <div className="flex flex-col gap-[18px] px-6 pb-4">
        <div className={cn("grid gap-2.5", b ? "grid-cols-2" : "grid-cols-1")}>
          <DiffBlock label={`A · #${r.pr} · ${a.files.length} ${a.files.length === 1 ? "file" : "files"}`} files={a.files.map((f) => f.path)} diff={a.diff} />
          {b && match && (
            <DiffBlock label={`B · #${match.pr} · ${b.files.length} ${b.files.length === 1 ? "file" : "files"}`} files={b.files.map((f) => f.path)} diff={b.diff} />
          )}
        </div>

        <div className="flex flex-col gap-2">
          {probs.map(([q, p]) => (
            <ProbRow key={q} q={q} p={p} raw={r.result.raw_probs?.[q]} rule={r.result.rule} />
          ))}
          {sameBase !== null && (
            <div className="flex items-center gap-2.5">
              <span className="w-[172px] flex-none font-mono text-xs text-subtle">same base</span>
              <Progress value={100} className={cn("h-1.5 bg-border", sameBase ? "[&>*]:bg-pass" : "[&>*]:bg-hold")} />
              <span className="w-12 text-right font-mono text-xs">{sameBase ? r.context?.base : "differs"}</span>
            </div>
          )}
        </div>

        <div className="rounded-[10px] border bg-card p-3 text-[13px] text-subtle">
          <div className="mb-1.5 font-mono text-[11px] text-muted-foreground">rule · policy.yaml</div>
          <div className="font-mono text-xs text-foreground">
            {r.result.rule} → {r.result.verdict} · route {r.result.route}
          </div>
          <div className="mt-1.5">{r.result.reason}</div>
          <div className="mt-1.5">
            model <span className="font-mono">{r.result.model_version}</span> · {Math.round(r.result.latency_ms)} ms · $
            {r.result.cost_usd.toFixed(4)}
            {r.result.drift?.length ? <> · drift: {r.result.drift.join(", ")}</> : null}
          </div>
        </div>
      </div>

      <SheetFooter className="gap-2 px-6 pt-2 pb-7">
        {cand ? (
          <MagneticButton
            variant="primary"
            strength={0.18}
            className="w-full justify-start rounded-[10px] bg-brand px-3.5 py-3 text-left font-semibold text-brand-foreground hover:bg-brand/90"
            onClick={() => onResolve("link_duplicate")}
          >
            <span>
              Link as duplicate of #{cand}{" "}
              <span className="font-normal text-[#2B1A4D]">· comments on both, keeps #{r.pr} open</span>
            </span>
          </MagneticButton>
        ) : (
          <MagneticButton
            variant="primary"
            strength={0.18}
            className="w-full justify-start rounded-[10px] bg-brand px-3.5 py-3 text-left font-semibold text-brand-foreground hover:bg-brand/90"
            onClick={() => onResolve("approve")}
          >
            <span>
              {r.result.verdict === "block" ? "Override the block on" : "Approve"} #{r.pr} as is{" "}
              <span className="font-normal text-[#2B1A4D]">· clears the {r.result.verdict} on this head</span>
            </span>
          </MagneticButton>
        )}
        {cand && (
          <Button
            variant="secondary"
            className="h-auto justify-start rounded-[10px] border border-border-strong px-3.5 py-2.5 text-left font-normal whitespace-normal"
            onClick={() => onResolve("approve")}
          >
            Approve #{r.pr} as is <span className="text-muted-foreground">· clears the hold on this head</span>
          </Button>
        )}
        {cand && (
          <Button
            variant="outline"
            className="h-auto justify-start rounded-[10px] border-border-strong bg-transparent px-3.5 py-2.5 font-normal text-subtle"
            onClick={() => onResolve("request_rebase")}
          >
            Ask for a rebase onto #{cand}
          </Button>
        )}
        <p className="mt-1 text-xs text-muted-foreground">
          Every resolution is logged with your login and the head SHA. Nothing here merges or closes.
        </p>
      </SheetFooter>
    </>
  );
}

function DiffBlock({ label, files, diff }: { label: string; files: string[]; diff: string }) {
  const lines = diff
    .split("\n")
    .filter((l) => l && !l.startsWith("---") && !l.startsWith("+++") && !l.startsWith("@@"))
    .slice(0, 14);
  return (
    <div className="min-w-0 rounded-[10px] border bg-card p-3">
      <div className="mb-1.5 font-mono text-[11px] text-muted-foreground">{label}</div>
      <pre className="overflow-hidden font-mono text-[11px] leading-normal whitespace-pre-wrap text-subtle">
        <span className="text-foreground/80">{files[0]}</span>
        {"\n"}
        {lines.map((l, i) => (
          <span
            key={i}
            className={cn(
              "block",
              l.startsWith("+") && "text-pass",
              l.startsWith("-") && "text-block",
            )}
          >
            {l.replace(/\t/g, "  ")}
          </span>
        ))}
      </pre>
    </div>
  );
}

function ProbRow({ q, p, raw, rule }: { q: string; p: number; raw?: number; rule: string }) {
  if (q === "silent_days") {
    return (
      <div className="flex items-center gap-2.5">
        <span className="w-[172px] flex-none font-mono text-xs text-subtle">silent_days</span>
        <span className="flex-1" />
        <span className="w-12 text-right font-mono text-xs">{Math.round(p)} d</span>
      </div>
    );
  }
  const colour =
    q === "duplicate_of"
      ? "[&>*]:bg-brand"
      : !RISK_QUESTIONS.has(q)
        ? "[&>*]:bg-subtle"
        : p >= 0.8
          ? "[&>*]:bg-block"
          : p >= 0.5
            ? "[&>*]:bg-hold"
            : "[&>*]:bg-subtle";
  const fired =
    (rule === "duplicate-of" && q === "duplicate_of") ||
    (rule === "weakened-tests" && q === "weakens_tests") ||
    (rule === "maintainer-judgement" && q === "needs_human_review");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="flex items-center gap-2.5" tabIndex={0}>
          <span className={cn("w-[172px] flex-none truncate font-mono text-xs text-subtle", fired && "text-foreground")}>{q}</span>
          <Progress value={p * 100} className={cn("h-1.5 bg-border", colour)} aria-label={q} />
          <span className="w-12 text-right font-mono text-xs">{p2(p)}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="left">
        calibrated {p2(p)}
        {raw !== undefined && <> · raw {p2(raw)}</>}
        {fired && " · the rule read this"}
      </TooltipContent>
    </Tooltip>
  );
}
