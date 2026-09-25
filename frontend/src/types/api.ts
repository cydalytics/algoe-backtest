/**
 * The backend contract.
 *
 * These types mirror src/api/payload.py one field at a time. Nothing in the
 * frontend computes a price, a probability or an expected margin - every
 * number below was produced by the same Python that the optimizer ran on,
 * so what the screen shows and what the solver decided cannot diverge.
 *
 * Anything the backend could not compute arrives as null rather than being
 * dropped or defaulted, so a missing number stays visibly missing.
 */

export type Phase =
  | "prematch"
  | "first_half"
  | "half_time"
  | "second_half"
  | "et_first_half"
  | "et_half_time"
  | "et_second_half"
  | "penalty_shootout"
  | "full_time";

export type SelectionStatus = "open" | "settled" | "closed" | "suspended";

export type AlertLevel = "good" | "warn" | "bad";

export type ThetaDim =
  | "goal_tg"
  | "goal_sup"
  | "goal_fh_tg"
  | "goal_fh_sup"
  | "corner_tg"
  | "corner_sup"
  | "corner_fh_tg"
  | "corner_fh_sup";

export type Theta = Record<ThetaDim, number | null>;

export interface Rules {
  true_prob: { id: string; label: string; note: string };
  turnover: { id: string; label: string; note: string };
  theta_semantics: string;
  theta_layer: string;
  bucket_minutes: number;
  optimizer: string;
  optimized_pools: string[];
}

export interface Alert {
  level: AlertLevel;
  text: string;
}

export interface ThetaMove {
  dim: ThetaDim;
  label: string;
  now: number | null;
  star: number | null;
  delta: number | null;
  /** The solver wanted to go further but hit the tick's safety band. */
  at_limit: boolean;
}

/** One row of the board. */
export interface BoardMatch {
  match_id: number;
  home: string;
  away: string;
  league: string;
  league_code: string;
  kickoff: string | null;
  phase: Phase;
  game_state: string;
  minute: number | null;
  in_play: boolean;
  score: [number, number];
  ht_score: [number | null, number | null];
  corners: [number, number];
  theta_age_min: number | null;
  theta_now: Theta;
  theta_star: Theta | null;
  pools: number;
  selections: number;
  open: number;
  turnover_5m: number | null;
  tickets_5m: number;
  turnover: number | null;
  gm_now: number | null;
  gm_star: number | null;
  uplift: number | null;
  uplift_bps: number | null;
  legs?: number;
  skipped?: number;
  solved: boolean;
  /** True while this match is still queued behind the background solver. */
  pending: boolean;
  seconds?: number | null;
  moves: ThetaMove[];
  alerts: Alert[];
}

/** How far the background solver has got through the card. */
export interface SolveProgress {
  solved: number;
  total: number;
  solving: boolean;
}

export interface BoardTotals {
  turnover: number;
  payout_now: number;
  gm_now: number;
  margin_now: number;
  legs: number;
  matches: number;
  selections: number;
  opt_turnover?: number;
  opt_gm_now?: number;
  gm_star?: number;
  uplift?: number;
  uplift_bps?: number;
  optimized_legs?: number;
  skipped_legs?: number;
}

export interface Board {
  run_id: string;
  as_of: string | null;
  source: string;
  totals: BoardTotals;
  warnings: string[];
  timings: Record<string, number>;
  rules: Rules;
  progress: SolveProgress;
  matches: BoardMatch[];
}

/** One priceable selection, with our number beside the feed's. */
export interface Selection {
  key: string;
  pool_id: number;
  pool_code: string;
  pool_name: string;
  family: string;
  seg: "FT" | "HT";
  domain: "goal" | "corner";
  kind: string;
  optimized: boolean;
  line_id: number;
  line_label: string;
  line_value: number | null;
  comb_id: number;
  selection: string;
  sel_label: string;
  is_main_line: boolean;

  hkjc_odds: number | null;
  hkjc_odds_prev: number | null;
  hkjc_true_odds: number | null;
  odds_age_sec: number | null;
  mkt_min: number | null;
  mkt_avg: number | null;
  mkt_max: number | null;
  mkt_n: number | null;
  mkt_age_sec: number | null;

