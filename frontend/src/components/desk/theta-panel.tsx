"use client";

/**
 * The theta rail.
 *
 * Eight numbers drive every price on the board, so this is where a trader
 * actually works. Each row shows where the parameter is now, where the
 * optimizer wants it, and how far it is allowed to travel this tick; the
 * slider moves it anywhere inside that band and the book re-prices from
 * the backend as it moves.
 *
 * The re-price is a round trip on purpose. The alternative - a copy of the
 * pricing engine in the browser - is how the number under a trader's
 * finger ends up disagreeing with the number the optimizer solved against.
 */

import { RotateCcw, Wand2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Tag } from "@/components/desk/chips";
import { Panel } from "@/components/desk/primitives";
import { api } from "@/lib/api";
import { fmtMoneyExact, fmtNum } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Cockpit, ExpectedGm, Theta, ThetaDim, WhatIf } from "@/types/api";

export interface ThetaPanelProps {
  cockpit: Cockpit;
  /** Draft theta, or null while the desk is looking at the live book. */
  draft: Partial<Record<ThetaDim, number>>;
  onDraft: (next: Partial<Record<ThetaDim, number>>) => void;
  onWhatIf: (result: WhatIf | null) => void;
}

export function ThetaPanel({ cockpit, draft, onDraft, onWhatIf }: ThetaPanelProps) {
  const { theta, optimizer, match } = cockpit;
  const now = theta.layers[theta.layer] ?? ({} as Theta);
  const star = optimizer?.theta_star ?? null;
  const [busy, setBusy] = useState(false);
  const [priced, setPriced] = useState<WhatIf | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const dirty = Object.keys(draft).length > 0;
  // A what-if only means anything while there is a draft on the sliders.
  // Deriving that here rather than clearing it from the effect keeps the
  // panel from rendering the previous answer against a reset theta.
  const result = dirty ? priced : null;

  // Debounced so dragging a slider does not queue a request per pixel.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!dirty) {
      onWhatIf(null);
      return;
    }
    timer.current = setTimeout(async () => {
      setBusy(true);
      try {
        const next = await api.price(match.match_id, draft);
        setPriced(next);
        onWhatIf(next);
      } catch {
        setPriced(null);
        onWhatIf(null);
      } finally {
        setBusy(false);
      }
    }, 220);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(draft), match.match_id]);

  const frozen = useMemo(
    () => new Set(match.ht_done ? theta.dims.filter((d) => d.dim.includes("_fh_")).map((d) => d.dim) : []),
    [match.ht_done, theta.dims],
  );

  return (
    <Panel
      title="Theta"
      hint={`${theta.layer} layer · ${fmtNum(theta.age_min, 0)} min old · ${theta.semantics}`}
      right={
        <div className="flex items-center gap-1.5">
          {star && (
            <button
              type="button"
              className="desk-btn"
              onClick={() =>
                onDraft(
                  Object.fromEntries(
                    theta.dims
                      .filter((d) => !frozen.has(d.dim))
                      .map((d) => [d.dim, star[d.dim] ?? 0]),
                  ) as Partial<Record<ThetaDim, number>>,
                )
              }
            >
              <Wand2 className="h-3.5 w-3.5" />
              Load θ*
            </button>
          )}
          <button
            type="button"
            className="desk-btn"
            disabled={!dirty}
            onClick={() => onDraft({})}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Reset
          </button>
        </div>
      }
      bodyClassName="overflow-auto"
    >
      <div className="divide-y divide-border/60">
        {theta.dims.map((spec) => {
          const isFrozen = frozen.has(spec.dim);
          const nowValue = now[spec.dim] ?? 0;
          const starValue = star?.[spec.dim] ?? null;
          const value = draft[spec.dim] ?? nowValue;
          const lo = Math.max(spec.bounds[0], nowValue - spec.max_move);
          const hi = Math.min(spec.bounds[1], nowValue + spec.max_move);
          return (
            <ThetaRow
              key={spec.dim}
              label={spec.label}
              dim={spec.dim}
              domain={spec.domain}
              now={nowValue}
              star={starValue}
              value={value}
              lo={lo}
              hi={hi}
              frozen={isFrozen}
              dirty={draft[spec.dim] != null}
              onChange={(v) => onDraft({ ...draft, [spec.dim]: v })}
            />
          );
        })}
      </div>

      <GmReadout
        busy={busy}
        dirty={dirty}
        baseline={result?.baseline ?? null}
        whatIf={result?.expected ?? null}
        gmNow={optimizer?.gm_now ?? null}
        gmStar={optimizer?.gm_star ?? null}
      />
    </Panel>
  );
}

