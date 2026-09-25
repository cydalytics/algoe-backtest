"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";

export const FILTER_META: { id: string; label: string }[] = [
  { id: "period", label: "Scope" },
  { id: "half", label: "Half" },
  { id: "clock_bin", label: "Clock" },
  // parquet runs carry a finer clock that splits the hours before kick-off
  { id: "clock_fine", label: "Clock+" },
  { id: "event_window", label: "Event" },
  { id: "family", label: "Family" },
  { id: "pool_code", label: "Pool" },
  { id: "selection", label: "Selection" },
  { id: "domain", label: "Domain" },
  { id: "seg", label: "Seg" },
  { id: "league", label: "League" },
  { id: "competition", label: "Comp" },
  { id: "tg_bin", label: "TG" },
  { id: "sup_bin", label: "SUP" },
  { id: "tg_sup", label: "TG×SUP" },
  { id: "odds_band", label: "Odds" },
  { id: "kind", label: "Kind" },
  // whether the selection took money last bucket; persistence scores lag=0 as zero
  { id: "lag_state", label: "Last 5m" },
];

export type FilterFacet = { value: string; n: number };
export type PickOpt = { id: string; label: string; baseline?: boolean; group?: string };
export type ModelStack = {
  turnover: string;
  true_prob: string;
  calibrator: string;
  algo: string;
};

export const GROUP_OPTS = [
  { value: "clock_bin", label: "Clock" },
  { value: "clock_fine", label: "Clock+" },
  { value: "half", label: "Half" },
  { value: "period", label: "Scope" },
  { value: "event_window", label: "Event" },
  { value: "pool_code", label: "Pool" },
  { value: "family", label: "Family" },
  { value: "selection", label: "Selection" },
  { value: "league", label: "League" },
  { value: "competition", label: "Comp" },
  { value: "tg_bin", label: "TG" },
  { value: "sup_bin", label: "SUP" },
  { value: "tg_sup", label: "TG×SUP" },
  { value: "odds_band", label: "Odds" },
  { value: "domain", label: "Domain" },
  { value: "seg", label: "Seg" },
  { value: "phase", label: "Phase" },
  { value: "lag_state", label: "Last 5m" },
  { value: "day", label: "Day" },
];

export function StackField({
  label,
  value,
  options,
  onChange,
  tone = "gold",
}: {
  label: string;
  value: string;
  options: PickOpt[];
  onChange: (id: string) => void;
  tone?: "gold" | "iris";
}) {
  return (
    <label className="flex min-w-0 items-center gap-1.5">
      <span className="shrink-0 text-[11px] text-muted-foreground">{label}</span>
      <select
        className={cn(
          "desk-input h-8 min-w-[7.25rem] max-w-[11rem] text-[12px]",
          tone === "iris" && "border-iris/45",
          tone === "gold" && "border-gold/35",
        )}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {groupedOptions(options).map((block) =>
          block.label ? (
            <optgroup key={block.label} label={block.label}>
              {block.items.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </optgroup>
          ) : (
            block.items.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))
          ),
        )}
      </select>
    </label>
  );
}

function groupedOptions(options: PickOpt[]): { label?: string; items: PickOpt[] }[] {
  const named = options.filter((o) => o.group);
  if (!named.length) return [{ items: options }];
  const order: string[] = [];
  const buckets = new Map<string, PickOpt[]>();
  for (const o of options) {
    const key = o.group ?? "";
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key)!.push(o);
  }
  return order.map((key) => ({ label: key || undefined, items: buckets.get(key)! }));
}

export function StackFields({
  stack,
  onChange,
  tOpts,
  pOpts,
  cOpts,
  aOpts,
  tone = "gold",
  simple = false,
}: {
  stack: ModelStack;
  onChange: (next: ModelStack) => void;
  tOpts: PickOpt[];
  pOpts: PickOpt[];
  cOpts: PickOpt[];
  aOpts: PickOpt[];
  tone?: "gold" | "iris";
  simple?: boolean;
}) {
  const hideCal = simple || cOpts.length <= 1;
  const hideAlgo = simple || aOpts.length <= 1;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <StackField
        label="Next 5 min $"
        value={stack.turnover}
        options={tOpts}
        tone={tone}
        onChange={(id) => onChange({ ...stack, turnover: id })}
      />
      <StackField
        label="True prob"
        value={stack.true_prob}
        options={pOpts}
        tone={tone}
        onChange={(id) => onChange({ ...stack, true_prob: id })}
      />
      {!hideCal && (
        <StackField
          label="Calibrate"
          value={stack.calibrator}
          options={cOpts}
          tone={tone}
          onChange={(id) => onChange({ ...stack, calibrator: id })}
        />
      )}
      {!hideAlgo && (
        <StackField
          label="Odds"
          value={stack.algo}
          options={aOpts}
          tone={tone}
          onChange={(id) => onChange({ ...stack, algo: id })}
        />
      )}
    </div>
  );
}