  true_prob: number | null;
  true_prob_src: string | null;
  book_sum: number | null;
  model_prob: number | null;
  fair_odds: number | null;
  sell_odds: number | null;
  status: SelectionStatus;
  edge_hkjc: number | null;
  edge_mkt: number | null;
  exp_gm_unit: number | null;

  t5m: number | null;
  tickets5m: number | null;
  avg_stake: number | null;
  t_hat: number | null;
  t_hat_src: string | null;
  t_share: number | null;
  t_trend: number | null;
  invested: number | null;
  tickets_today: number | null;

  model_prob_star: number | null;
  fair_odds_star: number | null;
  sell_odds_star: number | null;
  status_star: SelectionStatus | null;
  exp_gm_unit_star: number | null;
  odds_delta: number | null;
}

export interface BookLine {
  line_id: number;
  line_label: string | null;
  line_value: number | null;
  is_main_line: boolean;
  book_sum: number | null;
  turnover: number | null;
  selections: Selection[];
}

export interface BookPool {
  pool_code: string;
  pool_name: string | null;
  domain: string | null;
  seg: string | null;
  kind: string | null;
  optimized: boolean;
  margin: number | null;
  turnover: number | null;
  open: number;
  lines: BookLine[];
}

export interface BookFamily {
  family: string;
  turnover: number | null;
  pools: BookPool[];
}

export interface Book {
  families: BookFamily[];
  totals: { selections: number; open: number; turnover: number | null };
}

export interface BlockResult {
  block: "goal" | "corner";
  dims: ThetaDim[];
  legs: number;
  starts: number;
  success: boolean;
  message: string;
  seconds: number | null;
  gm_now: number | null;
  gm_star: number | null;
  uplift: number | null;
}

export interface PoolAttribution {
  pool_code: string;
  family: string;
  turnover: number;
  payout_now: number;
  payout_star: number;
  legs: number;
  gm_now: number;
  gm_star: number;
  uplift: number;
}

export interface GmCurve {
  x: number[];
  gm: number[];
  now: number;
  star: number;
}

export interface OptimizerResult {
  theta_now: Theta;
  theta_star: Theta;
  turnover: number | null;
  payout_now: number | null;
  payout_star: number | null;
  gm_now: number | null;
  gm_star: number | null;
  uplift: number | null;
  uplift_bps: number | null;
  legs: number;
  skipped: number;
  seconds: number | null;
  moves: ThetaMove[];
  /** Parameters the feed sent in a state no book can be built from. */
  repaired: ThetaRepair[];
  blocks: BlockResult[];
  per_pool: PoolAttribution[];
  curves: Partial<Record<ThetaDim, GmCurve>>;
}

export interface ThetaRepair {
  dim: ThetaDim;
  label: string;
  from: number | null;
  to: number | null;
}

export interface DimSpec {
  dim: ThetaDim;
  label: string;
  bounds: [number, number];
  max_move: number;
  domain: "goal" | "corner";
}

export interface MatchHeader {
  match_id: number;
  home: string;
  away: string;
  league: string;
  league_code: string;
  kickoff: string | null;
  frontend_id: string;
  phase: Phase;
  game_state: string;
  minute: number | null;
  in_play: boolean;
  ht_done: boolean;
  score: [number, number];
  ht_score: [number | null, number | null];
  corners: [number, number];
  ht_corners: [number | null, number | null];
}

export interface MatchEvent {
  at: string | null;
  type: string;
  side: string;
  detail: string;
  provider_id: number | null;
}

export interface Quote {
  key: string;
  label: string;
  bookmaker: string | null;
  odds: number | null;
  age_sec: number;
}

export interface HistoryPoint {
  at: string | null;
  odds: number | null;
  true_odds: number | null;
}

