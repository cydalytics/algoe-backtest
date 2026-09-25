"use client";

/**
 * Running a backtest on the real parquet folders.
 *
 * The one thing this screen has to make obvious is that a run is *incremental*.
 * Every panel day is cached, so a window is a set of days that either exist or
 * do not, and pressing Run only touches the ones that do not. Extending
 * 07-01..07-31 to 07-01..08-31 is 31 days of work, not 62. The coverage strip
 * says which is which before you commit to anything.
 *
 * Three layers, cheapest first, because they cost very different amounts:
 *
 *   1. Build      reads raw parquet into per-day panels, then scores turnover
 *                 WAPE and belief ECE on every row. Minutes per month.
 *   2. Register   turns cached days into the fact table. Seconds. Every model
 *                 gets its own column here, which is why the workbench can
 *                 switch turnover or belief afterwards with no rebuild.
 *   3. Optimise   solves TG/SUP per sampled bucket. Opt-in, and slow, so it is
 *                 its own button and its own cache. Does not change WAPE/ECE.
 *
 * Model choice deliberately does *not* appear on this form. Picking a model is
 * a question you ask of a finished run, not a parameter of building one.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Metric, Segmented } from "@/components/desk/primitives";
import { api, ApiError } from "@/lib/api";
import { fmtMoney } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  ParquetCoverage,
  ParquetDefaults,
  ParquetJob,
  ParquetProbe,
  ParquetRequest,
} from "@/types/api";

const POOL_OPTS = [
  { id: "HILO", label: "HILO" },
  { id: "HDC", label: "HDC" },
  { id: "CHLO", label: "CHLO" },
  { id: "CHDC", label: "CHDC" },
  { id: "FHLO", label: "FHLO" },
  { id: "FHHDC", label: "FHHDC" },
];

const CAL_OPTS = [
  { id: "shrink", label: "shrink" },
  { id: "temperature", label: "temp" },
  { id: "isotonic", label: "isotonic" },
];

const POLL_MS = 1500;

export type ParquetForm = {
  dataDir: string;
  start: string;
  end: string;
  pools: string[];
  prematchMin: number;
  fullSpan: boolean;
  lookback: number;
  hdcSign: string;
  cals: string[];
  optEvery: number;
  optMax: number;
  optStarts: number;
};

export function defaultForm(d: ParquetDefaults): ParquetForm {
  return {
    dataDir: d.data_dir,
    start: d.start,
    end: d.end,
    pools: [],
    prematchMin: d.prematch_window_min,
    fullSpan: false,
    lookback: d.lookback_days,
    hdcSign: d.hdc_sign,
    cals: d.calibrators.filter((c) => c !== "raw"),
    optEvery: d.optimize_every,
    optMax: 20000,
    optStarts: 3,
  };
}

export function toRequest(f: ParquetForm, extra?: Partial<ParquetRequest>): ParquetRequest {
  return {
    start: f.start,
    end: f.end,
    data_dir: f.dataDir,
    pools: f.pools.length ? f.pools : ["all"],
    prematch_window_min: f.prematchMin,
    full_span: f.fullSpan,
    lookback_days: f.lookback,
    hdc_sign: f.hdcSign,
    calibrators: f.cals,
    optimize_every: f.optEvery,
    optimize_max_buckets: f.optMax,
    optimize_starts: f.optStarts,
    ...extra,
  };
}

/** Whole days between two dates, inclusive, or null when the range is bad. */
function spanDays(start: string, end: string): number | null {
  const a = Date.parse(start);
  const b = Date.parse(end);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return null;
  return Math.round((b - a) / 86_400_000) + 1;
}

