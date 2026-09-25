"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { ParquetDefaults } from "@/types/api";

const FALLBACK: {
  turnover: { id: string; label: string }[];
  belief: { id: string; label: string }[];
  objective: { id: string; label: string }[];
} = {
  turnover: [{ id: "persist", label: "Last 5 min (baseline)" }],
  belief: [{ id: "p_true", label: "1 / HKJC true odds (baseline)" }],
  objective: [{ id: "egm", label: "Max expected gross margin (baseline)" }],
};

/**
 * The three stored series. Changing one loads that parquet combination.
 * Missing days are filled in the background; the page updates as they land.
 */
export function SeriesBar({
  onStarted,
  onError,
}: {
  onStarted: (jobId: string) => void;
  onError: (message: string) => void;
}) {
  const [defaults, setDefaults] = useState<ParquetDefaults | null>(null);
  const [turnover, setTurnover] = useState("persist");
  const [belief, setBelief] = useState("p_true");
  const [objective, setObjective] = useState("egm");
  const [start, setStart] = useState("2026-07-01");
  const [end, setEnd] = useState("2026-09-15");

  useEffect(() => {
    let alive = true;
    api
      .parquetDefaults()
      .then((d) => {
        if (!alive) return;
        setDefaults(d);
        setStart(d.start);
        setEnd(d.end);
      })
      .catch((err) => {
        if (alive) onError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [onError]);

  useEffect(() => {
    if (!defaults) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      api
        .parquetOpen({
          start,
          end,
          data_dir: defaults.data_dir,
          pools: ["all"],
          turnover_model: turnover,
          belief_model: belief,
          objective,
          solve: true,
        })
        .then((res) => {
          if (alive) onStarted(res.job.id);
        })
        .catch((err) => {
          if (alive) onError(err instanceof ApiError ? err.message : String(err));
        });
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [defaults, start, end, turnover, belief, objective, onStarted, onError]);

  const choices = defaults?.choices ?? FALLBACK;
  return (
    <div className="flex flex-wrap items-end gap-3 border-b border-border px-4 py-2">
      <Pick label="Turnover" value={turnover} options={choices.turnover} onChange={setTurnover} />
      <Pick label="True prob" value={belief} options={choices.belief} onChange={setBelief} />
      <Pick label="Optimizer" value={objective} options={choices.objective} onChange={setObjective} />
      <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
        From
        <input
          type="date"
          className="desk-input text-[12px]"
          value={start}
          onChange={(e) => setStart(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
        To
        <input
          type="date"
          className="desk-input text-[12px]"
          value={end}
          onChange={(e) => setEnd(e.target.value)}
        />
      </label>
    </div>
  );
}

function Pick({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { id: string; label: string }[];
  onChange: (id: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
      {label}
      <select
        className="desk-input min-w-[14rem] text-[12px] normal-case"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
