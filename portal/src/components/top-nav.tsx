"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { LogoMark } from "@/components/logo-mark";
import { usePortal } from "@/components/portal-context";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { USING_FIXTURES } from "@/lib/api";
import { int } from "@/lib/format";
import { cn } from "@/lib/utils";

const ALL = "__all__";

export function TopNav() {
  const path = usePathname();
  const { repo, setRepo, repos, queue, live } = usePortal();
  const holds = queue?.holds.filter((h) => !repo || h.repo === repo).length;

  const links = [
    { href: "/queue", label: "Queue", count: holds },
    { href: "/graph", label: "Graph" },
    { href: "/evidence", label: "Evidence" },
  ];

  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b bg-panel px-4 py-3.5 sm:px-7">
      <Link href="/queue" className="flex items-center gap-2.5">
        <LogoMark />
        <span className="text-[15px] font-semibold tracking-[-0.01em]">mergeone</span>
        <span className="eyebrow ml-1">portal</span>
      </Link>

      <nav className="flex flex-wrap gap-1" aria-label="Views">
        {links.map((l) => {
          const active = path === l.href || path.startsWith(`${l.href}/`);
          return (
            <Link
              key={l.href}
              href={l.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "rounded-md px-3 py-1.5 text-subtle transition-colors hover:text-foreground",
                active && "bg-secondary font-medium text-foreground",
              )}
            >
              {l.label}
              {l.count !== undefined && <span className="ml-1.5 font-mono text-xs text-hold">{l.count}</span>}
            </Link>
          );
        })}
      </nav>

      <div className="flex-1" />

      <label className="flex items-center gap-2 text-[13px] text-muted-foreground">
        <span>Repo</span>
        <Select value={repo || ALL} onValueChange={(v) => setRepo(v === ALL ? "" : v)}>
          <SelectTrigger size="sm" className="min-w-40 bg-card text-foreground" aria-label="Repository">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>all repositories</SelectItem>
            {repos.map((r) => (
              <SelectItem key={r} value={r}>
                {r}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>

      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex items-center gap-2 text-[13px] text-muted-foreground" tabIndex={0}>
            <span className="relative flex size-2">
              {!USING_FIXTURES && <span className="absolute inline-flex size-full animate-ping rounded-full bg-pass opacity-50" />}
              <span className={cn("relative inline-flex size-2 rounded-full", USING_FIXTURES ? "bg-subtle" : "bg-pass")} />
            </span>
            {USING_FIXTURES ? "fixtures" : "live"}
            {live && ` · ${int(live.per_hour)} decisions/h`}
          </div>
        </TooltipTrigger>
        <TooltipContent>
          {USING_FIXTURES
            ? "NEXT_PUBLIC_API_BASE is unset: every number on this page comes from src/lib/fixtures."
            : "Decisions per hour over the last 24 h, from GET /api/stats."}
        </TooltipContent>
      </Tooltip>
    </header>
  );
}