export function ParquetRunPanel({
  onRegistered,
  onClose,
}: {
  onRegistered: (info?: {
    runId?: string;
    refresh?: boolean;
    tab?: "turnover" | "optimize";
    jobId?: string;
  }) => void;
  onClose: () => void;
}) {
  const [defaults, setDefaults] = useState<ParquetDefaults | null>(null);
  const [form, setForm] = useState<ParquetForm | null>(null);
  const [cov, setCov] = useState<ParquetCoverage | null>(null);
  const [job, setJob] = useState<ParquetJob | null>(null);
  const [probe, setProbe] = useState<ParquetProbe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const lastRun = useRef<string | null>(null);
  const lastPublish = useRef<string>("");

  const set = useCallback(
    <K extends keyof ParquetForm>(key: K, value: ParquetForm[K]) =>
      setForm((f) => (f ? { ...f, [key]: value } : f)),
    [],
  );

  useEffect(() => {
    let alive = true;
    api
      .parquetDefaults()
      .then((d) => {
        if (!alive) return;
        setDefaults(d);
        setForm(defaultForm(d));
      })
      .catch((err) => alive && setError(msg(err)));
    return () => {
      alive = false;
    };
  }, []);

  // Adopt a build that is already running, so reopening the drawer mid-run
  // shows the run rather than an idle form.
  useEffect(() => {
    let alive = true;
    api
      .parquetJobs()
      .then(({ jobs, current }) => {
        if (!alive) return;
        const live = jobs.find((j) => j.id === current) ?? null;
        if (live) setJob(live);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  const days = form ? spanDays(form.start, form.end) : null;
  const tooWide = days != null && defaults != null && days > defaults.max_days;
  const request = useMemo(() => (form ? toRequest(form) : null), [form]);
  const running = job?.status === "running" || job?.status === "queued";

  // Coverage follows the window, debounced so typing a date does not fire a
  // request per keystroke. It also follows the *end* of a job: a finished build
  // has changed what is cached, and finished_at is a value that only appears
  // once, so depending on it refetches exactly then and not on every poll.
  const settledAt = running ? "live" : (job?.finished_at ?? "");
  const progressTick = running ? `${job?.progress?.done ?? 0}:${job?.progress?.n_optimized ?? 0}` : "";
  useEffect(() => {
    if (!request || days == null || tooWide) return;
    let alive = true;
    const timer = setTimeout(() => {
      api
        .parquetCoverage(request)
        .then((res) => {
          if (!alive) return;
          setCov(res.coverage);
          setError(null);
          const stored = res.coverage.runs?.[0]?.run_id;
          if (stored && stored !== lastRun.current) {
            lastRun.current = stored;
            onRegistered({ runId: stored, refresh: true, tab: "turnover" });
          }
        })
        .catch((err) => alive && setError(msg(err)));
    }, 350);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [request, days, tooWide, settledAt, progressTick]);

  // Poll a live job. The transition to a terminal status is handled here rather
  // than in an effect on `job`, because the parent should be told about a new
  // run once, on the tick that finds it, and not again on a re-render.
  // `onRegistered` must be stable, or restarting this interval on every render
  // would keep pushing the next poll past its own deadline.
  const jobId = running ? job!.id : null;
  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    const timer = setInterval(() => {
      api
        .parquetJob(jobId)
        .then(({ job: next }) => {
          if (!alive) return;
          setJob(next);
          const stamp = `${next.run_id ?? ""}:${next.status}:${next.progress?.done ?? 0}:${next.progress?.n_optimized ?? ""}`;
          if (next.run_id && stamp !== lastPublish.current) {
            lastPublish.current = stamp;
            const finished = next.status === "done" || next.status === "cancelled";
            if (finished) lastRun.current = next.run_id;
            onRegistered({
              runId: next.run_id,
              refresh: true,
              tab: finished
                ? next.kind === "parquet-optimize"
                  ? "optimize"
                  : "turnover"
                : undefined,
            });
          }
        })
        .catch((err) => {
          if (alive) setError(msg(err));
          clearInterval(timer);
        });
    }, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [jobId, onRegistered]);

  async function act(what: "build" | "optimize" | "register" | "forget" | "probe", rebuild = false) {
    if (!request) return;
    setBusy(true);
    setError(null);
    try {
      if (what === "probe") {
        setProbe(await api.parquetProbe(request));
      } else if (what === "forget") {
        const res = await api.parquetForget(request);
        setCov(res.coverage);
      } else if (what === "register") {
        const res = await api.parquetRegister(request);
        setJob(res.job);
        setShowLog(true);
        onRegistered({ tab: "turnover", refresh: false, jobId: res.job.id });
      } else {
        const body = { ...request, rebuild };
        const res =
          what === "build" ? await api.parquetBuild(body) : await api.parquetOptimize(body);
        setJob(res.job);
        setShowLog(true);
        if (what === "optimize") {
          lastPublish.current = "";
          onRegistered({ tab: "optimize", refresh: false, jobId: res.job.id });
          onClose();
        }
      }
    } catch (err) {
      setError(msg(err));
    } finally {
      setBusy(false);
    }
  }

  if (!form || !defaults) {
    return (
      <Shell onClose={onClose} title="Real data">
        <div className="px-4 py-6 text-[12px] text-muted-foreground">
          {error ?? "Reading engine defaults…"}
        </div>
      </Shell>
    );
  }

  const missing = cov?.n_missing ?? 0;
  const cached = cov?.n_cached ?? 0;
  const missingOpt = cov?.missing_opt_days?.length ?? 0;
  const canRun = !running && !busy && !tooWide && days != null;

  return (
    <Shell
      onClose={onClose}
      title="Real data"
      sub="Build scores turnover and belief on every row. Solve TG/SUP is a later, sampled step."
    >
      <div className="min-h-0 flex-1 space-y-4 overflow-auto px-4 py-4">
        <Field label="Data folder" hint="the Raw_Data root the notebook wrote">
          <input
            className="desk-input w-full font-mono text-[11px]"
            value={form.dataDir}
            spellCheck={false}
            onChange={(e) => set("dataDir", e.target.value)}
          />
        </Field>

        <div className="flex flex-wrap items-end gap-3">
          <Field label="From">
            <input
              className="desk-input w-[8.5rem] text-[12px]"
              type="date"
              value={form.start}
              onChange={(e) => set("start", e.target.value)}
            />
          </Field>
          <Field label="To">
            <input
              className="desk-input w-[8.5rem] text-[12px]"
              type="date"
              value={form.end}
              onChange={(e) => set("end", e.target.value)}
            />
          </Field>
          <div className="pb-1 text-[11px] text-muted-foreground">
            {days == null ? (
              <span className="text-negative">end is before start</span>
            ) : tooWide ? (
              <span className="text-negative">
                {days} days — the cap is {defaults.max_days}
              </span>
            ) : (
              <>
                {days} day{days === 1 ? "" : "s"}
              </>
            )}
          </div>
        </div>

        <Field label="Pools" hint="none lit means every pool. That whole set is saved. Lighting a subset stores a different set and does not delete this one.">
          <div className="flex flex-wrap gap-1">
            {POOL_OPTS.map((p) => {
              const on = form.pools.includes(p.id);
              return (
                <button
                  key={p.id}
                  type="button"
                  data-active={on}
                  className="desk-btn"
                  onClick={() =>
                    set("pools", on ? form.pools.filter((x) => x !== p.id) : [...form.pools, p.id])
                  }
                >
                  {p.label}
                </button>
              );
            })}
            {form.pools.length > 0 && (
              <button type="button" className="desk-btn" onClick={() => set("pools", [])}>
                All
              </button>
            )}
          </div>
        </Field>

        <Coverage cov={cov} />

        {cov?.cache_root && (
          <p className="px-1 text-[11px] leading-relaxed text-muted-foreground">
            Shared cache (same folder <span className="text-foreground">3-run.bat</span> writes):{" "}
            <span className="break-all text-foreground/80">{cov.cache_root}</span>
          </p>
        )}

        {cov && (
          <div className="grid grid-cols-4 border border-border">
            <Metric label="Cached" value={cached} sub={`${missing} to build`} tone="gold" />
            <Metric label="Rows" value={cov.rows.toLocaleString()} />
            <Metric label="Turnover" value={fmtMoney(cov.turnover)} />
            <Metric
              label="Solved"
              value={`${cov.n_optimized}/${cached}`}
              sub={missingOpt ? `${missingOpt} unsolved` : "all solved"}
            />
          </div>
        )}

        {cov?.data_dir_exists === false && (
          <div className="border border-negative/40 bg-negative/5 px-3 py-2 text-[11px] text-negative">
            That folder is not visible from the API process. Check the drive is mounted, then
            press Check data.
          </div>
        )}

        <details
          className="border border-border"
          open={advanced}
          onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}
        >
          <summary className="cursor-pointer px-3 py-2 text-[12px] text-muted-foreground">
            Shaping and optimiser settings
          </summary>
          <div className="space-y-3 border-t border-border px-3 py-3">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              These change what a cached day contains, so editing one opens a new cache space
              and the window rebuilds. Everything below the fold in the workbench does not.
            </p>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Prematch window">
                <div className="flex items-center gap-1">
                  <input
                    className="desk-input w-16 text-[12px]"
                    type="number"
                    min={0}
                    max={10080}
                    disabled={form.fullSpan}
                    value={form.prematchMin}
                    onChange={(e) => set("prematchMin", Number(e.target.value))}
                  />
                  <span className="text-[11px] text-muted-foreground">min before kick-off</span>
                </div>
              </Field>
              <label className="flex items-center gap-1.5 pb-1 text-[11px] text-muted-foreground">
                <input
                  type="checkbox"
                  checked={form.fullSpan}
                  onChange={(e) => set("fullSpan", e.target.checked)}
                />
                Whole selling span
              </label>
              <Field label="Lookback">
                <input
                  className="desk-input w-14 text-[12px]"
                  type="number"
                  min={0}
                  max={14}
                  value={form.lookback}
                  onChange={(e) => set("lookback", Number(e.target.value))}
                />
              </Field>
            </div>
            <Field label="Handicap sign" hint="how line_label is read; flip if HDC replication is poor">
              <Segmented
                value={form.hdcSign}
                options={[
                  { value: "home", label: "home handicap" },
                  { value: "flip", label: "flipped" },
                ]}
                onChange={(v) => set("hdcSign", v)}
              />
            </Field>
            <Field label="Calibrators" hint="raw is always included">
              <div className="flex flex-wrap gap-1">
                {CAL_OPTS.map((c) => {
                  const on = form.cals.includes(c.id);
                  return (
                    <button
                      key={c.id}
                      type="button"
                      data-active={on}
                      className="desk-btn"
                      onClick={() =>
                        set("cals", on ? form.cals.filter((x) => x !== c.id) : [...form.cals, c.id])
                      }
                    >
                      {c.label}
                    </button>
                  );
                })}
              </div>
            </Field>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Solve every" hint="buckets">
                <input
                  className="desk-input w-14 text-[12px]"
                  type="number"
                  min={1}
                  max={200}
                  value={form.optEvery}
                  onChange={(e) => set("optEvery", Number(e.target.value))}
                />
              </Field>
              <Field label="Max buckets">
                <input
                  className="desk-input w-20 text-[12px]"
                  type="number"
                  min={1}
                  value={form.optMax}
                  onChange={(e) => set("optMax", Number(e.target.value))}
                />
              </Field>
              <Field label="Restarts">
                <input
                  className="desk-input w-14 text-[12px]"
                  type="number"
                  min={1}
                  max={5}
                  value={form.optStarts}
                  onChange={(e) => set("optStarts", Number(e.target.value))}
                />
              </Field>
            </div>
          </div>
        </details>

        {job && <JobCard job={job} showLog={showLog} onToggleLog={() => setShowLog((v) => !v)} />}
        {probe && <ProbeCard probe={probe} onClose={() => setProbe(null)} />}
        {error && (
          <div className="border border-negative/40 bg-negative/5 px-3 py-2 text-[11px] text-negative">
            {error}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
        {/* Build is the primary action only when there is something to build.
            With the window fully cached the useful next step is opening it, and
            the build button becomes an explicit rebuild. */}
        <button
          type="button"
          className="desk-btn"
          data-active={canRun && missing > 0}
          disabled={!canRun}
          title={
            missing > 0
              ? "Read the raw folders for the days that are not cached yet, then score turnover and belief"
              : "Discard these cached days and read them from the raw folders again"
          }
          onClick={() => void act("build", missing === 0)}
        >
          {running
            ? "Building…"
            : missing > 0
              ? `Build ${missing} day${missing === 1 ? "" : "s"}`
              : `Rebuild ${cached} day${cached === 1 ? "" : "s"}`}
        </button>
        <button
          type="button"
          className="desk-btn"
          disabled={!canRun || cached === 0 || missingOpt === 0}
          title="Solve TG/SUP for the cached days. The Optimize tab updates after each day. Does not change WAPE or ECE."
          onClick={() => void act("optimize")}
        >
          {missingOpt > 0 ? `Solve ${missingOpt} day${missingOpt === 1 ? "" : "s"}` : "All solved"}
        </button>
        <button
          type="button"
          className="desk-btn"
          data-active={canRun && missing === 0 && cached > 0}
          disabled={!canRun || cached === 0}
          title="Re-assemble the fact table from cached days without building anything"
          onClick={() => void act("register")}
        >
          Open cached
        </button>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            className="desk-btn"
            disabled={busy || running}
            onClick={() => void act("probe")}
          >
            Check data
          </button>
          {running ? (
            <button
              type="button"
              className="desk-btn"
              onClick={() => void api.parquetCancel(job!.id).then(({ job: j }) => setJob(j))}
            >
              Cancel
            </button>
          ) : (
            <button
              type="button"
              className="desk-btn"
              disabled={!canRun || cached === 0}
              title="Delete the cached days in this window so the next build re-reads them"
              onClick={() => void act("forget")}
            >
              Forget
            </button>
          )}
        </div>
      </div>
    </Shell>
  );
}

/* ------------------------------------------------------------------- pieces */

function Shell({
  title,
  sub,
  onClose,
  children,
}: {
  title: string;
  sub?: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <div className="text-[15px] font-medium tracking-tight">{title}</div>
          {sub && (
            <p className="mt-1 max-w-md text-[12px] leading-relaxed text-muted-foreground">{sub}</p>
          )}
        </div>
        <button type="button" className="desk-btn" onClick={onClose}>
          Close
        </button>
      </div>
      {children}
    </>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="desk-label mb-1">
        {label}
        {hint && <span className="ml-2 font-normal normal-case text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

/**
 * One cell per day. This is the whole argument for the cache in one strip:
 * gold means built, solved days carry a mark, and a hollow cell is work the
 * next Build will do. A day with no money is built and empty, which is a fact
 * about the fixture list rather than a gap.
 */
function Coverage({ cov }: { cov: ParquetCoverage | null }) {
  if (!cov) {
    return (
      <div className="border border-border px-3 py-3 text-[11px] text-muted-foreground">
        Reading the cache…
      </div>
    );
  }
  const wide = cov.days.length > 120;
  return (
    <div className="border border-border px-3 py-3">
      <div className="mb-2 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          {cov.start} → {cov.end}
        </span>
        <span className="font-mono text-[10px]">space {cov.space}</span>
      </div>
      <div className="flex flex-wrap gap-[2px]">
        {cov.days.map((d) => (
          <div
            key={d.day}
            title={
              d.state === "cached"
                ? `${d.day} · ${d.rows.toLocaleString()} rows · ${d.matches} matches · ${fmtMoney(
                    d.turnover,
                  )}${d.optimized ? ` · ${d.opt_rows.toLocaleString()} solved` : ""}`
                : `${d.day} · not built`
            }
            className={cn(
              "h-4 border",
              wide ? "w-[5px]" : "w-2.5",
              d.state === "missing" && "border-border bg-transparent",
              d.state === "cached" && d.rows === 0 && "border-border bg-muted/40",
              d.state === "cached" && d.rows > 0 && "border-gold/50 bg-gold/35",
              d.optimized && "border-iris/70",
            )}
          />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-muted-foreground">
        <Legend className="border-gold/50 bg-gold/35" label="built" />
        <Legend className="border-iris/70 bg-gold/35" label="solved" />
        <Legend className="border-border bg-muted/40" label="no money" />
        <Legend className="border-border" label="to build" />
      </div>
    </div>
  );
}

function Legend({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className={cn("inline-block h-3 w-2.5 border", className)} />
      {label}
    </span>
  );
}

function JobCard({
  job,
  showLog,
  onToggleLog,
}: {
  job: ParquetJob;
  showLog: boolean;
  onToggleLog: () => void;
}) {
  const p = job.progress ?? {};
  const total = p.total ?? 0;
  const done = p.done ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((100 * done) / total)) : 0;
  const tone =
    job.status === "error"
      ? "text-negative"
      : job.status === "done"
        ? "text-positive"
        : job.status === "cancelled"
          ? "text-warn"
          : "text-gold";
  return (
    <div className="border border-border">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-[11px]">
        <span className={cn("font-medium", tone)}>{job.status}</span>
        <span className="text-muted-foreground">{job.kind}</span>
        {total > 0 && (
          <span className="font-mono text-muted-foreground">
            {done}/{total}
          </span>
        )}
        <button type="button" className="desk-btn ml-auto" onClick={onToggleLog}>
          {showLog ? "Hide log" : "Log"}
        </button>
      </div>
      {total > 0 && (
        <div className="h-1 w-full bg-muted/40">
          <div className="h-full bg-gold/70 transition-all" style={{ width: `${pct}%` }} />
        </div>
      )}
      <div className="px-3 py-2 text-[11px] text-muted-foreground">
        {p.day ? (
          <>
            <span className="text-foreground">{p.day}</span>
            {p.rows != null && <> · {p.rows.toLocaleString()} rows</>}
            {p.opt_rows != null && <> · {p.opt_rows.toLocaleString()} solved</>}
            {p.opt_lift_pct != null && (
              <> · lift so far {(p.opt_lift_pct * 100).toFixed(2)}%</>
            )}
          </>
        ) : (
          (p.message ?? "starting")
        )}
        {job.result?.built && (
          <div className="mt-1">
            built {job.result.built.built} day(s), {job.result.built.rows.toLocaleString()} rows in{" "}
            {job.result.built.seconds}s
          </div>
        )}
        {job.error && <div className="mt-1 text-negative">{job.error}</div>}
      </div>
      {showLog && job.log.length > 0 && (
        <pre className="max-h-52 overflow-auto border-t border-border bg-background/60 px-3 py-2 font-mono text-[10px] leading-relaxed text-muted-foreground">
          {job.log.join("\n")}
        </pre>
      )}
    </div>
  );
}

function ProbeCard({ probe, onClose }: { probe: ParquetProbe; onClose: () => void }) {
  const order: Record<string, number> = { FAIL: 0, WARN: 1, PASS: 2 };
  const checks = [...probe.checks].sort(
    (a, b) => (order[a.level] ?? 3) - (order[b.level] ?? 3),
  );
  return (
    <div className="border border-border">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-[11px]">
        <span className="font-medium">Data check</span>
        <span className="font-mono text-[10px] text-muted-foreground">{probe.data_dir}</span>
        <button type="button" className="desk-btn ml-auto" onClick={onClose}>
          Hide
        </button>
      </div>
      <table className="desk-grid" data-density="compact">
        <thead>
          <tr>
            <th>Table</th>
            <th className="text-right">Files</th>
            <th className="text-right">In window</th>
            <th className="text-right">Rows</th>
            <th>Coverage</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(probe.tables).map(([name, t]) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="text-right desk-value">{t.n_files}</td>
              <td className="text-right desk-value">{t.files_in_window}</td>
              <td className="text-right desk-value">{t.rows_total.toLocaleString()}</td>
              <td className="font-mono text-[10px] text-muted-foreground">
                {t.min_time ? `${t.min_time.slice(0, 10)} → ${t.max_time?.slice(0, 10)}` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="space-y-1 border-t border-border px-3 py-2">
        {checks.map((c) => (
          <div key={c.check} className="text-[11px] leading-relaxed">
            <span
              className={cn(
                "mr-2 font-mono text-[10px]",
                c.level === "FAIL" && "text-negative",
                c.level === "WARN" && "text-warn",
                c.level === "PASS" && "text-positive",
              )}
            >
              {c.level}
            </span>
            <span className="text-foreground">{c.check}</span>
            <div className="pl-10 text-muted-foreground">{c.detail}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function msg(err: unknown) {
  return err instanceof ApiError ? err.message : String(err);
}
