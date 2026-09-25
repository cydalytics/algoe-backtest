"use client";

/**
 * The board.
 *
 * One row per match, ordered by what the optimizer says is on the table.
 * The question this screen answers is "where do I look first", so the
 * columns are the ones that change that answer: how much money is coming,
 * what the current book earns on it, what the recommendation would earn
 * instead, and whether anything is wrong with the inputs.
 *
 * Uplift is shown in dollars and in basis points side by side. Dollars
 * decide what to work on; basis points say whether the edge is real or
 * just a big match, and a desk that only sees one of the two ends up
 * spending its attention on turnover rather than on margin.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { AlertChip, PhaseChip, Tag } from "@/components/desk/chips";
import { BarCell, Segmented } from "@/components/desk/primitives";
import { fmtMoney, fmtMoneyExact, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { BoardMatch } from "@/types/api";

type SortKey = "uplift" | "bps" | "turnover" | "gm" | "minute";
type Filter = "all" | "live" | "pre" | "action";

const SORTS: { value: SortKey; label: string }[] = [
  { value: "uplift", label: "Uplift $" },
  { value: "bps", label: "Uplift bps" },
  { value: "turnover", label: "Turnover" },
  { value: "gm", label: "E[GM]" },
  { value: "minute", label: "Clock" },
];

function value(m: BoardMatch, key: SortKey): number {
  switch (key) {
    case "uplift":
      return m.uplift ?? 0;
    case "bps":
      return m.uplift_bps ?? 0;
    case "turnover":
      return m.turnover ?? 0;
    case "gm":
      return m.gm_now ?? 0;
    case "minute":
      return m.in_play ? (m.minute ?? 0) : -1;
  }
}

function needsAction(m: BoardMatch): boolean {
  // A match still in the solver's queue has not failed at anything, so it
  // is not something to look at yet.
  if (m.pending) return false;
  return !m.solved || m.alerts.some((a) => a.level === "bad") || (m.uplift_bps ?? 0) >= 50;
}

export function BoardTable({ matches }: { matches: BoardMatch[] }) {
  const router = useRouter();
  const [sort, setSort] = useState<SortKey>("uplift");
  const [filter, setFilter] = useState<Filter>("all");

  const counts = useMemo(
    () => ({
      all: matches.length,
      live: matches.filter((m) => m.in_play).length,
      pre: matches.filter((m) => !m.in_play).length,
      action: matches.filter(needsAction).length,
    }),
    [matches],
  );

  const rows = useMemo(() => {
    const kept = matches.filter((m) => {
      if (filter === "live") return m.in_play;
      if (filter === "pre") return !m.in_play;
      if (filter === "action") return needsAction(m);
      return true;
    });
    return [...kept].sort((a, b) => value(b, sort) - value(a, sort));
  }, [matches, sort, filter]);

  const maxTurnover = Math.max(1, ...rows.map((m) => m.turnover ?? 0));
  const maxUplift = Math.max(1, ...rows.map((m) => Math.abs(m.uplift ?? 0)));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-3 py-2">
        <Segmented
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "All", count: counts.all },
            { value: "live", label: "In play", count: counts.live },
            { value: "pre", label: "Pre", count: counts.pre },
            { value: "action", label: "Needs a look", count: counts.action },
          ]}
        />
        <div className="flex items-center gap-2">
          <span className="desk-label">Sort</span>
          <Segmented value={sort} onChange={setSort} options={SORTS} size="xs" />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="desk-grid">
          <thead>
            <tr>
              <th className="pin-l w-[280px]">Match</th>
              <th className="w-[76px]">Phase</th>
              <th className="w-[62px] text-center">Score</th>
              <th className="text-right">Turnover 5m</th>
              <th className="text-right">E[GM] now</th>
              <th className="text-right">E[GM] θ*</th>
              <th className="text-right">Uplift</th>
              <th className="text-right">bps</th>
              <th className="w-[150px]">Recommended move</th>
              <th className="text-right">Book</th>
              <th className="text-right">θ age</th>
              <th className="min-w-[220px]">Flags</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr
                key={m.match_id}
                // The whole row opens the match. The link on the name stays
                // so middle-click, ctrl-click and the keyboard still work.
                onClick={() => router.push(`/match/${m.match_id}`)}
                className={cn(
                  "cursor-pointer hover:bg-surface-2/60",
                  needsAction(m) && "bg-row-alert/40",
                )}
              >
                <td className="pin-l">
                  <Link
                    href={`/match/${m.match_id}`}
                    className="block truncate hover:text-gold-soft"
                  >
                    <span className="font-medium">{m.home}</span>
                    <span className="px-1 text-muted-foreground">v</span>
                    <span className="font-medium">{m.away}</span>
                    <span className="ml-1.5 text-[10px] text-muted-foreground">
                      {m.league_code}
                    </span>
                  </Link>
                </td>
                <td>
                  <PhaseChip phase={m.phase} minute={m.minute} />
                </td>
                <td className="desk-value text-center">
                  {m.score[0]}–{m.score[1]}
                  <span className="ml-1 text-[10px] text-muted-foreground">
                    {m.corners[0]}–{m.corners[1]}c
                  </span>
                </td>
                <td className="text-right">
                  <BarCell value={m.turnover ?? 0} max={maxTurnover} tone="iris">
                    {fmtMoney(m.turnover_5m)}
                  </BarCell>
                </td>
                <td className="desk-value text-right">{fmtMoneyExact(m.gm_now)}</td>
                <td className="desk-value text-right text-gold-soft">
                  {fmtMoneyExact(m.gm_star)}
                </td>
                <td className="text-right">
                  <BarCell value={m.uplift ?? 0} max={maxUplift} tone="gold">
                    <span className={(m.uplift ?? 0) > 0 ? "text-positive" : ""}>
                      {fmtMoneyExact(m.uplift)}
                    </span>
                  </BarCell>
                </td>
                <td
                  className={cn(
                    "desk-value text-right",
                    (m.uplift_bps ?? 0) >= 50 && "text-gold-soft",
                  )}
                >
                  {fmtNum(m.uplift_bps, 0)}
                </td>
                <td>
                  <MoveSummary match={m} />
                </td>
                <td className="desk-value text-right text-muted-foreground">
                  {m.open}/{m.selections}
                </td>
                <td
                  className={cn(
                    "desk-value text-right",
                    (m.theta_age_min ?? 0) >= 10 && "text-warn",
                  )}
                >
                  {fmtNum(m.theta_age_min, 0)}m
                </td>
                <td>
                  <div className="flex flex-wrap gap-1">
                    {m.pending && <Tag tone="iris">queued to solve</Tag>}
                    {!m.pending && !m.solved && <Tag tone="negative">no solve</Tag>}
                    {m.alerts.map((a, i) => (
                      <AlertChip key={i} alert={a} />
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="p-8 text-center text-[12px] text-muted-foreground">
            No match matches this filter.
          </div>
        )}
      </div>
    </div>
  );
}

/** The two dimensions the recommendation leans on hardest. */
function MoveSummary({ match }: { match: BoardMatch }) {
  if (match.pending) {
    return <span className="text-[11px] text-muted-foreground">…</span>;
  }
  if (!match.moves.length) {
    return <span className="text-[11px] text-muted-foreground">hold</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {match.moves.slice(0, 2).map((mv) => (
        <span
          key={mv.dim}
          className={cn(
            "chip border-border bg-surface-2 normal-case tracking-normal",
            mv.at_limit && "border-warn/40 text-warn",
          )}
          title={
            mv.at_limit
              ? `${mv.label} is pinned at this tick's movement limit`
              : `${mv.label}: ${mv.now} → ${mv.star}`
          }
        >
          <span className="text-muted-foreground">{mv.label}</span>
          <span
            className={cn(
              "desk-value",
              (mv.delta ?? 0) > 0 ? "text-positive" : "text-negative",
            )}
          >
            {(mv.delta ?? 0) > 0 ? "+" : "−"}
            {Math.abs(mv.delta ?? 0).toFixed(2)}
          </span>
        </span>
      ))}
      {match.moves.length > 2 && (
        <span className="text-[10px] text-muted-foreground">
          +{match.moves.length - 2}
        </span>
      )}
    </div>
  );
}
