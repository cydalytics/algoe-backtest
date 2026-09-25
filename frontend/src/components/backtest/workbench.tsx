"use client";

import { useEffect, useMemo, useState } from "react";

import {
  BarChart,
  Heatmap,
  ReliabilityChart,
  ScatterChart,
} from "@/components/backtest/charts";
import {
  FilterBar,
  StackFields,
  buildFacets,
  shortBelief,
  shortTurnover,
  unique,
} from "@/components/backtest/controls";
import { EmptyState, Panel, Segmented } from "@/components/desk/primitives";
import { api, ApiError } from "@/lib/api";
import { fmtMoney, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  BacktestReport,
  BacktestSlice,
  CalBin,
  PlotPoint,
  SliceCompareRow,
  SliceRequest,
} from "@/types/api";

type Question = "money" | "belief" | "where";

const TURNOVER_PLAIN: Record<string, string> = {
  persistence: "Last 5 minutes",
  trailing_mean: "Mean of last 3 buckets",
  ema: "EMA of last 3 buckets",
  zero: "Always zero (sanity)",
  oracle: "Actual next 5 min (cheat / ceiling)",
};

const BELIEF_PLAIN: Record<string, string> = {
  hkjc_true: "1 / HKJC true odds",
  poisson: "Poisson from HKJC TG/SUP",
  demargin: "Public odds, margin stripped",
};

