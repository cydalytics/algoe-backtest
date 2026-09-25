"use client";

import { useCallback, useEffect, useState } from "react";

import {
  BarChart,
  DecompBar,
  Heatmap,
  HistChart,
  LineChart,
  ReliabilityChart,
  ScatterChart,
  Waterfall,
} from "@/components/backtest/charts";
import { CompareBench } from "@/components/backtest/compare";
import {
  ParquetBelief,
  ParquetKpis,
  ParquetTurnover,
} from "@/components/backtest/parquet-evaluate";
import { ParquetOptimize } from "@/components/backtest/parquet-report";
import { ParquetRunPanel } from "@/components/backtest/parquet-run";
import { SeriesBar } from "@/components/backtest/series-bar";
import { Workbench } from "@/components/backtest/workbench";
import { EmptyState, Metric, Panel, Segmented } from "@/components/desk/primitives";
import { api, API_BASE, ApiError } from "@/lib/api";
import { fmtMoney, fmtNum, fmtSigned } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  BacktestCombo,
  BacktestLab,
  BacktestReport,
  CalBin,
  CutRow,
  OptimizeMode,
  PlotPoint,
} from "@/types/api";

type LabId = "overview" | "turnover" | "belief" | "optimizer" | "policy";
type ViewId = "workbench" | "compare" | "labs" | "turnover" | "belief" | "optimize";
type SourceId = "parquet" | "labs";

const T_OPTS = [
  { id: "persistence", label: "persist" },
  { id: "trailing_mean", label: "trail" },
  { id: "gametime", label: "gametime" },
  { id: "blend", label: "blend" },
  { id: "momentum", label: "momentum" },
  { id: "match_share", label: "match mix" },
  { id: "oracle", label: "oracle" },
];
const P_OPTS = [
  { id: "hkjc_true", label: "HKJC true" },
  { id: "hkjc_offer", label: "HKJC offer" },
  { id: "market", label: "market" },
  { id: "model", label: "model" },
];
const CAL_OPTS = [
  { id: "raw", label: "raw" },
  { id: "shrink", label: "shrink" },
  { id: "temperature", label: "temp" },
  { id: "isotonic", label: "isotonic" },
  { id: "normalize", label: "norm" },
];
const ALGO_OPTS = [
  { id: "hold", label: "hold" },
  { id: "slsqp", label: "SLSQP" },
  { id: "coordinate", label: "coord" },
  { id: "grid", label: "grid" },
  { id: "tight", label: "tight" },
];
const POL_OPTS = [
  { id: "always", label: "always" },
  { id: "threshold", label: "threshold" },
  { id: "bps", label: "bps" },
];

