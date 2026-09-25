"use client";

/**
 * Data hooks.
 *
 * A tick is a five-minute bucket, so the board polls on that cadence rather
 * than hammering the API. Polling keeps the previous payload on screen while
 * the next one is in flight - a board that blanks out every refresh is
 * unreadable, and a stale number labelled stale is more useful than no
 * number at all.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { Board, Cockpit } from "@/types/api";

export interface Resource<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** True while refreshing something already on screen. */
  refreshing: boolean;
  updatedAt: number | null;
  reload: (refresh?: boolean) => Promise<void>;
}

function useResource<T>(
  load: (refresh: boolean) => Promise<T>,
  deps: unknown[],
  // A number, or a function of the current payload: the board polls on the
  // bucket normally but much faster while the backend is still solving,
  // and that is a property of what came back, not of the caller.
  pollMs: number | ((data: T | null) => number) = 0,
): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const alive = useRef(true);
  const hasData = useRef(false);

  const run = useCallback(
    async (refresh = false) => {
      if (hasData.current) setRefreshing(true);
      else setLoading(true);
      try {
        const next = await load(refresh);
        if (!alive.current) return;
        setData(next);
        hasData.current = true;
        setError(null);
        setUpdatedAt(Date.now());
      } catch (err) {
        if (!alive.current) return;
        setError(err instanceof ApiError ? err.message : String(err));
      } finally {
        if (alive.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    // The dependency list is the caller's, which is the whole point of a
    // generic resource hook; the lint rules both want a literal here.
    // eslint-disable-next-line react-hooks/exhaustive-deps, react-hooks/use-memo
    deps,
  );

  useEffect(() => {
    alive.current = true;
    hasData.current = false;
    void run();
    return () => {
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const interval = typeof pollMs === "function" ? pollMs(data) : pollMs;
  useEffect(() => {
    if (!interval) return;
    const id = setInterval(() => void run(), interval);
    return () => clearInterval(id);
  }, [run, interval]);

  return { data, error, loading, refreshing, updatedAt, reload: run };
}

/** How often to poll while the backend is still solving the card. */
const SOLVING_POLL_MS = 2_000;

// The board is served before the recommendations exist, so while they are
// landing it is worth asking often; once the card is solved there is
// nothing new until the next bucket.
export function useBoard(pollMs = 60_000): Resource<Board> {
  return useResource<Board>(
    (refresh) => api.board(refresh),
    [],
    (data) => (data?.progress?.solving ? SOLVING_POLL_MS : pollMs),
  );
}

export function useCockpit(matchId: number | string, pollMs = 60_000): Resource<Cockpit> {
  return useResource<Cockpit>(
    (refresh) => api.cockpit(matchId, refresh),
    [matchId],
    (data) => (data?.solving ? SOLVING_POLL_MS : pollMs),
  );
}

/** Seconds since a timestamp, ticking once a second. */
export function useAge(iso: string | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return Math.max(0, Math.round((now - then) / 1000));
}
