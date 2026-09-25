"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { EagleMark } from "@/components/desk/eagle-mark";
import { TickClock } from "@/components/desk/tick-clock";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { Health } from "@/types/api";

export function Navbar() {
  const pathname = usePathname();
  const health = useHealth();

  return (
    <header className="sticky top-0 z-50 border-b border-border bg-[#070b14]/95 backdrop-blur-sm">
      <div className="flex h-11 items-center gap-6 px-5">
        <Link href="/" className="flex shrink-0 items-center gap-2.5">
          <EagleMark className="size-5 text-gold" />
          <div className="flex items-baseline gap-2 leading-none">
            <span className="text-[13px] font-semibold tracking-[0.14em] text-foreground">
              ALGO E
            </span>
            <span className="text-[10px] font-medium tracking-[0.18em] text-gold">
              EAGLE-I
            </span>
          </div>
        </Link>

        <nav className="flex items-center gap-0.5">
          <Link
            href="/"
            className={cn(
              "px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.14em]",
              pathname === "/" ? "text-gold" : "text-muted-foreground hover:text-foreground",
            )}
          >
            Board
          </Link>
          <Link
            href="/backtest"
            className={cn(
              "px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.14em]",
              pathname.startsWith("/backtest")
                ? "text-gold"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            Backtest
          </Link>
          {pathname.startsWith("/match") && (
            <span className="px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.14em] text-gold">
              Cockpit
            </span>
          )}
        </nav>

        <div className="ml-auto flex items-center gap-5">
          <TickClock />
          <ConnectionPill health={health} />
        </div>
      </div>
      <div className="desk-rule" />
    </header>
  );
}

/** The one thing worth knowing at all times: is the pipeline reachable. */
function ConnectionPill({ health }: { health: Health | null | "down" }) {
  if (health === "down") {
    return (
      <div className="flex items-center gap-1.5" title="The API is not responding">
        <span className="size-1.5 rounded-full bg-negative" />
        <span className="desk-label !text-negative">Offline</span>
      </div>
    );
  }
  if (!health) {
    return (
      <div className="flex items-center gap-1.5">
        <span className="size-1.5 rounded-full bg-muted-foreground" />
        <span className="desk-label">…</span>
      </div>
    );
  }
  const degraded = health.status !== "ok";
  return (
    <div
      className="flex items-center gap-1.5"
      title={health.last_error ?? `source: ${health.source}`}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          degraded ? "bg-warn" : health.stale ? "bg-muted-foreground" : "bg-positive",
        )}
      />
      <span className={cn("desk-label", degraded ? "!text-warn" : "!text-positive")}>
        {health.source}
      </span>
    </div>
  );
}

function useHealth() {
  const [health, setHealth] = useState<Health | null | "down">(null);
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const next = await api.health();
        if (alive) setHealth(next);
      } catch {
        if (alive) setHealth("down");
      }
    };
    void poll();
    const id = setInterval(poll, 15_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);
  return health;
}
