"use client";

/**
 * The status strip that sits above everything.
 *
 * A trading screen has to answer "can I trust what I am looking at" before
 * it answers anything else, so this shows where the data came from, how old
 * the tick is, and what the pipeline warned about - and it says the two MVP
 * simplifications out loud rather than letting the numbers imply a model
 * that is not there.
 */

import { AlertTriangle, Database, RefreshCw } from "lucide-react";
import { useState } from "react";

import { LastUpdate, Tag } from "@/components/desk/chips";
import { fmtMoneyExact, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Board } from "@/types/api";

export function TickBar({
  board,
  refreshing,
  onRefresh,
  updatedAt,
}: {
  board: Board;
  refreshing: boolean;
  onRefresh: () => void;
  updatedAt: number | null;
}) {
  const [showWarnings, setShowWarnings] = useState(false);
  const t = board.totals;
  const simulated = board.source === "sim";

  return (
    <div className="shrink-0 border-b border-border bg-surface/60">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-3 py-2">
        <div className="flex items-center gap-2">
          <Database className="h-3.5 w-3.5 text-muted-foreground" />
          <Tag tone={simulated ? "warn" : "iris"}>
            {simulated ? "simulated data" : board.source}
          </Tag>
          <span className="desk-value text-[11px] text-muted-foreground">
            {board.run_id}
          </span>
        </div>

        <Stat label="Matches" value={String(t.matches)} />
        <Stat label="Legs priced" value={`${t.optimized_legs ?? t.legs} / ${t.selections}`} />
        <Stat label="Turnover 5m" value={fmtMoneyExact(t.opt_turnover ?? t.turnover)} />
        <Stat label="E[GM] now" value={fmtMoneyExact(t.opt_gm_now ?? t.gm_now)} />
        <Stat
          label="E[GM] at θ*"
          value={fmtMoneyExact(t.gm_star)}
          className="text-gold-soft"
        />
        <Stat
          label="Uplift"
          value={`${fmtMoneyExact(t.uplift)}  ·  ${fmtNum(t.uplift_bps, 0)} bps`}
          className={(t.uplift ?? 0) > 0 ? "text-positive" : undefined}
        />

        <div className="ml-auto flex items-center gap-2">
          {board.progress?.solving && (
            <Tag tone="iris" title="The board is priced; recommendations are still landing">
              <RefreshCw className="mr-1 inline h-3 w-3 animate-spin" />
              solving {board.progress.solved}/{board.progress.total}
            </Tag>
          )}
          {board.warnings.length > 0 && (
            <button
              type="button"
              onClick={() => setShowWarnings((v) => !v)}
              data-active={showWarnings}
              className="desk-btn"
            >
              <AlertTriangle className="h-3.5 w-3.5 text-warn" />
              {board.warnings.length} note{board.warnings.length > 1 ? "s" : ""}
            </button>
          )}
          <LastUpdate asOf={board.as_of} fetchedAt={updatedAt} />
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="desk-btn"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
            {refreshing ? "Ticking" : "Run tick"}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-border/60 px-3 py-1">
        <span className="desk-label">MVP rules</span>
        <Tag tone="muted" title={board.rules.true_prob.note}>
          {board.rules.true_prob.label}
        </Tag>
        <Tag tone="muted" title={board.rules.turnover.note}>
          {board.rules.turnover.label}
        </Tag>
        <Tag tone="muted" title="How the algo_param expectancies are read in play">
          θ = {board.rules.theta_semantics}
        </Tag>
        <Tag tone="muted">layer {board.rules.theta_layer}</Tag>
        <span className="ml-auto text-[10px] text-muted-foreground">
          {Object.entries(board.timings)
            .map(([k, v]) => `${k} ${v.toFixed(2)}s`)
            .join("  ·  ")}
        </span>
      </div>

      {showWarnings && (
        <ul className="border-t border-border/60 bg-row-alert/50 px-3 py-2 text-[11px] leading-relaxed">
          {board.warnings.map((w, i) => (
            <li key={i} className="text-warn/90">
              — {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="desk-label">{label}</span>
      <span className={cn("desk-value text-[12px]", className)}>{value}</span>
    </div>
  );
}
