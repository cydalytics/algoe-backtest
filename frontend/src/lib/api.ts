/**
 * Typed client for the Algo E backend.
 *
 * Everything the UI displays comes through here. There is no fallback to
 * demo data: if the backend is down the screen says so, because a trading
 * board that quietly invents numbers is worse than one that admits it has
 * none.
 *
 * Requests are same-origin: next.config.ts proxies /api/* through to
 * FastAPI, so the API's address is a run-time setting on the server rather
 * than a value baked into this bundle at build time. Set
 * NEXT_PUBLIC_API_BASE only to bypass that proxy and call the API directly.
 */

import type {
  BacktestReport,
  BacktestRunRow,
  BacktestCompare,
  BacktestSlice,
  CompareRequest,
  SliceRequest,
  Board,
  Cockpit,
  GmCurve,
  Health,
  ParquetCoverage,
  ParquetDefaults,
  ParquetJob,
  ParquetProbe,
  ParquetRequest,
  ThetaDim,
  WhatIf,
} from "@/types/api";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly url: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  let res: Response;
  try {
    res = await fetch(url, { ...init, cache: "no-store" });
  } catch {
    // fetch only rejects when the request never reached the server, so this
    // is the "is the backend actually running" case and deserves saying so.
    throw new ApiError(
      `cannot reach the API at ${API_BASE || "/api"} — is the backend running?`,
      0,
      url,
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* a non-JSON error body is not worth a second failure */
    }
    throw new ApiError(detail, res.status, url);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  board: (refresh = false) =>
    request<Board>(`/api/desk${refresh ? "?refresh=true" : ""}`),

  cockpit: (matchId: number | string, refresh = false) =>
    request<Cockpit>(`/api/desk/${matchId}${refresh ? "?refresh=true" : ""}`),

  /**
   * Solve one match now instead of waiting for the background queue.
   * Opening a cockpit is the trader saying this is the one they care
   * about, so it should not sit behind the rest of the card.
   */
  solve: (matchId: number | string) =>
    request<Cockpit>(`/api/desk/${matchId}/solve`, { method: "POST" }),

  curves: (matchId: number | string) =>
    request<{
      match_id: number;
      turnover: number | null;
      curves: Partial<Record<ThetaDim, GmCurve>>;
    }>(`/api/desk/${matchId}/curves`),

  tick: () => request<Board>("/api/tick", { method: "POST" }),

  /** Price one match at an arbitrary theta - the cockpit's what-if. */
  price: (matchId: number, theta: Partial<Record<ThetaDim, number>>, pools?: string[]) =>
    request<WhatIf>("/api/price", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ match_id: matchId, theta, pools }),
    }),

  backtestLatest: () => request<{ report: BacktestReport | null }>("/api/backtest/latest"),

  backtestGet: (runId: string) =>
    request<{ report: BacktestReport }>(`/api/backtest/${encodeURIComponent(runId)}`),

  backtestList: () => request<{ runs: BacktestRunRow[] }>("/api/backtest"),

  runBacktest: (body: {
    seed?: number;
    n_matches: number;
    n_ticks: number;
    turnover?: string[];
    true_prob?: string[];
    calibrators?: string[];
    algos?: string[];
    policies?: string[];
    elasticities?: number[];
    optimize: "none" | "star" | "all";
  }) =>
    request<{ report: BacktestReport }>("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  backtestSlice: (runId: string, body: SliceRequest) =>
    request<{ run_id: string; slice: BacktestSlice }>(
      `/api/backtest/${encodeURIComponent(runId)}/slice`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),

  backtestCompare: (runId: string, body: CompareRequest) =>
    request<{ run_id: string; compare: BacktestCompare }>(
      `/api/backtest/${encodeURIComponent(runId)}/compare`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),

  /* --------------------------------------------------------------- parquet
   * Real-data runs. Building is a job because it reads months of parquet and
   * takes longer than a request should: /build returns a job id, and the page
   * polls /jobs/{id} until it settles. Once a run is registered the backtest*
   * calls above serve it, so switching model never comes back here.
   */
  parquetDefaults: () => request<ParquetDefaults>("/api/parquet/defaults"),

  parquetProbe: (body: ParquetRequest) => post<ParquetProbe>("/api/parquet/probe", body),

  parquetCoverage: (body: ParquetRequest) =>
    post<{ coverage: ParquetCoverage; job: ParquetJob | null }>("/api/parquet/coverage", body),

  parquetBuild: (body: ParquetRequest) =>
    post<{ job: ParquetJob }>("/api/parquet/build", body),

  parquetOptimize: (body: ParquetRequest) =>
    post<{ job: ParquetJob }>("/api/parquet/optimize", body),

  parquetRegister: (body: ParquetRequest) =>
    post<{ job: ParquetJob }>("/api/parquet/register", body),

  parquetOpen: (body: ParquetRequest) =>
    post<{ job: ParquetJob; run_id: string }>("/api/parquet/open", body),

  parquetForget: (body: ParquetRequest) =>
    post<{ dropped: number; coverage: ParquetCoverage }>("/api/parquet/forget", body),

  parquetJob: (jobId: string) =>
    request<{ job: ParquetJob }>(`/api/parquet/jobs/${encodeURIComponent(jobId)}?log_tail=200`),

  parquetJobs: () => request<{ jobs: ParquetJob[]; current: string | null }>("/api/parquet/jobs"),

  parquetCancel: (jobId: string) =>
    request<{ job: ParquetJob }>(`/api/parquet/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    }),
};

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
