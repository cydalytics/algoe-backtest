"use client";

/**
 * What a real-data run is made of, and whether the optimiser beat the board.
 *
 * The workbench answers "which model is better on these rows". This view
 * answers the two questions that come before that and cannot be asked of a
 * simulated run:
 *
 *   1. What is actually in the window? Days, matches, money, and how much of it
 *      settled. A backtest over a month with three fixtures in it is not wrong,
 *      but you need to know that is what you are reading.
 *   2. If we had priced it ourselves, would we have made more? That is the
 *      optimiser, and it only means something once the pricer is shown to
 *      reproduce HKJC's own board from HKJC's own parameters. Replication
 *      first, lift second — a lift on top of a pricer that cannot reproduce the
 *      board is a bug wearing a profit.
 *
 * The E[GM] comparison is deliberately three-way, because "we beat them" splits
 * into two very different claims:
 *
 *   board odds      what HKJC actually showed
 *   our pricer, their theta   same parameters, our book. Difference here is
 *                             margin and rounding, not insight.
 *   our pricer, our theta     the only column that is a parameter edge.
 */

import { BarChart, HistChart, ScatterChart } from "@/components/backtest/charts";
import { Metric, Panel } from "@/components/desk/primitives";
import { fmtMoney, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  BacktestReport,
  OptAgg,
  OptHist,
  OptThetaDomain,
  ParquetCoverage,
  ParquetVerdict,
} from "@/types/api";

const ACCURACY_TOPICS = new Set([
  "next 5min = last 5min",
  "market-wide money per bucket",
  "true prob = 1 / true odds",
  "gross margin",
]);

export function ParquetOptimize({
  report,
  onOpenRun,
  pending = false,
}: {
  report: BacktestReport;
  onOpenRun: () => void;
  pending?: boolean;
}) {
  return <ParquetReport report={report} onOpenRun={onOpenRun} pending={pending} />;
}