export interface Cockpit {
  run_id: string;
  as_of: string | null;
  source: string;
  /** No recommendation on this tick yet. */
  pending: boolean;
  /** ...and the background solver is still working towards one. */
  solving: boolean;
  rules: Rules;
  match: MatchHeader;
  theta: {
    layers: Record<string, Theta>;
    layer: string;
    age_min: number | null;
    splits: Record<string, number | null>;
    semantics: string;
    dims: DimSpec[];
  };
  optimizer: OptimizerResult | null;
  book: Book;
  events: MatchEvent[];
  quotes: { books: string[]; rows: Quote[] };
  history: Record<string, HistoryPoint[]>;
}

export interface WhatIfSelection {
  key: string;
  pool_code: string;
  line_label: string;
  sel_label: string;
  hkjc_odds: number | null;
  sell_odds: number | null;
  sell_odds_what: number | null;
  model_prob_what: number | null;
  status_what: SelectionStatus | null;
  exp_gm_unit_what: number | null;
  t_hat: number | null;
}

export interface ExpectedGm {
  turnover: number;
  payout: number;
  gm: number;
  margin_pct: number;
  legs: number;
}

export interface WhatIf {
  match_id: number;
  theta: Theta;
  expected: ExpectedGm;
  baseline: ExpectedGm;
  selections: WhatIfSelection[];
}

export interface Health {
  status: "ok" | "degraded";
  source: string;
  tick_age_seconds: number;
  stale: boolean;
  last_error: string | null;
}

/** W06 backtest report — mirrors src/backtest/runner.py labs. */
export type OptimizeMode = "none" | "star" | "all";

export interface CutRow {
  key: string;
  n: number;
  [k: string]: string | number | undefined;
}

export interface CalBin {
  lo: number;
  hi: number;
  n: number;
  p_hat: number | null;
  p_obs: number | null;
}

export interface PlotPoint {
  x?: number;
  y?: number;
  n?: number;
  key?: string;
  value?: number;
  mae?: number;
  pred?: number;
  actual?: number;
  bias?: number;
  exp?: number;
  lo?: number;
  hi?: number;
  p_hat?: number | null;
  p_obs?: number | null;
  odds?: number;
}

export interface TurnoverPack {
  n: number;
  mae: number;
  mse?: number;
  rmse: number;
  mape_pct: number;
  mdape_pct?: number;
  smape_pct?: number;
  wape_pct?: number;
  wmae?: number;
  mean_diff: number;
  bias_pct: number;
  sum_pred: number;
  sum_actual: number;
  r2?: number;
  pearson?: number;
  spearman?: number;
  theil_u?: number;
  residual_std?: number;
  residual_skew?: number;
  share_mae?: number;
  cosine?: number;
  top10_overlap?: number;
  hit_precision?: number;
  hit_recall?: number;
  match_mae?: number;
  deciles: { decile: number; n: number; mean_pred: number; mean_actual: number; bias: number }[];
  by_phase: CutRow[];
  by_pool: CutRow[];
  by_family: CutRow[];
  plots?: Record<string, PlotPoint[]>;
}

export interface TrueOddsPack {
  n: number;
  logloss: number;
  brier: number;
  accuracy?: number;
  spherical?: number;
  bias: number;
  mean_prob: number;
  mean_y: number;
  ece?: number;
  mce?: number;
  ace?: number;
  sharpness?: number;
  auc?: number;
  cal_slope?: number;
  cal_intercept?: number;
  brier_reliability?: number;
  brier_resolution?: number;
  brier_uncertainty?: number;
  book_abs?: number;
  book_bad_pct?: number;
  calibration: CalBin[];
  by_phase: CutRow[];
  by_pool: CutRow[];
  by_family: CutRow[];
  plots?: Record<string, unknown>;
}

export interface GmPack {
  n: number;
  n_settled: number;
  exp_gm_now: number;
  exp_gm_star: number;
  exp_lift: number;
  realized_gm_now: number;
  realized_gm_star: number;
  realized_gm?: number;
  realized_lift: number;
  realized_lift_bps: number;
  optimism?: number;
  hit_rate?: number;
  tick_vol?: number;
  cvar_5?: number;
  worst_tick?: number;
  days_better: number;
  days_worse: number;
  days_tie: number;
  ticks_better: number;
  ticks_worse: number;
  ticks_tie: number;
  by_phase: CutRow[];
  by_pool: CutRow[];
  by_family: CutRow[];
  by_day: CutRow[];
  plots?: Record<string, PlotPoint[]>;
}

