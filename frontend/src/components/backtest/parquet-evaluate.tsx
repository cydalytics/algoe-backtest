"use client";

/**
 * The two accuracy questions, scored on every cached row as soon as Build
 * finishes. Optimize is a later, sampled step and lives in parquet-report.tsx.
 */

import {
  BarChart,
  HistChart,
  LineChart,
  ReliabilityChart,
  ScatterChart,
} from "@/components/backtest/charts";
import { CoveragePanel, VerdictRow } from "@/components/backtest/parquet-report";
import { EmptyState, Metric, Panel } from "@/components/desk/primitives";
import { fmtMoney, fmtNum } from "@/lib/format";
import type { BacktestReport, ParquetVerdict } from "@/types/api";

const TURNOVER_TOPICS = new Set([
  "next 5min = last 5min",
  "market-wide money per bucket",
  "turnover is not one number",
]);
const BELIEF_TOPICS = new Set([
  "true prob = 1 / true odds",
  "gross margin",
  "belief is not one number",
]);

const CUTS = new Set([
  "book",
  "phase",
  "domain",
  "kind",
  "side",
  "line_role",
  "matchup",
  "clock_bin",
  "event_window",
  "odds_bin",
  "quote_age",
  "belief_source",
]);
const CUT_LABEL: Record<string, string> = {
  book: "when × contract",
  phase: "phase",
  domain: "domain",
  kind: "contract",
  side: "side",
  line_role: "line",
  matchup: "TG × SUP",
  clock_bin: "clock",
  event_window: "after event",
  odds_bin: "price",
  quote_age: "quote age",
  belief_source: "p source",
};

function pct(v: number | null | undefined, digits = 2) {
  return v == null ? "—" : `${fmtNum(v * 100, digits)}%`;
}

function histBins(hist?: { edges: number[]; counts: number[] }) {
  if (!hist?.counts?.length) return [];
  return hist.counts.map((n, i) => ({ lo: hist.edges[i], hi: hist.edges[i + 1], n }));
}

export function ParquetKpis({
  report,
  pending = false,
}: {
  report: BacktestReport;
  pending?: boolean;
}) {
  const h = report.headline;
  const live = report.optimizer_live;
  const solving = Boolean(live?.solving || pending);
  return (
    <div className="shrink-0">
      {solving && (
        <div className="border-b border-border bg-iris/10 px-4 py-1.5 text-[11px] text-foreground">
          Solving TG/SUP {live?.done ?? 0}/{live?.total ?? 0}
          {live?.day ? ` · ${live.day}` : ""}
          {h.opt_lift_pct != null ? ` · lift so far ${pct(h.opt_lift_pct)}` : ""}
          . Dashboard refreshes after each day.
        </div>
      )}
      <div className="grid grid-cols-3 border-b border-border">
        <Metric
          label="Persist WAPE"
          value={pct(h.persist_wape)}
          sub="next 5 min = last 5 min · every row"
          tone={(h.persist_wape ?? 9) < 0.6 ? "positive" : "warn"}
        />
        <Metric
          label="Money ECE"
          value={pct(h.money_ece)}
          sub="true prob = 1 / true odds · every settled row"
          tone={(h.money_ece ?? 9) < 0.03 ? "positive" : "warn"}
        />
        <Metric
          label="E[GM] lift"
          value={h.opt_lift_pct == null ? "—" : pct(h.opt_lift_pct)}
          sub={
            solving
              ? "updates after each solved day"
              : h.opt_lift_pct == null
                ? "Solve when you want business value"
                : "optimised TG/SUP vs board · sampled"
          }
          tone={
            h.opt_lift_pct == null ? "default" : h.opt_lift_pct >= 0 ? "positive" : "negative"
          }
        />
      </div>
    </div>
  );
}

