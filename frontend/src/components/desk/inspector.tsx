"use client";

/**
 * The inspector.
 *
 * Six views over the same match, each answering one question a trader asks
 * after the board has told them where to look:
 *
 *   Solve    what did the optimizer do, and did it converge
 *   Curves   is the recommendation a peak or a plateau
 *   Pools    where does the uplift actually come from
 *   Market   what is everyone else showing on this selection
 *   Flow     who is betting, how much, and is it speeding up
 *   Tape     what has happened on the pitch
 */

import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Age, Tag } from "@/components/desk/chips";
import { BarCell, EmptyState, Segmented } from "@/components/desk/primitives";
import { api } from "@/lib/api";
import { fmtMoney, fmtMoneyExact, fmtNum, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Cockpit, GmCurve, Selection, ThetaDim } from "@/types/api";

type Tab = "solve" | "curves" | "pools" | "market" | "flow" | "tape";

const TABS: { value: Tab; label: string }[] = [
  { value: "solve", label: "Solve" },
  { value: "curves", label: "Curves" },
  { value: "pools", label: "Pools" },
  { value: "market", label: "Market" },
  { value: "flow", label: "Flow" },
  { value: "tape", label: "Tape" },
];

export function Inspector({
  cockpit,
  selection,
}: {
  cockpit: Cockpit;
  selection: Selection | null;
}) {
  const [tab, setTab] = useState<Tab>("solve");
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b border-border px-3 py-1.5">
        <Segmented value={tab} onChange={setTab} options={TABS} size="xs" />
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "solve" && <SolveTab cockpit={cockpit} />}
        {tab === "curves" && <CurvesTab cockpit={cockpit} />}
        {tab === "pools" && <PoolsTab cockpit={cockpit} />}
        {tab === "market" && <MarketTab cockpit={cockpit} selection={selection} />}
        {tab === "flow" && <FlowTab cockpit={cockpit} selection={selection} />}
        {tab === "tape" && <TapeTab cockpit={cockpit} />}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ solve */