export interface AlgoDiag {
  algo?: string;
  n_solved?: number;
  n_failed?: number;
  seconds?: number;
  mean_abs_theta?: number;
  n_repaired?: number;
  exp_uplift?: number;
  n_ticks?: number;
}

export interface BacktestCombo {
  id: string;
  turnover_model?: string;
  true_prob_source?: string;
  calibrator?: string;
  algo?: string;
  policy?: string;
  elasticity?: number;
  optimized?: boolean;
  turnover_label?: string;
  true_prob_label?: string;
  calibrator_label?: string;
  algo_label?: string;
  policy_label?: string;
  step?: string;
  delta?: number;
  n_rows?: number;
  turnover: TurnoverPack;
  true_odds: TrueOddsPack;
  gm: GmPack;
  diag?: AlgoDiag;
}

export interface BacktestLab {
  axis: string;
  baseline?: string;
  leader?: string | null;
  candidates: BacktestCombo[];
}

export interface LeaderRow {
  id: string;
  turnover?: string;
  true_prob?: string;
  metric: string;
  value: number;
  n: number;
}

export interface BacktestReport {
  run_id: string;
  created_at: string;
  mode?: string;
  config: {
    seed: number;
    n_matches: number;
    n_ticks: number;
    start: string;
    turnover: string[];
    true_prob: string[];
    calibrators?: string[];
    algos?: string[];
    policies?: string[];
    elasticities?: number[];
    optimize: OptimizeMode;
    solved_combos: string[];
    bucket_minutes: number;
  };
  n_ticks: number;
  n_matches: number;
  n_rows: number;
  seconds: number;
  labs?: {
    turnover: BacktestLab;
    true_prob: BacktestLab;
    optimizer: BacktestLab;
    policy: BacktestLab;
  };
  attribution?: BacktestCombo[];
  cross?: {
    hint?: string;
    heatmap?: { rows: string[]; cols: string[]; cells: { row: string; col: string; value: number | null }[] };
    turnover_mae?: { key: string; value: number }[];
    logloss?: { key: string; value: number }[];
  };
  combos: BacktestCombo[];
  leaderboard: {
    turnover: LeaderRow[];
    true_odds: LeaderRow[];
    gm: LeaderRow[];
  };
  // A parquet run names no winner and runs no labs, so the fields a sweep fills
  // are optional here rather than absent from the type.
  headline: {
    best_turnover: string | null;
    best_true_odds: string | null;
    best_algo?: string | null;
    best_gm?: string | null;
    days_better: number;
    days_worse: number;
    ticks_better: number;
    ticks_worse: number;
    mvp?: string;
    mvp_turnover_mae?: number;
    mvp_logloss?: number;
    mvp_ece?: number;
    mvp_realized_gm?: number;
    mvp_realized_lift?: number;
    attribution_delta?: number;
    // parquet only
    days?: number;
    rows?: number;
    settled_rows?: number;
    turnover?: number;
    matches?: number;
    optimized_days?: number;
    opt_lift_pct?: number | null;
    opt_margin?: number | null;
    board_margin?: number | null;
    persist_wape?: number | null;
    persist_mae?: number | null;
    market_wape?: number | null;
    money_ece?: number | null;
    money_log_loss?: number | null;
    realized_margin?: number | null;
    expected_margin?: number | null;
  };
  catalog: {
    turnover: Record<string, string>;
    true_prob: Record<string, string>;
    calibrators?: Record<string, string>;
    algos?: Record<string, string>;
    policies?: Record<string, string>;
  };
  n_fact_rows?: number;
  n_days?: number;
  workbench?: {
    has_facts?: boolean;
    baselines?: {
      turnover: string;
      true_prob: string;
      calibrator: string;
      algo: string;
    };
    turnover?: string[];
    beliefs?: string[];
    algos?: string[];
    filters?: Record<string, string[]>;
  };
  // present only on mode === "parquet"
  coverage?: ParquetCoverage;
  window?: ParquetWindow;
  accuracy?: ParquetAccuracy | null;
  selection?: { turnover: string; belief: string; objective: string };
  optimizer?: ParquetOptimizer | null;
  optimizer_live?: {
    solving: boolean;
    day?: string | null;
    done?: number;
    total?: number;
    opt_rows?: number | null;
  } | null;
  verdict?: ParquetVerdict[];
}

