"use client";

import { useEffect, useState } from "react";

/**
 * Wall-clock time, read only after mount (never during render, which Cache Components prerenders).
 * Returns null until the first tick, then refreshes every `everyMs`.
 */
export function useNow(everyMs = 30_000): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const raf = requestAnimationFrame(tick);
    const id = setInterval(tick, everyMs);
    return () => {
      cancelAnimationFrame(raf);
      clearInterval(id);
    };
  }, [everyMs]);
  return now;
}
