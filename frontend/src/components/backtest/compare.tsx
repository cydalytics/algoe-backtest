"use client";

import { useEffect, useMemo, useState } from "react";

import {
  BarChart,
  DualLine,
  DualReliability,
  GroupedBar,
  LevelReliability,
  OverlayHist,
  SideLegend,
} from "@/components/backtest/charts";
import {
  FilterBar,
  StackFields,
  buildFacets,
  shortBelief,
  shortTurnover,
  stackLabel,
  unique,
} from "@/components/backtest/controls";
import { EmptyState, Metric, Panel } from "@/components/desk/primitives";
import { api, ApiError } from "@/lib/api";
import { fmtMoney, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  BacktestCompare,
  BacktestReport,
  CompareDeltaRow,
  CompareRequest,
  StackPick,
} from "@/types/api";

function parseBelief(token?: string | null): { source: string; cal: string } {
  if (!token) return { source: "hkjc_true", cal: "raw" };
  if (token.includes("/")) {
    const [source, cal] = token.split("/", 2);
    return { source, cal };
  }
  return { source: token, cal: "raw" };
}

export function CompareBench({ report }: { report: BacktestReport }) {
  const wb = report.workbench;
  const baselines = wb?.baselines ?? {
    turnover: "persistence",
    true_prob: "hkjc_true",
    calibrator: "raw",
    algo: "hold",
  };
  // B opens on the run's own winner where there is one, otherwise on any model
  // this run actually carries that is not already the baseline. A labs sweep
  // names a winner; a parquet run does not, and defaulting to a name it never
  // scored would put the select on a value it cannot request.
  const [left, setLeft] = useState<StackPick>({
    turnover: baselines.turnover,
    true_prob: baselines.true_prob,
    calibrator: baselines.calibrator,
    algo: baselines.algo,
  });
  const [right, setRight] = useState<StackPick>(() => {
    const have = wb?.turnover ?? [];
    const bestT = report.headline.best_turnover;
    const other = have.find((id) => id !== baselines.turnover);
    const bestP = parseBelief(report.headline.best_true_odds);
    return {
      turnover:
        bestT && bestT !== baselines.turnover ? bestT : (other ?? bestT ?? baselines.turnover),
      true_prob: bestP.source,
      calibrator: bestP.cal,
      algo: baselines.algo,
    };
  });
  const [filters, setFilters] = useState<Record<string, string[]>>({});
  const [groupBy, setGroupBy] = useState("clock_bin");
  const [cmp, setCmp] = useState<BacktestCompare | null>(null);
  const [error, setError] = useState<string | null>(null);

  const body: CompareRequest = useMemo(
    () => ({ left, right, group_by: groupBy, filters }),
    [left, right, groupBy, filters],
  );

  // Derived, not a flag: loading is whether the pair on screen is the pair we
  // have an answer for. See the same comment in workbench.tsx.
  const key = JSON.stringify([report.run_id, body]);
  const [answered, setAnswered] = useState<string | null>(null);
  const loading = Boolean(wb?.has_facts) && answered !== key;

  useEffect(() => {
    if (!wb?.has_facts) return;
    let alive = true;
    api
      .backtestCompare(report.run_id, body)
      .then((res) => {
        if (!alive) return;
        setCmp(res.compare);
        setError(null);
      })
      .catch((err) => {
        if (!alive) return;
        setError(err instanceof ApiError ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setAnswered(key);
      });
    return () => {
      alive = false;
    };
  }, [report.run_id, wb?.has_facts, body, key]);

  if (!wb?.has_facts) {
    return (
      <EmptyState
        title="This run has no row-level facts"
        body="Open New run and run labs again so A and B can be scored on the same rows."
      />
    );
  }

  const models = cmp?.models ?? {
    turnover: wb.turnover ?? [],
    beliefs: wb.beliefs ?? [],
    algos: wb.algos ?? ["hold"],
  };
  const tNames = models.turnover.length ? models.turnover : [left.turnover, right.turnover];
  const beliefTokens = models.beliefs.length
    ? models.beliefs
    : [`${left.true_prob}/${left.calibrator}`, `${right.true_prob}/${right.calibrator}`];
  const sources = unique(beliefTokens.map((t) => t.split("/")[0]));
  const cals = unique(beliefTokens.map((t) => (t.includes("/") ? t.split("/")[1] : "raw")));
  const algos = models.algos.length ? models.algos : [left.algo];
  const facets = cmp?.facets ?? {};
  const aName = stackLabel(left);
  const bName = stackLabel(right);

  const tOpts = tNames.map((id) => ({
    id,
    label: shortTurnover(id, report.catalog.turnover[id]),
    baseline: id === baselines.turnover,
  }));
  const pOpts = sources.map((id) => ({
    id,
    label: shortBelief(id, report.catalog.true_prob[id]),
    baseline: id === baselines.true_prob,
  }));
  const cOpts = cals.map((id) => ({ id, label: id, baseline: id === baselines.calibrator }));
  const aOpts = algos.map((id) => ({ id, label: id, baseline: id === baselines.algo }));

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="shrink-0 space-y-2 border-b border-border px-4 py-2">
        <div className="grid gap-2 lg:grid-cols-2">
          <StackCard
            title="A · control"
            tone="gold"
            stack={left}
            set={setLeft}
            tOpts={tOpts}
            pOpts={pOpts}
            cOpts={cOpts}
            aOpts={aOpts}
            simple={report.mode === "parquet"}
          />
          <StackCard
            title="B · challenger"
            tone="iris"
            stack={right}
            set={setRight}
            tOpts={tOpts}
            pOpts={pOpts}
            cOpts={cOpts}
            aOpts={aOpts}
            simple={report.mode === "parquet"}
            swap={() => {
              setLeft(right);
              setRight(left);
            }}
            copyFromA={() => setRight(left)}
          />
        </div>
        <FilterBar
          filters={filters}
          onFilters={setFilters}
          groupBy={groupBy}
          onGroupBy={setGroupBy}
          facets={buildFacets(facets, wb.filters)}
        />
      </div>

      {error && (
        <div className="shrink-0 border-b border-border px-4 py-2 text-[11px] text-negative">{error}</div>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        {!cmp && loading ? (
          <div className="p-6 text-[12px] text-muted-foreground">Scoring A vs B…</div>
        ) : cmp ? (
          <div className="space-y-3 p-3">
            <CompareVerdict cmp={cmp} aName={aName} bName={bName} />
            <DailyMetrics cmp={cmp} loading={loading} />
            <SideLegend a={aName} b={bName} />
            <div className="grid gap-3 xl:grid-cols-2">
              <Panel title="Daily WAPE" hint="each day is one observation · lower is better">
                <DualLine
                  data={(cmp.daily?.days ?? []).map((d) => ({ key: d.day.slice(5), a: d.wape_a, b: d.wape_b }))}
                />
              </Panel>
              <Panel title="Daily Δ WAPE" hint="B − A in points · green days B wins">
                <BarChart
                  signed
                  lowerIsBetter
                  format={(v) => `${fmtNum(v, 1)}`}
                  data={(cmp.daily?.days ?? []).map((d) => ({ key: d.day.slice(5), value: d.diff, n: d.n }))}
                />
              </Panel>
            </div>
            <div className="grid gap-3 xl:grid-cols-2">
              <Panel title="Level reliability" hint="mean forecast vs mean actual by forecast quantile · on the diagonal is unbiased">
                <LevelReliability a={cmp.level?.a ?? []} b={cmp.level?.b ?? []} />
              </Panel>
              <Panel title={`WAPE by ${cmp.group_by.replace("_", " ")}`} hint="share of that cut's money missed · gold A · iris B">
                <GroupedBar
                  data={cmp.cuts.map((r) => ({
                    key: r.key,
                    a: r.wape_a ?? 0,
                    b: r.wape_b ?? 0,
                  }))}
                />
              </Panel>
            </div>
            <div className="grid gap-3 xl:grid-cols-2">
              <Panel title="Turnover residual" hint="forecast − actual HK$ · tighter pile wins">
                <OverlayHist data={cmp.dists.residual ?? []} />
              </Panel>
              <Panel title="The numbers" hint="row-level, Δ is B − A">
                <ScoreTable
                  rows={cmp.delta.filter((r) =>
                    ["wape_pct", "bias_pct", "mae", "rmse", "theil_u", "logloss", "ece", "exp_gm_star", "realized_gm"].includes(r.key),
                  )}
                />
              </Panel>
            </div>
            <Panel title="Belief reliability" hint="true probability · dashed line is perfect calibration">
              <DualReliability
                a={cmp.left.true_odds.calibration}
                b={cmp.right.true_odds.calibration}
              />
            </Panel>
            <Panel title={`Cut table · ${cmp.group_by}`} hint="same rows, both stacks">
              <CutDeltaTable rows={cmp.cuts} />
            </Panel>
            {cmp.note && (
              <p className="px-1 pb-2 text-[11px] leading-relaxed text-muted-foreground">{cmp.note}</p>
            )}
          </div>
        ) : (
          <EmptyState title="No comparison" body="Pick two stacks on the same slice." />
        )}
      </div>
    </div>
  );
}

function CompareVerdict({
  cmp,
  aName,
  bName,
}: {
  cmp: BacktestCompare;
  aName: string;
  bName: string;
}) {
  const ll = findDelta(cmp, "logloss");
  const money = turnoverVerdict(cmp.daily);
  const belief =
    ll == null
      ? ""
      : Math.abs(ll) < 0.0005
        ? " Belief is a tie — same true-prob quality."
        : ll < 0
          ? ` Belief also tighter on B (Δ log-loss ${fmtDelta(ll, 3)}).`
          : ` Belief is worse on B (Δ log-loss ${fmtDelta(ll, 3)}).`;
  return (
    <div className="rounded-sm border border-border bg-card px-4 py-3">
      <div className="text-[15px] font-medium tracking-tight">
        {money}
        {belief}
      </div>
      <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
        A is {aName}. B is {bName}. Same {cmp.n_rows.toLocaleString()} rows
        {cmp.n_unscored ? ` (${cmp.n_unscored.toLocaleString()} left out: no forecast on one side)` : ""}. Δ is B − A.
      </p>
    </div>
  );
}

// Days, not rows, are the unit: rows in one day share matches and money.
const MIN_DAYS = 10;

function turnoverVerdict(d?: BacktestCompare["daily"]): string {
  if (!d || !d.n_days || d.wape_a == null || d.wape_b == null) return "Turnover is not scored.";
  const gap = d.wape_b - d.wape_a;
  const head =
    Math.abs(gap) < 0.05
      ? `Turnover WAPE is a tie at ${fmtNum(d.wape_a, 1)}%.`
      : gap < 0
        ? `B misses less money — WAPE ${fmtNum(d.wape_a, 1)}% → ${fmtNum(d.wape_b, 1)}%.`
        : `A misses less money — WAPE ${fmtNum(d.wape_a, 1)}% vs B ${fmtNum(d.wape_b, 1)}%.`;
  if (d.n_days < MIN_DAYS || d.dm_stat == null) {
    return `${head} Only ${d.n_days} day(s): too few to call.`;
  }
  const strong = Math.abs(d.dm_stat) >= 2;
  const who = d.dm_stat < 0 ? "B" : "A";
  return strong
    ? `${head} ${who} is better across days (t ${fmtNum(d.dm_stat, 1)}, ${d.b_better_days}/${d.n_days} days to B).`
    : `${head} Not separable across days (t ${fmtNum(d.dm_stat, 1)}, ${d.b_better_days}/${d.n_days} days to B).`;
}

function DailyMetrics({ cmp, loading }: { cmp: BacktestCompare; loading: boolean }) {
  const d = cmp.daily;
  const bias = cmp.delta.find((r) => r.key === "bias_pct");
  const few = !d || d.n_days < MIN_DAYS;
  return (
    <div className={cn("grid grid-cols-2 border border-border sm:grid-cols-3 xl:grid-cols-6", loading && "opacity-40")}>
      <Metric
        label="Scored rows"
        value={cmp.n_rows.toLocaleString()}
        sub={cmp.n_unscored ? `${cmp.n_unscored.toLocaleString()} unscored` : "same rows for A and B"}
      />
      <Metric
        label="WAPE A → B"
        value={d?.wape_a != null && d?.wape_b != null ? `${fmtNum(d.wape_a, 1)} → ${fmtNum(d.wape_b, 1)}%` : "—"}
        sub="Σ|error| ÷ Σ actual"
        tone={d?.wape_a != null && d?.wape_b != null ? toneDelta(d.wape_b - d.wape_a, true) : "default"}
      />
      <Metric
        label="Skill vs A"
        value={d?.skill != null ? `${fmtNum(d.skill * 100, 1)}%` : "—"}
        sub="1 − WAPE_B ÷ WAPE_A"
        tone={d?.skill != null ? toneDelta(d.skill, false) : "default"}
      />
      <Metric
        label="Days B better"
        value={d ? `${d.b_better_days ?? 0} / ${d.n_days}` : "—"}
        sub={few ? `need ≥ ${MIN_DAYS} days` : "paired by day"}
      />
      <Metric
        label="Daily Δ, 95% CI"
        value={d?.ci95 ? `${fmtNum(d.ci95[0], 1)} … ${fmtNum(d.ci95[1], 1)}` : "—"}
        sub={d?.dm_stat != null ? `DM t ${fmtNum(d.dm_stat, 1)} · pts` : "pts"}
        tone={!few && d?.ci95 ? (d.ci95[1] < 0 ? "positive" : d.ci95[0] > 0 ? "negative" : "default") : "default"}
      />
      <Metric
        label="|Bias| A → B"
        value={bias ? `${fmtNum(bias.a, 1)} → ${fmtNum(bias.b, 1)}%` : "—"}
        sub="Σ forecast vs Σ actual"
        tone={bias ? toneDelta(bias.delta, true) : "default"}
      />
    </div>
  );
}

function StackCard({
  title,
  tone,
  stack,
  set,
  tOpts,
  pOpts,
  cOpts,
  aOpts,
  swap,
  copyFromA,
  simple = false,
}: {
  title: string;
  tone: "gold" | "iris";
  stack: StackPick;
  set: (next: StackPick) => void;
  tOpts: { id: string; label: string; baseline?: boolean }[];
  pOpts: { id: string; label: string; baseline?: boolean }[];
  cOpts: { id: string; label: string; baseline?: boolean }[];
  aOpts: { id: string; label: string; baseline?: boolean }[];
  swap?: () => void;
  copyFromA?: () => void;
  simple?: boolean;
}) {
  return (
    <div className={cn(tone === "iris" && "lg:border-l lg:border-border lg:pl-4")}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className={cn("text-[12px] font-medium", tone === "gold" ? "text-gold-soft" : "text-iris")}>
          {title}
        </div>
        <div className="flex gap-1">
          {copyFromA && (
            <button type="button" className="desk-btn" onClick={copyFromA}>
              Copy A
            </button>
          )}
          {swap && (
            <button type="button" className="desk-btn" onClick={swap}>
              Swap
            </button>
          )}
        </div>
      </div>
      <StackFields
        stack={stack}
        onChange={set}
        tOpts={tOpts}
        pOpts={pOpts}
        cOpts={cOpts}
        aOpts={aOpts}
        tone={tone}
        simple={simple}
      />
    </div>
  );
}

function ScoreTable({ rows }: { rows: CompareDeltaRow[] }) {
  return (
    <table className="desk-grid" data-density="compact">
      <thead>
        <tr>
          <th>Metric</th>
          <th className="text-right">A</th>
          <th className="text-right">B</th>
          <th className="text-right">Δ</th>
          <th className="text-right">Win</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <td>
              <span className="text-muted-foreground">{r.family}</span>
              <span className="ml-2">{r.label}</span>
            </td>
            <td className="text-right desk-value text-gold-soft">{fmtCell(r, r.a)}</td>
            <td className="text-right desk-value">{fmtCell(r, r.b)}</td>
            <td
              className={cn(
                "text-right desk-value",
                r.winner === "right" && "text-positive",
                r.winner === "left" && "text-negative",
              )}
            >
              {fmtCell(r, r.delta, true)}
            </td>
            <td className="text-right text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              {r.winner === "tie" ? "—" : r.winner === "right" ? "B" : "A"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CutDeltaTable({ rows }: { rows: BacktestCompare["cuts"] }) {
  if (!rows.length) {
    return <div className="px-3 py-4 text-[11px] text-muted-foreground">No groups on this slice.</div>;
  }
  return (
    <div className="overflow-auto">
      <table className="desk-grid" data-density="compact">
        <thead>
          <tr>
            <th>Cut</th>
            <th className="text-right">N</th>
            <th className="text-right">Turnover</th>
            <th className="text-right">WAPE A</th>
            <th className="text-right">WAPE B</th>
            <th className="text-right">Δ WAPE</th>
            <th className="text-right">Bias A</th>
            <th className="text-right">Bias B</th>
            <th className="text-right">MAE A</th>
            <th className="text-right">MAE B</th>
            <th className="text-right">Δ MAE</th>
            <th className="text-right">LL A</th>
            <th className="text-right">LL B</th>
            <th className="text-right">Δ LL</th>
            <th className="text-right">E[GM] A</th>
            <th className="text-right">E[GM] B</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>{r.key}</td>
              <td className="text-right desk-value">{r.n.toLocaleString()}</td>
              <td className="text-right desk-value">{fmtMoney(r.turnover)}</td>
              <td className="text-right desk-value">{fmtPct(r.wape_a)}</td>
              <td className="text-right desk-value">{fmtPct(r.wape_b)}</td>
              <td className={cn("text-right desk-value", (r.d_wape ?? 0) < 0 ? "text-positive" : (r.d_wape ?? 0) > 0 ? "text-negative" : "")}>
                {fmtNum(r.d_wape, 1)}
              </td>
              <td className="text-right desk-value">{fmtPct(r.bias_a)}</td>
              <td className="text-right desk-value">{fmtPct(r.bias_b)}</td>
              <td className="text-right desk-value">{fmtNum(r.mae_a, 0)}</td>
              <td className="text-right desk-value">{fmtNum(r.mae_b, 0)}</td>
              <td className={cn("text-right desk-value", (r.d_mae ?? 0) < 0 ? "text-positive" : (r.d_mae ?? 0) > 0 ? "text-negative" : "")}>
                {fmtNum(r.d_mae, 0)}
              </td>
              <td className="text-right desk-value">{fmtNum(r.logloss_a, 3)}</td>
              <td className="text-right desk-value">{fmtNum(r.logloss_b, 3)}</td>
              <td className={cn("text-right desk-value", (r.d_logloss ?? 0) < 0 ? "text-positive" : (r.d_logloss ?? 0) > 0 ? "text-negative" : "")}>
                {fmtNum(r.d_logloss, 3)}
              </td>
              <td className="text-right desk-value">{fmtMoney(r.exp_gm_a)}</td>
              <td className="text-right desk-value">{fmtMoney(r.exp_gm_b)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmtPct(v: number | undefined | null) {
  return v == null || Number.isNaN(v) ? "—" : `${fmtNum(v, 1)}%`;
}

function findDelta(cmp: BacktestCompare | null, key: string) {
  return cmp?.delta.find((r) => r.key === key)?.delta;
}

function fmtDelta(v: number | undefined, dec: number) {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${fmtNum(Math.abs(v), dec)}`;
}

function toneDelta(v: number | undefined, lowerBetter: boolean): "positive" | "negative" | "default" {
  if (v == null || v === 0) return "default";
  const better = lowerBetter ? v < 0 : v > 0;
  return better ? "positive" : "negative";
}

function fmtCell(row: CompareDeltaRow, v: number, signed = false) {
  const abs = Math.abs(v);
  const sign = signed ? (v > 0 ? "+" : v < 0 ? "−" : "") : "";
  if (row.key.includes("gm") || row.key.includes("lift") || row.key === "optimism") {
    return `${signed && v !== 0 ? (v > 0 ? "+" : "−") : ""}${fmtMoney(abs)}`;
  }
  if (row.key === "mae" || row.key === "mse" || row.key === "rmse") return `${sign}${fmtNum(abs, 0)}`;
  if (row.key === "wape_pct" || row.key === "bias_pct") return `${sign}${fmtNum(abs, 1)}%`;
  return `${sign}${fmtNum(abs, 3)}`;
}