/* ------------------------------------------------------------------ parquet */

export interface ParquetDayState {
  day: string;
  state: "cached" | "missing";
  rows: number;
  turnover: number;
  matches: number;
  optimized: boolean;
  opt_rows: number;
  built_at: string | null;
}

export interface ParquetCoverage {
  space: string;
  engine: number;
  start: string;
  end: string;
  days: ParquetDayState[];
  n_days: number;
  n_cached: number;
  n_missing: number;
  n_optimized: number;
  rows: number;
  turnover: number;
  config: Record<string, unknown>;
  missing_opt_days?: string[];
  data_dir_exists?: boolean;
  cache_root?: string;
  runs?: { run_id: string; created_at: string; n_rows: number; start?: string; end?: string }[];
}

export interface ParquetWindow {
  start: string;
  end: string;
  days_scored: string[];
  days_empty: string[];
  days_trimmed: string[];
  settled_rows: number;
  turnover: number;
  memory_mb?: number;
}

export interface ParquetVerdict {
  topic: string;
  verdict: string;
  detail: string;
}

export interface AccErr {
  n: number;
  mae: number | null;
  rmse: number | null;
  wape: number | null;
  bias: number | null;
  bias_pct: number | null;
  sum_actual: number;
  sum_forecast: number;
}

export interface AccCalBin {
  bin: number;
  lo: number;
  hi: number;
  n: number;
  p_mean: number | null;
  y_mean: number | null;
  weight: number;
  y_mean_money: number | null;
}

export interface AccBelief {
  n: number;
  log_loss: number | null;
  brier: number | null;
  mean_signed_err: number | null;
  money_log_loss: number | null;
  money_brier: number | null;
  money_signed_err: number | null;
  weight: number;
}

export interface AccGm {
  n: number;
  turnover: number;
  dividend: number;
  realized_gm: number;
  expected_gm: number;
  realized_margin: number | null;
  expected_margin: number | null;
  margin_gap: number | null;
  settled_turnover: number;
}

export interface ParquetAccuracy {
  coverage: {
    rows: number;
    days: number;
    matches: number;
    pools: string[];
    turnover: number;
    money_rows: number;
    settled_rows: number;
    settled_selections: number;
  };
  turnover: {
    models: Record<string, AccErr>;
    by_slice: Array<AccErr & { dim: string; value: string; model: string }>;
    resid_hist: Record<string, { edges: number[]; counts: number[] }>;
    bucket_level: Record<string, { n: number; mae: number | null; wape: number | null; bias_pct: number | null }>;
    scatter: Array<{ turnover?: number; f_persist?: number; f_ema?: number; pool_name?: string; clock_bin?: string }>;
  };
  belief: {
    models: Record<string, AccBelief>;
    calibration: Record<string, { bins: AccCalBin[]; ece: number | null; money_ece: number | null }>;
    book_sum_hist: { edges: number[]; counts: number[] };
    by_slice?: Array<AccBelief & { dim: string; value: string }>;
    scatter: Array<{ p_true?: number; p_sell_norm?: number; y_frac?: number; turnover?: number }>;
  };
  gm: {
    overall: AccGm;
    by_slice: Array<AccGm & { dim: string; value: string }>;
  };
  daily: Array<{
    day: string;
    rows: number;
    matches: number;
    turnover: number;
    forecast_persist: number;
    realized_gm: number;
    expected_gm: number;
    settled_turnover: number;
  }>;
  verdict: ParquetVerdict[];
}