export function ParquetTurnover({
  report,
  onOpenRun,
}: {
  report: BacktestReport;
  onOpenRun: () => void;
}) {
  const acc = report.accuracy;
  if (!acc) {
    return (
      <NeedRescore
        title="Turnover has not been scored yet"
        body="This stored run predates the split. Open Run and click Open cached — it re-scores every row and does not rebuild parquet."
        onOpenRun={onOpenRun}
      />
    );
  }
  const tm = acc.turnover.models;
  const persist = tm["active|persist"];
  const zero = tm["active|zero"];
  const ema = tm["active|ema"];
  const model = report.selection?.turnover ?? "persist";
  const byClock = acc.turnover.by_slice.filter((r) => r.dim === "clock_bin" && r.model === model);
  const byPool = acc.turnover.by_slice.filter((r) => r.dim === "pool_name" && r.model === model);
  const scatter = acc.turnover.scatter
    .filter((r) => (r.f_persist ?? 0) > 0 && (r.turnover ?? 0) > 0)
    .map((r) => ({ x: r.f_persist as number, y: r.turnover as number }));
  const models = ["persist", "ema", "ma3", "zero"] as const;
  const subsets = ["all", "active", "money"] as const;

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="grid gap-3 p-3 lg:grid-cols-2">
        <VerdictList
          rows={(report.verdict ?? []).filter((v) => TURNOVER_TOPICS.has(v.topic))}
          fallback={acc.verdict.filter((v) => TURNOVER_TOPICS.has(v.topic))}
          hint="accuracy on every active row — not a sample"
        />
        <Panel title="Scoreboard" hint="zero is the do-nothing benchmark">
          <div className="grid grid-cols-3">
            <Metric label="Persist WAPE" value={pct(persist?.wape)} sub={`${persist?.n.toLocaleString() ?? 0} active buckets`} />
            <Metric label="Forecast-zero" value={pct(zero?.wape)} sub="must lose to persist" />
            <Metric label="EMA(3) WAPE" value={pct(ema?.wape)} />
            <Metric label="Persist MAE" value={persist?.mae == null ? "—" : fmtMoney(persist.mae)} />
            <Metric label="Bias" value={pct(persist?.bias_pct)} />
            <Metric
              label="Market-wide WAPE"
              value={pct(acc.turnover.bucket_level.persist?.wape)}
              sub="money summed per 5-min bucket"
            />
          </div>
        </Panel>
        <Panel title="Daily turnover" hint="actual vs last-5-min forecast">
          <LineChart
            data={acc.daily.map((d) => ({ key: d.day.slice(5), value: d.turnover }))}
          />
        </Panel>
        <Panel title="Daily persistence forecast" hint="should sit on the actual line">
          <LineChart
            data={acc.daily.map((d) => ({ key: d.day.slice(5), value: d.forecast_persist }))}
          />
        </Panel>
        <Panel title="Forecast vs actual" hint="one point per sampled active bucket">
          <ScatterChart data={scatter} xLabel="Last 5 min (HKD)" yLabel="Next 5 min (HKD)" />
        </Panel>
        <Panel title="Forecast error" hint="persist minus actual">
          <HistChart data={histBins(acc.turnover.resid_hist.persist)} />
        </Panel>
        <Panel title="WAPE by clock" hint="where last-5-min breaks">
          <BarChart
            data={byClock.map((r) => ({ key: r.value, value: (r.wape ?? 0) * 100 }))}
            format={(v) => `${fmtNum(v, 1)}%`}
          />
        </Panel>
        <Panel title="WAPE by pool" hint="pools that need a real model first">
          <BarChart
            data={byPool.map((r) => ({ key: r.value, value: (r.wape ?? 0) * 100 }))}
            format={(v) => `${fmtNum(v, 1)}%`}
          />
        </Panel>
        <CutTable
          title="Book the cut, not the average"
          hint="Phase, goal vs corner, totals vs handicap, side, line vs theta, TG×SUP, clock, post-goal, quote age. Open cached to refresh."
          rows={acc.turnover.by_slice
            .filter((r) => r.model === model && CUTS.has(r.dim))
            .map((r) => ({
              dim: CUT_LABEL[r.dim] ?? r.dim,
              value: r.value,
              n: r.n,
              money: r.sum_actual,
              metric: r.wape,
            }))}
          metricLabel="WAPE"
        />
        <Panel title="Model × subset" hint="all / active / money" className="lg:col-span-2">
          <table className="desk-grid" data-density="compact">
            <thead>
              <tr>
                <th>Model</th>
                <th>Subset</th>
                <th className="text-right">Buckets</th>
                <th className="text-right">Actual</th>
                <th className="text-right">MAE</th>
                <th className="text-right">WAPE</th>
                <th className="text-right">Bias</th>
              </tr>
            </thead>
            <tbody>
              {models.flatMap((model) =>
                subsets.map((subset) => {
                  const m = tm[`${subset}|${model}`];
                  if (!m) return null;
                  return (
                    <tr key={`${model}-${subset}`}>
                      <td>{model}</td>
                      <td>{subset}</td>
                      <td className="text-right desk-value">{m.n.toLocaleString()}</td>
                      <td className="text-right desk-value">{fmtMoney(m.sum_actual)}</td>
                      <td className="text-right desk-value">{m.mae == null ? "—" : fmtMoney(m.mae)}</td>
                      <td className="text-right desk-value">{pct(m.wape)}</td>
                      <td className="text-right desk-value">{pct(m.bias_pct)}</td>
                    </tr>
                  );
                }),
              )}
            </tbody>
          </table>
        </Panel>
        {report.coverage && (
          <CoveragePanel cov={report.coverage} win={report.window} onOpenRun={onOpenRun} />
        )}
      </div>
    </div>
  );
}

