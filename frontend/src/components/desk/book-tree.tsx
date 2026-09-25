"use client";

/**
 * The book.
 *
 * Every selection the feed is carrying, grouped family → pool → line, with
 * our price beside HKJC's and the external market's. This is the screen a
 * trader scans to answer "is anything mispriced", so the three prices sit
 * next to each other rather than on separate tabs.
 *
 * Columns worth explaining:
 *   TRUE    the belief - 1 / HKJC true odds under the MVP rule
 *   MODEL   our probability at the theta being shown
 *   SELL    our offer after the pool margin, on the price ladder
 *   HOLD    margin per dollar sold at the belief; negative means we are
 *           writing business we expect to lose money on
 *   θ*      what the same selection becomes at the optimizer's theta
 */

import { ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";

import { StatusChip, Tag } from "@/components/desk/chips";
import { Segmented } from "@/components/desk/primitives";
import { fmtMoney, fmtNum, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Book, Selection, WhatIf } from "@/types/api";

type Scope = "main" | "open" | "all";

export function BookTree({
  book,
  whatIf,
  selectedKey,
  onSelect,
}: {
  book: Book;
  whatIf: WhatIf | null;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  const [scope, setScope] = useState<Scope>("main");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const whatIfByKey = useMemo(() => {
    const map = new Map<string, number>();
    for (const s of whatIf?.selections ?? []) {
      if (s.sell_odds_what != null) map.set(s.key, s.sell_odds_what);
    }
    return map;
  }, [whatIf]);

  const keep = (s: Selection) =>
    scope === "all" ||
    (scope === "open" && s.status === "open") ||
    (scope === "main" && s.is_main_line && s.status === "open");

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-1.5">
        <Segmented
          value={scope}
          onChange={setScope}
          size="xs"
          options={[
            { value: "main", label: "Main lines" },
            { value: "open", label: "All open" },
            { value: "all", label: "Everything", count: book.totals.selections },
          ]}
        />
        {whatIf && <Tag tone="gold">what-if priced</Tag>}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="desk-grid" data-density="compact">
          <thead>
            <tr>
              <th className="pin-l w-[230px]">Selection</th>
              <th className="text-right">HKJC</th>
              <th className="text-right">True</th>
              <th className="text-right">Model</th>
              <th className="text-right">Sell</th>
              <th className="text-right">θ*</th>
              <th className="text-right">Mkt avg</th>
              <th className="text-right">Edge</th>
              <th className="text-right">Hold</th>
              <th className="text-right">T̂ 5m</th>
              <th className="w-[70px]">Status</th>
            </tr>
          </thead>
          {book.families.map((family) => {
            const pools = family.pools
              .map((p) => ({
                ...p,
                lines: p.lines
                  .map((l) => ({ ...l, selections: l.selections.filter(keep) }))
                  .filter((l) => l.selections.length > 0),
              }))
              .filter((p) => p.lines.length > 0);
            if (!pools.length) return null;
            const famId = `f:${family.family}`;
            const famOpen = !collapsed.has(famId);
            return (
              <tbody key={family.family}>
                <tr className="bg-surface-2/60">
                  <td className="pin-l bg-surface-2/60" colSpan={11}>
                    <button
                      type="button"
                      onClick={() => toggle(famId)}
                      className="flex w-full items-center gap-1.5 text-left"
                    >
                      {famOpen ? (
                        <ChevronDown className="h-3 w-3 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="h-3 w-3 text-muted-foreground" />
                      )}
                      <span className="desk-label text-foreground/80">
                        {family.family}
                      </span>
                      <span className="desk-value text-[10px] text-muted-foreground">
                        {fmtMoney(family.turnover)} forecast
                      </span>
                    </button>
                  </td>
                </tr>
                {famOpen &&
                  pools.map((pool) =>
                    pool.lines.map((line) => (
                      <PoolLine
                        key={`${pool.pool_code}:${line.line_id}`}
                        poolCode={pool.pool_code}
                        poolName={pool.pool_name}
                        optimized={pool.optimized}
                        margin={pool.margin}
                        lineLabel={line.line_label}
                        bookSum={line.book_sum}
                        isMain={line.is_main_line}
                        selections={line.selections}
                        whatIfByKey={whatIfByKey}
                        selectedKey={selectedKey}
                        onSelect={onSelect}
                      />
                    )),
                  )}
              </tbody>
            );
          })}
        </table>
      </div>
    </div>
  );
}

function PoolLine({
  poolCode,
  poolName,
  optimized,
  margin,
  lineLabel,
  bookSum,
  isMain,
  selections,
  whatIfByKey,
  selectedKey,
  onSelect,
}: {
  poolCode: string;
  poolName: string | null;
  optimized: boolean;
  margin: number | null;
  lineLabel: string | null;
  bookSum: number | null;
  isMain: boolean;
  selections: Selection[];
  whatIfByKey: Map<string, number>;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  // A true book that does not add up distorts every margin on the line, so
  // it is called out here rather than left inside a column.
  const incoherent = bookSum != null && Math.abs(bookSum - 1) > 0.06;

  return (
    <>
      <tr>
        <td className="pin-l !py-1" colSpan={11}>
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-medium text-foreground/90">{poolCode}</span>
            {lineLabel && (
              <span className="desk-value text-[11px] text-gold-soft">{lineLabel}</span>
            )}
            {isMain && <Tag tone="gold">main</Tag>}
            {!optimized && <Tag tone="muted">carried</Tag>}
            <span className="text-[10px] text-muted-foreground">
              {poolName} · margin {fmtPct(margin, 1)}
            </span>
            {incoherent && (
              <Tag tone="warn" title="Sum of 1/true odds across this line">
                book {fmtNum(bookSum, 3)}
              </Tag>
            )}
          </div>
        </td>
      </tr>
      {selections.map((s) => {
        const what = whatIfByKey.get(s.key) ?? null;
        return (
          <tr
            key={s.key}
            data-selected={selectedKey === s.key}
            onClick={() => onSelect(s.key)}
            className="cursor-pointer"
          >
            <td className="pin-l pl-5">
              <span className="truncate text-[11px]">{s.sel_label}</span>
            </td>
            <td className="desk-value text-right text-muted-foreground">
              {fmtNum(s.hkjc_odds, 2)}
            </td>
            <td className="desk-value text-right">{fmtNum(s.hkjc_true_odds, 2)}</td>
            <td className="desk-value text-right text-muted-foreground">
              {fmtPct(s.model_prob, 1)}
            </td>
            <td className="desk-value text-right font-medium">{fmtNum(s.sell_odds, 2)}</td>
            <td className="desk-value text-right">
              {what != null ? (
                <span className="text-gold-soft">{fmtNum(what, 2)}</span>
              ) : (
                <StarOdds now={s.sell_odds} star={s.sell_odds_star} />
              )}
            </td>
            <td className="desk-value text-right text-muted-foreground">
              {fmtNum(s.mkt_avg, 2)}
              {s.mkt_n ? (
                <span className="ml-1 text-[9px] text-muted-foreground/60">
                  ×{s.mkt_n}
                </span>
              ) : null}
            </td>
            <td
              className={cn(
                "desk-value text-right",
                (s.edge_mkt ?? 0) > 0.02 && "text-warn",
              )}
              title="Our offer against the external consensus"
            >
              {s.edge_mkt == null ? "—" : fmtPct(s.edge_mkt, 1)}
            </td>
            <td
              className={cn(
                "desk-value text-right",
                (s.exp_gm_unit ?? 0) < 0 ? "text-negative" : "text-positive",
              )}
            >
              {s.exp_gm_unit == null ? "—" : fmtPct(s.exp_gm_unit, 1)}
            </td>
            <td className="desk-value text-right text-muted-foreground">
              {fmtMoney(s.t_hat)}
              {s.t_hat_src !== "observed" && (
                <span className="ml-1 text-[9px] text-warn/80">{s.t_hat_src}</span>
              )}
            </td>
            <td>
              <StatusChip status={s.status} />
            </td>
          </tr>
        );
      })}
    </>
  );
}

function StarOdds({ now, star }: { now: number | null; star: number | null }) {
  if (star == null) return <span className="text-muted-foreground">—</span>;
  const moved = now != null && Math.abs(star - now) >= 0.01;
  return (
    <span className={moved ? "text-gold-soft" : "text-muted-foreground"}>
      {star.toFixed(2)}
    </span>
  );
}