/** One E[GM] comparison, summed over a set of buckets. */
export interface OptAgg {
  buckets: number;
  turnover_forecast: number;
  realized_turnover: number;
  egm_opt: number;
  egm_hkjc_theta: number;
  egm_actual_odds: number;
  realized_gm: number;
  margin_opt: number | null;
  margin_hkjc_theta: number | null;
  margin_actual_odds: number | null;
  margin_realized: number | null;
  lift_vs_actual: number;
  lift_vs_actual_pct: number | null;
  lift_from_pricer: number;
  lift_from_theta: number;
  buckets_better: number;
  buckets_worse: number;
}

export interface OptHist {
  edges: number[];
  counts: number[];
}

export interface OptThetaDomain {
  n: number;
  tg_hkjc_mean: number | null;
  tg_opt_mean: number | null;
  sup_hkjc_mean: number | null;
  sup_opt_mean: number | null;
  d_tg_mean: number | null;
  d_tg_mae: number | null;
  d_sup_mean: number | null;
  d_sup_mae: number | null;
  d_tg_hist?: OptHist;
  d_sup_hist?: OptHist;
  scatter?: { x: number; y: number }[];
}

export interface OptPriceSet {
  n: number;
  mean_rel: number;
  median_rel: number;
  mae_rel: number;
  shorter_share?: number;
  hist?: OptHist;
  by_pool?: { pool_name: string; n: number; mae_rel: number; mean_rel: number }[];
}

export interface ParquetOptimizer {
  coverage: {
    buckets_seen: number;
    buckets_scored: number;
    solver_ok: number | null;
    solver_fallback: number | null;
    matches: number;
    sampling: string;
    demand: string;
    scipy: boolean;
    hdc_sign: string;
  };
  totals: Record<"all" | "prematch" | "inplay" | string, OptAgg>;
  by_slice: (OptAgg & { dim: string; value: string })[];
  daily: (OptAgg & { day: string })[];
  theta: Record<"goal" | "corner" | string, OptThetaDomain>;
  prices: {
    n: number;
    replication: OptPriceSet;
    opt_vs_actual: OptPriceSet;
    scatter?: { x: number; y: number }[];
  };
  scatter?: { x: number; y: number }[];
  verdict: ParquetVerdict[];
}

export interface ParquetJob {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  request: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress: {
    stage?: string;
    message?: string;
    day?: string;
    month?: string;
    rows?: number;
    turnover?: number;
    opt_rows?: number | null;
    done?: number;
    total?: number;
    n_optimized?: number;
    opt_lift_pct?: number | null;
    published?: boolean;
  };
  result: {
    built?: { built: number; rows: number; turnover: number; seconds: number };
    optimizer?: { built: number; rows: number; seconds: number } | null;
    run_id?: string;
    rows?: number;
    days?: number;
    solved_days?: number;
  } | null;
  error: string | null;
  run_id: string | null;
  log: string[];
  cancel_requested: boolean;
}

export interface ParquetDefaults {
  data_dir: string;
  start: string;
  end: string;
  pools: string[];
  bucket_minutes: number;
  prematch_window_min: number;
  lookback_days: number;
  true_prob_source: string;
  calibrators: string[];
  optimize_every: number;
  optimize_demand: string;
  hdc_sign: string;
  engine: number;
  max_days: number;
  cache_root: string;
  choices?: {
    turnover: { id: string; label: string }[];
    belief: { id: string; label: string }[];
    objective: { id: string; label: string }[];
  };
  turnover_labels: Record<string, string>;
  belief_labels: Record<string, string>;
}

export interface ParquetRequest {
  start: string;
  end: string;
  data_dir?: string;
  pools?: string[];
  bucket_minutes?: number;
  prematch_window_min?: number;
  full_span?: boolean;
  lookback_days?: number;
  true_prob_source?: string;
  normalize_book?: boolean;
  optimize?: boolean;
  optimize_every?: number;
  optimize_max_buckets?: number;
  optimize_starts?: number;
  optimize_demand?: string;
  hdc_sign?: string;
  calibrators?: string[];
  rebuild?: boolean;
  turnover_model?: string;
  belief_model?: string;
  objective?: string;
  solve?: boolean;
}

