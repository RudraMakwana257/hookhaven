"use client";

import { useEffect, useState } from "react";

/** Re-render periodically so relative times ("5m ago") do not go stale on screen. */
export function useTick(intervalMs = 30_000): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
}
