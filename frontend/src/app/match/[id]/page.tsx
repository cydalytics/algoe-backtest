"use client";

/**
 * The cockpit.
 *
 * Three columns: the theta rail a trader works in, the book they are
 * pricing, and the inspector that explains it. Everything on the screen
 * comes from one backend tick, so the recommendation, the book and the
 * attribution are always describing the same moment.
 */

import { ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import { use, useEffect, useMemo, useRef, useState } from "react";

import { LastUpdate, PhaseChip, Tag } from "@/components/desk/chips";
import { BookTree } from "@/components/desk/book-tree";
import { Inspector } from "@/components/desk/inspector";
import { EmptyState, Panel, SkeletonRows } from "@/components/desk/primitives";
import { ThetaPanel } from "@/components/desk/theta-panel";
import { api } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { useCockpit } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { Selection, ThetaDim, WhatIf } from "@/types/api";

export default function CockpitPage({ params }: PageProps<"/match/[id]">) {
  const { id } = use(params);
  const { data, error, loading, refreshing, reload } = useCockpit(id);
  const [draft, setDraft] = useState<Partial<Record<ThetaDim, number>>>({});
  const [whatIf, setWhatIf] = useState<WhatIf | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const selections = useMemo<Selection[]>(
    () =>
      data
        ? data.book.families
            .flatMap((f) => f.pools)
            .flatMap((p) => p.lines)
            .flatMap((l) => l.selections)
        : [],
    [data],
  );

  const selected = useMemo(
    () => selections.find((s) => s.key === selectedKey) ?? null,
    [selections, selectedKey],
  );

  // Opening a match means the trader wants this one, so jump it to the
  // front of the solver's queue rather than waiting for it to come round.
  const pending = data?.pending && data?.solving;
  const jumped = useRef<string | null>(null);
  useEffect(() => {
    if (!pending || jumped.current === String(id)) return;
    jumped.current = String(id);
    void api
      .solve(id)
      .then(() => reload())
      .catch(() => {
        /* the background queue will get to it anyway */
      });
  }, [pending, id, reload]);

  if (loading && !data) {
    return <SkeletonRows rows={14} cols={9} />;
  }
  if (error && !data) {
    return <EmptyState title="Cannot load this match" body={error} />;
  }
  if (!data) return null;

  const m = data.match;
  const opt = data.optimizer;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-border bg-surface/60 px-3 py-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <Link href="/" className="desk-btn">
            <ArrowLeft className="h-3.5 w-3.5" />
            Board
          </Link>

          <div className="flex items-baseline gap-2">
            <h1 className="text-[15px] font-semibold">
              {m.home} <span className="px-1 text-muted-foreground">v</span> {m.away}
            </h1>
            <span className="desk-value text-[15px] text-gold-soft">
              {m.score[0]}–{m.score[1]}
            </span>
            <span className="text-[11px] text-muted-foreground">
              {m.corners[0]}–{m.corners[1]} corners
            </span>
          </div>

          <PhaseChip phase={m.phase} minute={m.minute} />
          <Tag tone="muted">{m.league}</Tag>
          {m.ht_score[0] != null && (
            <Tag tone="muted">
              HT {m.ht_score[0]}–{m.ht_score[1]}
            </Tag>
          )}
          <Tag
            tone={(data.theta.age_min ?? 0) >= 10 ? "warn" : "muted"}
            title="Age of the algo_param snapshot behind these parameters"
          >
            θ {fmtNum(data.theta.age_min, 0)}m old
          </Tag>

          <div className="ml-auto flex items-center gap-3">
            {opt ? (
              <>
                <span className="desk-label">Uplift</span>
                <span
                  className={cn(
                    "desk-value text-[14px]",
                    (opt.uplift ?? 0) > 0 ? "text-positive" : "text-muted-foreground",
                  )}
                >
                  {fmtNum(opt.uplift, 0)}{" "}
                  <span className="text-[11px] text-muted-foreground">
                    ({fmtNum(opt.uplift_bps, 0)} bps)
                  </span>
                </span>
              </>
            ) : (
              <Tag tone={data.solving ? "iris" : "muted"}>
                {data.solving ? "solving…" : "no recommendation"}
              </Tag>
            )}
            <LastUpdate asOf={data.as_of} />
            <button
              type="button"
              className="desk-btn"
              onClick={() => void reload(true)}
              disabled={refreshing}
            >
              <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
              Tick
            </button>
          </div>
        </div>

        {data.rules && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <span className="desk-label">Assumptions</span>
            <Tag tone="muted" title={data.rules.true_prob.note}>
              {data.rules.true_prob.label}
            </Tag>
            <Tag tone="muted" title={data.rules.turnover.note}>
              {data.rules.turnover.label}
            </Tag>
            <Tag tone="muted">θ read as {data.theta.semantics}</Tag>
          </div>
        )}
      </header>

      {/*
        The desk never scrolls sideways. On a wide screen the three panels
        sit next to each other; below that the inspector drops underneath
        the book and spans the full width, which is far more readable than
        a 460px column pushed off the right-hand edge. Narrower again and
        everything stacks and the page scrolls down.
      */}
      <div
        className={cn(
          "grid min-h-0 flex-1 gap-px bg-border",
          "grid-cols-1 overflow-y-auto",
          "lg:grid-cols-[minmax(220px,260px)_minmax(0,1fr)]",
          "lg:grid-rows-[minmax(0,1fr)_minmax(230px,36%)] lg:overflow-y-hidden",
          "2xl:grid-cols-[260px_minmax(0,1fr)_minmax(380px,440px)]",
          "2xl:grid-rows-[minmax(0,1fr)]",
        )}
      >
        <div className="flex min-h-[280px] flex-col bg-background lg:col-start-1 lg:row-start-1 lg:min-h-0">
          <ThetaPanel
            cockpit={data}
            draft={draft}
            onDraft={setDraft}
            onWhatIf={setWhatIf}
          />
        </div>

        <Panel
          title="Book"
          hint={`${data.book.totals.open} of ${data.book.totals.selections} selections open`}
          className="min-h-[320px] border-0 lg:col-start-2 lg:row-start-1 lg:min-h-0"
          bodyClassName="flex min-h-0 flex-col"
        >
          <BookTree
            book={data.book}
            whatIf={whatIf}
            selectedKey={selectedKey}
            onSelect={setSelectedKey}
          />
        </Panel>

        <Panel
          title="Inspector"
          hint={selected ? `${selected.pool_code} ${selected.sel_label}` : undefined}
          className={cn(
            "min-h-[280px] border-0 lg:min-h-0",
            "lg:col-start-1 lg:row-start-2 lg:col-span-2",
            "2xl:col-start-3 2xl:row-start-1 2xl:col-span-1",
          )}
          bodyClassName="flex min-h-0 flex-col"
        >
          <Inspector cockpit={data} selection={selected} />
        </Panel>
      </div>
    </div>
  );
}