export interface ParquetProbeTable {
  path: string;
  exists: boolean;
  n_files: number;
  files_in_window: number;
  rows_total: number;
  columns_raw: string[];
  canonical: Record<string, string>;
  missing_canonical: string[];
  min_time: string | null;
  max_time: string | null;
  sample: Record<string, unknown>[];
}

export interface ParquetProbe {
  data_dir: string;
  window: [string, string];
  tables: Record<string, ParquetProbeTable>;
  checks: { level: "PASS" | "WARN" | "FAIL" | string; check: string; detail: string }[];
}

export interface BacktestRunRow {
  run_id: string;
  created_at: string;
  seed: number;
  n_ticks: number;
  n_matches: number;
  n_rows: number;
  n_combos: number;
  optimized: number;
  best_turnover: string | null;
  best_true_odds: string | null;
  days_better: number | null;
  days_worse: number | null;
}

export interface FacetValue {
  value: string;
  n: number;
}

export interface SliceCompareRow {
  id: string;
  label: string;
  baseline?: boolean;
  selected?: boolean;
  n: number;
  mae?: number;
  mse?: number;
  rmse?: number;
  wape_pct?: number;
  r2?: number;
  theil_u?: number;
  logloss?: number;
  brier?: number;
  accuracy?: number;
  ece?: number;
  auc?: number;
  bias?: number;
  exp_gm_star?: number;
  exp_lift?: number;
  realized_gm?: number;
  realized_lift?: number;
  optimism?: number;
}

export interface SliceBreakdownRow {
  key: string;
  n: number;
  mae: number;
  mse: number;
  rmse: number;
  wape_pct: number;
  logloss: number;
  brier: number;
  accuracy: number;
  ece: number;
  exp_gm_star: number;
  realized_gm: number;
  realized_lift: number;
}

export interface SliceGallery {
  mae_by?: Record<string, { key: string; value: number; n?: number }[]>;
  logloss_by?: Record<string, { key: string; value: number; n?: number }[]>;
  accuracy_by?: Record<string, { key: string; value: number; n?: number }[]>;
  gm_by?: Record<string, { key: string; value: number; n?: number }[]>;
  exp_gm_by?: Record<string, { key: string; value: number; n?: number }[]>;
  tg_sup_mae?: { rows: string[]; cols: string[]; cells: { row: string; col: string; value: number | null }[] };
  tg_sup_logloss?: { rows: string[]; cols: string[]; cells: { row: string; col: string; value: number | null }[] };
  tg_sup_gm?: { rows: string[]; cols: string[]; cells: { row: string; col: string; value: number | null }[] };
  clock_mae?: { key: string; value: number; n?: number }[];
  clock_logloss?: { key: string; value: number; n?: number }[];
  clock_gm?: { key: string; value: number; n?: number }[];
}

export interface BacktestSlice {
  n_rows: number;
  n_full: number;
  stack: {
    turnover: string;
    turnover_label: string;
    true_prob: string;
    true_prob_label: string;
    calibrator: string;
    calibrator_label: string;
    algo: string;
    algo_label: string;
    baseline: { turnover: boolean; true_prob: boolean; algo: boolean };
  };
  filters: Record<string, string[]>;
  group_by: string;
  models: { turnover: string[]; beliefs: string[]; algos: string[] };
  facets: Record<string, FacetValue[]>;
  turnover: TurnoverPack;
  true_odds: TrueOddsPack;
  gm: GmPack;
  compare: {
    turnover: SliceCompareRow[];
    belief: SliceCompareRow[];
    algo: SliceCompareRow[];
  };
  breakdown: SliceBreakdownRow[];
  gallery: SliceGallery;
  note?: string;
}

export interface SliceRequest {
  turnover: string;
  true_prob: string;
  calibrator: string;
  algo: string;
  group_by: string;
  filters: Record<string, string[]>;
}

