"use client";

/**
 * The small labels that carry state across the desk.
 *
 * All of them read straight off backend fields. None of them decide
 * anything - a chip that inferred its own colour from a threshold the
 * backend does not know about would be a second opinion on the screen.
 */

import { useEffect, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";
import type { Alert, AlertLevel, Phase, SelectionStatus } from "@/types/api";

const PHASE_LABEL: Record<Phase, string> = {
  prematch: "PRE",
  first_half: "1H",
  half_time: "HT",
  second_half: "2H",
  et_first_half: "ET1",
  et_half_time: "ETHT",
  et_second_half: "ET2",
  penalty_shootout: "PENS",
  full_time: "FT",
};

export function PhaseChip({ phase, minute }: { phase: Phase; minute?: number | null }) {
  const live = phase !== "prematch" && phase !== "full_time";
  return (
    <span
      className={cn(
        "chip",
        live
          ? "border-live/40 bg-live/10 text-live"
          : "border-border bg-surface-2 text-muted-foreground",
      )}
    >
      {live && <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-live" />}
      {PHASE_LABEL[phase] ?? phase}
      {live && minute != null && (
        <span className="font-mono tabular-nums">{Math.round(minute)}&apos;</span>
      )}
    </span>
  );
}

const STATUS_STYLE: Record<SelectionStatus, string> = {
  open: "border-positive/35 bg-positive/10 text-positive",
  settled: "border-border bg-surface-2 text-muted-foreground",
  closed: "border-warn/35 bg-warn/10 text-warn",
  suspended: "border-negative/35 bg-negative/10 text-negative",
};

export function StatusChip({ status }: { status: SelectionStatus | null }) {
  if (!status) return <span className="text-muted-foreground">—</span>;
  return <span className={cn("chip", STATUS_STYLE[status])}>{status}</span>;
}

const LEVEL_STYLE: Record<AlertLevel, string> = {
  good: "border-positive/35 bg-positive/10 text-positive",
  warn: "border-warn/35 bg-warn/10 text-warn",
  bad: "border-negative/35 bg-negative/10 text-negative",
};

export function AlertChip({ alert }: { alert: Alert }) {
  return (
    <span className={cn("chip normal-case tracking-normal", LEVEL_STYLE[alert.level])}>
      {alert.text}
    </span>
  );
}

export function Tag({
  children,
  tone = "muted",
  title,
}: {
  children: ReactNode;
  tone?: "muted" | "gold" | "iris" | "positive" | "negative" | "warn";
  title?: string;
}) {
  const style = {
    muted: "border-border bg-surface-2 text-muted-foreground",
    gold: "border-gold/35 bg-gold/10 text-gold-soft",
    iris: "border-iris/35 bg-iris/10 text-iris",
    positive: "border-positive/35 bg-positive/10 text-positive",
    negative: "border-negative/35 bg-negative/10 text-negative",
    warn: "border-warn/35 bg-warn/10 text-warn",
  }[tone];
  return (
    <span className={cn("chip", style)} title={title}>
      {children}
    </span>
  );
}

/**
 * When the data on screen was taken, and how long ago that was.
 *
 * The stamp is the snapshot's own as-of, not the moment the browser
 * fetched it: on a desk the question is always how old the prices are,
 * and a fetch time would answer a different one. It counts up on its own
 * so a stalled pipeline is visible without waiting for the next poll.
 */
export function LastUpdate({
  asOf,
  fetchedAt,
  staleAfter = 420,
}: {
  asOf: string | null;
  fetchedAt?: number | null;
  staleAfter?: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  if (!asOf) return <span className="desk-label">no tick yet</span>;
  const stamp = new Date(asOf);
  const seconds = Math.max(0, (now - stamp.getTime()) / 1000);
  const ago =
    seconds < 90
      ? `${Math.round(seconds)}s`
      : seconds < 5400
        ? `${Math.round(seconds / 60)}m`
        : `${(seconds / 3600).toFixed(1)}h`;

  return (
    <span
      className="desk-label whitespace-nowrap"
      title={
        `Data as of ${stamp.toLocaleString()}` +
        (fetchedAt ? `\nFetched ${new Date(fetchedAt).toLocaleTimeString()}` : "")
      }
    >
      updated{" "}
      <span
        className={cn(
          "desk-value",
          seconds >= staleAfter ? "text-warn" : "text-foreground",
        )}
      >
        {stamp.toLocaleTimeString()}
      </span>{" "}
      <span className={seconds >= staleAfter ? "text-warn" : undefined}>
        ({ago} ago)
      </span>
    </span>
  );
}

/** How long ago, in the shortest form that is still unambiguous. */
export function Age({ seconds, warnAt = 300 }: { seconds: number | null; warnAt?: number }) {
  if (seconds == null) return <span className="text-muted-foreground">—</span>;
  const label =
    seconds < 90
      ? `${Math.round(seconds)}s`
      : seconds < 5400
        ? `${Math.round(seconds / 60)}m`
        : `${(seconds / 3600).toFixed(1)}h`;
  return (
    <span className={cn("desk-value", seconds >= warnAt && "text-warn")}>{label}</span>
  );
}
