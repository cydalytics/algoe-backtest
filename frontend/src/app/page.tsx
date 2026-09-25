"use client";

import { BoardTable } from "@/components/desk/board-table";
import { EmptyState, SkeletonRows } from "@/components/desk/primitives";
import { TickBar } from "@/components/desk/tick-bar";
import { API_BASE } from "@/lib/api";
import { useBoard } from "@/lib/hooks";

export default function BoardPage() {
  const { data, error, loading, refreshing, updatedAt, reload } = useBoard();

  if (loading && !data) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="border-b border-border px-3 py-3 text-[12px] text-muted-foreground">
          Running the first tick — pricing every pool on the card.
        </div>
        <SkeletonRows rows={12} cols={8} />
      </div>
    );
  }

  if (error && !data) {
    return (
      <EmptyState
        title="No connection to the pipeline"
        body={`${error}  ·  Expected the API at ${API_BASE}. Start it with: python -m uvicorn src.api.main:app --port 8001`}
      />
    );
  }

  if (!data) return null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <TickBar
        board={data}
        refreshing={refreshing}
        updatedAt={updatedAt}
        onRefresh={() => void reload(true)}
      />
      {error && (
        <div className="shrink-0 border-b border-negative/30 bg-negative/10 px-3 py-1 text-[11px] text-negative">
          Last refresh failed: {error} — showing the previous tick.
        </div>
      )}
      <BoardTable matches={data.matches} />
    </div>
  );
}