export interface StackPick {
  turnover: string;
  true_prob: string;
  calibrator: string;
  algo: string;
}

export interface CompareDeltaRow {
  key: string;
  label: string;
  family: "turnover" | "belief" | "gm" | string;
  a: number;
  b: number;
  delta: number;
  lower_better: boolean;
  winner: "left" | "right" | "tie";
}

export interface CompareCutRow {
  key: string;
  n: number;
  turnover?: number;
  wape_a?: number;
  wape_b?: number;
  d_wape?: number | null;
  bias_a?: number;
  bias_b?: number;
  mae_a?: number;
  mae_b?: number;
  d_mae?: number | null;
  logloss_a?: number;
  logloss_b?: number;
  d_logloss?: number | null;
  accuracy_a?: number;
  accuracy_b?: number;
  d_accuracy?: number | null;
  exp_gm_a?: number;
  exp_gm_b?: number;
  d_exp_gm?: number | null;
  realized_gm_a?: number;
  realized_gm_b?: number;
  d_realized_gm?: number | null;
}

export interface CompareDists {
  t_hat?: OverlayBin[];
  residual?: OverlayBin[];
  abs_residual?: OverlayBin[];
  true_prob?: OverlayBin[];
  p_minus_y?: OverlayBin[];
  t_hat_cdf?: { a: { x: number; y: number }[]; b: { x: number; y: number }[] };
  residual_cdf?: { a: { x: number; y: number }[]; b: { x: number; y: number }[] };
  abs_residual_cdf?: { a: { x: number; y: number }[]; b: { x: number; y: number }[] };
  true_prob_cdf?: { a: { x: number; y: number }[]; b: { x: number; y: number }[] };
  qq_abs_residual?: { x: number; y: number }[];
  qq_true_prob?: { x: number; y: number }[];
  paired_abs_err?: { lo: number; hi: number; n: number }[];
  row_wins?: { n: number; left: number; right: number; tie: number; mean_abs_delta: number };
}

export interface OverlayBin {
  lo: number;
  hi: number;
  a: number;
  b: number;
  a_density?: number;
  b_density?: number;
}

export interface CompareSide {
  stack: BacktestSlice["stack"] & { id?: string };
  turnover: TurnoverPack;
  true_odds: TrueOddsPack;
  gm: GmPack;
  breakdown: SliceBreakdownRow[];
}

export interface CompareDay {
  day: string;
  wape_a: number;
  wape_b: number;
  diff: number;
  turnover: number;
  bias_a: number;
  bias_b: number;
  n: number;
}

/** Day-level paired test of B against A on WAPE (percentage points). */
export interface CompareDaily {
  n_days: number;
  wape_a?: number;
  wape_b?: number;
  skill?: number | null;
  mean_diff?: number;
  se?: number | null;
  dm_stat?: number | null;
  ci95?: [number, number] | null;
  b_better_days?: number;
  a_better_days?: number;
  days: CompareDay[];
}

export interface LevelBin {
  pred: number;
  actual: number;
  n: number;
  money: number;
}

export interface BacktestCompare {
  n_rows: number;
  n_full: number;
  n_unscored?: number;
  filters: Record<string, string[]>;
  group_by: string;
  models: { turnover: string[]; beliefs: string[]; algos: string[] };
  facets: Record<string, FacetValue[]>;
  left: CompareSide;
  right: CompareSide;
  delta: CompareDeltaRow[];
  dists: CompareDists;
  daily?: CompareDaily;
  level?: { a: LevelBin[]; b: LevelBin[] };
  cuts: CompareCutRow[];
  note?: string;
}

export interface CompareRequest {
  left: StackPick;
  right: StackPick;
  group_by: string;
  filters: Record<string, string[]>;
}

export interface BacktestRequest {
  seed?: number;
  n_matches: number;
  n_ticks: number;
  turnover: string[];
  true_prob: string[];
  calibrators?: string[];
  algos?: string[];
  policies?: string[];
  elasticities?: number[];
  optimize: OptimizeMode;
}