export function ParquetBelief({
  report,
  onOpenRun,
}: {
  report: BacktestReport;
  onOpenRun: () => void;
}) {
  const acc = report.accuracy;
  if (!acc) {
    return (
      <NeedRescore
        title="Belief has not been scored yet"
        body="This stored run predates the split. Open Run and click Open cached — it re-scores every settled row and does not rebuild parquet."
        onOpenRun={onOpenRun}
      />
    );
  }
  const beliefId = report.selection?.belief ?? "p_true";
  const cal = acc.belief.calibration[beliefId] ?? acc.belief.calibration.p_true;
  const moneyBins = (cal?.bins ?? []).map((b) => ({
    lo: b.lo,
    hi: b.hi,
    n: b.n,
    p_hat: b.p_mean,
    p_obs: b.y_mean_money,
  }));
  const countBins = (cal?.bins ?? []).map((b) => ({
    lo: b.lo,
    hi: b.hi,
    n: b.n,
    p_hat: b.p_mean,
    p_obs: b.y_mean,
  }));
  const gmPool = acc.gm.by_slice.filter((r) => r.dim === "pool_name");
  const gmClock = acc.gm.by_slice.filter((r) => r.dim === "clock_bin");
  const nSel = acc.coverage.settled_selections ?? 0;

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="grid gap-3 p-3 lg:grid-cols-2">
        <VerdictList
          rows={(report.verdict ?? []).filter((v) => BELIEF_TOPICS.has(v.topic))}
          fallback={acc.verdict.filter((v) => BELIEF_TOPICS.has(v.topic))}
          hint={`${nSel.toLocaleString()} independent settled selections`}
        />
        <Panel title="Scoreboard" hint="p_true vs public odds">
          <div className="grid grid-cols-3">
            <Metric label="Money ECE" value={pct(cal?.money_ece)} tone={(cal?.money_ece ?? 9) < 0.03 ? "positive" : "warn"} />
            <Metric label="Count ECE" value={pct(cal?.ece)} />
            <Metric label="Money log-loss" value={acc.belief.models.p_true?.money_log_loss == null ? "—" : fmtNum(acc.belief.models.p_true.money_log_loss, 4)} />
            <Metric label="Realised margin" value={pct(acc.gm.overall.realized_margin)} />
            <Metric label="Expected margin" value={pct(acc.gm.overall.expected_margin)} sub="under 1 / true odds" />
            <Metric label="Public log-loss" value={acc.belief.models.p_sell?.money_log_loss == null ? "—" : fmtNum(acc.belief.models.p_sell.money_log_loss, 4)} />
          </div>
        </Panel>
        <Panel title="Calibration, money-weighted" hint="on the dashed line means honest">
          <ReliabilityChart bins={moneyBins} />
        </Panel>
        <Panel title="Calibration, unweighted" hint="thin markets get equal say">
          <ReliabilityChart bins={countBins} />
        </Panel>
        <Panel title="True-odds book sum" hint="coherent true odds sum to 1.00">
          <HistChart data={histBins(acc.belief.book_sum_hist)} decimals={2} />
        </Panel>
        <Panel title="Daily gross margin" hint="realised vs expected under the belief">
          <LineChart
            data={acc.daily.map((d) => ({ key: d.day.slice(5), value: d.realized_gm }))}
          />
        </Panel>
        <Panel title="Margin by pool" hint="realised share of turnover">
          <BarChart
            data={gmPool.map((r) => ({ key: r.value, value: (r.realized_margin ?? 0) * 100 }))}
            signed
            format={(v) => `${fmtNum(v, 1)}%`}
          />
        </Panel>
        <CutTable
          title="Where 1 / true odds is long or short"
          hint="signed error by the book you would actually turn on. Negative means the belief is short that slice."
          rows={(acc.belief.by_slice ?? [])
            .filter((r) => CUTS.has(r.dim))
            .map((r) => ({
              dim: CUT_LABEL[r.dim] ?? r.dim,
              value: r.value,
              n: r.n,
              money: r.weight,
              metric: r.money_signed_err,
            }))}
          metricLabel="Signed"
        />
        <Panel title="Margin by clock" hint="in-play is where a wrong belief costs">
          <BarChart
            data={gmClock.map((r) => ({ key: r.value, value: (r.realized_margin ?? 0) * 100 }))}
            signed
            format={(v) => `${fmtNum(v, 1)}%`}
          />
        </Panel>
        <Panel title="Belief models" hint="p_true = 1/true odds" className="lg:col-span-2">
          <table className="desk-grid" data-density="compact">
            <thead>
              <tr>
                <th>Belief</th>
                <th className="text-right">Rows</th>
                <th className="text-right">Log-loss</th>
                <th className="text-right">Money log-loss</th>
                <th className="text-right">ECE</th>
                <th className="text-right">Money ECE</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(acc.belief.models).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className="text-right desk-value">{v.n.toLocaleString()}</td>
                  <td className="text-right desk-value">{v.log_loss == null ? "—" : fmtNum(v.log_loss, 4)}</td>
                  <td className="text-right desk-value">
                    {v.money_log_loss == null ? "—" : fmtNum(v.money_log_loss, 4)}
                  </td>
                  <td className="text-right desk-value">{pct(acc.belief.calibration[k]?.ece)}</td>
                  <td className="text-right desk-value">{pct(acc.belief.calibration[k]?.money_ece)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        {report.coverage && (
          <CoveragePanel cov={report.coverage} win={report.window} onOpenRun={onOpenRun} />
        )}
      </div>
    </div>
  );
}