function ThetaRow({
  label,
  dim,
  domain,
  now,
  star,
  value,
  lo,
  hi,
  frozen,
  dirty,
  onChange,
}: {
  label: string;
  dim: string;
  domain: "goal" | "corner";
  now: number;
  star: number | null;
  value: number;
  lo: number;
  hi: number;
  frozen: boolean;
  dirty: boolean;
  onChange: (v: number) => void;
}) {
  const span = hi - lo || 1;
  const pct = (v: number) => ((Math.min(Math.max(v, lo), hi) - lo) / span) * 100;
  const step = domain === "corner" ? 0.05 : 0.01;

  return (
    <div className={cn("px-3 py-2", frozen && "opacity-45")}>
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-baseline gap-1.5">
          <span className="text-[11px] font-medium">{label}</span>
          <span className="text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
            {dim}
          </span>
          {frozen && <Tag tone="muted">settled</Tag>}
        </div>
        <div className="flex items-baseline gap-2 font-mono text-[11px] tabular-nums">
          <span className={cn(dirty ? "text-gold-soft" : "text-foreground")}>
            {value.toFixed(3)}
          </span>
          {star != null && Math.abs(star - now) > 1e-4 && (
            <span className="text-[10px] text-muted-foreground">
              θ* {star.toFixed(3)}
            </span>
          )}
        </div>
      </div>

      <div className="relative mt-1.5">
        <input
          type="range"
          min={lo}
          max={hi}
          step={step}
          value={value}
          disabled={frozen}
          onChange={(e) => onChange(Number(e.target.value))}
          className="theta-slider"
          aria-label={label}
        />
        {/* Where the desk is now, and where the solver wants to be. */}
        <span
          className="pointer-events-none absolute top-1/2 h-3 w-px -translate-y-1/2 bg-muted-foreground/70"
          style={{ left: `${pct(now)}%` }}
          title={`current ${now.toFixed(3)}`}
        />
        {star != null && (
          <span
            className="pointer-events-none absolute top-1/2 h-3 w-px -translate-y-1/2 bg-gold"
            style={{ left: `${pct(star)}%` }}
            title={`optimizer ${star.toFixed(3)}`}
          />
        )}
      </div>
      <div className="mt-0.5 flex justify-between font-mono text-[9px] text-muted-foreground/70">
        <span>{lo.toFixed(2)}</span>
        <span>{hi.toFixed(2)}</span>
      </div>
    </div>
  );
}

function GmReadout({
  busy,
  dirty,
  baseline,
  whatIf,
  gmNow,
  gmStar,
}: {
  busy: boolean;
  dirty: boolean;
  baseline: ExpectedGm | null;
  whatIf: ExpectedGm | null;
  gmNow: number | null;
  gmStar: number | null;
}) {
  return (
    <div className="sticky bottom-0 border-t border-border-strong bg-surface-2/95 px-3 py-2 backdrop-blur">
      <div className="grid grid-cols-3 gap-2 text-center">
        <Cell label="E[GM] now" value={fmtMoneyExact(gmNow)} />
        <Cell label="at θ*" value={fmtMoneyExact(gmStar)} tone="gold" />
        <Cell
          label={busy ? "pricing…" : "your θ"}
          value={dirty && whatIf ? fmtMoneyExact(whatIf.gm) : "—"}
          tone={
            dirty && whatIf && baseline
              ? whatIf.gm >= baseline.gm
                ? "positive"
                : "negative"
              : "muted"
          }
        />
      </div>
      {dirty && whatIf && baseline && (
        <p className="mt-1 text-center text-[10px] text-muted-foreground">
          {whatIf.gm >= baseline.gm ? "Better than" : "Worse than"} the live book by{" "}
          {fmtMoneyExact(Math.abs(whatIf.gm - baseline.gm))} over {whatIf.legs} legs
          carrying {fmtMoneyExact(whatIf.turnover)}.
        </p>
      )}
    </div>
  );
}

function Cell({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "gold" | "positive" | "negative" | "muted";
}) {
  const cls = {
    default: "text-foreground",
    gold: "text-gold-soft",
    positive: "text-positive",
    negative: "text-negative",
    muted: "text-muted-foreground",
  }[tone];
  return (
    <div>
      <div className="desk-label">{label}</div>
      <div className={cn("desk-value text-[13px]", cls)}>{value}</div>
    </div>
  );
}
