"""Self-contained HTML report.

No CDN, no JavaScript libraries, no fonts to download: every chart is SVG
generated here, so the file opens on an air-gapped machine by double-clicking it.
Every axis has ticks, units and a label, because a chart without them is useless.
"""
from __future__ import annotations

import html
import json
import math
from typing import List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------- theme
# Eagle-I Terminal palette, lifted from frontend/src/app/globals.css so this
# standalone report and the desk web app read as one product. Gold is the
# primary series, iris the comparison, green/red reserved for signed money.
BG = "#05080f"
CARD = "#0b111c"
SURFACE = "#0e1522"
SURFACE2 = "#131d2e"
INK = "#e8eef8"
MUTED = "#8496b0"
GRID = "#1b2637"
GRID_STRONG = "#2b3a52"
GOLD = "#d4af37"
GOLD_SOFT = "#eccd76"
IRIS = "#5b8def"
POS = "#35c48c"
NEG = "#e0555c"
WARN = "#e0a33a"

# names kept so call sites read by role, not by colour
BLUE = GOLD          # primary series
ORANGE = IRIS        # comparison series
GREEN = POS
RED = NEG
GREY = GRID_STRONG


def money(v: Optional[float], dp: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    v = float(v)
    a = abs(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if a >= div:
            return f"{v / div:,.{dp}f}{suf}"
    return f"{v:,.0f}"


def pct(v: Optional[float], dp: int = 2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{100 * float(v):.{dp}f}%"


def num(v: Optional[float], dp: int = 4) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{float(v):,.{dp}f}"


def esc(s) -> str:
    return html.escape(str(s))


def nice_ticks(lo: float, hi: float, target: int = 5) -> List[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mag * mult:
            stepv = mag * mult
            break
    else:
        stepv = mag * 10
    start = math.floor(lo / stepv) * stepv
    out, v = [], start
    while v <= hi + stepv * 1e-9:
        if v >= lo - stepv * 1e-9:
            out.append(round(v, 12))
        v += stepv
    return out


# ------------------------------------------------------------------- SVG frame
class Plot:
    """Linear/log cartesian frame with real axes."""

    def __init__(self, w=760, h=320, pad=(28, 18, 52, 68),
                 xlabel="", ylabel="", xlog=False, ylog=False):
        self.w, self.h = w, h
        self.pt, self.pr, self.pb, self.pl = pad
        self.xlabel, self.ylabel = xlabel, ylabel
        self.xlog, self.ylog = xlog, ylog
        self.parts: List[str] = []
        self.x0, self.x1 = 0.0, 1.0
        self.y0, self.y1 = 0.0, 1.0

    @property
    def iw(self) -> float:
        return self.w - self.pl - self.pr

    @property
    def ih(self) -> float:
        return self.h - self.pt - self.pb

    def set_x(self, lo, hi):
        if self.xlog:
            lo, hi = max(lo, 1e-9), max(hi, 1e-8)
        if hi <= lo:
            hi = lo + 1
        self.x0, self.x1 = lo, hi

    def set_y(self, lo, hi):
        if self.ylog:
            lo, hi = max(lo, 1e-9), max(hi, 1e-8)
        if hi <= lo:
            hi = lo + 1
        self.y0, self.y1 = lo, hi

    def sx(self, v: float) -> float:
        if self.xlog:
            v = math.log10(max(v, 1e-9))
            a, b = math.log10(self.x0), math.log10(self.x1)
        else:
            a, b = self.x0, self.x1
        return self.pl + (v - a) / (b - a) * self.iw

    def sy(self, v: float) -> float:
        if self.ylog:
            v = math.log10(max(v, 1e-9))
            a, b = math.log10(self.y0), math.log10(self.y1)
        else:
            a, b = self.y0, self.y1
        return self.pt + self.ih - (v - a) / (b - a) * self.ih

    # ---- decorations
    def grid(self, xticks: Sequence[Tuple[float, str]], yticks: Sequence[Tuple[float, str]]):
        p = self.parts
        p.append(f'<rect x="{self.pl}" y="{self.pt}" width="{self.iw}" height="{self.ih}" '
                 f'fill="{BG}" stroke="{GRID_STRONG}"/>')
        for v, lab in yticks:
            y = self.sy(v)
            if not (self.pt - 1 <= y <= self.pt + self.ih + 1):
                continue
            p.append(f'<line x1="{self.pl}" y1="{y:.1f}" x2="{self.pl + self.iw}" '
                     f'y2="{y:.1f}" stroke="{GRID}" stroke-dasharray="2 3"/>')
            p.append(f'<text x="{self.pl - 8}" y="{y + 3.5:.1f}" text-anchor="end" '
                     f'font-size="10" fill="{MUTED}">{esc(lab)}</text>')
        for v, lab in xticks:
            x = self.sx(v)
            if not (self.pl - 1 <= x <= self.pl + self.iw + 1):
                continue
            p.append(f'<line x1="{x:.1f}" y1="{self.pt}" x2="{x:.1f}" '
                     f'y2="{self.pt + self.ih}" stroke="{GRID}" stroke-dasharray="2 3"/>')
            p.append(f'<text x="{x:.1f}" y="{self.pt + self.ih + 16}" text-anchor="middle" '
                     f'font-size="10" fill="{MUTED}">{esc(lab)}</text>')
        if self.xlabel:
            p.append(f'<text x="{self.pl + self.iw / 2}" y="{self.h - 8}" '
                     f'text-anchor="middle" font-size="11" fill="{INK}">{esc(self.xlabel)}</text>')
        if self.ylabel:
            cy = self.pt + self.ih / 2
            p.append(f'<text x="14" y="{cy}" text-anchor="middle" font-size="11" '
                     f'fill="{INK}" transform="rotate(-90 14 {cy})">{esc(self.ylabel)}</text>')

    def legend(self, items: Sequence[Tuple[str, str]]):
        # above the frame, so it can never sit on top of the data
        x = self.pl
        y = self.pt - 9
        for label, colour in items:
            self.parts.append(f'<rect x="{x}" y="{y - 8}" width="10" height="10" rx="2" '
                              f'fill="{colour}"/>')
            self.parts.append(f'<text x="{x + 15}" y="{y + 1}" font-size="10" '
                              f'fill="{INK}">{esc(label)}</text>')
            x += 20 + 7 * len(label)

    def diagonal(self, colour=GREY):
        lo = max(self.x0, self.y0)
        hi = min(self.x1, self.y1)
        if hi <= lo:
            return
        self.parts.append(f'<line x1="{self.sx(lo):.1f}" y1="{self.sy(lo):.1f}" '
                          f'x2="{self.sx(hi):.1f}" y2="{self.sy(hi):.1f}" '
                          f'stroke="{colour}" stroke-dasharray="4 4"/>')

    def hline(self, v: float, colour=GREY):
        y = self.sy(v)
        self.parts.append(f'<line x1="{self.pl}" y1="{y:.1f}" x2="{self.pl + self.iw}" '
                          f'y2="{y:.1f}" stroke="{colour}" stroke-width="1.2"/>')

    def path(self, pts: Sequence[Tuple[float, float]], colour: str, width=1.8):
        if len(pts) < 2:
            if pts:
                self.dots(pts, colour, 2.5)
            return
        d = " ".join(("M" if i == 0 else "L") + f"{self.sx(x):.1f},{self.sy(y):.1f}"
                     for i, (x, y) in enumerate(pts))
        self.parts.append(f'<path d="{d}" fill="none" stroke="{colour}" '
                          f'stroke-width="{width}"/>')

    def dots(self, pts: Sequence[Tuple[float, float]], colour: str, r=2.0, opacity=0.5):
        for x, y in pts:
            self.parts.append(f'<circle cx="{self.sx(x):.1f}" cy="{self.sy(y):.1f}" '
                              f'r="{r}" fill="{colour}" fill-opacity="{opacity}"/>')

    def render(self) -> str:
        # capped at its natural width so a wide monitor does not scale a 300px
        # chart into a 600px slab of whitespace
        return (f'<svg viewBox="0 0 {self.w} {self.h}" width="100%" '
                f'style="max-width:{self.w}px;height:auto;display:block;margin:0 auto" '
                f'preserveAspectRatio="xMidYMid meet" '
                f'font-family="system-ui,Segoe UI,Arial,sans-serif">'
                + "".join(self.parts) + "</svg>")


# --------------------------------------------------------------------- charts
def line_chart(x_labels: List[str], series: List[Tuple[str, List[Optional[float]], str]],
               ylabel="", xlabel="", money_axis=True, h=300, w=1180) -> str:
    vals = [v for _, ys, _ in series for v in ys if v is not None]
    if not vals:
        return empty("no data")
    p = Plot(w=w, h=h, xlabel=xlabel, ylabel=ylabel)
    lo, hi = min(vals + [0.0]), max(vals)
    p.set_x(0, max(len(x_labels) - 1, 1))
    p.set_y(lo, hi * 1.05 if hi > 0 else 1)
    fmt = money if money_axis else (lambda v: num(v, 2))
    yt = [(v, fmt(v)) for v in nice_ticks(p.y0, p.y1, 5)]
    stepv = max(1, len(x_labels) // 10)
    xt = [(i, x_labels[i]) for i in range(0, len(x_labels), stepv)]
    p.grid(xt, yt)
    if lo < 0:
        p.hline(0.0)
    for name, ys, colour in series:
        pts = [(i, v) for i, v in enumerate(ys) if v is not None]
        p.path(pts, colour)
    p.legend([(n, c) for n, _, c in series])
    return p.render()


def bar_chart(cats: List[str], series: List[Tuple[str, List[Optional[float]], str]],
              ylabel="", xlabel="", as_pct=True, h=300, hline: Optional[float] = None) -> str:
    vals = [v for _, ys, _ in series for v in ys if v is not None]
    if not vals:
        return empty("no data")
    p = Plot(h=h, pad=(28, 18, 74, 68), xlabel=xlabel, ylabel=ylabel)
    lo = min(vals + [0.0])
    hi = max(vals)
    p.set_x(0, max(len(cats), 1))
    p.set_y(lo, hi * 1.12 if hi > 0 else 1)
    fmt = (lambda v: pct(v, 0)) if as_pct else money
    yt = [(v, fmt(v)) for v in nice_ticks(p.y0, p.y1, 5)]
    p.grid([], yt)
    slot = p.iw / max(len(cats), 1)
    k = len(series)
    bw = slot * 0.72 / k
    for si, (name, ys, colour) in enumerate(series):
        for i, v in enumerate(ys):
            if v is None:
                continue
            x = p.pl + i * slot + slot * 0.14 + si * bw
            y = p.sy(max(v, 0)) if v >= 0 else p.sy(0)
            hh = abs(p.sy(v) - p.sy(0))
            p.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                           f'height="{max(hh, 0.5):.1f}" fill="{colour}" rx="1.5"/>')
    for i, c in enumerate(cats):
        x = p.pl + i * slot + slot / 2
        p.parts.append(f'<text x="{x:.1f}" y="{p.pt + p.ih + 14}" text-anchor="end" '
                       f'font-size="9.5" fill="{MUTED}" '
                       f'transform="rotate(-35 {x:.1f} {p.pt + p.ih + 14})">{esc(c)}</text>')
    if hline is not None:
        p.hline(hline, RED)
    if len(series) > 1:
        p.legend([(n, c) for n, _, c in series])
    return p.render()


def scatter_chart(points: List[Tuple[float, float]], xlabel="", ylabel="",
                  log=True, h=340, note="") -> str:
    pts = [(x, y) for x, y in points
           if x is not None and y is not None and (not log or (x > 0 and y > 0))]
    if not pts:
        return empty("no data")
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    lo = min(xs + ys)
    hi = max(xs + ys)
    p = Plot(h=h, xlabel=xlabel, ylabel=ylabel, xlog=log, ylog=log)
    if log:
        lo = max(lo, 1.0)
        p.set_x(lo / 1.5, hi * 1.5)
        p.set_y(lo / 1.5, hi * 1.5)
        decades = [10 ** e for e in range(int(math.floor(math.log10(p.x0))),
                                          int(math.ceil(math.log10(p.x1))) + 1)]
        ticks = [(v, money(v, 0)) for v in decades]
    else:
        p.set_x(lo, hi)
        p.set_y(lo, hi)
        ticks = [(v, money(v)) for v in nice_ticks(lo, hi, 5)]
    p.grid(ticks, ticks)
    p.dots(pts, BLUE, 2.2, 0.35)
    p.diagonal(INK)   # drawn last, otherwise the point cloud hides it
    if note:
        p.parts.append(f'<text x="{p.pl + p.iw - 6}" y="{p.pt + 14}" text-anchor="end" '
                       f'font-size="10" fill="{MUTED}">{esc(note)}</text>')
    return p.render()


def hist_chart(edges: List[float], counts: List[float], xlabel="", ylabel="rows",
               h=280, symlog=False, marker: Optional[float] = None) -> str:
    if not counts or sum(counts) == 0:
        return empty("no data")
    n = len(counts)
    p = Plot(h=h, pad=(28, 18, 56, 68), xlabel=xlabel, ylabel=ylabel)
    p.set_x(0, n)
    p.set_y(0, max(counts) * 1.1)
    yt = [(v, money(v, 0)) for v in nice_ticks(0, p.y1, 4)]
    stepv = max(1, n // 8)
    xt = [(i + 0.5, _edge_label(edges[i], symlog)) for i in range(0, n, stepv)]
    p.grid(xt, yt)
    slot = p.iw / n
    for i, c in enumerate(counts):
        if c <= 0:
            continue
        x = p.pl + i * slot
        y = p.sy(c)
        p.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(slot - 1, 1):.1f}" '
                       f'height="{p.sy(0) - y:.1f}" fill="{BLUE}" fill-opacity="0.75"/>')
    if marker is not None:
        for i in range(n):
            if edges[i] <= marker < edges[i + 1]:
                x = p.sx(i + (marker - edges[i]) / (edges[i + 1] - edges[i]))
                p.parts.append(f'<line x1="{x:.1f}" y1="{p.pt}" x2="{x:.1f}" '
                               f'y2="{p.pt + p.ih}" stroke="{RED}" stroke-width="1.5"/>')
                p.parts.append(f'<text x="{x + 4:.1f}" y="{p.pt + 12}" font-size="10" '
                               f'fill="{RED}">{num(marker, 2)}</text>')
                break
    return p.render()


def _edge_label(v: float, symlog: bool) -> str:
    if symlog:
        return ("-" if v < 0 else "") + money(abs(v), 0)
    return num(v, 2) if abs(v) < 100 else money(v, 0)


def calibration_chart(cal: dict, weight="money", h=340) -> str:
    series = []
    colours = {"p_true": BLUE, "p_sell": ORANGE}
    for model, pack in cal.items():
        bins = pack.get("bins") or []
        key = "y_mean_money" if weight == "money" else "y_mean"
        pts = [(b["p_mean"], b[key]) for b in bins
               if b.get("p_mean") is not None and b.get(key) is not None
               and (b["weight"] > 0 if weight == "money" else b["n"] > 0)]
        if pts:
            series.append((model, sorted(pts), colours.get(model, GREY)))
    if not series:
        return empty("no settled rows to calibrate against")
    p = Plot(h=h, xlabel="believed probability", ylabel=f"realised rate ({weight}-weighted)")
    p.set_x(0, 1)
    p.set_y(0, 1)
    ticks = [(v, num(v, 1)) for v in nice_ticks(0, 1, 5)]
    p.grid(ticks, ticks)
    p.diagonal()
    for name, pts, colour in series:
        p.path(pts, colour)
        p.dots(pts, colour, 3.0, 0.95)
    p.legend([(n, c) for n, _, c in series])
    return p.render()


def empty(msg: str) -> str:
    return f'<div class="empty">{esc(msg)}</div>'


# ---------------------------------------------------------------------- tables
def table(headers: List[str], rows: List[List[str]], right_from=1) -> str:
    th = "".join(f'<th class="{"r" if i >= right_from else ""}">{esc(h)}</th>'
                 for i, h in enumerate(headers))
    trs = []
    for r in rows:
        tds = "".join(f'<td class="{"r" if i >= right_from else ""}">{c}</td>'
                      for i, c in enumerate(r))
        trs.append(f"<tr>{tds}</tr>")
    return f'<table><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table>'


def kpi(label: str, value: str, sub: str = "", tone: str = "") -> str:
    return (f'<div class="kpi {tone}"><div class="kpi-l">{esc(label)}</div>'
            f'<div class="kpi-v">{value}</div><div class="kpi-s">{esc(sub)}</div></div>')


def card(title: str, body: str, hint: str = "") -> str:
    h = f'<div class="hint">{esc(hint)}</div>' if hint else ""
    return f'<section class="card"><h3>{esc(title)}</h3>{h}{body}</section>'


CSS = f"""
*{{box-sizing:border-box}}
body{{margin:0;background:{BG};color:{INK};
 font:13px/1.5 "Segoe UI","SF Pro Text",Inter,ui-sans-serif,system-ui,
 "Microsoft JhengHei","PingFang SC",sans-serif;
 background-image:radial-gradient(1200px 400px at 50% -180px,
   rgba(11,75,176,.16),transparent 70%);background-attachment:fixed}}
:is(th,td,.tabular,.kpi-v){{font-variant-numeric:tabular-nums}}
header{{padding:18px 26px 14px;border-bottom:1px solid {GRID}}}
header h1{{margin:0 0 6px;font-size:17px;font-weight:650;letter-spacing:-.01em}}
header .rule{{height:1px;margin:0 0 10px;background:linear-gradient(90deg,
 {GOLD} 0%,rgba(212,175,55,.35) 42%,transparent 100%)}}
header .m{{color:{MUTED};font-size:11.5px}}
main{{padding:18px 26px 64px;max-width:1560px;margin:0 auto}}
h2{{font-size:11px;margin:30px 0 10px;padding-bottom:7px;font-weight:600;
 text-transform:uppercase;letter-spacing:.14em;color:{GOLD_SOFT};
 border-bottom:1px solid {GRID_STRONG}}}
h3{{font-size:12px;margin:0 0 3px;font-weight:600;color:{INK}}}
.hint{{color:{MUTED};font-size:11px;margin-bottom:9px;max-width:78ch}}
.grid{{display:grid;gap:12px}}
.g2{{grid-template-columns:repeat(auto-fit,minmax(440px,1fr))}}
.g1{{grid-template-columns:1fr}}
.card{{background:linear-gradient(180deg,{SURFACE} 0%,{CARD} 64px);
 border:1px solid {GRID};border-radius:3px;padding:12px 14px;
 box-shadow:inset 0 1px 0 rgba(255,255,255,.03),0 1px 2px rgba(0,0,0,.4)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(172px,1fr));gap:10px}}
.kpi{{background:{CARD};border:1px solid {GRID};border-left:2px solid {GRID_STRONG};
 border-radius:3px;padding:9px 11px}}
.kpi.good{{border-left-color:{POS}}}.kpi.bad{{border-left-color:{NEG}}}
.kpi.warn{{border-left-color:{WARN}}}
.kpi-l{{color:{MUTED};font-size:10px;text-transform:uppercase;letter-spacing:.14em;
 font-weight:500}}
.kpi-v{{font-size:20px;font-weight:640;margin:3px 0 1px;letter-spacing:-.01em}}
.kpi-s{{color:{MUTED};font-size:11px}}
table{{width:100%;border-collapse:separate;border-spacing:0;font-size:12px}}
th{{color:{MUTED};font-weight:500;font-size:10px;text-transform:uppercase;
 letter-spacing:.14em;background:{SURFACE};padding:6px 8px;text-align:left;
 border-bottom:1px solid {GRID_STRONG};position:sticky;top:0}}
td{{padding:6px 8px;border-bottom:1px solid rgba(27,38,55,.62);white-space:nowrap}}
td.r,th.r{{text-align:right}}
tbody tr:hover{{background:rgba(91,141,239,.07)}}
.scroll{{max-height:460px;overflow:auto}}
.v{{display:flex;gap:11px;padding:9px 0;border-bottom:1px solid rgba(27,38,55,.62)}}
.v:last-child{{border-bottom:0}}
.tag{{flex:none;font-size:9.5px;font-weight:600;padding:2px 7px;border-radius:2px;
 height:18px;text-transform:uppercase;letter-spacing:.1em;border:1px solid}}
.t-ok{{background:rgba(53,196,140,.12);color:{POS};border-color:rgba(53,196,140,.4)}}
.t-bad{{background:rgba(224,85,92,.12);color:{NEG};border-color:rgba(224,85,92,.4)}}
.t-mid{{background:rgba(224,163,58,.12);color:{WARN};border-color:rgba(224,163,58,.4)}}
.v-t{{font-weight:600;font-size:12px}}
.v-d{{color:{MUTED};font-size:12px}}
.empty{{padding:34px;text-align:center;color:{MUTED};font-size:11.5px;
 border:1px dashed {GRID_STRONG};border-radius:3px}}
.note{{background:rgba(224,163,58,.07);border:1px solid rgba(224,163,58,.32);
 border-radius:3px;padding:9px 12px;font-size:11.5px;color:{GOLD_SOFT};margin:10px 0}}
.note b{{color:{INK}}}
pre{{color:{MUTED};background:{BG};border:1px solid {GRID};border-radius:3px;padding:10px}}
"""


def _tone(verdict: str) -> str:
    v = verdict.lower()
    if any(k in v for k in ("usable", "good", "calibrated", "consistent")) \
            and "mis-calibrated" not in v and "no better" not in v:
        return "t-ok"
    if any(k in v for k in ("weak", "diverges", "mis-calibrated", "no better")):
        return "t-bad"
    return "t-mid"


def optimizer_section(o: dict) -> str:
    """Section 4: solve TG/SUP per bucket, price the book, compare with HKJC."""
    cov = o["coverage"]
    a = o["totals"]["all"]
    px = o.get("prices") or {}
    theta = o.get("theta") or {}
    daily = o.get("daily") or []
    P = []

    P.append("<h2>4 &nbsp;Optimised TG / SUP per 5-min bucket, and the board it opens</h2>")
    P.append(
        f"<div class='note'>Each scored bucket solves for the total goals and "
        f"supremacy that maximise expected gross margin on the money about to "
        f"arrive, prices every live line from that theta, and compares the result "
        f"with the odds HKJC actually opened. Demand comes from "
        f"<b>{esc(cov['demand'])}</b>. Sampling: <b>{esc(cov['sampling'])}</b>, "
        f"giving {cov['buckets_scored']:,} scored buckets across "
        f"{cov['matches']:,} matches "
        f"({(cov['solver_ok'] or 0):,} solved by SLSQP, {(cov['solver_fallback'] or 0):,} fell "
        f"back to a grid search). A full pass would be one solve per match-bucket, "
        f"which is millions over this window, so every number here is an estimate "
        f"over the sample.</div>")

    replication = px.get("replication")
    ovp = px.get("opt_vs_actual")
    kpis = [
        kpi("E[GM] lift vs HKJC", pct(a["lift_vs_actual_pct"]),
            f"{money(a['lift_vs_actual'])} on {money(a['turnover_forecast'])}",
            "good" if (a["lift_vs_actual"] or 0) > 0 else "bad"),
        kpi("Margin, optimised", pct(a["margin_opt"]),
            f"HKJC board {pct(a['margin_actual_odds'])}"),
        kpi("Buckets improved", f"{a['buckets_better']:,}",
            f"of {a['buckets']:,} scored"),
        kpi("Pricer replication", pct((replication or {}).get("mae_rel")),
            "our price vs HKJC at their own theta",
            "good" if ((replication or {}).get("mae_rel") or 9) < 0.05 else "bad"),
        kpi("Realised margin", pct(a["margin_realized"]),
            "same buckets, settled money only"),
    ]
    P.append(f"<div class='kpis'>{''.join(kpis)}</div>")

    # --- the decomposition, which is the point of the section
    P.append("<div class='grid g2'>")
    steps = [
        ["HKJC board, as opened", money(a["egm_actual_odds"]), pct(a["margin_actual_odds"]), "reference"],
        ["same theta, our pricer", money(a["egm_hkjc_theta"]), pct(a["margin_hkjc_theta"]),
         f"{money(a['lift_from_pricer'])} from pricing/margin differences"],
        ["optimised theta", money(a["egm_opt"]), pct(a["margin_opt"]),
         f"{money(a['lift_from_theta'])} from moving theta"],
        ["realised, settled money", money(a["realized_gm"]), pct(a["margin_realized"]),
         "single draw, not an expectation"],
    ]
    P.append(card(
        "Where the expected margin comes from",
        table(["book", "E[GM]", "margin", "step"], steps, right_from=1),
        "Read top to bottom. The middle row isolates pricing and margin differences "
        "from the parameter choice: only the last step is a genuine theta edge. If "
        "row two is far from row one, fix the pricer before believing row three."))
    P.append(card(
        "Prematch vs in-play",
        table(["scope", "buckets", "turnover", "E[GM] opt", "E[GM] HKJC", "lift", "lift %"],
              [[esc(k), f"{v['buckets']:,}", money(v["turnover_forecast"]),
                money(v["egm_opt"]), money(v["egm_actual_odds"]),
                money(v["lift_vs_actual"]), pct(v["lift_vs_actual_pct"])]
               for k, v in o["totals"].items()], right_from=1),
        "In-play is where theta moves fastest and where a parameter edge is worth "
        "the most, but also where demand forecasting is hardest."))
    P.append("</div>")

    # --- theta comparison
    P.append("<div class='grid g2'>")
    for domain, t in theta.items():
        P.append(card(
            f"Optimised vs HKJC total goals \u2014 {esc(domain)}",
            scatter_chart([(p["x"], p["y"]) for p in t.get("scatter", [])],
                          xlabel=f"HKJC TG ({domain})", ylabel="optimised TG",
                          log=False,
                          note=f"MAE {num(t['d_tg_mae'], 3)}, mean "
                               f"{num(t['d_tg_mean'], 3)}"),
            "On the dashed line means the optimiser agrees with HKJC. Systematic "
            "offset above or below the line is the parameter edge, in goals."))
    if theta:
        trow = [[esc(d), f"{t['n']:,}", num(t["tg_hkjc_mean"], 3), num(t["tg_opt_mean"], 3),
                 num(t["d_tg_mean"], 3), num(t["d_tg_mae"], 3),
                 num(t["sup_hkjc_mean"], 3), num(t["sup_opt_mean"], 3),
                 num(t["d_sup_mean"], 3), num(t["d_sup_mae"], 3)]
                for d, t in theta.items()]
        P.append(card(
            "Theta shift, in goals",
            table(["domain", "buckets", "TG HKJC", "TG opt", "\u0394 TG mean",
                   "\u0394 TG MAE", "SUP HKJC", "SUP opt", "\u0394 SUP mean",
                   "\u0394 SUP MAE"], trow, right_from=1),
            "Mean delta is the direction of the edge; MAE is how far the optimiser "
            "roams bucket to bucket. A large MAE with a near-zero mean means it is "
            "chasing noise rather than holding a view."))
    P.append("</div>")

    # --- prices
    P.append("<div class='grid g2'>")
    if replication:
        P.append(card(
            "Pricer replication: our price at HKJC's own theta",
            hist_chart(replication["hist"]["edges"], replication["hist"]["counts"],
                       xlabel="our odds / HKJC odds - 1", marker=0.0),
            "This is the check to read first. Feed HKJC's parameters into our pricer "
            "and we should reproduce their board. Mass away from zero means the "
            "margin table or the line convention is off, and any lift above is "
            "measuring that error rather than a real edge."))
    if ovp:
        P.append(card(
            "Our board vs HKJC's board",
            hist_chart(ovp["hist"]["edges"], ovp["hist"]["counts"],
                       xlabel="optimised odds / HKJC odds - 1", marker=0.0),
            f"Left of zero means we would have opened shorter than HKJC. "
            f"{pct(ovp['shorter_share'])} of prices are shorter, median "
            f"{pct(ovp['median_rel'])}."))
    P.append("</div>")

    if replication and replication.get("by_pool"):
        P.append("<div class='grid g1'>" + card(
            "Pricer replication by pool",
            table(["pool", "prices", "mean rel error", "mean abs rel error"],
                  [[esc(r["pool_name"]), f"{r['n']:,}", pct(r["mean_rel"]),
                    pct(r["mae_rel"])] for r in replication["by_pool"]], right_from=1),
            "A single bad pool usually means its margin is wrong in the table, or "
            "its handicap sign is reversed. Try --hdc-sign flip for the DC pools.")
            + "</div>")

    # --- daily and by clock
    if daily:
        P.append("<div class='grid g1'>" + card(
            "Daily expected gross margin",
            line_chart([d["day"] for d in daily],
                       [("optimised", [d["egm_opt"] for d in daily], GOLD),
                        ("HKJC board", [d["egm_actual_odds"] for d in daily], IRIS),
                        ("realised", [d["realized_gm"] for d in daily], POS)],
                       ylabel="expected GM (HKD, sampled buckets)", xlabel="day"),
            "Sampled buckets only, so the level is a fraction of the real book. The "
            "gap between the lines is what matters, not their height.") + "</div>")

    by_clock = o.get("by_slice") or []
    if by_clock:
        P.append("<div class='grid g2'>")
        P.append(card(
            "Lift by match clock",
            bar_chart([r["value"] for r in by_clock],
                      [("lift, share of turnover",
                        [r["lift_vs_actual_pct"] for r in by_clock], GOLD)],
                      ylabel="E[GM] lift / turnover", xlabel="clock bucket", hline=0.0),
            "Where the parameter edge actually pays. Bars below the red line are "
            "phases where the optimiser would have priced worse than HKJC."))
        P.append(card(
            "Margin by clock: optimised vs HKJC",
            bar_chart([r["value"] for r in by_clock],
                      [("optimised", [r["margin_opt"] for r in by_clock], GOLD),
                       ("HKJC board", [r["margin_actual_odds"] for r in by_clock], IRIS)],
                      ylabel="margin (share of turnover)", xlabel="clock bucket"),
            "Both priced against the same true probabilities and the same demand."))
        P.append("</div>")

    rows = [[esc(r["value"]), f"{r['buckets']:,}", money(r["turnover_forecast"]),
             money(r["egm_opt"]), money(r["egm_hkjc_theta"]), money(r["egm_actual_odds"]),
             pct(r["margin_opt"]), pct(r["margin_actual_odds"]),
             pct(r["lift_vs_actual_pct"]), f"{r['buckets_better']:,}"]
            for r in by_clock]
    if rows:
        P.append("<div class='grid g1'>" + card(
            "Optimiser detail by clock",
            table(["clock", "buckets", "turnover", "E[GM] opt", "E[GM] same theta",
                   "E[GM] HKJC", "margin opt", "margin HKJC", "lift %", "better"],
                  rows, right_from=1)) + "</div>")
    return "".join(P)


def build_html(rep: dict, cfg: dict) -> str:
    cov = rep["coverage"]
    tm = rep["turnover"]["models"]
    bl = rep["turnover"]["bucket_level"]
    bm = rep["belief"]["models"]
    cal = rep["belief"]["calibration"]
    gm = rep["gm"]["overall"]
    daily = rep["daily"]

    # ---- header + verdicts
    parts = [f"<!doctype html><html><head><meta charset='utf-8'>"
             f"<title>AlgoE baseline backtest {esc(cfg['start_date'])} to "
             f"{esc(cfg['end_date'])}</title><style>{CSS}</style></head><body>"]
    parts.append(
        f"<header><h1>AlgoE backtest &mdash; {esc(cfg['start_date'])} to "
        f"{esc(cfg['end_date'])}</h1><div class='rule'></div>"
        f"<div class='m'>assumptions under test: true probability = 1 / true odds"
        f" &nbsp;&middot;&nbsp; next {cfg['bucket_minutes']}-min turnover = last "
        f"{cfg['bucket_minutes']}-min turnover &nbsp;&middot;&nbsp; pools "
        f"{esc(', '.join(cfg['pools']))} &nbsp;&middot;&nbsp; {cov['days']} days, "
        f"{cov['rows']:,} selection-buckets, {cov['matches']:,} matches, "
        f"turnover {money(cov['turnover'])}</div></header><main>")

    vrows = "".join(
        f"<div class='v'><span class='tag {_tone(v['verdict'])}'>{esc(v['verdict'])}</span>"
        f"<div><div class='v-t'>{esc(v['topic'])}</div>"
        f"<div class='v-d'>{esc(v['detail'])}</div></div></div>"
        for v in rep.get("verdict", []))
    parts.append("<h2>Verdict</h2>")
    parts.append(f"<section class='card'>{vrows or empty('no verdict')}</section>")

    # ---- KPIs
    persist = tm.get("active|persist", {})
    zero = tm.get("active|zero", {})
    pt = bm.get("p_true", {})
    ptc = cal.get("p_true", {})
    kpis = [
        kpi("Persistence WAPE", pct(persist.get("wape")),
            "per selection-bucket, active only",
            "good" if (persist.get("wape") or 9) < 0.6 else "warn"),
        kpi("Market-wide WAPE", pct((bl.get("persist") or {}).get("wape")),
            "money summed across all selections",
            "good" if ((bl.get("persist") or {}).get("wape") or 9) < 0.25 else "warn"),
        kpi("Forecast bias", pct(persist.get("bias_pct")),
            f"vs forecast-zero WAPE {pct(zero.get('wape'))}"),
        kpi("Money-weighted ECE", pct(ptc.get("money_ece")),
            f"over {cov.get('settled_selections', 0):,} settled selections",
            "good" if (ptc.get("money_ece") or 9) < 0.03 else "bad"),
        kpi("Money log-loss", num(pt.get("money_log_loss")),
            f"public odds {num((bm.get('p_sell') or {}).get('money_log_loss'))}"),
        kpi("Realised margin", pct(gm.get("realized_margin")),
            f"expected {pct(gm.get('expected_margin'))}",
            "good" if abs(gm.get("margin_gap") or 1) < 0.01 else "bad"),
    ]
    parts.append(f"<div class='kpis'>{''.join(kpis)}</div>")

    if cov.get("settled_rows", 0) == 0:
        parts.append("<div class='note'>No settled rows were found, so every "
                     "probability and gross-margin number is unavailable. That means "
                     "Match_Investments had no usable <code>dividend</code> column for "
                     "this window. Turnover forecasting results below are still valid."
                     "</div>")

    # ---- section 1: next-bucket money
    days = [d["day"] for d in daily]
    parts.append("<h2>1 &nbsp;Does next 5-min turnover equal last 5-min turnover?</h2>")
    parts.append("<div class='grid g1'>")
    parts.append(card(
        "Daily turnover: actual vs persistence forecast",
        line_chart(days,
                   [("actual", [d["turnover"] for d in daily], BLUE),
                    ("persistence", [d["forecast_persist"] for d in daily], ORANGE)],
                   ylabel="turnover (HKD)", xlabel="day"),
        "If the two lines sit on top of each other the assumption carries the daily "
        "level. Gaps show which days it under- or over-shoots."))
    parts.append("</div><div class='grid g2'>")
    parts.append(card(
        "Forecast vs actual per selection-bucket",
        scatter_chart([(r.get("f_persist"), r.get("turnover"))
                       for r in rep["turnover"]["scatter"]],
                      xlabel="forecast = last 5 min (HKD, log)",
                      ylabel="actual next 5 min (HKD, log)",
                      note=f"sample of {len(rep['turnover']['scatter']):,} active buckets"),
        "Dashed line is a perfect forecast. Points are only drawn where both values are "
        "positive, since a log axis cannot show zeros."))
    parts.append(card(
        "Forecast error distribution",
        hist_chart(rep["turnover"]["resid_hist"].get("persist", {}).get("edges", []),
                   rep["turnover"]["resid_hist"].get("persist", {}).get("counts", []),
                   xlabel="forecast minus actual (HKD, log-spaced bins)",
                   symlog=True),
        "Left of centre means the forecast was too low. A tall centre bar is good."))
    parts.append("</div>")

    tur_rows = []
    for name in ("persist", "ema", "ma3", "zero"):
        for subset in ("all", "active", "money"):
            m = tm.get(f"{subset}|{name}")
            if not m:
                continue
            tur_rows.append([esc(name), esc(subset), f"{m['n']:,}", money(m["sum_actual"]),
                             money(m["sum_forecast"]), money(m["mae"]),
                             pct(m["wape"]), pct(m["bias_pct"])])
    parts.append("<div class='grid g1'>" + card(
        "Turnover model scoreboard",
        table(["model", "subset", "buckets", "actual", "forecast", "MAE", "WAPE", "bias"],
              tur_rows, right_from=2),
        "subset: all = every live selection-bucket including zeros; active = buckets "
        "with money now or in the previous bucket; money = buckets that took money. "
        "'zero' is the do-nothing benchmark any model must beat."))

    by_clock = [r for r in rep["turnover"]["by_slice"]
                if r["dim"] == "clock_bin" and r["model"] == "persist"]
    by_pool = [r for r in rep["turnover"]["by_slice"]
               if r["dim"] == "pool_name" and r["model"] == "persist"]
    parts.append("</div><div class='grid g2'>")
    parts.append(card(
        "WAPE by match clock",
        bar_chart([r["value"] for r in by_clock],
                  [("persistence", [r["wape"] for r in by_clock], BLUE)],
                  ylabel="WAPE", xlabel="clock bucket"),
        "Where the assumption breaks. Tall bars are phases where money moves too fast "
        "for the last bucket to describe the next one."))
    parts.append(card(
        "WAPE by pool",
        bar_chart([r["value"] for r in by_pool],
                  [("persistence", [r["wape"] for r in by_pool], BLUE)],
                  ylabel="WAPE", xlabel="pool"),
        "Pools where persistence is weakest need a real turnover model first."))
    parts.append("</div>")

    # ---- section 2: belief
    parts.append("<h2>2 &nbsp;Is 1 / true odds the right probability?</h2>")
    n_sel = cov.get("settled_selections", 0)
    noise = 0.5 / math.sqrt(n_sel) if n_sel else None
    parts.append(
        f"<div class='note'>Read these two sections against their effective sample "
        f"size. Every 5-min bucket of one selection shares a single settled outcome, "
        f"so the {cov.get('settled_rows', 0):,} scored rows carry only "
        f"<b>{n_sel:,} independent outcomes</b>. Sampling noise alone moves ECE and the "
        f"realised-vs-expected margin gap by roughly "
        f"&plusmn;{pct(noise) if noise else 'n/a'}, so treat anything smaller than that "
        f"as agreement, not as signal.</div>")
    parts.append("<div class='grid g2'>")
    parts.append(card(
        "Calibration, money-weighted",
        calibration_chart(cal, "money"),
        "Believed probability against what actually happened, weighting each row by the "
        "money taken at that price. On the dashed line means the belief is honest."))
    parts.append(card(
        "Calibration, unweighted",
        calibration_chart(cal, "count"),
        "Same curve counting every selection equally, so thin markets get equal say."))
    parts.append("</div><div class='grid g2'>")
    parts.append(card(
        "True-odds book sum per line",
        hist_chart(rep["belief"]["book_sum_hist"]["edges"],
                   rep["belief"]["book_sum_hist"]["counts"],
                   xlabel="sum of 1/true_odds across a line", marker=1.0),
        "A coherent set of true odds sums to 1.00 (red line). Mass away from 1.00 means "
        "true_odds is stale or carries its own margin, and normalising is doing real work."))
    bel_rows = [[esc(k), f"{v['n']:,}", num(v["log_loss"]), num(v["brier"]),
                 num(v["money_log_loss"]), num(v["money_brier"]),
                 pct(v["money_signed_err"]),
                 pct((cal.get(k) or {}).get("ece")),
                 pct((cal.get(k) or {}).get("money_ece"))]
                for k, v in bm.items()]
    parts.append(card(
        "Belief scoreboard",
        table(["belief", "rows", "log-loss", "Brier", "money log-loss", "money Brier",
               "signed err", "ECE", "money ECE"], bel_rows),
        "p_true = 1/true_odds; p_sell = public odds normalised. If p_true does not beat "
        "p_sell, the true-odds feed adds nothing over the price already on the board."))
    parts.append("</div>")

    # ---- section 3: money
    parts.append("<h2>3 &nbsp;Does the belief price the book correctly?</h2>")
    parts.append("<div class='grid g1'>")
    parts.append(card(
        "Daily gross margin: realised vs expected",
        line_chart(days,
                   [("realised (turnover - dividend)",
                     [d["realized_gm"] for d in daily], GREEN),
                    ("expected under 1/true_odds",
                     [d["expected_gm"] for d in daily], ORANGE)],
                   ylabel="gross margin (HKD)", xlabel="day"),
        "Realised is real money out of settled bets. Expected is what the belief says "
        "the same turnover should have earned. Persistent separation means the belief is "
        "biased, not just noisy."))
    parts.append("</div>")
    gm_pool = [r for r in rep["gm"]["by_slice"] if r["dim"] == "pool_name"]
    gm_clock = [r for r in rep["gm"]["by_slice"] if r["dim"] == "clock_bin"]
    parts.append("<div class='grid g2'>")
    parts.append(card(
        "Margin by pool",
        bar_chart([r["value"] for r in gm_pool],
                  [("realised", [r["realized_margin"] for r in gm_pool], GREEN),
                   ("expected", [r["expected_margin"] for r in gm_pool], ORANGE)],
                  ylabel="margin (share of turnover)", xlabel="pool"),
        "Gaps between the pairs show where the belief mis-prices."))
    parts.append(card(
        "Margin by match clock",
        bar_chart([r["value"] for r in gm_clock],
                  [("realised", [r["realized_margin"] for r in gm_clock], GREEN),
                   ("expected", [r["expected_margin"] for r in gm_clock], ORANGE)],
                  ylabel="margin (share of turnover)", xlabel="clock bucket"),
        "In-play phases are where a wrong belief costs the most."))
    parts.append("</div>")
    gm_rows = [[esc(r["dim"]), esc(r["value"]), f"{r['n']:,}", money(r["turnover"]),
                money(r["realized_gm"]), money(r["expected_gm"]),
                pct(r["realized_margin"]), pct(r["expected_margin"]),
                pct(r["margin_gap"])] for r in rep["gm"]["by_slice"]]
    parts.append("<div class='grid g1'>" + card(
        "Gross margin detail",
        table(["dim", "value", "buckets", "turnover", "realised GM", "expected GM",
               "realised margin", "expected margin", "gap"], gm_rows, right_from=2)) + "</div>")

    if rep.get("optimizer"):
        parts.append(optimizer_section(rep["optimizer"]))

    # ---- appendix
    slice_rows = [[esc(r["dim"]), esc(r["value"]), esc(r["model"]), f"{r['n']:,}",
                   money(r["sum_actual"]), money(r["mae"]), pct(r["wape"]),
                   pct(r["bias_pct"])] for r in rep["turnover"]["by_slice"]]
    parts.append("<h2>Appendix</h2><div class='grid g1'>")
    parts.append(card("Turnover accuracy by slice",
                      table(["dim", "value", "model", "buckets", "actual", "MAE",
                             "WAPE", "bias"], slice_rows, right_from=3)))
    parts.append(card("Run configuration",
                      f"<pre style='font-size:11.5px;overflow:auto'>"
                      f"{esc(json.dumps(cfg, indent=2))}</pre>"))
    parts.append("</div></main></body></html>")
    return "".join(parts)