export function ParquetReport({
  report,
  onOpenRun,
  pending = false,
}: {
  report: BacktestReport;
  onOpenRun: () => void;
  pending?: boolean;
}) {
  const cov = report.coverage;
  const win = report.window;
  const opt = report.optimizer ?? null;
  const solverVerdicts = (report.verdict ?? []).filter((v) => !ACCURACY_TOPICS.has(v.topic));

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="grid gap-3 p-3 lg:grid-cols-2">
        {solverVerdicts.length > 0 && (
          <Panel
            title="What the solver says"
            hint="read the replication line first"
            className="lg:col-span-2"
          >
            <div className="divide-y divide-border">
              {solverVerdicts.map((v) => (
                <VerdictRow key={v.topic} v={v} />
              ))}
            </div>
          </Panel>
        )}

        {cov && <CoveragePanel cov={cov} win={win} onOpenRun={onOpenRun} />}

        {opt ? (
          <>
            <Panel
              title="Where the money would have come from"
              hint="same forecast demand, three books"
            >
              <ThreeWay a={opt.totals.all} />
            </Panel>

            <Panel title="E[GM] by scope" hint="prematch and in-play behave differently">
              <ScopeTable totals={opt.totals} />
            </Panel>

            <Panel
              title="Pricer replication"
              hint={`our odds ÷ HKJC's, on their theta · n=${opt.prices.replication.n.toLocaleString()}`}
            >
              <div className="grid grid-cols-3">
                <Metric
                  label="MAE"
                  value={`${fmtNum(opt.prices.replication.mae_rel * 100, 2)}%`}
                  tone={opt.prices.replication.mae_rel < 0.05 ? "positive" : "negative"}
                />
                <Metric
                  label="Bias"
                  value={`${fmtNum(opt.prices.replication.mean_rel * 100, 2)}%`}
                  hint="positive means we quote longer than HKJC"
                />
                <Metric
                  label="Median"
                  value={`${fmtNum(opt.prices.replication.median_rel * 100, 2)}%`}
                />
              </div>
              <Hist hist={opt.prices.replication.hist} scale={100} />
            </Panel>

            {opt.prices.replication.by_pool && (
              <Panel title="Replication by pool" hint="where the pricer disagrees">
                <table className="desk-grid" data-density="compact">
                  <thead>
                    <tr>
                      <th>Pool</th>
                      <th className="text-right">N</th>
                      <th className="text-right">MAE</th>
                      <th className="text-right">Bias</th>
                    </tr>
                  </thead>
                  <tbody>
                    {opt.prices.replication.by_pool.map((p) => (
                      <tr key={p.pool_name}>
                        <td>{p.pool_name}</td>
                        <td className="text-right desk-value">{p.n.toLocaleString()}</td>
                        <td
                          className={cn(
                            "text-right desk-value",
                            p.mae_rel > 0.05 && "text-negative",
                          )}
                        >
                          {fmtNum(p.mae_rel * 100, 2)}%
                        </td>
                        <td className="text-right desk-value">{fmtNum(p.mean_rel * 100, 2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>
            )}

            <Panel title="Where we moved theta" hint="ours minus HKJC's, per domain">
              <ThetaTable theta={opt.theta} />
            </Panel>

            {opt.theta.goal?.d_tg_hist && (
              <Panel title="Goal TG shift" hint="goals, ours minus theirs">
                <Hist hist={opt.theta.goal.d_tg_hist} />
              </Panel>
            )}

            {opt.daily.length > 1 && (
              <Panel title="Lift by day" hint="a single good day is not an edge">
                <BarChart
                  data={opt.daily.map((d) => ({ key: d.day.slice(5), value: d.lift_vs_actual }))}
                  signed
                  money
                />
              </Panel>
            )}

            {(opt.scatter?.length ?? 0) > 0 && (
              <Panel
                title="Board E[GM] vs ours"
                hint="one point per solved bucket, above the line is a win"
              >
                <ScatterChart
                  data={opt.scatter!}
                  xLabel="Board E[GM]"
                  yLabel="Optimised E[GM]"
                />
              </Panel>
            )}

            {opt.by_slice.length > 0 && (
              <Panel
                title="Where the lift lives"
                hint="prematch vs in-play, clock, TG, |SUP|, and bet type. A lift that dies in the book you would turn on is not a result."
                className="lg:col-span-2"
              >
                <SliceTable rows={opt.by_slice} />
              </Panel>
            )}

            <Panel title="Solver" hint="sampling and health">
              <div className="grid grid-cols-3">
                <Metric label="Buckets solved" value={opt.coverage.buckets_scored.toLocaleString()} />
                <Metric
                  label="Seen"
                  value={opt.coverage.buckets_seen.toLocaleString()}
                  sub={opt.coverage.sampling}
                />
                <Metric label="Matches" value={opt.coverage.matches.toLocaleString()} />
                <Metric label="Demand" value={opt.coverage.demand} hint="turnover model fed to E[GM]" />
                <Metric label="Handicap sign" value={opt.coverage.hdc_sign} />
                <Metric
                  label="SLSQP"
                  value={opt.coverage.scipy ? "available" : "grid fallback"}
                  tone={opt.coverage.scipy ? "default" : "warn"}
                />
              </div>
            </Panel>
          </>
        ) : (
          <Panel title="Optimize TG/SUP" hint="sampled · does not change the two scores above" className="lg:col-span-2">
            <div className="space-y-2 px-3 py-3 text-[12px] leading-relaxed text-muted-foreground">
              {report.optimizer_live?.solving || pending ? (
                <p>
                  Solving {report.optimizer_live?.done ?? 0}/{report.optimizer_live?.total ?? 0}
                  {report.optimizer_live?.day ? ` · ${report.optimizer_live.day}` : ""}.
                  Charts appear after the first day lands.
                </p>
              ) : (
                <>
                  <p>
                    Build first, then Solve. Turnover WAPE and money ECE are already on every cached
                    row. This step samples buckets and solves TG/SUP. It is the business-value
                    question, and it does not change the two accuracy scores above.
                  </p>
                  <button type="button" className="desk-btn" onClick={onOpenRun}>
                    Open Run and solve
                  </button>
                </>
              )}
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- pieces */

export function VerdictRow({ v }: { v: ParquetVerdict }) {
  const tone =
    v.verdict === "better" ||
    v.verdict === "close" ||
    v.verdict === "pass" ||
    v.verdict === "usable" ||
    v.verdict === "good" ||
    v.verdict === "calibrated" ||
    v.verdict === "consistent"
      ? "text-positive"
      : v.verdict === "worse" ||
          v.verdict === "poor" ||
          v.verdict === "fail" ||
          v.verdict === "mis-calibrated" ||
          v.verdict === "weak" ||
          v.verdict === "diverges"
        ? "text-negative"
        : "text-gold";
  return (
    <div className="px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className={cn("font-mono text-[10px] uppercase", tone)}>{v.verdict}</span>
        <span className="text-[12px] text-foreground">{v.topic}</span>
      </div>
      <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{v.detail}</p>
    </div>
  );
}

export function CoveragePanel({
  cov,
  win,
  onOpenRun,
}: {
  cov: ParquetCoverage;
  win?: BacktestReport["window"];
  onOpenRun: () => void;
}) {
  const empty = win?.days_empty ?? [];
  const trimmed = win?.days_trimmed ?? [];
  return (
    <Panel
      title="What is in this window"
      hint={`cache space ${cov.space}`}
      right={
        <button type="button" className="desk-btn" onClick={onOpenRun}>
          Extend
        </button>
      }
    >
      <div className="space-y-3 px-3 py-3">
        <div className="flex flex-wrap gap-[2px]">
          {cov.days.map((d) => (
            <div
              key={d.day}
              title={
                d.state === "cached"
                  ? `${d.day} · ${d.rows.toLocaleString()} rows · ${d.matches} matches · ${fmtMoney(d.turnover)}`
                  : `${d.day} · not built`
              }
              className={cn(
                "h-4 w-2.5 border",
                d.state === "missing" && "border-border",
                d.state === "cached" && d.rows === 0 && "border-border bg-muted/40",
                d.state === "cached" && d.rows > 0 && "border-gold/50 bg-gold/35",
                d.optimized && "border-iris/70",
              )}
            />
          ))}
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11px] text-muted-foreground">
          <Row label="Built" value={`${cov.n_cached} of ${cov.n_days} days`} />
          <Row label="Solved" value={`${cov.n_optimized} days`} />
          <Row label="Panel rows" value={cov.rows.toLocaleString()} />
          <Row label="Memory" value={win?.memory_mb ? `${win.memory_mb} MB` : "—"} />
        </div>
        {empty.length > 0 && (
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {empty.length} day{empty.length === 1 ? "" : "s"} in this window carried no money and
            were skipped: {empty.slice(0, 6).join(", ")}
            {empty.length > 6 && ` +${empty.length - 6}`}. They are cached as empty, so they will
            not be rebuilt.
          </p>
        )}
        {trimmed.length > 0 && (
          <p className="text-[11px] leading-relaxed text-warn">
            The window was too large to hold in memory, so the {trimmed.length} oldest day
            {trimmed.length === 1 ? "" : "s"} were dropped from the fact table (
            {trimmed[0]} → {trimmed[trimmed.length - 1]}). They are still cached; narrow the
            window to score them.
          </p>
        )}
      </div>
    </Panel>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span>{label}</span>
      <span className="desk-value text-foreground">{value}</span>
    </div>
  );
}

/**
 * The three books side by side. Margin is the honest unit here: E[GM] scales
 * with whatever demand the forecast happened to predict, margin does not.
 */
function ThreeWay({ a }: { a: OptAgg }) {
  if (!a) return null;
  const rows: { key: string; label: string; gm: number; margin: number | null; note: string }[] = [
    {
      key: "board",
      label: "HKJC's board",
      gm: a.egm_actual_odds,
      margin: a.margin_actual_odds,
      note: "the odds that were actually shown",
    },
    {
      key: "theirs",
      label: "Our pricer, their theta",
      gm: a.egm_hkjc_theta,
      margin: a.margin_hkjc_theta,
      note: "same parameters — this gap is margin, not insight",
    },
    {
      key: "ours",
      label: "Our pricer, our theta",
      gm: a.egm_opt,
      margin: a.margin_opt,
      note: "the only column that is a parameter edge",
    },
  ];
  return (
    <>
      <table className="desk-grid" data-density="compact">
        <thead>
          <tr>
            <th>Book</th>
            <th className="text-right">E[GM]</th>
            <th className="text-right">Margin</th>
            <th className="text-right">vs board</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const d = r.gm - a.egm_actual_odds;
            return (
              <tr key={r.key}>
                <td>
                  {r.label}
                  <div className="text-[10px] text-muted-foreground">{r.note}</div>
                </td>
                <td className="text-right desk-value">{fmtMoney(r.gm)}</td>
                <td className="text-right desk-value">
                  {r.margin == null ? "—" : `${fmtNum(r.margin * 100, 2)}%`}
                </td>
                <td
                  className={cn(
                    "text-right desk-value",
                    r.key !== "board" && d > 0 && "text-positive",
                    r.key !== "board" && d < 0 && "text-negative",
                  )}
                >
                  {r.key === "board" ? "—" : fmtMoney(d)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="grid grid-cols-3 border-t border-border">
        <Metric
          label="From the pricer"
          value={fmtMoney(a.lift_from_pricer)}
          sub="margin and rounding"
        />
        <Metric
          label="From theta"
          value={fmtMoney(a.lift_from_theta)}
          sub="the genuine edge"
          tone={a.lift_from_theta >= 0 ? "positive" : "negative"}
        />
        <Metric
          label="Buckets + / −"
          value={`${a.buckets_better} / ${a.buckets_worse}`}
          sub={`of ${a.buckets.toLocaleString()}`}
        />
      </div>
    </>
  );
}

function ScopeTable({ totals }: { totals: Record<string, OptAgg> }) {
  const keys = ["all", "prematch", "inplay"].filter((k) => totals[k]?.buckets);
  return (
    <table className="desk-grid" data-density="compact">
      <thead>
        <tr>
          <th>Scope</th>
          <th className="text-right">Buckets</th>
          <th className="text-right">Margin ours</th>
          <th className="text-right">Margin board</th>
          <th className="text-right">Lift</th>
          <th className="text-right">+ / −</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => {
          const a = totals[k];
          return (
            <tr key={k}>
              <td>{k}</td>
              <td className="text-right desk-value">{a.buckets.toLocaleString()}</td>
              <td className="text-right desk-value">
                {a.margin_opt == null ? "—" : `${fmtNum(a.margin_opt * 100, 2)}%`}
              </td>
              <td className="text-right desk-value">
                {a.margin_actual_odds == null ? "—" : `${fmtNum(a.margin_actual_odds * 100, 2)}%`}
              </td>
              <td
                className={cn(
                  "text-right desk-value",
                  a.lift_vs_actual > 0 && "text-positive",
                  a.lift_vs_actual < 0 && "text-negative",
                )}
              >
                {fmtMoney(a.lift_vs_actual)}
              </td>
              <td className="text-right desk-value">
                {a.buckets_better} / {a.buckets_worse}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ThetaTable({ theta }: { theta: Record<string, OptThetaDomain> }) {
  const keys = Object.keys(theta).filter((k) => (theta[k]?.n ?? 0) > 0);
  if (!keys.length) {
    return <div className="px-3 py-4 text-[11px] text-muted-foreground">No solved theta.</div>;
  }
  const num = (v: number | null | undefined) => (v == null ? "—" : fmtNum(v, 3));
  return (
    <table className="desk-grid" data-density="compact">
      <thead>
        <tr>
          <th>Domain</th>
          <th className="text-right">N</th>
          <th className="text-right">TG theirs</th>
          <th className="text-right">TG ours</th>
          <th className="text-right">ΔTG</th>
          <th className="text-right">|ΔTG|</th>
          <th className="text-right">ΔSUP</th>
          <th className="text-right">|ΔSUP|</th>
        </tr>
      </thead>
      <tbody>
        {keys.map((k) => {
          const d = theta[k];
          return (
            <tr key={k}>
              <td>{k}</td>
              <td className="text-right desk-value">{d.n.toLocaleString()}</td>
              <td className="text-right desk-value">{num(d.tg_hkjc_mean)}</td>
              <td className="text-right desk-value">{num(d.tg_opt_mean)}</td>
              <td className="text-right desk-value">{num(d.d_tg_mean)}</td>
              <td className="text-right desk-value">{num(d.d_tg_mae)}</td>
              <td className="text-right desk-value">{num(d.d_sup_mean)}</td>
              <td className="text-right desk-value">{num(d.d_sup_mae)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function SliceTable({ rows }: { rows: (OptAgg & { dim: string; value: string })[] }) {
  const dims = [...new Set(rows.map((r) => r.dim))];
  return (
    <div className="max-h-80 overflow-auto">
      <table className="desk-grid" data-density="compact">
        <thead>
          <tr>
            <th>Cut</th>
            <th className="text-right">Buckets</th>
            <th className="text-right">Margin ours</th>
            <th className="text-right">Margin board</th>
            <th className="text-right">Lift</th>
          </tr>
        </thead>
        <tbody>
          {dims.flatMap((dim) =>
            rows
              .filter((r) => r.dim === dim)
              .map((r) => (
                <tr key={`${r.dim}-${r.value}`}>
                  <td>
                    <span className="text-muted-foreground">{r.dim}</span> {r.value}
                  </td>
                  <td className="text-right desk-value">{r.buckets.toLocaleString()}</td>
                  <td className="text-right desk-value">
                    {r.margin_opt == null ? "—" : `${fmtNum(r.margin_opt * 100, 2)}%`}
                  </td>
                  <td className="text-right desk-value">
                    {r.margin_actual_odds == null
                      ? "—"
                      : `${fmtNum(r.margin_actual_odds * 100, 2)}%`}
                  </td>
                  <td
                    className={cn(
                      "text-right desk-value",
                      r.lift_vs_actual > 0 && "text-positive",
                      r.lift_vs_actual < 0 && "text-negative",
                    )}
                  >
                    {fmtMoney(r.lift_vs_actual)}
                  </td>
                </tr>
              )),
          )}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Engine histograms are edges+counts; the chart wants lo/hi/n bins.
 *
 * ``scale`` exists because the two things plotted here are in different units:
 * a relative price error reads as a percentage, a theta shift reads in goals.
 */
function Hist({ hist, scale = 1 }: { hist?: OptHist; scale?: number }) {
  if (!hist?.counts?.length) return null;
  const bins = hist.counts.map((n, i) => ({
    lo: hist.edges[i] * scale,
    hi: hist.edges[i + 1] * scale,
    n,
  }));
  return <HistChart data={bins} height={180} />;
}
