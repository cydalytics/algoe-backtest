"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export function Panel({
  title,
  hint,
  right,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  hint?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cn("desk-panel flex min-h-0 flex-col overflow-hidden", className)}>
      {(title || right) && (
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-3 py-2">
          <div className="flex min-w-0 items-baseline gap-2">
            <h2 className="desk-label text-foreground/80">{title}</h2>
            {hint && (
              <span className="truncate text-[10px] text-muted-foreground/70">{hint}</span>
            )}
          </div>
          {right}
        </header>
      )}
      <div className={cn("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </section>
  );
}

export function Metric({
  label,
  value,
  sub,
  tone = "default",
  hint,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "default" | "positive" | "negative" | "gold" | "warn";
  hint?: string;
}) {
  const toneClass = {
    default: "text-foreground",
    positive: "text-positive",
    negative: "text-negative",
    gold: "text-gold-soft",
    warn: "text-warn",
  }[tone];
  return (
    <div className="min-w-0 px-3 py-2" title={hint}>
      <div className="desk-label truncate">{label}</div>
      <div className={cn("desk-value mt-0.5 text-[17px] leading-tight", toneClass)}>{value}</div>
      {sub != null && (
        <div className="mt-0.5 truncate text-[10px] text-muted-foreground">{sub}</div>
      )}
    </div>
  );
}

/** Inline trend for a table row. No axes — shape only. */
export function Sparkline({
  data,
  width = 68,
  height = 18,
  tone = "iris",
  className,
}: {
  data: number[];
  width?: number;
  height?: number;
  tone?: "iris" | "gold" | "positive" | "negative";
  className?: string;
}) {
  if (!data.length) {
    return <span className="text-[10px] text-muted-foreground/50">—</span>;
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const step = data.length > 1 ? width / (data.length - 1) : width;
  const pts = data.map((v, i) => [i * step, height - ((v - min) / span) * (height - 2) - 1]);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const area = `${d} L${width},${height} L0,${height} Z`;
  const stroke = `var(--${tone === "iris" ? "iris" : tone})`;
  const id = `sp-${tone}`;

  return (
    <svg width={width} height={height} className={cn("overflow-visible", className)} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={d} fill="none" stroke={stroke} strokeWidth="1.2" strokeLinejoin="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="1.6" fill={stroke} />
    </svg>
  );
}

/** Horizontal magnitude bar rendered behind a number. */
export function BarCell({
  value,
  max,
  tone = "iris",
  children,
  align = "right",
}: {
  value: number;
  max: number;
  tone?: "iris" | "gold" | "positive" | "negative";
  children: ReactNode;
  align?: "left" | "right";
}) {
  const pct = max > 0 ? Math.min(100, (Math.abs(value) / max) * 100) : 0;
  return (
    <div className="relative">
      <div
        className={cn(
          "absolute inset-y-[2px] rounded-[1px] opacity-[0.16]",
          align === "right" ? "right-0" : "left-0"
        )}
        style={{ width: `${pct.toFixed(2)}%`, backgroundColor: `var(--${tone})` }}
      />
      <div className={cn("relative desk-value", align === "right" ? "text-right" : "text-left")}>
        {children}
      </div>
    </div>
  );
}

export function Delta({
  value,
  dec = 2,
  suffix = "",
  zero = "—",
}: {
  value: number | null | undefined;
  dec?: number;
  suffix?: string;
  zero?: string;
}) {
  if (value == null || Number.isNaN(value)) {
    return <span className="text-muted-foreground">{zero}</span>;
  }
  if (Math.abs(value) < Math.pow(10, -dec) / 2) {
    return <span className="text-muted-foreground">0{suffix}</span>;
  }
  return (
    <span className={value > 0 ? "text-positive" : "text-negative"}>
      {value > 0 ? "+" : "−"}
      {Math.abs(value).toFixed(dec)}
      {suffix}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  size = "sm",
}: {
  value: T;
  options: { value: T; label: string; count?: number }[];
  onChange: (v: T) => void;
  size?: "sm" | "xs";
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-sm border border-border">
      {options.map((o, i) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          data-active={value === o.value}
          className={cn(
            "desk-btn h-auto rounded-none border-0 px-2.5",
            size === "xs" ? "py-[3px] text-[10px]" : "py-1 text-[11px]",
            i > 0 && "border-l border-border"
          )}
        >
          {o.label}
          {o.count != null && (
            <span className="font-mono text-[10px] text-muted-foreground">{o.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

export function SkeletonRows({ rows = 8, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-[6px] p-3">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-2">
          {Array.from({ length: cols }).map((_, c) => (
            <div
              key={c}
              className="skeleton h-4 rounded-[2px]"
              style={{ width: c === 0 ? "26%" : `${10 + ((r + c) % 3) * 3}%` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex h-full min-h-[160px] flex-col items-center justify-center gap-1 px-6 text-center">
      <div className="desk-label text-foreground/70">{title}</div>
      <p className="max-w-sm text-[11px] leading-relaxed text-muted-foreground">{body}</p>
    </div>
  );
}
