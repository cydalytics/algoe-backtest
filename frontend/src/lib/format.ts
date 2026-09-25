export function fmtMoney(v: number | null | undefined, dec = 0): string {
  if (v == null || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(1)}m`;
  if (abs >= 1_000) {
    const k = abs / 1_000;
    return `${sign}${k >= 10 ? k.toFixed(0) : k.toFixed(1).replace(/\.0$/, "")}k`;
  }
  return `${sign}${abs.toLocaleString("en-HK", {
    maximumFractionDigits: dec,
    minimumFractionDigits: dec,
  })}`;
}

/**
 * Money at a precision where a difference still reconciles.
 *
 * `fmtMoney` rounds anything over ten thousand to whole thousands, which is
 * right for a dense table but wrong wherever two figures are shown beside their
 * delta: 68.4k and 71.4k both print as "68k" and "72k" next to "+3.0k", and a
 * trader is left doing arithmetic that does not work.
 */
export function fmtMoneyExact(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (abs >= 1_000_000) return `${sign}${(abs / 1_000_000).toFixed(2)}m`;
  if (abs >= 1_000) return `${sign}${(abs / 1_000).toFixed(1)}k`;
  return `${sign}${Math.round(abs).toLocaleString("en-HK")}`;
}

export function fmtHkd(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "−" : v > 0 ? "+" : "";
  return `${sign}${fmtMoney(Math.abs(v))}`;
}

export function fmtNum(v: number | null | undefined, dec = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-HK", {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  });
}

export function fmtSigned(v: number | null | undefined, dec = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${fmtNum(v, dec)}`;
}

export function fmtPct(v: number | null | undefined, dec = 0): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(dec)}%`;
}

export function nextTick(from = new Date()): Date {
  const ms = 5 * 60 * 1000;
  const t = from.getTime();
  const next = Math.ceil(t / ms) * ms;
  return new Date(next === t ? next + ms : next);
}

export function formatTickClock(now = new Date()): { label: string; urgent: boolean } {
  const target = nextTick(now);
  const sec = Math.max(0, Math.floor((target.getTime() - now.getTime()) / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return {
    label: `${m}:${s.toString().padStart(2, "0")}`,
    urgent: sec <= 60,
  };
}

export function normalizeTeam(name: string): string {
  return name
    .toLowerCase()
    .replace(/\b(fc|cf|afc|sc|united|city|hotspur)\b/g, "")
    .replace(/[^a-z0-9]/g, "")
    .trim();
}