function VerdictList({
  rows,
  fallback,
  hint,
}: {
  rows: ParquetVerdict[];
  fallback: ParquetVerdict[];
  hint: string;
}) {
  const list = rows.length ? rows : fallback;
  return (
    <Panel title="What this part says" hint={hint} className="lg:col-span-2">
      {list.length ? (
        <div className="divide-y divide-border">
          {list.map((v) => (
            <VerdictRow key={v.topic} v={v} />
          ))}
        </div>
      ) : (
        <div className="px-3 py-3 text-[12px] text-muted-foreground">No verdict yet.</div>
      )}
    </Panel>
  );
}

function CutTable({
  title,
  hint,
  rows,
  metricLabel,
}: {
  title: string;
  hint: string;
  rows: { dim: string; value: string; n: number; money: number; metric: number | null }[];
  metricLabel: string;
}) {
  if (!rows.length) return null;
  const ordered = [...rows].sort((a, b) => b.money - a.money);
  return (
    <Panel title={title} hint={hint} className="lg:col-span-2">
      <div className="max-h-80 overflow-auto">
        <table className="desk-grid" data-density="compact">
          <thead>
            <tr>
              <th>Cut</th>
              <th className="text-right">Rows</th>
              <th className="text-right">Money</th>
              <th className="text-right">{metricLabel}</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((r) => (
              <tr key={`${r.dim}-${r.value}`}>
                <td>
                  <span className="text-muted-foreground">{r.dim}</span> {r.value}
                </td>
                <td className="text-right desk-value">{r.n.toLocaleString()}</td>
                <td className="text-right desk-value">{fmtMoney(r.money)}</td>
                <td className="text-right desk-value">{pct(r.metric)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function NeedRescore({
  title,
  body,
  onOpenRun,
}: {
  title: string;
  body: string;
  onOpenRun: () => void;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-auto p-3">
      <EmptyState title={title} body={body} />
      <div className="px-3">
        <button type="button" className="desk-btn" onClick={onOpenRun}>
          Open Run
        </button>
      </div>
    </div>
  );
}

