"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "@/lib/api";
import type { QueueResponse, Stats } from "@/lib/types";

interface PortalState {
  /** "" = all repositories */
  repo: string;
  setRepo: (repo: string) => void;
  repos: string[];
  queue: QueueResponse | null;
  /** 24 h stats for the live indicator */
  live: Stats | null;
  reloadQueue: () => void;
}

const Ctx = createContext<PortalState | null>(null);

export function PortalProvider({ children }: { children: ReactNode }) {
  const [repo, setRepo] = useState("");
  const [queue, setQueue] = useState<QueueResponse | null>(null);
  const [live, setLive] = useState<Stats | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    api.getQueue().then((q) => alive && setQueue(q)).catch(() => alive && setQueue({ holds: [] }));
    return () => {
      alive = false;
    };
  }, [tick]);

  useEffect(() => {
    let alive = true;
    const load = () => api.getStats(86400).then((s) => alive && setLive(s)).catch(() => undefined);
    load();
    const id = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const repos = useMemo(() => {
    const set = new Set<string>();
    queue?.holds.forEach((h) => set.add(h.repo));
    return [...set].sort();
  }, [queue]);

  const value = useMemo(
    () => ({ repo, setRepo, repos, queue, live, reloadQueue: () => setTick((t) => t + 1) }),
    [repo, repos, queue, live],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePortal(): PortalState {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePortal outside PortalProvider");
  return v;
}
