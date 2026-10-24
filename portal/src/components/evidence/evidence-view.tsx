"use client";

import { TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ActivityHeatmap } from "@/components/ui/activity-heatmap";
import { Badge } from "@/components/ui/badge";
import { BarChart } from "@/components/ui/bar-chart";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { LiveLineChart } from "@/components/ui/live-line-chart";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, USING_FIXTURES } from "@/lib/api";
import { useNow } from "@/lib/use-now";
import { HEX, int, p2 } from "@/lib/format";
import type { ModelDrift, QuestionDrift, Stats } from "@/lib/types";
import { cn } from "@/lib/utils";

const WINDOWS = [
  { s: 86400, label: "24 h" },
  { s: 604800, label: "7 d" },
  { s: 2419200, label: "28 d" },
];

/** docs/drift-monitor.md: degraded at shift > 0.20 (or spread halved). "watch" is a portal-only hint at ≥ 0.15. */
const DRIFT_SHIFT = 0.2;
const WATCH_SHIFT = 0.15;
type DriftState = "ok" | "watch" | "degraded";
const driftState = (q: QuestionDrift): DriftState => (q.degraded ? "degraded" : q.shift >= WATCH_SHIFT ? "watch" : "ok");
const STATE_HEX: Record<DriftState, string> = { ok: HEX.pass, watch: HEX.hold, degraded: HEX.block };

/** Rules the shipped policy evaluates, and which question each reads (mergeone/gate/policy.yaml). */
const RULES: { rule: string; when: string; reads: string[] }[] = [
  { rule: "duplicate-of", when: "duplicate_of ≥ 0.7 · hold", reads: ["duplicate_of"] },
  { rule: "weakened-tests", when: "weakens_tests ≥ 0.6 · block", reads: ["weakens_tests"] },
  { rule: "concurrency-risk", when: "touches_concurrency ≥ 0.6 ∧ logic ≥ 0.5 · hold", reads: ["touches_concurrency", "is_logic_change"] },
  { rule: "maintainer-judgement", when: "needs_human_review ≥ 0.5 · hold", reads: ["needs_human_review"] },
  { rule: "stale", when: "silent_days ≥ 14 · hold", reads: ["silent_days"] },
  { rule: "max-rounds", when: "> max_rounds · hold", reads: [] },
  { rule: "on_error", when: "model outage · hold", reads: [] },
];

const usd = (n: number) => `$${n.toFixed(2)}`;

