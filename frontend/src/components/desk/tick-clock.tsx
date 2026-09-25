"use client";

import { useSyncExternalStore } from "react";
import { formatTickClock } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The wall clock is an external system, not React state.
 *
 * Reading it through a store keeps the server render and the first client
 * render identical — the clock is simply absent until the browser subscribes —
 * which is what stops the whole header from tripping a hydration mismatch every
 * time the second ticks over mid-render.
 */
function subscribe(onChange: () => void) {
  const id = setInterval(onChange, 1000);
  return () => clearInterval(id);
}

export function TickClock() {
  const now = useSyncExternalStore(
    subscribe,
    () => Math.floor(Date.now() / 1000),
    () => null
  );

  const { label, urgent } = now
    ? formatTickClock(new Date(now * 1000))
    : { label: "–:––", urgent: false };

  return (
    <div className="flex items-baseline gap-2">
      <span className="desk-label">Next tick</span>
      <span
        className={cn(
          "font-mono text-sm font-medium tabular-nums",
          urgent ? "text-live" : "text-foreground"
        )}
      >
        {label}
      </span>
    </div>
  );
}