export default function BacktestPage() {
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [view, setView] = useState<ViewId>("workbench");
  const [lab, setLab] = useState<LabId>("overview");
  const [pick, setPick] = useState<Record<string, string>>({});
  const [setupOpen, setSetupOpen] = useState(false);
  // Real parquet is the default source: the simulated bench is for when there
  // is no data to read, not the other way round.
  const [source, setSource] = useState<SourceId>("parquet");
  const [watchJob, setWatchJob] = useState<string | null>(null);

  const [matches, setMatches] = useState(3);
  const [ticks, setTicks] = useState(6);
  const [optimize, setOptimize] = useState<OptimizeMode>("none");
  const [turnover, setTurnover] = useState(["persistence", "trailing_mean", "gametime", "momentum", "oracle"]);
  const [trueProb, setTrueProb] = useState(["hkjc_true", "market", "model"]);
  const [cals, setCals] = useState(["raw", "shrink", "temperature"]);
  const [algos, setAlgos] = useState(["hold"]);
  const [policies, setPolicies] = useState(["always", "threshold"]);

  useEffect(() => {
    let alive = true;
    Promise.all([
      api.backtestLatest(),
      api.parquetJobs().catch(() => ({ jobs: [], current: null as string | null })),
    ])
      .then(([res, jobsRes]) => {
        if (!alive) return;
        setReport(res.report);
        const live = jobsRes.jobs.find((j) => j.id === jobsRes.current);
        const solving =
          live != null &&
          (live.status === "running" || live.status === "queued") &&
          live.kind === "parquet-optimize";
        if (solving && live) {
          setWatchJob(live.id);
          setView("optimize");
        } else if (res.report?.mode === "parquet") {
          setView("turnover");
        }
      })
      .catch((err) => {
        if (!alive) return;
        setError(err instanceof ApiError ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const res = await api.runBacktest({
        n_matches: matches,
        n_ticks: ticks,
        turnover,
        true_prob: trueProb,
        calibrators: cals,
        algos: optimize === "none" ? ["hold"] : optimize === "all" ? algos : ["hold", "slsqp"],
        policies,
        elasticities: [0, 1],
        optimize,
      });
      setReport(res.report);
      setLab("overview");
      setSetupOpen(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  /** Adopt a run the parquet panel just registered, and show what it built.
   *
   *  Stable by construction: the panel polls on an interval keyed to this
   *  callback, so a fresh closure each render would keep resetting the clock.
   */
  const onSeriesStarted = useCallback((jobId: string) => {
    setWatchJob(jobId);
    setView("turnover");
  }, []);
  const onSeriesError = useCallback((message: string) => {
    setError(message);
  }, []);

  const onParquetRun = useCallback(
    (info?: { runId?: string; refresh?: boolean; tab?: ViewId; jobId?: string }) => {
      if (info?.tab) setView(info.tab);
      if (info?.jobId) setWatchJob(info.jobId);
      if (info && info.refresh === false) return;
      const req = info?.runId ? api.backtestGet(info.runId) : api.backtestLatest();
      req
        .then((res) => {
          setReport(res.report);
          setError(null);
        })
        .catch((err) => setError(err instanceof ApiError ? err.message : String(err)));
    },
    [],
  );

  // The Run drawer unmounts when it closes, so the page itself watches a live
  // Solve job and reloads that run after each published day.
  useEffect(() => {
    if (!watchJob) return;
    let alive = true;
    const tick = () => {
      api
        .parquetJob(watchJob)
        .then(async ({ job }) => {
          if (!alive) return;
          const finished =
            job.status === "done" || job.status === "error" || job.status === "cancelled";
          if (job.run_id) {
            try {
              const res = await api.backtestGet(job.run_id);
              if (!alive) return;
              setReport(res.report);
              setError(null);
            } catch (err) {
              if (alive) setError(err instanceof ApiError ? err.message : String(err));
            }
          }
          if (finished) setWatchJob(null);
        })
        .catch((err) => {
          if (alive) setError(err instanceof ApiError ? err.message : String(err));
        });
    };
    tick();
    const timer = window.setInterval(tick, 1500);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [watchJob]);

  const labs = report?.labs;
  const isParquet = report?.mode === "parquet";
  // A parquet run has no labs by design; only a stored sweep that predates the
  // fact book is actually stale.
  const stale = report && !labs && !isParquet;
  const parquetEval = view === "turnover" || view === "belief" || view === "optimize";
  const activeView: ViewId = isParquet
    ? view === "labs"
      ? "turnover"
      : view
    : parquetEval
      ? "labs"
      : view;

  if (loading && !report) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center text-[12px] text-muted-foreground">
        Loading the last backtest…
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <header className="shrink-0 border-b border-border px-4 py-2">
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-[14px] font-medium tracking-tight">Backtest</div>
          {report && (
            <Segmented
              value={activeView}
              options={
                isParquet
                  ? [
                      { value: "workbench", label: "One model" },
                      { value: "compare", label: "A vs B" },
                      { value: "turnover", label: "Turnover" },
                      { value: "belief", label: "Belief" },
                      { value: "optimize", label: "Optimize" },
                    ]
                  : [
                      { value: "workbench", label: "One model" },
                      { value: "compare", label: "A vs B" },
                      { value: "labs", label: "Labs" },
                    ]
              }
              onChange={setView}
            />
          )}
          {report && activeView === "labs" && (
            <Segmented
              value={lab}
              options={[
                { value: "overview", label: "Overview" },
                { value: "turnover", label: "Turnover" },
                { value: "belief", label: "Belief" },
                { value: "optimizer", label: "Optimizer" },
                { value: "policy", label: "Policy" },
              ]}
              onChange={setLab}
            />
          )}
          <div className="ml-auto flex flex-wrap items-center gap-3">
            {report && (
              <div className="text-[11px] text-muted-foreground">
                {isParquet ? (
                  <>
                    <span className="text-gold/80">real data</span>{" "}
                    {report.window?.start} → {report.window?.end} ·{" "}
                    {report.n_rows.toLocaleString()} rows · {report.n_days ?? 0} days
                  </>
                ) : (
                  <>
                    <span className="text-muted-foreground/70">simulated</span>{" "}
                    {report.n_rows.toLocaleString()} rows · {report.n_ticks} ticks
                  </>
                )}
                <span className="ml-2 text-muted-foreground/70">{report.run_id}</span>
              </div>
            )}
            <button
              type="button"
              className="desk-btn"
              data-active={setupOpen}
              onClick={() => setSetupOpen(true)}
            >
              Run
            </button>
          </div>
        </div>
        <SeriesBar onStarted={onSeriesStarted} onError={onSeriesError} />
        {error && (
          <div className="mt-2 text-[11px] text-negative">
            {error}
            {!report && ` · API at ${API_BASE || "/api"}`}
          </div>
        )}
        {stale && (
          <div className="mt-2 text-[11px] text-warn">
            This stored run predates the fact book. Open Run to build one that can switch model.
          </div>
        )}
      </header>

      {setupOpen && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/45"
          onClick={() => setSetupOpen(false)}
        >
          <aside
            className="flex h-full w-full max-w-lg flex-col border-l border-border bg-card shadow-[0_0_40px_rgba(0,0,0,0.45)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="border-b border-border px-4 pt-3">
              <Segmented
                value={source}
                options={[
                  { value: "parquet", label: "Real data" },
                  { value: "labs", label: "Simulated" },
                ]}
                onChange={setSource}
              />
              <p className="py-2 text-[11px] leading-relaxed text-muted-foreground">
                {source === "parquet"
                  ? "Build scores turnover and belief on every row. Solve TG/SUP later if you want the sampled business-value number."
                  : "Generates a synthetic book. Use it to exercise the screen when the data drive is not mounted."}
              </p>
            </div>
            {source === "parquet" ? (
              <ParquetRunPanel
                onRegistered={onParquetRun}
                onClose={() => setSetupOpen(false)}
              />
            ) : (
              <SetupDrawer
                matches={matches}
                setMatches={setMatches}
                ticks={ticks}
                setTicks={setTicks}
                turnover={turnover}
                setTurnover={setTurnover}
                trueProb={trueProb}
                setTrueProb={setTrueProb}
                cals={cals}
                setCals={setCals}
                algos={algos}
                setAlgos={setAlgos}
                policies={policies}
                setPolicies={setPolicies}
                optimize={optimize}
                setOptimize={setOptimize}
                running={running}
                onClose={() => setSetupOpen(false)}
                onRun={() => void run()}
              />
            )}
          </aside>
        </div>
      )}

      {!report ? (
        <EmptyState
          title="No backtest yet"
          body="Open Run, point it at the parquet folders and Build. Turnover and belief numbers appear as soon as the panels exist. Optimize TG/SUP is a later, sampled step."
        />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {isParquet && <ParquetKpis report={report} pending={Boolean(watchJob)} />}
          {activeView === "labs" && <Headline report={report} />}
          {activeView === "workbench" ? (
            <Workbench key={report.run_id} report={report} />
          ) : activeView === "compare" ? (
            <CompareBench key={`${report.run_id}-cmp`} report={report} />
          ) : activeView === "turnover" ? (
            <ParquetTurnover
              key={`${report.run_id}-turn`}
              report={report}
              onOpenRun={() => setSetupOpen(true)}
            />
          ) : activeView === "belief" ? (
            <ParquetBelief
              key={`${report.run_id}-bel`}
              report={report}
              onOpenRun={() => setSetupOpen(true)}
            />
          ) : activeView === "optimize" ? (
            <ParquetOptimize
              key={`${report.run_id}-opt`}
              report={report}
              pending={Boolean(watchJob)}
              onOpenRun={() => setSetupOpen(true)}
            />
          ) : (
            <div className="min-h-0 flex-1 overflow-auto">
              {lab === "overview" && <Overview report={report} />}
              {lab === "turnover" && labs && (
                <TurnoverLab
                  lab={labs.turnover}
                  selected={pick.turnover ?? labs.turnover.leader ?? labs.turnover.candidates[0]?.id}
                  onSelect={(id) => setPick((s) => ({ ...s, turnover: id }))}
                />
              )}
              {lab === "belief" && labs && (
                <BeliefLab
                  lab={labs.true_prob}
                  selected={pick.belief ?? labs.true_prob.leader ?? labs.true_prob.candidates[0]?.id}
                  onSelect={(id) => setPick((s) => ({ ...s, belief: id }))}
                />
              )}
              {lab === "optimizer" && labs && (
                <AlgoLab
                  lab={labs.optimizer}
                  selected={pick.algo ?? labs.optimizer.leader ?? labs.optimizer.candidates[0]?.id}
                  onSelect={(id) => setPick((s) => ({ ...s, algo: id }))}
                />
              )}
              {lab === "policy" && labs && (
                <PolicyLab
                  lab={labs.policy}
                  selected={pick.policy ?? labs.policy.leader ?? labs.policy.candidates[0]?.id}
                  onSelect={(id) => setPick((s) => ({ ...s, policy: id }))}
                />
              )}
              {stale && lab !== "overview" && (
                <div className="p-4 text-[12px] text-muted-foreground">
                  Re-run to open the {lab} lab.
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SetupDrawer({
  matches,
  setMatches,
  ticks,
  setTicks,
  turnover,
  setTurnover,
  trueProb,
  setTrueProb,
  cals,
  setCals,
  algos,
  setAlgos,
  policies,
  setPolicies,
  optimize,
  setOptimize,
  running,
  onClose,
  onRun,
}: {
  matches: number;
  setMatches: (n: number) => void;
  ticks: number;
  setTicks: (n: number) => void;
  turnover: string[];
  setTurnover: (v: string[]) => void;
  trueProb: string[];
  setTrueProb: (v: string[]) => void;
  cals: string[];
  setCals: (v: string[]) => void;
  algos: string[];
  setAlgos: (v: string[]) => void;
  policies: string[];
  setPolicies: (v: string[]) => void;
  optimize: OptimizeMode;
  setOptimize: (v: OptimizeMode) => void;
  running: boolean;
  onClose: () => void;
  onRun: () => void;
}) {
  return (
    <>
      <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <div className="text-[15px] font-medium tracking-tight">Simulated</div>
          <p className="mt-1 max-w-md text-[12px] leading-relaxed text-muted-foreground">
            Builds the fact book from generated matches. After it finishes you can mix turnover
            and belief in the workbench without another sweep.
          </p>
        </div>
        <button type="button" className="desk-btn" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-auto px-4 py-4">
        <div className="flex flex-wrap gap-4">
          <Num label="Matches" value={matches} set={setMatches} min={1} max={12} />
          <Num label="Ticks" value={ticks} set={setTicks} min={2} max={24} />
        </div>
        <ChipSet label="Turnover models to score" options={T_OPTS} value={turnover} onChange={setTurnover} />
        <ChipSet label="Belief sources" options={P_OPTS} value={trueProb} onChange={setTrueProb} />
        <ChipSet label="Calibrators" options={CAL_OPTS} value={cals} onChange={setCals} />
        <ChipSet label="Algos" options={ALGO_OPTS} value={algos} onChange={setAlgos} />
        <ChipSet label="Policy" options={POL_OPTS} value={policies} onChange={setPolicies} />
        <div>
          <div className="mb-1 text-[11px] text-muted-foreground">Solve</div>
          <Segmented
            value={optimize}
            options={[
              { value: "none", label: "Hold only" },
              { value: "star", label: "Hold + SLSQP" },
              { value: "all", label: "All algos" },
            ]}
            onChange={setOptimize}
          />
        </div>
      </div>
      <div className="border-t border-border px-4 py-3">
        <button
          type="button"
          className="desk-btn data-[active=true]:pointer-events-none"
          data-active={running}
          disabled={running}
          onClick={onRun}
        >
          {running ? "Running…" : "Run labs"}
        </button>
      </div>
    </>
  );
}

function Headline({ report }: { report: BacktestReport }) {
  const h = report.headline;
  return (
    <div className="grid shrink-0 grid-cols-2 border-b border-border md:grid-cols-6">
      <Metric label="Best turnover" value={h.best_turnover ?? "—"} sub="lowest MAE" tone="gold" />
      <Metric label="Best belief" value={h.best_true_odds ?? "—"} sub="lowest log-loss" tone="gold" />
      <Metric label="Best algo" value={h.best_algo ?? h.best_gm ?? "—"} sub="realised GM" tone="gold" />
      <Metric
        label="Days + / −"
        value={`${h.days_better} / ${h.days_worse}`}
        sub={`ticks ${h.ticks_better} / ${h.ticks_worse}`}
        tone={h.days_better >= h.days_worse ? "positive" : "negative"}
      />
      <Metric label="MVP MAE" value={fmtNum(h.mvp_turnover_mae ?? 0, 1)} sub={h.mvp} />
      <Metric
        label="Attribution Δ"
        value={fmtMoney(h.attribution_delta ?? h.mvp_realized_lift ?? 0)}
        sub={`ECE ${fmtNum(h.mvp_ece ?? 0, 3)}`}
        tone={(h.attribution_delta ?? 0) >= 0 ? "positive" : "negative"}
      />
    </div>
  );
}

function Overview({ report }: { report: BacktestReport }) {
  const attr = report.attribution ?? [];
  const labs = report.labs;
  return (
    <div className="grid gap-3 p-3 lg:grid-cols-2">
      <Panel title="What each lever is for" hint="four independent questions">
        <div className="space-y-2 px-3 py-2 text-[12px] leading-relaxed text-muted-foreground">
          <p>
            <span className="text-foreground">One model</span> scores a single stack.{" "}
            <span className="text-foreground">A vs B</span> puts two stacks on the same rows.{" "}
            These labs keep the old four-axis read:{" "}
            <span className="text-foreground">Turnover</span> is next-bucket money,{" "}
            <span className="text-foreground">belief</span> is calibrated true-prob,{" "}
            <span className="text-foreground">optimizer</span> is whether a search beats hold,{" "}
            <span className="text-foreground">policy</span> is whether we apply the solve if demand moves.
          </p>
        </div>
      </Panel>
      <Panel title="Attribution" hint="realised GM, add one lever at a time">
        <Waterfall
          steps={attr.map((s) => ({
            key: (s.step ?? s.id).replace(/^\+ /, ""),
            value: s.gm.realized_gm ?? s.gm.realized_gm_star,
            delta: s.delta,
          }))}
        />
        <table className="desk-grid" data-density="compact">
          <thead>
            <tr>
              <th>Step</th>
              <th className="text-right">R[GM]</th>
              <th className="text-right">Δ</th>
            </tr>
          </thead>
          <tbody>
            {attr.map((s) => (
              <tr key={s.id}>
                <td>{s.step ?? s.id}</td>
                <td className="text-right desk-value">{fmtMoney(s.gm.realized_gm)}</td>
                <td
                  className={cn(
                    "text-right desk-value",
                    (s.delta ?? 0) > 0 && "text-positive",
                    (s.delta ?? 0) < 0 && "text-negative",
                  )}
                >
                  {fmtMoney(s.delta ?? 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      {labs && (
        <>
          <Panel title="Turnover MAE" hint="oracle is the ceiling">
            <BarChart
              data={labs.turnover.candidates.map((c) => ({
                key: c.id,
                value: c.turnover.mae,
              }))}
            />
          </Panel>
          <Panel title="Belief log-loss" hint="source / calibrator">
            <BarChart
              data={labs.true_prob.candidates.map((c) => ({
                key: c.id,
                value: c.true_odds.logloss,
              }))}
            />
          </Panel>
          <Panel title="Optimizer realised GM" hint="same stack, different search">
            <BarChart
              data={labs.optimizer.candidates.map((c) => ({
                key: c.id,
                value: c.gm.realized_gm ?? c.gm.realized_gm_star,
              }))}
              signed
            />
          </Panel>
          <Panel title="Policy / elasticity" hint="apply the solve, or not">
            <BarChart
              data={labs.policy.candidates.map((c) => ({
                key: c.id,
                value: c.gm.realized_gm ?? c.gm.realized_gm_star,
              }))}
              signed
            />
          </Panel>
        </>
      )}
      {report.cross?.heatmap && (
        <Panel title="Belief × realised GM (hold)" hint={report.cross.hint}>
          <Heatmap
            rows={report.cross.heatmap.rows}
            cols={report.cross.heatmap.cols}
            cells={report.cross.heatmap.cells}
          />
        </Panel>
      )}
      {report.cross?.turnover_mae && (
        <Panel title="Turnover MAE vs belief log-loss">
          <div className="grid grid-cols-2">
            <BarChart data={report.cross.turnover_mae} />
            <BarChart data={report.cross.logloss ?? []} />
          </div>
        </Panel>
      )}
    </div>
  );
}

function TurnoverLab({
  lab,
  selected,
  onSelect,
}: {
  lab: BacktestLab;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const combo = lab.candidates.find((c) => c.id === selected) ?? lab.candidates[0];
  const t = combo.turnover;
  const plots = t.plots ?? {};
  return (
    <div>
      <CandidateBar lab={lab} selected={combo.id} onSelect={onSelect} metric={(c) => fmtNum(c.turnover.mae, 0)} />
      <div className="grid gap-3 p-3 lg:grid-cols-2">
        <Panel title="Accuracy" hint={combo.turnover_label}>
          <div className="grid grid-cols-4">
            <Metric label="MAE" value={fmtNum(t.mae, 1)} />
            <Metric label="MSE" value={fmtNum(t.mse ?? 0, 0)} />
            <Metric label="RMSE" value={fmtNum(t.rmse, 1)} />
            <Metric label="WAPE" value={`${fmtNum(t.wape_pct ?? t.mape_pct, 1)}%`} />
            <Metric label="sMAPE" value={`${fmtNum(t.smape_pct ?? 0, 1)}%`} />
            <Metric label="Bias" value={`${fmtSigned(t.bias_pct, 1)}%`} />
            <Metric label="R²" value={fmtNum(t.r2 ?? 0, 2)} />
            <Metric label="Pearson" value={fmtNum(t.pearson ?? 0, 2)} />
            <Metric label="Theil U" value={fmtNum(t.theil_u ?? 0, 2)} sub="vs persist" />
            <Metric label="Share MAE" value={fmtNum(t.share_mae ?? 0, 4)} hint="allocation" />
            <Metric label="Cosine" value={fmtNum(t.cosine ?? 0, 2)} />
            <Metric label="Top-10 overlap" value={fmtNum(t.top10_overlap ?? 0, 2)} />
            <Metric label="Match MAE" value={fmtNum(t.match_mae ?? 0, 0)} />
          </div>
        </Panel>
        <Panel title="Predicted vs actual">
          <ScatterChart data={(plots.scatter ?? []) as { x: number; y: number }[]} />
        </Panel>
        <Panel title="Residual vs predicted">
          <ScatterChart
            data={(plots.residual_vs_pred ?? []).map((p) => ({ x: p.x ?? 0, y: p.y ?? 0 }))}
            diagonal={false}
          />
        </Panel>
        <Panel title="Residual histogram">
          <HistChart data={(plots.residual_hist ?? []) as { lo: number; hi: number; n: number }[]} />
        </Panel>
        <Panel title="MAE by tick">
          <LineChart
            data={(plots.mae_by_tick ?? []).map((p) => ({
              key: String(p.key ?? ""),
              value: Number(p.mae ?? p.value ?? 0),
            }))}
          />
        </Panel>
        <Panel title="Decile bias" hint="pred vs actual">
          <BarChart
            data={(plots.deciles ?? []).map((d) => ({
              key: String(d.key ?? ""),
              value: Number(d.bias ?? 0),
            }))}
            signed
          />
        </Panel>
        <Panel title="MAE by gametime">
          <BarChart data={(plots.mae_by_phase ?? []) as { key: string; value: number }[]} />
        </Panel>
        <Panel title="MAE by bet type">
          <BarChart data={(plots.mae_by_pool ?? []) as { key: string; value: number }[]} />
        </Panel>
        <Panel title="By gametime">
          <CutTable rows={t.by_phase} cols={turnoverCols} />
        </Panel>
        <Panel title="By bet type">
          <CutTable rows={t.by_pool} cols={turnoverCols} />
        </Panel>
      </div>
    </div>
  );
}

function BeliefLab({
  lab,
  selected,
  onSelect,
}: {
  lab: BacktestLab;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const combo = lab.candidates.find((c) => c.id === selected) ?? lab.candidates[0];
  const o = combo.true_odds;
  const plots = (o.plots ?? {}) as Record<string, unknown>;
  return (
    <div>
      <CandidateBar
        lab={lab}
        selected={combo.id}
        onSelect={onSelect}
        metric={(c) => fmtNum(c.true_odds.logloss, 3)}
      />
      <div className="grid gap-3 p-3 lg:grid-cols-2">
        <Panel title="Scores" hint={`${combo.true_prob_label} · ${combo.calibrator_label}`}>
          <div className="grid grid-cols-4">
            <Metric label="Log-loss" value={fmtNum(o.logloss, 4)} />
            <Metric label="Brier" value={fmtNum(o.brier, 4)} />
            <Metric label="Accuracy" value={fmtNum(o.accuracy ?? 0, 3)} />
            <Metric label="ECE" value={fmtNum(o.ece ?? 0, 4)} />
            <Metric label="MCE" value={fmtNum(o.mce ?? 0, 4)} />
            <Metric label="Bias" value={fmtSigned(o.bias, 3)} />
            <Metric label="AUC" value={fmtNum(o.auc ?? 0.5, 3)} />
            <Metric label="Slope" value={fmtNum(o.cal_slope ?? 0, 2)} sub="y ≈ a+bp" />
            <Metric label="Sharpness" value={fmtNum(o.sharpness ?? 0, 3)} />
            <Metric label="Book |Σ−1|" value={fmtNum(o.book_abs ?? 0, 3)} />
            <Metric label="Bad books" value={`${fmtNum(o.book_bad_pct ?? 0, 1)}%`} />
            <Metric label="Mean p" value={fmtNum(o.mean_prob, 3)} />
            <Metric label="Mean y" value={fmtNum(o.mean_y, 3)} />
          </div>
        </Panel>
        <Panel title="Reliability" hint="equal-width">
          <ReliabilityChart bins={(plots.reliability as CalBin[]) ?? o.calibration} />
        </Panel>
        <Panel title="Reliability" hint="equal-mass">
          <ReliabilityChart bins={(plots.reliability_equal_mass as CalBin[]) ?? []} />
        </Panel>
        <Panel title="Favourite–longshot" hint="bias vs 1/p">
          <BarChart
            data={((plots.favourite_longshot as PlotPoint[]) ?? []).map((d) => ({
              key: String(d.key ?? d.odds ?? ""),
              value: Number(d.bias ?? 0),
            }))}
            signed
          />
        </Panel>
        <Panel title="Brier decomposition">
          <DecompBar items={(plots.brier_decomp as { key: string; value: number }[]) ?? []} />
        </Panel>
        <Panel title="p − y histogram">
          <HistChart data={(plots.residual_hist as { lo: number; hi: number; n: number }[]) ?? []} />
        </Panel>
        <Panel title="Log-loss by gametime">
          <BarChart data={(plots.logloss_by_phase as { key: string; value: number }[]) ?? []} />
        </Panel>
        <Panel title="ECE by bet type">
          <BarChart data={(plots.ece_by_pool as { key: string; value: number }[]) ?? []} />
        </Panel>
        <Panel title="Book-sum histogram">
          <HistChart data={(plots.book_sum_hist as { lo: number; hi: number; n: number }[]) ?? []} />
        </Panel>
        <Panel title="By gametime">
          <CutTable rows={o.by_phase} cols={oddsCols} />
        </Panel>
        <Panel title="By bet type">
          <CutTable rows={o.by_pool} cols={oddsCols} />
        </Panel>
      </div>
    </div>
  );
}

function AlgoLab({
  lab,
  selected,
  onSelect,
}: {
  lab: BacktestLab;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const combo = lab.candidates.find((c) => c.id === selected) ?? lab.candidates[0];
  const g = combo.gm;
  const plots = g.plots ?? {};
  const d = combo.diag ?? {};
  return (
    <div>
      <CandidateBar
        lab={lab}
        selected={combo.id}
        onSelect={onSelect}
        metric={(c) => fmtMoney(c.gm.realized_gm ?? c.gm.realized_gm_star)}
      />
      <div className="grid gap-3 p-3 lg:grid-cols-2">
        <Panel title="Money" hint={combo.algo_label}>
          <div className="grid grid-cols-3">
            <Metric label="R[GM]" value={fmtMoney(g.realized_gm ?? g.realized_gm_star)} />
            <Metric
              label="R[lift]"
              value={fmtMoney(g.realized_lift)}
              tone={g.realized_lift >= 0 ? "positive" : "negative"}
            />
            <Metric label="E[lift]" value={fmtMoney(g.exp_lift)} />
            <Metric label="Optimism" value={fmtMoney(g.optimism ?? 0)} hint="E − R" />
            <Metric label="Hit rate" value={fmtNum(g.hit_rate ?? 0, 2)} />
            <Metric label="CVaR 5%" value={fmtMoney(g.cvar_5 ?? 0)} />
            <Metric label="Days + / −" value={`${g.days_better} / ${g.days_worse}`} />
            <Metric label="Ticks + / −" value={`${g.ticks_better} / ${g.ticks_worse}`} />
            <Metric label="Worst tick" value={fmtMoney(g.worst_tick ?? 0)} />
          </div>
        </Panel>
        <Panel title="Solver">
          <div className="grid grid-cols-3">
            <Metric label="Solved" value={d.n_solved ?? 0} />
            <Metric label="Failed" value={d.n_failed ?? 0} />
            <Metric label="Seconds" value={fmtNum(d.seconds ?? 0, 1)} />
            <Metric label="|Δθ|" value={fmtNum(d.mean_abs_theta ?? 0, 3)} />
            <Metric label="Repaired" value={d.n_repaired ?? 0} />
            <Metric label="E[uplift]" value={fmtMoney(d.exp_uplift ?? 0)} />
          </div>
        </Panel>
        <Panel title="Realised lift by tick">
          <BarChart
            data={(plots.lift_by_tick ?? []).map((p) => ({
              key: String(p.key ?? ""),
              value: Number(p.value ?? 0),
            }))}
            signed
          />
        </Panel>
        <Panel title="E[lift] vs realised" hint="points are ticks">
          <ScatterChart
            data={(plots.exp_vs_realized ?? []).map((p) => ({
              x: Number(p.x ?? 0),
              y: Number(p.y ?? 0),
            }))}
          />
        </Panel>
        <Panel title="Better / worse">
          <BarChart data={(plots.better_worse ?? []) as { key: string; value: number }[]} />
        </Panel>
        <Panel title="Optimism by tick" hint="expected lift − realised">
          <LineChart
            data={(plots.optimism_by_tick ?? []).map((p) => ({
              key: String(p.key ?? ""),
              value: Number(p.value ?? 0),
            }))}
            color="var(--warn, #d4af37)"
          />
        </Panel>
        <Panel title="Lift by bet type">
          <BarChart
            data={(plots.lift_by_pool ?? []) as { key: string; value: number }[]}
            signed
          />
        </Panel>
        <Panel title="Lift by gametime">
          <BarChart
            data={(plots.lift_by_phase ?? []) as { key: string; value: number }[]}
            signed
          />
        </Panel>
        <Panel title="By gametime">
          <CutTable rows={g.by_phase} cols={gmCols} />
        </Panel>
        <Panel title="By bet type">
          <CutTable rows={g.by_pool} cols={gmCols} />
        </Panel>
      </div>
    </div>
  );
}

function PolicyLab({
  lab,
  selected,
  onSelect,
}: {
  lab: BacktestLab;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const combo = lab.candidates.find((c) => c.id === selected) ?? lab.candidates[0];
  const g = combo.gm;
  return (
    <div>
      <CandidateBar
        lab={lab}
        selected={combo.id}
        onSelect={onSelect}
        metric={(c) => fmtMoney(c.gm.realized_gm ?? c.gm.realized_gm_star)}
      />
      <div className="grid gap-3 p-3 lg:grid-cols-2">
        <Panel title="This policy" hint={`${combo.algo_label} · e=${combo.elasticity}`}>
          <div className="grid grid-cols-3">
            <Metric label="R[GM]" value={fmtMoney(g.realized_gm ?? g.realized_gm_star)} />
            <Metric
              label="R[lift]"
              value={fmtMoney(g.realized_lift)}
              tone={g.realized_lift >= 0 ? "positive" : "negative"}
            />
            <Metric label="E[lift]" value={fmtMoney(g.exp_lift)} />
            <Metric label="Elasticity" value={fmtNum(combo.elasticity ?? 0, 1)} />
            <Metric label="Policy" value={combo.policy_label ?? combo.policy ?? "—"} />
            <Metric label="Hit rate" value={fmtNum(g.hit_rate ?? 0, 2)} />
          </div>
        </Panel>
        <Panel title="Compare realised GM">
          <BarChart
            data={lab.candidates.map((c) => ({
              key: c.id,
              value: c.gm.realized_gm ?? c.gm.realized_gm_star,
            }))}
            signed
          />
        </Panel>
        <Panel title="Compare optimism">
          <BarChart
            data={lab.candidates.map((c) => ({
              key: c.id,
              value: c.gm.optimism ?? 0,
            }))}
            signed
          />
        </Panel>
        <Panel title="Compare CVaR 5%">
          <BarChart
            data={lab.candidates.map((c) => ({
              key: c.id,
              value: c.gm.cvar_5 ?? 0,
            }))}
            signed
          />
        </Panel>
        <Panel title="By gametime">
          <CutTable rows={g.by_phase} cols={gmCols} />
        </Panel>
        <Panel title="By bet type">
          <CutTable rows={g.by_pool} cols={gmCols} />
        </Panel>
      </div>
    </div>
  );
}

function CandidateBar({
  lab,
  selected,
  onSelect,
  metric,
}: {
  lab: BacktestLab;
  selected: string;
  onSelect: (id: string) => void;
  metric: (c: BacktestCombo) => string;
}) {
  return (
    <div className="flex flex-wrap gap-1 border-b border-border px-3 py-2">
      {lab.candidates.map((c) => (
        <button
          key={c.id}
          type="button"
          data-active={c.id === selected}
          className="desk-btn"
          onClick={() => onSelect(c.id)}
        >
          {c.id}
          <span className="ml-2 text-muted-foreground">{metric(c)}</span>
        </button>
      ))}
    </div>
  );
}

const turnoverCols: [string, string][] = [
  ["key", "Slice"],
  ["n", "N"],
  ["mae", "MAE"],
  ["share_mae", "Share MAE"],
  ["bias_pct", "Bias%"],
];
const oddsCols: [string, string][] = [
  ["key", "Slice"],
  ["n", "N"],
  ["logloss", "Log-loss"],
  ["brier", "Brier"],
  ["ece", "ECE"],
  ["bias", "Bias"],
];
const gmCols: [string, string][] = [
  ["key", "Slice"],
  ["n", "N"],
  ["exp_lift", "E[lift]"],
  ["realized_lift", "R[lift]"],
  ["realized_gm", "R[GM]"],
];

function CutTable({ rows, cols }: { rows: CutRow[]; cols: [string, string][] }) {
  if (!rows?.length) {
    return <div className="px-3 py-4 text-[11px] text-muted-foreground">No cut available.</div>;
  }
  return (
    <table className="desk-grid" data-density="compact">
      <thead>
        <tr>
          {cols.map(([k, label]) => (
            <th key={k} className={k === "key" ? "" : "text-right"}>
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            {cols.map(([k]) => (
              <td key={k} className={k === "key" ? "" : "text-right desk-value"}>
                {formatCut(row, k)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatCut(row: CutRow, key: string) {
  const v = row[key];
  if (v == null) return "—";
  if (key === "key") return String(v);
  if (key === "n") return Number(v).toLocaleString();
  if (key === "mape_pct" || key === "bias_pct") return `${fmtNum(Number(v), 1)}%`;
  if (key === "exp_lift" || key === "realized_lift" || key === "realized_gm") return fmtMoney(Number(v));
  if (key === "logloss" || key === "brier" || key === "bias" || key === "ece" || key === "share_mae" || key === "accuracy") {
    return fmtNum(Number(v), 3);
  }
  return fmtNum(Number(v), 1);
}

function ChipSet({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { id: string; label: string }[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div>
      <div className="desk-label mb-1">{label}</div>
      <div className="flex flex-wrap gap-1">
        {options.map((o) => {
          const on = value.includes(o.id);
          return (
            <button
              key={o.id}
              type="button"
              data-active={on}
              className="desk-btn"
              onClick={() => onChange(on ? value.filter((v) => v !== o.id) : [...value, o.id])}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Num({
  label,
  value,
  set,
  min,
  max,
}: {
  label: string;
  value: number;
  set: (n: number) => void;
  min: number;
  max: number;
}) {
  return (
    <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
      {label}
      <input
        className="desk-input w-14"
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => set(Number(e.target.value))}
      />
    </label>
  );
}