export function Workbench({ report }: { report: BacktestReport }) {
  const wb = report.workbench;
  const baselines = wb?.baselines ?? {
    turnover: "persistence",
    true_prob: "hkjc_true",
    calibrator: "raw",
    algo: "hold",
  };

  const [turnover, setTurnover] = useState(baselines.turnover);
  const [trueProb, setTrueProb] = useState(baselines.true_prob);
  const [calibrator, setCalibrator] = useState(baselines.calibrator);
  const [algo, setAlgo] = useState(baselines.algo);
  const [filters, setFilters] = useState<Record<string, string[]>>({});
  const [groupBy, setGroupBy] = useState("clock_bin");
  const [question, setQuestion] = useState<Question>("money");
  const [slice, setSlice] = useState<BacktestSlice | null>(null);
  const [error, setError] = useState<string | null>(null);

  const body: SliceRequest = useMemo(
    () => ({
      turnover,
      true_prob: trueProb,
      calibrator,
      algo,
      group_by: groupBy,
      filters,
    }),
    [turnover, trueProb, calibrator, algo, groupBy, filters],
  );

  // Loading is derived rather than a flag set inside the effect: it is simply
  // whether the stack on screen is the one we have an answer for. That also
  // makes a run with no facts non-loading without a special case.
  const key = JSON.stringify([report.run_id, body]);
  const [answered, setAnswered] = useState<string | null>(null);
  const loading = Boolean(wb?.has_facts) && answered !== key;

  useEffect(() => {
    if (!wb?.has_facts) return;
    let alive = true;
    api
      .backtestSlice(report.run_id, body)
      .then((res) => {
        if (!alive) return;
        setSlice(res.slice);
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
        body="Open New run and run labs again. The workbench needs per-row forecasts so you can change turnover and belief without a new sweep."
      />
    );
  }

  const models = slice?.models ?? {
    turnover: wb.turnover ?? [],
    beliefs: wb.beliefs ?? [],
    algos: wb.algos ?? ["hold"],
  };
  const tNames = models.turnover.length ? models.turnover : [turnover];
  const beliefTokens = models.beliefs.length ? models.beliefs : [`${trueProb}/${calibrator}`];
  const sources = unique(beliefTokens.map((t) => t.split("/")[0]));
  const cals = unique(beliefTokens.map((t) => (t.includes("/") ? t.split("/")[1] : "raw")));
  const algos = models.algos.length ? models.algos : [algo];
  const facets = slice?.facets ?? {};
  const parquet = report.mode === "parquet";

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="shrink-0 space-y-2 border-b border-border px-4 py-2">
        {parquet && (
          <p className="text-[12px] leading-relaxed text-muted-foreground">
            The test is{" "}
            <span className="text-foreground">next 5 min $ = last 5 min</span>
            {" × "}
            <span className="text-foreground">true prob = 1 / HKJC true odds</span>
            . Other names in the menus are only benchmarks — they re-score the same
            rows, they are not extra backtests.
          </p>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <StackFields
            simple={parquet}
            stack={{ turnover, true_prob: trueProb, calibrator, algo }}
            onChange={(next) => {
              setTurnover(next.turnover);
              setTrueProb(next.true_prob);
              setCalibrator(next.calibrator);
              setAlgo(next.algo);
            }}
            tOpts={tNames.map((id) => ({
              id,
              label: parquet
                ? (TURNOVER_PLAIN[id] ?? shortTurnover(id, report.catalog.turnover[id]))
                : shortTurnover(id, report.catalog.turnover[id]),
              baseline: id === baselines.turnover,
              group: parquet
                ? id === baselines.turnover
                  ? "Your baseline"
                  : "Only to beat last 5 min"
                : undefined,
            }))}
            pOpts={sources.map((id) => ({
              id,
              label: parquet
                ? (BELIEF_PLAIN[id] ?? shortBelief(id, report.catalog.true_prob[id]))
                : shortBelief(id, report.catalog.true_prob[id]),
              baseline: id === baselines.true_prob,
              group: parquet
                ? id === baselines.true_prob
                  ? "Your baseline"
                  : "Stand-in if true odds is missing"
                : undefined,
            }))}
            cOpts={cals.map((id) => ({
              id,
              label: id,
              baseline: id === baselines.calibrator,
            }))}
            aOpts={algos.map((id) => ({
              id,
              label: id,
              baseline: id === baselines.algo,
            }))}
          />
          {!parquet && (
            <p className="max-w-sm text-[11px] leading-relaxed text-muted-foreground">
              Desk baseline is last 5 min × 1 / HKJC true odds. Menus re-score the same rows.
            </p>
          )}
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
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-background/95 px-4 py-1.5 backdrop-blur-sm">
          <Segmented
            value={question}
            options={[
              { value: "money", label: "Next-bucket money" },
              { value: "belief", label: "True-prob" },
              { value: "where", label: "Where it breaks" },
            ]}
            onChange={setQuestion}
          />
          <div className="text-[11px] text-muted-foreground">
            {slice
              ? `${slice.n_rows.toLocaleString()} of ${slice.n_full.toLocaleString()} rows`
              : loading
                ? "Scoring…"
                : "—"}
          </div>
        </div>
        {!slice && loading ? (
          <div className="p-6 text-[12px] text-muted-foreground">Scoring the slice…</div>
        ) : slice ? (
          <div className="space-y-3 p-3">
            <Verdict slice={slice} question={question} />
            {question === "money" && <MoneyView slice={slice} />}
            {question === "belief" && <BeliefView slice={slice} />}
            {question === "where" && <WhereView slice={slice} />}
            {slice.note && (
              <p className="px-1 pb-2 text-[11px] leading-relaxed text-muted-foreground">{slice.note}</p>
            )}
          </div>
        ) : (
          <EmptyState title="No slice" body="Pick a stack and filters, or re-run labs." />
        )}
      </div>
    </div>
  );
}

function persistRow(slice: BacktestSlice) {
  return (
    slice.compare.turnover.find((r) => r.baseline) ??
    slice.compare.turnover.find((r) => r.id === "persistence")
  );
}

function selectedTurnover(slice: BacktestSlice) {
  return slice.compare.turnover.find((r) => r.selected) ?? slice.compare.turnover[0];
}

function selectedBelief(slice: BacktestSlice) {
  return slice.compare.belief.find((r) => r.selected) ?? slice.compare.belief[0];
}

function Verdict({ slice, question }: { slice: BacktestSlice; question: Question }) {
  const mine = selectedTurnover(slice);
  const persist = persistRow(slice);
  const belief = selectedBelief(slice);
  const baseBelief = slice.compare.belief.find((r) => r.baseline) ?? slice.compare.belief[0];
  const dMae = mine && persist && mine.mae != null && persist.mae != null ? mine.mae - persist.mae : null;
  const dLl =
    belief && baseBelief && belief.logloss != null && baseBelief.logloss != null
      ? belief.logloss - baseBelief.logloss
      : null;

  let title = "";
  let body = "";
  if (question === "money") {
    const name = slice.stack.turnover_label || slice.stack.turnover;
    if (dMae == null) title = `${name} · MAE ${fmtNum(slice.turnover.mae, 0)}`;
    else if (Math.abs(dMae) < 1) title = `${name} is even with last 5 minutes`;
    else if (dMae < 0) title = `${name} beats last 5 min by ${fmtNum(Math.abs(dMae), 0)} MAE`;
    else title = `${name} is worse than last 5 min by ${fmtNum(dMae, 0)} MAE`;
    body = `${slice.n_rows.toLocaleString()} legs · WAPE ${fmtNum(slice.turnover.wape_pct ?? slice.turnover.mape_pct, 1)}%. This is next-bucket HK$ error, not GM.`;
  } else if (question === "belief") {
    const name = `${slice.stack.true_prob_label || slice.stack.true_prob} / ${slice.stack.calibrator}`;
    if (dLl == null) title = `${name} · log-loss ${fmtNum(slice.true_odds.logloss, 3)}`;
    else if (Math.abs(dLl) < 0.0005) title = `${name} matches the HKJC-true baseline`;
    else if (dLl < 0) title = `${name} is tighter than HKJC true by ${fmtNum(Math.abs(dLl), 3)} log-loss`;
    else title = `${name} is looser than HKJC true by ${fmtNum(dLl, 3)} log-loss`;
    body = `ECE ${fmtNum(slice.true_odds.ece, 3)} · accuracy ${fmtNum(slice.true_odds.accuracy, 3)}. The dashed line on the chart is perfect calibration.`;
  } else {
    title = `Break the same ${slice.n_rows.toLocaleString()} rows by ${slice.group_by.replace("_", " ")}`;
    body = "Look for the clock, pool or TG/SUP cell where MAE or log-loss jumps. That is where the model is lying.";
  }

  return (
    <div className="rounded-sm border border-border bg-card px-4 py-3">
      <div className="text-[15px] font-medium tracking-tight">{title}</div>
      <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{body}</p>
      {question === "money" && slice.stack.algo === "hold" && (
        <p className="mt-1 text-[11px] text-muted-foreground/80">
          R[GM] stays {fmtMoney(slice.gm.realized_gm ?? slice.gm.realized_gm_star)} at hold — same tickets, same posted prices.
        </p>
      )}
    </div>
  );
}

function MoneyView({ slice }: { slice: BacktestSlice }) {
  const plots = slice.turnover.plots ?? {};
  const clock = slice.gallery.mae_by?.clock_bin ?? slice.gallery.clock_mae ?? [];
  return (
    <div className="space-y-3">
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel title="Forecast vs what actually landed" hint="HK$ next 5 min · dashed line is perfect">
          <ScatterChart
            data={(plots.scatter ?? []) as { x: number; y: number }[]}
            xLabel="Forecast HK$"
            yLabel="Actual HK$"
          />
        </Panel>
        <Panel title="MAE along the clock" hint="where the forecast is expensive">
          <BarChart data={clock} money />
        </Panel>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Does this beat last 5 minutes?" hint="MAE on the same rows · gold = selected">
          <BarChart
            money
            data={slice.compare.turnover.map((r) => ({
              key: r.label || r.id,
              value: r.mae ?? 0,
            }))}
          />
        </Panel>
        <Panel title="Turnover models" hint="same slice">
          <CompareTable
            rows={slice.compare.turnover}
            cols={[
              ["id", "Model"],
              ["mae", "MAE"],
              ["wape_pct", "WAPE"],
              ["rmse", "RMSE"],
            ]}
          />
        </Panel>
      </div>
    </div>
  );
}

function BeliefView({ slice }: { slice: BacktestSlice }) {
  const o = slice.true_odds;
  const plots = (o.plots ?? {}) as Record<string, unknown>;
  return (
    <div className="space-y-3">
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel title="Did predicted probability match outcomes?" hint="points on the diagonal are calibrated">
          <ReliabilityChart bins={(plots.reliability as CalBin[]) ?? o.calibration} />
        </Panel>
        <Panel title="Favourite–longshot bias" hint="above 0 = we overstate that band">
          <BarChart
            signed
            data={((plots.favourite_longshot as PlotPoint[]) ?? []).map((d) => ({
              key: String(d.key ?? d.odds ?? ""),
              value: Number(d.bias ?? 0),
            }))}
          />
        </Panel>
      </div>
      <Panel title="Belief sources on this slice" hint="log-loss · lower is better">
        <div className="grid gap-3 lg:grid-cols-2">
          <BarChart
            data={slice.compare.belief.map((r) => ({
              key: r.id,
              value: r.logloss ?? 0,
            }))}
          />
          <CompareTable
            rows={slice.compare.belief}
            cols={[
              ["id", "Stack"],
              ["logloss", "Log-loss"],
              ["ece", "ECE"],
              ["accuracy", "Acc"],
            ]}
          />
        </div>
      </Panel>
    </div>
  );
}

function WhereView({ slice }: { slice: BacktestSlice }) {
  const gal = slice.gallery;
  const pool = gal.mae_by?.pool_code ?? [];
  return (
    <div className="space-y-3">
      <BreakdownTable rows={slice.breakdown} groupBy={slice.group_by} />
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="MAE by pool" hint="which bet type is expensive">
          <BarChart data={pool} money />
        </Panel>
        <Panel title="TG × SUP MAE" hint="scoreline shape">
          <Heatmap
            rows={gal.tg_sup_mae?.rows ?? []}
            cols={gal.tg_sup_mae?.cols ?? []}
            cells={gal.tg_sup_mae?.cells ?? []}
            dec={0}
          />
        </Panel>
      </div>
    </div>
  );
}

function BreakdownTable({
  rows,
  groupBy,
}: {
  rows: BacktestSlice["breakdown"];
  groupBy: string;
}) {
  return (
    <Panel title={`Breakdown · ${groupBy}`} hint="metrics recompute on the cut">
      {!rows.length ? (
        <div className="px-3 py-4 text-[11px] text-muted-foreground">No groups on this slice.</div>
      ) : (
        <div className="overflow-auto">
          <table className="desk-grid" data-density="compact">
            <thead>
              <tr>
                <th>{groupBy}</th>
                <th className="text-right">N</th>
                <th className="text-right">MAE</th>
                <th className="text-right">MSE</th>
                <th className="text-right">WAPE</th>
                <th className="text-right">Log-loss</th>
                <th className="text-right">Acc</th>
                <th className="text-right">ECE</th>
                <th className="text-right">E[GM]</th>
                <th className="text-right">R[GM]</th>
                <th className="text-right">R[lift]</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td>{r.key}</td>
                  <td className="text-right desk-value">{r.n.toLocaleString()}</td>
                  <td className="text-right desk-value">{fmtNum(r.mae, 0)}</td>
                  <td className="text-right desk-value">{fmtNum(r.mse, 0)}</td>
                  <td className="text-right desk-value">{fmtNum(r.wape_pct, 1)}%</td>
                  <td className="text-right desk-value">{fmtNum(r.logloss, 3)}</td>
                  <td className="text-right desk-value">{fmtNum(r.accuracy, 3)}</td>
                  <td className="text-right desk-value">{fmtNum(r.ece, 3)}</td>
                  <td className="text-right desk-value">{fmtMoney(r.exp_gm_star)}</td>
                  <td className="text-right desk-value">{fmtMoney(r.realized_gm)}</td>
                  <td className="text-right desk-value">{fmtMoney(r.realized_lift)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function CompareTable({
  rows,
  cols,
}: {
  rows: SliceCompareRow[];
  cols: [string, string][];
}) {
  return (
    <table className="desk-grid" data-density="compact">
      <thead>
        <tr>
          {cols.map(([k, label]) => (
            <th key={k} className={k === "id" ? "" : "text-right"}>
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} className={cn(r.selected && "bg-iris/10", r.baseline && !r.selected && "text-gold-soft")}>
            {cols.map(([k]) => (
              <td key={k} className={k === "id" ? "" : "text-right desk-value"}>
                {k === "id" ? (
                  <span>
                    {String(r.id)}
                    {r.baseline ? <span className="ml-1 text-[9px] text-gold">MVP</span> : null}
                  </span>
                ) : (
                  formatCompare((r as unknown as Record<string, unknown>)[k], k)
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function formatCompare(v: unknown, key: string) {
  if (v == null) return "—";
  const n = Number(v);
  if (key.includes("gm") || key.includes("lift") || key === "optimism") return fmtMoney(n);
  if (key === "wape_pct") return `${fmtNum(n, 1)}%`;
  if (key === "mae" || key === "mse" || key === "rmse") return fmtNum(n, 0);
  return fmtNum(n, 3);
}