export function FilterChips({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: string; n: number }[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  if (!options.length) return null;
  const shown = options.slice(0, 16);
  return (
    <div>
      <div className="desk-label mb-1">{label}</div>
      <div className="flex flex-wrap gap-1">
        <button
          type="button"
          data-active={value.length === 0}
          className="desk-btn"
          onClick={() => onChange([])}
        >
          All
        </button>
        {shown.map((o) => {
          const on = value.includes(o.value);
          return (
            <button
              key={o.value}
              type="button"
              data-active={on}
              className="desk-btn"
              onClick={() =>
                onChange(on ? value.filter((v) => v !== o.value) : [...value, o.value])
              }
            >
              {o.value}
              {o.n > 0 && (
                <span className="ml-1 font-mono text-[9px] text-muted-foreground">{o.n}</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function patchFilters(prev: Record<string, string[]>, id: string, next: string[]) {
  const copy = { ...prev };
  if (!next.length) delete copy[id];
  else copy[id] = next;
  return copy;
}

export function buildFacets(
  live: Record<string, FilterFacet[]> | undefined,
  stored?: Record<string, string[]>,
): Record<string, FilterFacet[]> {
  return Object.fromEntries(
    FILTER_META.map((f) => [
      f.id,
      live?.[f.id] ?? (stored?.[f.id] ?? []).map((v) => ({ value: v, n: 0 })),
    ]),
  );
}

export function FilterBar({
  filters,
  onFilters,
  groupBy,
  onGroupBy,
  facets,
}: {
  filters: Record<string, string[]>;
  onFilters: (next: Record<string, string[]>) => void;
  groupBy: string;
  onGroupBy: (id: string) => void;
  facets: Record<string, FilterFacet[]>;
}) {
  const [open, setOpen] = useState(false);
  const extraActive = Object.entries(filters)
    .filter(([id]) => id !== "period")
    .reduce((n, [, values]) => n + values.length, 0);
  const pills = Object.entries(filters)
    .filter(([id]) => id !== "period")
    .flatMap(([id, values]) => {
      const label = FILTER_META.find((f) => f.id === id)?.label ?? id;
      return values.map((value) => ({ id, label, value }));
    });
  const period = filters.period ?? [];
  const scopeAll = period.length === 0;
  const scopePrematch = period.length === 1 && period[0] === "prematch";
  const scopeInplay = period.length === 1 && period[0] === "inplay";

  function setScope(next: string[]) {
    onFilters(patchFilters(filters, "period", next));
  }

  return (
    <div className="shrink-0">
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex overflow-hidden rounded-sm border border-border">
          <button
            type="button"
            className="desk-btn h-auto rounded-none border-0 px-2.5 py-1 text-[11px]"
            data-active={scopeAll}
            onClick={() => setScope([])}
          >
            All
          </button>
          <button
            type="button"
            className="desk-btn h-auto rounded-none border-0 border-l border-border px-2.5 py-1 text-[11px]"
            data-active={scopePrematch}
            onClick={() => setScope(["prematch"])}
          >
            Prematch
          </button>
          <button
            type="button"
            className="desk-btn h-auto rounded-none border-0 border-l border-border px-2.5 py-1 text-[11px]"
            data-active={scopeInplay}
            onClick={() => setScope(["inplay"])}
          >
            In-play
          </button>
        </div>
        <button type="button" className="desk-btn" data-active={open} onClick={() => setOpen((v) => !v)}>
          More filters
          {extraActive > 0 && (
            <span className="ml-1 font-mono text-[10px] text-muted-foreground">{extraActive}</span>
          )}
        </button>
        {pills.slice(0, 5).map((p) => (
          <button
            key={`${p.id}-${p.value}`}
            type="button"
            className="desk-btn"
            title={`Remove ${p.label} ${p.value}`}
            onClick={() =>
              onFilters(patchFilters(filters, p.id, (filters[p.id] ?? []).filter((v) => v !== p.value)))
            }
          >
            {p.label} {p.value}
            <span className="ml-1 text-muted-foreground">×</span>
          </button>
        ))}
        {pills.length > 5 && (
          <span className="text-[11px] text-muted-foreground">+{pills.length - 5}</span>
        )}
        {extraActive > 0 && (
          <button type="button" className="desk-btn" onClick={() => onFilters({})}>
            Clear
          </button>
        )}
        <label className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
          Break down by
          <select
            className="desk-input h-8 w-[8.5rem] text-[12px]"
            value={groupBy}
            onChange={(e) => onGroupBy(e.target.value)}
          >
            {GROUP_OPTS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {open && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={() => setOpen(false)}>
          <aside
            className="flex h-full w-full max-w-lg flex-col border-l border-border bg-card shadow-[0_0_40px_rgba(0,0,0,0.45)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
              <div>
                <div className="text-[15px] font-medium tracking-tight">Filters</div>
                <p className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                  Slice the same fact rows. Leave a row on All to keep every value.
                </p>
              </div>
              <button type="button" className="desk-btn" onClick={() => setOpen(false)}>
                Done
              </button>
            </div>
            <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 py-3">
              {FILTER_META.map((f) => (
                <FilterChips
                  key={f.id}
                  label={f.label}
                  options={facets[f.id] ?? []}
                  value={filters[f.id] ?? []}
                  onChange={(next) => onFilters(patchFilters(filters, f.id, next))}
                />
              ))}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

export function unique(xs: string[]) {
  return [...new Set(xs.filter(Boolean))];
}

export function shortTurnover(id: string, label?: string) {
  const map: Record<string, string> = {
    persistence: "last 5m",
    naive_lag5: "lag-5",
    trailing_mean: "trail",
    gametime: "gametime",
    blend: "blend",
    momentum: "momentum",
    match_share: "match mix",
    phase_scale: "phase",
    oracle: "oracle",
    zero: "zero",
    ema: "EMA",
    hurdle: "hurdle (walk-fwd)",
  };
  return map[id] ?? label ?? id;
}

export function shortBelief(id: string, label?: string) {
  const map: Record<string, string> = {
    hkjc_true: "HKJC true",
    hkjc_offer: "HKJC offer",
    market: "market",
    model: "model",
    model_basis: "model·basis",
    model_model: "model·model",
    poisson: "Poisson",
    demargin: "de-margin",
  };
  return map[id] ?? label ?? id;
}

export function stackLabel(s: {
  turnover: string;
  true_prob: string;
  calibrator: string;
  algo: string;
}) {
  return `${shortTurnover(s.turnover)} · ${shortBelief(s.true_prob)}/${s.calibrator} · ${s.algo}`;
}
