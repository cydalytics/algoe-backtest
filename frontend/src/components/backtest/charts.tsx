"use client";

import type { ReactNode } from "react";
import {
  Bar,
  BarChart as ReBar,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart as ReLine,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart as ReScatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { fmtMoney, fmtNum } from "@/lib/format";

export type XY = { x: number; y: number; n?: number; key?: string };
export type Bar = { key: string; value: number; n?: number; pred?: number; actual?: number };
export type HistBin = { lo: number; hi: number; n: number };
export type CalBin = { lo: number; hi: number; n: number; p_hat: number | null; p_obs: number | null };
export type HeatCell = { row: string; col: string; value: number | null };

const GOLD = "var(--gold)";
const IRIS = "var(--iris)";
const POS = "var(--positive)";
const NEG = "var(--negative)";
const MUTED = "var(--muted-foreground)";
const BORDER = "var(--border)";

const tick = { fontSize: 10, fill: MUTED };
const tipStyle = {
  background: "var(--popover)",
  border: "1px solid var(--border-strong)",
  borderRadius: 3,
  fontSize: 11,
};

function Frame({ height = 260, children }: { height?: number; children: ReactNode }) {
  return (
    <div className="w-full px-2 py-2">
      <ResponsiveContainer width="100%" height={height}>
        {children}
      </ResponsiveContainer>
    </div>
  );
}

function Empty() {
  return <div className="px-3 py-8 text-[12px] text-muted-foreground">No sample on this slice.</div>;
}

function moneyTick(v: number) {
  return fmtMoney(v);
}

export function BarChart({
  data,
  height = 240,
  signed = false,
  lowerIsBetter = false,
  format = (v: number) => fmtNum(v, 1),
  money = false,
}: {
  data: Bar[];
  height?: number;
  signed?: boolean;
  /** For signed bars: colour negative values as the good side. */
  lowerIsBetter?: boolean;
  format?: (v: number) => string;
  money?: boolean;
}) {
  if (!data.length) return <Empty />;
  const rows = data.map((d) => ({ name: d.key, value: d.value, n: d.n }));
  const fmt = money ? moneyTick : format;
  const angled = rows.length > 6;
  return (
    <Frame height={height}>
      <ReBar data={rows} margin={{ top: 8, right: 8, left: 4, bottom: angled ? 28 : 4 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" vertical={false} />
        <XAxis
          dataKey="name"
          tick={tick}
          stroke="var(--border-strong)"
          interval={0}
          angle={angled ? -30 : 0}
          textAnchor={angled ? "end" : "middle"}
          height={angled ? 48 : 28}
        />
        <YAxis
          tick={tick}
          tickFormatter={fmt}
          width={48}
          stroke="var(--border-strong)"
          domain={signed ? [(lo: number) => Math.min(0, lo), (hi: number) => Math.max(0, hi)] : undefined}
        />
        <Tooltip
          contentStyle={tipStyle}
          formatter={(v) => [fmt(Number(v)), ""]}
          labelFormatter={(label) => String(label)}
        />
        {signed && <ReferenceLine y={0} stroke="var(--border-strong)" />}
        <Bar dataKey="value" radius={[2, 2, 0, 0]} isAnimationActive={false}>
          {rows.map((d) => (
            <Cell
              key={d.name}
              fill={signed ? ((d.value >= 0) !== lowerIsBetter ? POS : NEG) : GOLD}
              fillOpacity={0.88}
            />
          ))}
        </Bar>
      </ReBar>
    </Frame>
  );
}

export function LineChart({
  data,
  height = 240,
  color = IRIS,
}: {
  data: { key: string; value: number }[];
  height?: number;
  color?: string;
}) {
  if (!data.length) return <Empty />;
  const rows = data.map((d) => ({ name: d.key, value: d.value }));
  return (
    <Frame height={height}>
      <ReLine data={rows} margin={{ top: 8, right: 12, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" />
        <XAxis dataKey="name" tick={tick} stroke="var(--border-strong)" />
        <YAxis tick={tick} tickFormatter={moneyTick} width={48} stroke="var(--border-strong)" />
        <Tooltip contentStyle={tipStyle} formatter={(v) => [fmtMoney(Number(v)), ""]} />
        <Line type="monotone" dataKey="value" stroke={color} strokeWidth={1.8} dot={{ r: 2.5, fill: GOLD }} isAnimationActive={false} />
      </ReLine>
    </Frame>
  );
}

export function ScatterChart({
  data,
  height = 300,
  diagonal = true,
  xLabel = "Forecast",
  yLabel = "Actual",
  money = true,
}: {
  data: XY[];
  height?: number;
  diagonal?: boolean;
  xLabel?: string;
  yLabel?: string;
  money?: boolean;
}) {
  if (!data.length) return <Empty />;
  const xs = data.map((d) => d.x);
  const ys = data.map((d) => d.y);
  const lo = Math.min(...xs, ...ys);
  const hi = Math.max(...xs, ...ys);
  const pad = (hi - lo) * 0.04 || 1;
  const domain: [number, number] = [lo - pad, hi + pad];
  const fmt = money ? moneyTick : (v: number) => fmtNum(v, 2);
  return (
    <Frame height={height}>
      <ReScatter margin={{ top: 8, right: 16, left: 8, bottom: 12 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" />
        <XAxis
          type="number"
          dataKey="x"
          name={xLabel}
          domain={domain}
          tick={tick}
          tickFormatter={fmt}
          stroke="var(--border-strong)"
          label={{ value: xLabel, position: "insideBottom", offset: -4, fill: MUTED, fontSize: 10 }}
        />
        <YAxis
          type="number"
          dataKey="y"
          name={yLabel}
          domain={domain}
          tick={tick}
          tickFormatter={fmt}
          width={52}
          stroke="var(--border-strong)"
          label={{ value: yLabel, angle: -90, position: "insideLeft", fill: MUTED, fontSize: 10 }}
        />
        <Tooltip
          contentStyle={tipStyle}
          formatter={(v, name) => [fmt(Number(v)), String(name)]}
          cursor={{ stroke: BORDER }}
        />
        {diagonal && (
          <ReferenceLine
            segment={[
              { x: domain[0], y: domain[0] },
              { x: domain[1], y: domain[1] },
            ]}
            stroke={BORDER}
            strokeDasharray="4 4"
          />
        )}
        <Scatter data={data} fill={IRIS} fillOpacity={0.45} r={3} isAnimationActive={false} />
      </ReScatter>
    </Frame>
  );
}

export function HistChart({
  data,
  height = 220,
  xLabel = "",
  decimals,
}: {
  data: HistBin[];
  height?: number;
  xLabel?: string;
  /** Bin-edge precision. Defaults to whole units, which is right for money but
   *  collapses every label to zero on a quantity that lives inside ±1. */
  decimals?: number;
}) {
  if (!data.length) return <Empty />;
  const span = Math.abs(data[data.length - 1].hi - data[0].lo);
  const dp = decimals ?? (span >= 20 ? 0 : span >= 2 ? 1 : 2);
  const rows = data.map((d) => ({
    name: fmtNum((d.lo + d.hi) / 2, dp),
    n: d.n,
    lo: d.lo,
    hi: d.hi,
  }));
  return (
    <Frame height={height}>
      <ReBar data={rows} margin={{ top: 8, right: 8, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" vertical={false} />
        <XAxis dataKey="name" tick={tick} stroke="var(--border-strong)" />
        <YAxis tick={tick} width={36} stroke="var(--border-strong)" />
        <Tooltip
          contentStyle={tipStyle}
          formatter={(v) => [Number(v).toLocaleString(), "rows"]}
          labelFormatter={(_, payload) => {
            const p = payload?.[0]?.payload as { lo?: number; hi?: number } | undefined;
            return p ? `${fmtNum(p.lo, dp)} to ${fmtNum(p.hi, dp)}${xLabel ? ` ${xLabel}` : ""}` : "";
          }}
        />
        <Bar dataKey="n" fill={IRIS} fillOpacity={0.8} radius={[2, 2, 0, 0]} isAnimationActive={false} />
      </ReBar>
    </Frame>
  );
}

export function ReliabilityChart({ bins }: { bins: CalBin[] }) {
  const live = bins.filter((b) => b.n > 0 && b.p_hat != null && b.p_obs != null);
  if (!live.length) return <Empty />;
  const rows = live.map((b) => ({
    x: b.p_hat as number,
    y: b.p_obs as number,
    n: b.n,
  }));
  return (
    <Frame height={300}>
      <ReScatter margin={{ top: 8, right: 16, left: 8, bottom: 16 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" />
        <XAxis
          type="number"
          dataKey="x"
          domain={[0, 1]}
          tick={tick}
          stroke="var(--border-strong)"
          label={{ value: "Predicted probability", position: "insideBottom", offset: -2, fill: MUTED, fontSize: 10 }}
        />
        <YAxis
          type="number"
          dataKey="y"
          domain={[0, 1]}
          tick={tick}
          width={40}
          stroke="var(--border-strong)"
          label={{ value: "Realised rate", angle: -90, position: "insideLeft", fill: MUTED, fontSize: 10 }}
        />
        <Tooltip
          contentStyle={tipStyle}
          formatter={(v, name) => [fmtNum(Number(v), 3), String(name)]}
          labelFormatter={() => ""}
        />
        <ReferenceLine
          segment={[
            { x: 0, y: 0 },
            { x: 1, y: 1 },
          ]}
          stroke={BORDER}
          strokeDasharray="4 4"
        />
        <Scatter data={rows} fill={GOLD} fillOpacity={0.9} isAnimationActive={false} />
      </ReScatter>
    </Frame>
  );
}

export function Heatmap({
  rows,
  cols,
  cells,
  dec = 0,
}: {
  rows: string[];
  cols: string[];
  cells: HeatCell[];
  dec?: number;
}) {
  if (!rows.length || !cols.length) return <Empty />;
  const values = cells.map((c) => c.value).filter((v): v is number => v != null);
  const lo = Math.min(...values, 0);
  const hi = Math.max(...values, 1);
  const lookup = new Map(cells.map((c) => [`${c.row}|${c.col}`, c.value]));
  return (
    <div className="overflow-auto">
      <table className="desk-grid" data-density="compact">
        <thead>
          <tr>
            <th />
            {cols.map((c) => (
              <th key={c} className="text-right">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r}>
              <td>{r}</td>
              {cols.map((c) => {
                const v = lookup.get(`${r}|${c}`);
                const t = v == null ? 0 : (v - lo) / (hi - lo || 1);
                return (
                  <td
                    key={c}
                    className="text-right desk-value"
                    style={{
                      background: `color-mix(in srgb, var(--gold) ${Math.round(t * 40)}%, transparent)`,
                    }}
                  >
                    {v == null ? "—" : fmtNum(v, dec)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Waterfall({
  steps,
}: {
  steps: { key: string; value: number; delta?: number }[];
}) {
  if (!steps.length) return <Empty />;
  const rows = steps.map((s, i) => ({
    name: s.key,
    value: s.value,
    delta: s.delta ?? (i === 0 ? s.value : s.value - steps[i - 1].value),
  }));
  return (
    <Frame height={240}>
      <ReBar data={rows} margin={{ top: 8, right: 8, left: 4, bottom: 28 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" vertical={false} />
        <XAxis dataKey="name" tick={tick} stroke="var(--border-strong)" interval={0} angle={-20} textAnchor="end" height={48} />
        <YAxis tick={tick} tickFormatter={moneyTick} width={48} stroke="var(--border-strong)" />
        <Tooltip contentStyle={tipStyle} formatter={(v) => [fmtMoney(Number(v)), "R[GM]"]} />
        <ReferenceLine y={0} stroke={BORDER} />
        <Bar dataKey="value" radius={[2, 2, 0, 0]} isAnimationActive={false}>
          {rows.map((d, i) => (
            <Cell key={d.name} fill={i === 0 ? GOLD : d.delta >= 0 ? POS : NEG} fillOpacity={0.88} />
          ))}
        </Bar>
      </ReBar>
    </Frame>
  );
}

export function DecompBar({
  items,
}: {
  items: { key: string; value: number }[];
}) {
  if (!items.length) return <Empty />;
  const max = Math.max(...items.map((i) => Math.abs(i.value)), 1e-9);
  return (
    <div className="space-y-2 px-3 py-3">
      {items.map((item) => (
        <div key={item.key}>
          <div className="flex justify-between text-[11px] text-muted-foreground">
            <span>{item.key}</span>
            <span className="desk-value">{fmtNum(item.value, 4)}</span>
          </div>
          <div className="mt-0.5 h-2 rounded-sm bg-border/40">
            <div
              className="h-2 rounded-sm"
              style={{
                width: `${Math.min(100, (Math.abs(item.value) / max) * 100)}%`,
                background: IRIS,
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

export function OverlayHist({
  data,
  height = 260,
}: {
  data: { lo: number; hi: number; a: number; b: number; a_density?: number; b_density?: number }[];
  height?: number;
}) {
  if (!data.length) return <Empty />;
  const rows = data.map((d) => ({
    name: fmtNum((d.lo + d.hi) / 2, 0),
    A: d.a_density ?? d.a,
    B: d.b_density ?? d.b,
    lo: d.lo,
    hi: d.hi,
  }));
  return (
    <Frame height={height}>
      <ReBar data={rows} margin={{ top: 8, right: 8, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" vertical={false} />
        <XAxis dataKey="name" tick={tick} stroke="var(--border-strong)" />
        <YAxis tick={tick} width={36} stroke="var(--border-strong)" />
        <Tooltip
          contentStyle={tipStyle}
          formatter={(v, name) => [fmtNum(Number(v), 3), String(name)]}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Bar dataKey="A" fill={GOLD} fillOpacity={0.55} isAnimationActive={false} />
        <Bar dataKey="B" fill={IRIS} fillOpacity={0.55} isAnimationActive={false} />
      </ReBar>
    </Frame>
  );
}

export function OverlayCdf({
  a,
  b,
  height = 260,
}: {
  a: { x: number; y: number }[];
  b: { x: number; y: number }[];
  height?: number;
}) {
  if (!a.length && !b.length) return <Empty />;
  const n = Math.max(a.length, b.length);
  const rows = Array.from({ length: n }, (_, i) => ({
    x: a[i]?.x ?? b[i]?.x ?? i,
    A: a[i]?.y,
    B: b[i]?.y,
  }));
  return (
    <Frame height={height}>
      <ReLine data={rows} margin={{ top: 8, right: 12, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" />
        <XAxis dataKey="x" tick={tick} tickFormatter={(v) => fmtNum(Number(v), 0)} stroke="var(--border-strong)" />
        <YAxis domain={[0, 1]} tick={tick} width={36} stroke="var(--border-strong)" />
        <Tooltip contentStyle={tipStyle} formatter={(v, name) => [fmtNum(Number(v), 3), String(name)]} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Line type="monotone" dataKey="A" stroke={GOLD} strokeWidth={1.8} dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="B" stroke={IRIS} strokeWidth={1.8} dot={false} isAnimationActive={false} />
      </ReLine>
    </Frame>
  );
}

export function DualReliability({
  a,
  b,
}: {
  a: CalBin[];
  b: CalBin[];
}) {
  const liveA = a.filter((x) => x.n > 0 && x.p_hat != null && x.p_obs != null);
  const liveB = b.filter((x) => x.n > 0 && x.p_hat != null && x.p_obs != null);
  if (!liveA.length && !liveB.length) return <Empty />;
  const toRows = (bins: CalBin[]) =>
    bins
      .filter((bin) => bin.p_hat != null && bin.p_obs != null)
      .map((bin) => ({ x: bin.p_hat as number, y: bin.p_obs as number, n: bin.n }));
  return (
    <Frame height={300}>
      <ReScatter margin={{ top: 8, right: 16, left: 8, bottom: 16 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" />
        <XAxis
          type="number"
          dataKey="x"
          domain={[0, 1]}
          tick={tick}
          stroke="var(--border-strong)"
          label={{ value: "Predicted probability", position: "insideBottom", offset: -2, fill: MUTED, fontSize: 10 }}
        />
        <YAxis
          type="number"
          dataKey="y"
          domain={[0, 1]}
          tick={tick}
          width={40}
          stroke="var(--border-strong)"
          label={{ value: "Realised rate", angle: -90, position: "insideLeft", fill: MUTED, fontSize: 10 }}
        />
        <Tooltip contentStyle={tipStyle} formatter={(v) => [fmtNum(Number(v), 3), ""]} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <ReferenceLine
          segment={[
            { x: 0, y: 0 },
            { x: 1, y: 1 },
          ]}
          stroke={BORDER}
          strokeDasharray="4 4"
        />
        <Scatter name="A" data={toRows(liveA)} fill={GOLD} isAnimationActive={false} />
        <Scatter name="B" data={toRows(liveB)} fill={IRIS} isAnimationActive={false} />
      </ReScatter>
    </Frame>
  );
}

export function GroupedBar({
  data,
  height = 260,
  signed = false,
  money = false,
}: {
  data: { key: string; a: number; b: number }[];
  height?: number;
  signed?: boolean;
  money?: boolean;
}) {
  if (!data.length) return <Empty />;
  const rows = data.map((d) => ({ name: d.key, A: d.a, B: d.b }));
  const fmt = money ? moneyTick : (v: number) => fmtNum(v, 1);
  const angled = rows.length > 6;
  return (
    <Frame height={height}>
      <ReBar data={rows} margin={{ top: 8, right: 8, left: 4, bottom: angled ? 28 : 4 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" vertical={false} />
        <XAxis
          dataKey="name"
          tick={tick}
          stroke="var(--border-strong)"
          interval={0}
          angle={angled ? -30 : 0}
          textAnchor={angled ? "end" : "middle"}
          height={angled ? 48 : 28}
        />
        <YAxis tick={tick} tickFormatter={fmt} width={48} stroke="var(--border-strong)" />
        <Tooltip contentStyle={tipStyle} formatter={(v, name) => [fmt(Number(v)), String(name)]} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {signed && <ReferenceLine y={0} stroke={BORDER} />}
        <Bar dataKey="A" fill={GOLD} fillOpacity={0.88} radius={[2, 2, 0, 0]} isAnimationActive={false} />
        <Bar dataKey="B" fill={IRIS} fillOpacity={0.88} radius={[2, 2, 0, 0]} isAnimationActive={false} />
      </ReBar>
    </Frame>
  );
}

export function DualLine({
  data,
  height = 260,
  unit = "%",
}: {
  data: { key: string; a: number; b: number }[];
  height?: number;
  unit?: string;
}) {
  if (!data.length) return <Empty />;
  const rows = data.map((d) => ({ name: d.key, A: d.a, B: d.b }));
  const fmt = (v: number) => `${fmtNum(v, 1)}${unit}`;
  return (
    <Frame height={height}>
      <ReLine data={rows} margin={{ top: 8, right: 12, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" />
        <XAxis dataKey="name" tick={tick} stroke="var(--border-strong)" minTickGap={16} />
        <YAxis tick={tick} tickFormatter={fmt} width={48} stroke="var(--border-strong)" />
        <Tooltip contentStyle={tipStyle} formatter={(v, name) => [fmt(Number(v)), String(name)]} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Line type="monotone" dataKey="A" stroke={GOLD} strokeWidth={1.6} dot={{ r: 2 }} isAnimationActive={false} />
        <Line type="monotone" dataKey="B" stroke={IRIS} strokeWidth={1.6} dot={{ r: 2 }} isAnimationActive={false} />
      </ReLine>
    </Frame>
  );
}

/** Mean forecast vs mean actual per forecast-quantile bin, log-log. The
 *  zero-forecast bin is drawn at the left edge so its missed money shows. */
export function LevelReliability({
  a,
  b,
  height = 300,
}: {
  a: { pred: number; actual: number; n: number }[];
  b: { pred: number; actual: number; n: number }[];
  height?: number;
}) {
  const all = [...a, ...b];
  if (!all.length) return <Empty />;
  const positive = all.flatMap((d) => [d.pred, d.actual]).filter((v) => v > 0);
  const lo = Math.max(1, Math.min(...positive) / 1.5);
  const hi = Math.max(...positive) * 1.5;
  const toRows = (bins: { pred: number; actual: number; n: number }[]) =>
    bins.map((d) => ({ x: Math.max(d.pred, lo), y: Math.max(d.actual, lo), n: d.n, zero: d.pred <= 0 }));
  return (
    <Frame height={height}>
      <ReScatter margin={{ top: 8, right: 16, left: 8, bottom: 16 }}>
        <CartesianGrid stroke={BORDER} strokeDasharray="2 3" />
        <XAxis
          type="number"
          dataKey="x"
          scale="log"
          domain={[lo, hi]}
          tick={tick}
          tickFormatter={moneyTick}
          stroke="var(--border-strong)"
          label={{ value: "Mean forecast (left edge = forecast 0)", position: "insideBottom", offset: -2, fill: MUTED, fontSize: 10 }}
        />
        <YAxis
          type="number"
          dataKey="y"
          scale="log"
          domain={[lo, hi]}
          tick={tick}
          tickFormatter={moneyTick}
          width={52}
          stroke="var(--border-strong)"
          label={{ value: "Mean actual", angle: -90, position: "insideLeft", fill: MUTED, fontSize: 10 }}
        />
        <Tooltip
          contentStyle={tipStyle}
          formatter={(v, name) => [fmtMoney(Number(v)), String(name)]}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <ReferenceLine
          segment={[
            { x: lo, y: lo },
            { x: hi, y: hi },
          ]}
          stroke={BORDER}
          strokeDasharray="4 4"
        />
        <Scatter name="A" data={toRows(a)} fill={GOLD} line={{ stroke: GOLD }} isAnimationActive={false} />
        <Scatter name="B" data={toRows(b)} fill={IRIS} line={{ stroke: IRIS }} isAnimationActive={false} />
      </ReScatter>
    </Frame>
  );
}

export function SideLegend({ a, b }: { a: string; b: string }) {
  return (
    <div className="flex flex-wrap items-center gap-4 px-1 pb-2 text-[12px] text-muted-foreground">
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block size-2 rounded-full bg-gold" />
        A · {a}
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block size-2 rounded-full bg-iris" />
        B · {b}
      </span>
    </div>
  );
}