function SolveTab({ cockpit }: { cockpit: Cockpit }) {
  const opt = cockpit.optimizer;
  if (!opt) {
    return cockpit.solving ? (
      <EmptyState
        title="Solving this match…"
        body="The board is priced already. The optimizer works down the card
              richest first, and this match's recommendation will appear here
              on the next poll."
      />
    ) : (
      <EmptyState
        title="Not solved"
        body="The optimizer did not run for this match on this tick — it
              carries no forecast money on any pool it controls."
      />
    );
  }
  return (
    <div className="space-y-3 p-3">
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-sm border border-border bg-border">
        <Cell label="Turnover" value={fmtMoneyExact(opt.turnover)} />
        <Cell label="E[GM] now" value={fmtMoneyExact(opt.gm_now)} />
        <Cell label="E[GM] θ*" value={fmtMoneyExact(opt.gm_star)} tone="gold" />
        <Cell
          label={`Uplift · ${fmtNum(opt.uplift_bps, 0)} bps`}
          value={fmtMoneyExact(opt.uplift)}
          tone={(opt.uplift ?? 0) > 0 ? "positive" : "default"}
        />
      </div>

      {opt.repaired?.length > 0 && (
        <div className="rounded-sm border border-warn/35 bg-warn/10 px-2 py-1.5">
          <div className="desk-label text-warn">Incoherent parameters</div>
          <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground">
            The feed sent values no book can be built from — a supremacy
            larger than the total it is a difference of, or a first half
            larger than the match. They were clamped before pricing, which
            is what the grid does anyway.
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {opt.repaired.map((r) => (
              <Tag key={r.dim} tone="warn">
                {r.label} {fmtNum(r.from, 3)} → {fmtNum(r.to, 3)}
              </Tag>
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="desk-label mb-1">Blocks</div>
        {/* The solver message is a sentence, not a column: putting it in
            the table is what used to push this panel off the screen. */}
        <div className="space-y-1">
          {opt.blocks.map((b) => (
            <div key={b.block} className="desk-surface rounded-sm px-2 py-1.5">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[11px] font-medium capitalize">{b.block}</span>
                <span className="desk-value text-[11px]">
                  {fmtMoneyExact(b.gm_now)}{" "}
                  <span className="text-muted-foreground">→</span>{" "}
                  <span className="text-gold-soft">{fmtMoneyExact(b.gm_star)}</span>
                  <span
                    className={cn(
                      "ml-2",
                      (b.uplift ?? 0) > 0 ? "text-positive" : "text-muted-foreground",
                    )}
                  >
                    {(b.uplift ?? 0) > 0 ? "+" : ""}
                    {fmtMoneyExact(b.uplift)}
                  </span>
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
                <Tag tone={b.success ? "positive" : "negative"}>{b.message}</Tag>
                <span>
                  {b.legs} legs · {b.starts} starts · {fmtNum(b.seconds, 2)}s
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div className="desk-label mb-1">Recommended moves</div>
        {opt.moves.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            The current parameters are already the best this tick allows.
          </p>
        ) : (
          <table className="desk-grid">
            <thead>
              <tr>
                <th>Dimension</th>
                <th className="text-right">Now</th>
                <th className="text-right">θ*</th>
                <th className="text-right">Δ</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {opt.moves.map((m) => (
                <tr key={m.dim}>
                  <td>
                    {m.label}
                    <span className="ml-1.5 text-[10px] text-muted-foreground">
                      {m.dim}
                    </span>
                  </td>
                  <td className="desk-value text-right">{fmtNum(m.now, 3)}</td>
                  <td className="desk-value text-right text-gold-soft">
                    {fmtNum(m.star, 3)}
                  </td>
                  <td
                    className={cn(
                      "desk-value text-right",
                      (m.delta ?? 0) > 0 ? "text-positive" : "text-negative",
                    )}
                  >
                    {fmtNum(m.delta, 3)}
                  </td>
                  <td>
                    {m.at_limit && (
                      <Tag tone="warn">pinned at this tick&apos;s movement limit</Tag>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p className="text-[10px] leading-relaxed text-muted-foreground">
        {opt.legs} legs entered the objective and {opt.skipped} were skipped — a leg
        needs forecast money and a true price to carry any weight. Solved in{" "}
        {fmtNum(opt.seconds, 2)}s.
      </p>
    </div>
  );
}

/* ----------------------------------------------------------------- curves */

function CurvesTab({ cockpit }: { cockpit: Cockpit }) {
  // Curves cost about as much as the solve, so the tick does not carry them
  // and this tab asks for them the first time a trader opens it. The result
  // is stamped with the match it belongs to, so switching matches shows
  // "computing" rather than the previous match's curves.
  const [fetched, setFetched] = useState<{
    matchId: number;
    curves?: Partial<Record<ThetaDim, GmCurve>>;
    error?: string;
  } | null>(null);
  const embedded = cockpit.optimizer?.curves ?? {};
  const hasEmbedded = Object.keys(embedded).length > 0;
  const matchId = cockpit.match.match_id;

  useEffect(() => {
    if (hasEmbedded) return;
    let alive = true;
    api
      .curves(matchId)
      .then((res) => alive && setFetched({ matchId, curves: res.curves }))
      .catch(
        (err) => alive && setFetched({ matchId, error: String(err?.message ?? err) }),
      );
    return () => {
      alive = false;
    };
  }, [matchId, hasEmbedded]);

  const ready = hasEmbedded || fetched?.matchId === matchId;
  const curves = hasEmbedded ? embedded : (fetched?.curves ?? {});
  const dims = Object.keys(curves) as ThetaDim[];

  if (ready && fetched?.error) {
    return <EmptyState title="Could not compute the curves" body={fetched.error} />;
  }
  if (!ready) {
    return <EmptyState title="Computing response curves…" body="This takes a moment." />;
  }
  if (!dims.length) {
    return (
      <EmptyState
        title="No response curves"
        body="Every dimension is either settled or carries no forecast money on this tick."
      />
    );
  }
  return (
    <div className="grid grid-cols-1 gap-2 p-3 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-2">
      {dims.map((dim) => {
        const c = curves[dim]!;
        const data = c.x.map((x, i) => ({ x, gm: c.gm[i] }));
        const label = cockpit.theta.dims.find((d) => d.dim === dim)?.label ?? dim;
        return (
          <div key={dim} className="desk-surface rounded-sm p-2">
            <div className="mb-1 flex items-baseline justify-between">
              <span className="text-[11px] font-medium">{label}</span>
              <span className="desk-value text-[10px] text-muted-foreground">
                {c.now.toFixed(3)} → <span className="text-gold-soft">{c.star.toFixed(3)}</span>
              </span>
            </div>
            <ResponsiveContainer width="100%" height={110}>
              <LineChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="2 3" />
                <XAxis
                  dataKey="x"
                  tick={{ fontSize: 9, fill: "var(--muted-foreground)" }}
                  tickFormatter={(v: number) => v.toFixed(2)}
                  stroke="var(--border-strong)"
                />
                <YAxis
                  tick={{ fontSize: 9, fill: "var(--muted-foreground)" }}
                  tickFormatter={(v: number) => fmtMoney(v)}
                  width={38}
                  stroke="var(--border-strong)"
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--popover)",
                    border: "1px solid var(--border-strong)",
                    borderRadius: 3,
                    fontSize: 11,
                  }}
                  formatter={(v) => [fmtMoneyExact(Number(v)), "E[GM]"]}
                  labelFormatter={(v) => `${label} = ${Number(v).toFixed(3)}`}
                />
                <ReferenceLine x={c.now} stroke="var(--muted-foreground)" strokeDasharray="3 2" />
                <ReferenceLine x={c.star} stroke="var(--gold)" />
                <Line
                  type="monotone"
                  dataKey="gm"
                  stroke="var(--iris)"
                  strokeWidth={1.4}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ pools */

function PoolsTab({ cockpit }: { cockpit: Cockpit }) {
  const rows = cockpit.optimizer?.per_pool ?? [];
  if (!rows.length) return <EmptyState title="No attribution" body="Nothing was solved." />;
  const maxUplift = Math.max(1, ...rows.map((r) => Math.abs(r.uplift)));
  const maxTurnover = Math.max(1, ...rows.map((r) => r.turnover));
  return (
    <table className="desk-grid">
      <thead>
        <tr>
          <th>Pool</th>
          <th className="text-right">Turnover</th>
          <th className="text-right" title="Expected gross margin at the current theta">
            E[GM]
          </th>
          <th className="text-right" title="Margin as a percentage of turnover">
            %
          </th>
          <th className="text-right">Uplift</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.pool_code}>
            <td>
              <span className="font-medium">{r.pool_code}</span>{" "}
              <span className="text-[10px] text-muted-foreground">
                {r.legs} legs
              </span>
            </td>
            <td className="text-right">
              <BarCell value={r.turnover} max={maxTurnover} tone="iris">
                {fmtMoney(r.turnover)}
              </BarCell>
            </td>
            <td
              className={cn(
                "desk-value text-right",
                r.gm_now < 0 && "text-negative",
              )}
              title={`${fmtMoneyExact(r.gm_now)} now, ${fmtMoneyExact(r.gm_star)} at theta*`}
            >
              {fmtMoney(r.gm_now)}
            </td>
            <td className="desk-value text-right text-muted-foreground">
              {r.turnover > 0 ? fmtPct(r.gm_now / r.turnover, 1) : "—"}
            </td>
            <td className="text-right">
              <BarCell value={r.uplift} max={maxUplift} tone="gold">
                <span className={r.uplift > 0 ? "text-positive" : "text-muted-foreground"}>
                  {fmtMoneyExact(r.uplift)}
                </span>
              </BarCell>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ----------------------------------------------------------------- market */

function MarketTab({
  cockpit,
  selection,
}: {
  cockpit: Cockpit;
  selection: Selection | null;
}) {
  const rows = useMemo(() => {
    if (!selection) return [];
    return cockpit.quotes.rows
      .filter((q) => q.key === selection.key)
      .sort((a, b) => (b.odds ?? 0) - (a.odds ?? 0));
  }, [cockpit.quotes.rows, selection]);

  if (!selection) {
    return (
      <EmptyState
        title="Pick a selection"
        body={`${cockpit.quotes.books.length} bookmakers are being tracked on this match. Click a row in the book to see who is pricing it and where.`}
      />
    );
  }

  return (
    <div className="p-3">
      <div className="mb-2 flex items-baseline gap-2">
        <span className="text-[12px] font-medium">
          {selection.pool_code} {selection.line_label} {selection.sel_label}
        </span>
        <span className="desk-value text-[11px] text-muted-foreground">
          ours {fmtNum(selection.sell_odds, 2)} · HKJC {fmtNum(selection.hkjc_odds, 2)}
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          No external book is quoting this selection.
        </p>
      ) : (
        <table className="desk-grid">
          <thead>
            <tr>
              <th>Bookmaker</th>
              <th className="text-right">Odds</th>
              <th className="text-right">vs ours</th>
              <th className="text-right">Age</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((q, i) => {
              const gap =
                q.odds != null && selection.sell_odds
                  ? q.odds / selection.sell_odds - 1
                  : null;
              return (
                <tr key={`${q.bookmaker}-${i}`}>
                  <td>{q.bookmaker}</td>
                  <td className="desk-value text-right">{fmtNum(q.odds, 2)}</td>
                  <td
                    className={cn(
                      "desk-value text-right",
                      (gap ?? 0) > 0 ? "text-warn" : "text-positive",
                    )}
                    title="Positive means they are showing a longer price than we are"
                  >
                    {gap == null ? "—" : fmtPct(gap, 1)}
                  </td>
                  <td className="text-right">
                    <Age seconds={q.age_sec} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- flow */

function FlowTab({
  cockpit,
  selection,
}: {
  cockpit: Cockpit;
  selection: Selection | null;
}) {
  const all = useMemo(
    () =>
      cockpit.book.families
        .flatMap((f) => f.pools)
        .flatMap((p) => p.lines)
        .flatMap((l) => l.selections)
        .filter((s) => (s.t_hat ?? 0) > 0)
        .sort((a, b) => (b.t_hat ?? 0) - (a.t_hat ?? 0))
        .slice(0, 25),
    [cockpit.book],
  );
  const max = Math.max(1, ...all.map((s) => s.t_hat ?? 0));

  return (
    <div className="p-3">
      <p className="desk-label mb-2">
        Where the money is going — forecast next bucket, biggest first
      </p>
      <table className="desk-grid" data-density="compact">
        <thead>
          <tr>
            <th>Selection</th>
            <th className="text-right">Forecast</th>
            <th className="text-right" title="Tickets in the last five minutes, and the average stake on them">
              Tickets
            </th>
            <th className="text-right" title="This bucket against the one before it">
              Trend
            </th>
          </tr>
        </thead>
        <tbody>
          {all.map((s) => (
            <tr key={s.key} data-selected={selection?.key === s.key}>
              <td>
                <span className="text-[11px] text-muted-foreground">{s.pool_code}</span>{" "}
                <span className="text-[11px]">
                  {s.line_label} {s.sel_label}
                </span>
              </td>
              <td className="text-right">
                <BarCell value={s.t_hat ?? 0} max={max} tone="iris">
                  {fmtMoney(s.t_hat)}
                </BarCell>
              </td>
              <td
                className="desk-value text-right text-muted-foreground"
                title={`${fmtMoney(s.avg_stake)} average stake · ${fmtMoney(s.invested)} today`}
              >
                {fmtNum(s.tickets5m, 0)}
                <span className="ml-1 text-[10px]">@{fmtMoney(s.avg_stake)}</span>
              </td>
              <td
                className={cn(
                  "desk-value text-right",
                  (s.t_trend ?? 1) > 1.5 && "text-warn",
                )}
              >
                {s.t_trend == null ? "—" : `${s.t_trend.toFixed(2)}×`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------- tape */

function TapeTab({ cockpit }: { cockpit: Cockpit }) {
  if (!cockpit.events.length) {
    return (
      <EmptyState
        title="Nothing on the tape"
        body="No incident has been reported for this match yet."
      />
    );
  }
  return (
    <table className="desk-grid" data-density="compact">
      <thead>
        <tr>
          <th className="w-[86px]">Time</th>
          <th className="w-[70px]">Type</th>
          <th>Detail</th>
          <th className="w-[70px] text-right">Provider</th>
        </tr>
      </thead>
      <tbody>
        {cockpit.events.map((e, i) => (
          <tr key={i}>
            <td className="desk-value text-muted-foreground">
              {e.at ? new Date(e.at).toLocaleTimeString() : "—"}
            </td>
            <td>
              <Tag
                tone={
                  e.type === "GOAL"
                    ? "positive"
                    : e.type === "RED"
                      ? "negative"
                      : e.type.includes("CANCEL")
                        ? "warn"
                        : "muted"
                }
              >
                {e.type}
              </Tag>
            </td>
            <td className="text-[11px]">{e.detail}</td>
            <td className="desk-value text-right text-muted-foreground">
              {e.provider_id ?? "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ----------------------------------------------------------------- shared */

function Cell({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "gold" | "positive";
}) {
  const cls = {
    default: "text-foreground",
    gold: "text-gold-soft",
    positive: "text-positive",
  }[tone];
  return (
    <div className="bg-card px-3 py-2">
      <div className="desk-label">{label}</div>
      <div className={cn("desk-value text-[14px]", cls)}>{value}</div>
    </div>
  );
}