export function EvidenceView() {
  const [windowS, setWindowS] = useState(604800);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const now = useNow(60_000);

  useEffect(() => {
    let alive = true;
    api
      .getStats(windowS)
      .then((s) => {
        if (!alive) return;
        setStats(s);
        setError(null);
      })
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [windowS]);

  // The model being served is the one with the most decisions in the current window.
  const [model, drift] = useMemo((): [string, ModelDrift | null] => {
    const entries = Object.entries(stats?.drift ?? {});
    if (!entries.length) return ["", null];
    entries.sort((a, b) => Math.max(0, ...Object.values(b[1].questions).map((q) => q.n)) - Math.max(0, ...Object.values(a[1].questions).map((q) => q.n)));
    return entries[0];
  }, [stats]);

  const winLabel = WINDOWS.find((w) => w.s === windowS)?.label ?? `${windowS}s`;
  const days = Math.max(1, Math.round(windowS / 86400));
  const daily = stats?.history?.daily ?? [];
  const recent = daily.slice(-Math.max(2, days));

  const v = stats?.verdicts ?? {};
  const total = Math.max(1, stats?.decisions ?? 0);
  const passPct = ((v.pass ?? 0) / total) * 100;
  const holdPct = ((v.hold ?? 0) / total) * 100;
  const blockPct = ((v.block ?? 0) / total) * 100;
  const holds = (stats?.routes?.dedup ?? 0) + (stats?.routes?.human ?? 0);
  const dedupShare = holds ? Math.round(((stats?.routes?.dedup ?? 0) / holds) * 100) : 0;
  const res = stats?.resolutions;
  const cal = stats?.calibration;
  const calAge = cal && now !== null ? Math.max(0, Math.round((now - Date.parse(cal.fit_at)) / 86400000)) : null;

  const questions = Object.entries(drift?.questions ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const degraded = drift?.degraded ?? [];
  const readByRule = (q: string) => RULES.filter((r) => r.reads.includes(q)).map((r) => r.rule);

  const liveSource = useCallback((push: (v: number) => void) => {
    let alive = true;
    const tick = () => api.getStats(3600).then((s) => alive && push(s.per_hour)).catch(() => undefined);
    tick();
    const id = setInterval(tick, 15_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <main className="mx-auto flex w-full max-w-[1360px] flex-col gap-6 p-4 sm:p-7">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.02em]">Is the gate still right?</h1>
          <p className="mt-1 text-muted-foreground">
            What it decided this window, what the model is answering compared with last month, and the benchmark every default
            came from.
          </p>
        </div>
        <div className="flex-1" />
        <label className="flex items-center gap-2 text-[13px] text-muted-foreground">
          window
          <Select value={String(windowS)} onValueChange={(s) => setWindowS(Number(s))}>
            <SelectTrigger size="sm" className="w-24 bg-card text-foreground" aria-label="Window">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOWS.map((w) => (
                <SelectItem key={w.s} value={String(w.s)}>
                  {w.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
      </div>

      {degraded.length > 0 && (
        <Alert className="border-hold bg-[#FFF8E6] text-foreground">
          <TriangleAlert className="text-hold" />
          <AlertTitle>
            {degraded.length} {degraded.length === 1 ? "question" : "questions"} degraded ·{" "}
            <span className="font-mono">{degraded.join(", ")}</span>
          </AlertTitle>
          <AlertDescription className="text-subtle">
            <p>
            {degraded.some((q) => readByRule(q).length)
              ? `Rules reading it (${degraded.flatMap(readByRule).join(", ")}) are skipped while drift.enabled is on; those PRs take on_drift (hold → human).`
              : "No shipped rule reads it, so no verdict changed."}{" "}
            Model <span className="font-mono">{model}</span>.
            </p>
          </AlertDescription>
        </Alert>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Could not load /api/stats</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {stats && (
        <StatGrid>
          <StatCard
            label={`decisions · ${winLabel}`}
            value={stats.decisions}
            trend={recent.map((d) => d.decisions)}
            trendLabels={recent.map((d) => d.day)}
            period={`${int(stats.per_hour)} / h · p99 ${stats.p99_ms === null ? "—" : `${Math.round(stats.p99_ms)} ms`}`}
            format={int}
            color={HEX.brand}
            className="rounded-xl! bg-card!"
          />
          <StatCard
            label="pass · hold · block"
            value={passPct}
            trend={recent.map((d) => (d.pass / Math.max(1, d.decisions)) * 100)}
            trendLabels={recent.map((d) => d.day)}
            period={`hold ${holdPct.toFixed(0)} % · block ${blockPct.toFixed(0)} % · holds ${dedupShare} % dedup`}
            format={(n) => `${n.toFixed(0)} % pass`}
            color={HEX.pass}
            className="rounded-xl! bg-card!"
          />
          <StatCard
            label="holds resolved by a human"
            value={res ? (res.agreed / Math.max(1, res.total)) * 100 : 0}
            trend={[]}
            period={res ? `${int(res.agreed)} of ${int(res.total)} agreed · ${int(res.approved_as_is)} approved as is` : "no resolutions logged yet"}
            format={(n) => `${n.toFixed(0)} % agreed`}
            color={HEX.hold}
            className="rounded-xl! bg-card!"
          />
          <StatCard
            label={`cost · ${winLabel}`}
            value={stats.cost_usd}
            trend={recent.map((d) => d.cost_usd)}
            trendLabels={recent.map((d) => d.day)}
            goodWhen="down"
            period={`$${(stats.cost_usd / total).toFixed(5)} / PR${model ? ` · ${model}` : ""}`}
            format={usd}
            color={HEX.subtle}
            className="rounded-xl! bg-card!"
          />
          <StatCard
            label="calibration age"
            value={calAge ?? 0}
            trend={[]}
            goodWhen="down"
            period={cal ? `fit on ${int(cal.n)} PRs · refit from gatelog` : "calibration block not reported"}
            format={(n) => `${Math.round(n)} d old`}
            color={HEX.muted}
            className="rounded-xl! bg-card!"
          />
        </StatGrid>
      )}

      <Card className="gap-3.5 rounded-xl px-[22px] py-5 shadow-none">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="text-base font-semibold">Drift — what the model answers now vs the reference window</h2>
          <span className="text-[13px] text-muted-foreground">
            raw mean per question, 7 d vs the 28 d before · degraded when the shift exceeds {DRIFT_SHIFT.toFixed(2)} or spread halves · n ≥
            200 · reference: <span className="font-mono">{drift?.reference ?? "—"}</span>
          </span>
        </div>

        <div className="overflow-x-auto">
          <div className="grid min-w-[640px] grid-cols-[190px_minmax(0,1fr)_100px_70px_90px] items-center gap-x-4 gap-y-2.5 text-[13px]">
            <div className="font-mono text-[11px] text-muted-foreground">question</div>
            <div className="font-mono text-[11px] text-muted-foreground">mean raw · 8 weeks</div>
            <div className="text-right font-mono text-[11px] text-muted-foreground">ref → now</div>
            <div className="text-right font-mono text-[11px] text-muted-foreground">shift</div>
            <div className="font-mono text-[11px] text-muted-foreground">state</div>
            {questions.map(([q, d]) => {
              const s = driftState(d);
              const weeks = stats?.drift_weekly?.[model]?.[q] ?? [d.ref_mean_raw, d.mean_raw];
              const labels = weeks.map((_, i) => (i === weeks.length - 1 ? "now" : `w-${weeks.length - 1 - i}`));
              return (
                <DriftRow
                  key={q}
                  q={q}
                  d={d}
                  state={s}
                  data={weeks.map((m, i) => {
                    const last = i === weeks.length - 1;
                    return { label: labels[i], past: last ? 0 : m, now: last ? m : 0 };
                  })}
                />
              );
            })}
            {!questions.length && (
              <div className="col-span-5 py-4 text-muted-foreground">
                No drift block yet: the gatelog has no decisions with raw probabilities in this window.
              </div>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t pt-3 text-[13px] text-subtle">
          <span>
            Rules reading a degraded question are skipped and the PR takes <span className="font-mono">on_drift</span> (hold → human).
            Nothing refits on its own.
          </span>
          <div className="flex-1" />
          <RefitDialog model={model} n={cal?.n} />
          <a
            href="https://github.com/haalp/mergeone/blob/main/docs/drift-monitor.md"
            target="_blank"
            rel="noreferrer"
            className="text-brand hover:underline"
          >
            how this is computed →
          </a>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="gap-3 rounded-xl px-[22px] py-5 shadow-none">
          <h2 className="text-base font-semibold">Rules — what fired, and what humans did with it</h2>
          <Table className="text-[13px]">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="font-mono text-[11px] font-normal text-muted-foreground">rule</TableHead>
                <TableHead className="text-right font-mono text-[11px] font-normal text-muted-foreground">fired</TableHead>
                <TableHead className="text-right font-mono text-[11px] font-normal text-muted-foreground">agreed</TableHead>
                <TableHead className="text-right font-mono text-[11px] font-normal text-muted-foreground">overridden</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {RULES.map((r) => {
                const fired = stats?.rules?.[r.rule] ?? 0;
                const br = res?.by_rule?.[r.rule];
                const agreed = br ? br.agreed / Math.max(1, br.agreed + br.overridden) : null;
                return (
                  <TableRow key={r.rule}>
                    <TableCell className="font-mono whitespace-normal">
                      {r.rule} <span className="text-muted-foreground">{r.when}</span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{int(fired)}</TableCell>
                    <TableCell className={cn("text-right tabular-nums", agreed === null ? "text-muted-foreground" : agreed >= 0.9 ? "text-pass" : "text-hold")}>
                      {agreed === null ? "—" : `${Math.round(agreed * 100)} %`}
                    </TableCell>
                    <TableCell className={cn("text-right tabular-nums", !br && "text-muted-foreground")}>{br ? int(br.overridden) : "—"}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          <p className="text-xs text-muted-foreground">
            &ldquo;Agreed&rdquo; = the human resolution matched the verdict (linked, assigned, or left blocked). Overrides are the labels for
            the next refit.
          </p>
        </Card>

        <Card className="gap-3 rounded-xl px-[22px] py-5 shadow-none">
          <h2 className="text-base font-semibold">Where the defaults come from — gatebench</h2>
          <ul className="flex flex-col gap-2 text-[13px]">
            {[
              { run: "run 6B", c: HEX.pass, t: "A hold for duplication was right 98 times in 100; 44 the same change. The reason a hold never closes a PR." },
              { run: "run 5", c: HEX.hold, t: "needs_human_review predicts ≥ 2 review rounds at AUC 0.76. The reason it holds, not blocks." },
              { run: "run 7", c: HEX.block, t: "The hosted model changed under an unchanged version string. The reason this page has a drift section." },
              { run: "run 1", c: HEX.block, t: "Nothing in a diff predicts a 30-day revert. The reason there is no “will this break prod” score." },
            ].map((g) => (
              <li key={g.run} className="flex items-baseline gap-2.5">
                <span className="w-[54px] flex-none font-mono" style={{ color: g.c }}>
                  {g.run}
                </span>
                <span className="text-subtle">{g.t}</span>
              </li>
            ))}
          </ul>
          <div className="mt-auto flex gap-3.5 border-t pt-2 text-[13px]">
            <a href="https://mergeone.dev/updates/" className="text-brand hover:underline">
              all runs, dated →
            </a>
            <a href="https://github.com/haalp/mergeone/blob/main/PROTOCOL.md" className="text-subtle hover:text-foreground">
              protocol
            </a>
            <a href="https://github.com/haalp/mergeone/blob/main/docs/EVIDENCE.md" className="text-subtle hover:text-foreground">
              evidence
            </a>
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="gap-3 rounded-xl px-[22px] py-5 shadow-none">
          <div className="flex items-baseline gap-3">
            <h2 className="text-base font-semibold">Decisions per hour</h2>
            <span className="text-[13px] text-muted-foreground">
              {USING_FIXTURES ? "fixtures: a flat line is expected" : "GET /api/stats?window=3600, every 15 s"}
            </span>
          </div>
          <LiveLineChart source={liveSource} intervalMs={15_000} windowSeconds={900} height={180} color={HEX.brand} unit="/h" formatValue={int} ariaLabel="Decisions per hour, live" />
        </Card>
        <Card className="gap-3 rounded-xl px-[22px] py-5 shadow-none">
          <div className="flex items-baseline gap-3">
            <h2 className="text-base font-semibold">Decisions per day</h2>
            <span className="text-[13px] text-muted-foreground">from the gatelog history block</span>
          </div>
          {daily.length ? (
            <ActivityHeatmap mode="calendar" data={daily.map((d) => ({ date: d.day, value: d.decisions }))} weeks={8} weekStart={1} color={HEX.brand} unit="decisions" ariaLabel="Decisions per day" />
          ) : (
            <p className="text-[13px] text-muted-foreground">Shows once the gatelog is long enough to report a history block.</p>
          )}
        </Card>
      </div>
    </main>
  );
}

function DriftRow({
  q,
  d,
  state,
  data,
}: {
  q: string;
  d: QuestionDrift;
  state: DriftState;
  data: { label: string; past: number; now: number }[];
}) {
  return (
    <>
      <div className="truncate font-mono">{q}</div>
      <BarChart
        data={data}
        variant="stacked"
        series={[
          { key: "past", label: "earlier weeks", color: HEX.borderStrong },
          { key: "now", label: "this week", color: STATE_HEX[state] },
        ]}
        height={84}
        showLegend={false}
        formatValue={(n) => n.toFixed(2)}
        formatTick={(n) => n.toFixed(2)}
        ariaLabel={`${q}: weekly mean raw probability`}
        className="[&_svg]:overflow-visible"
      />
      <div className="text-right font-mono">
        {p2(d.ref_mean_raw)} → {p2(d.mean_raw)}
      </div>
      <div className={cn("text-right font-mono", state === "degraded" && "text-block")}>{p2(d.shift)}</div>
      <div>
        <Badge
          variant="outline"
          className="border-transparent bg-secondary font-normal"
          style={{ color: STATE_HEX[state] }}
        >
          {state}
        </Badge>
      </div>
    </>
  );
}

function RefitDialog({ model, n }: { model: string; n?: number }) {
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary" className="border border-border-strong">
          Refit calibration from gatelog
        </Button>
      </DialogTrigger>
      <DialogContent className="bg-card">
        <DialogHeader>
          <DialogTitle>Refit the calibration map?</DialogTitle>
          <DialogDescription>
            Runs <span className="font-mono text-foreground">mergeone calibrate --from-gatelog --since 7d</span> for{" "}
            <span className="font-mono text-foreground">{model || "the served model"}</span> on decisions that have an outcome, and
            writes a new calibration file with <span className="font-mono">fit_stats</span>. The degraded flag clears once the new fit is
            the reference. {n ? `The current map was fit on ${int(n)} PRs.` : ""}
          </DialogDescription>
        </DialogHeader>
        <p className="text-[13px] text-subtle">
          A refit hides the drift it answers. Do it after the run that explains the drift, not instead of it.
        </p>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button
            disabled={busy}
            className="bg-brand text-brand-foreground hover:bg-brand/90"
            onClick={async () => {
              setBusy(true);
              try {
                const r = await api.refitCalibration(7);
                toast.success(r.mock ? "Refit requested (mock)" : "Refit requested", {
                  description: "Logged. The new map applies after it is written and reloaded.",
                });
                setOpen(false);
              } catch (e) {
                toast.error("Refit not requested", { description: String(e) });
              } finally {
                setBusy(false);
              }
            }}
          >
            Refit
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
