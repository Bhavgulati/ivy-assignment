import { useEffect, useRef, useState } from "react";
import { loadManifest } from "../core/manifest";
import type { Flags, Manifest } from "../core/manifest";
import * as api from "../api/client";

export function useManifest() {
  const [state, setState] = useState<{
    manifest: Manifest | null;
    flags: Flags | null;
    error: string | null;
  }>({ manifest: null, flags: null, error: null });

  useEffect(() => {
    let alive = true;
    loadManifest()
      .then(({ manifest, flags }) => alive && setState({ manifest, flags, error: null }))
      .catch((e) => alive && setState({ manifest: null, flags: null, error: String(e) }));
    return () => {
      alive = false;
    };
  }, []);

  return state;
}

// A full collection, pulled once and cached for the session.
//
// The insights screen needs aggregates the API does not provide - the
// documented /v1/analytics/summary is a 404 - so the only way to compute them
// is to hold the collection. At fifty records a page that is eighty-two
// requests for listings, which is why progress is reported and partial results
// are rendered as they arrive rather than blocking on the whole thing.
const cache = new Map<string, unknown[]>();

export function useCollection<T>(path: string, enabled = true) {
  const [rows, setRows] = useState<T[]>(() => (cache.get(path) as T[]) ?? []);
  const [progress, setProgress] = useState({ got: 0, total: 0 });
  const [done, setDone] = useState(() => cache.has(path));
  const [error, setError] = useState<string | null>(null);
  const signal = useRef({ cancelled: false });

  useEffect(() => {
    if (!enabled || cache.has(path)) return;
    const sig = { cancelled: false };
    signal.current = sig;
    setDone(false);
    api
      .crawl<T>(path, undefined, (got, total) => {
        if (!sig.cancelled) setProgress({ got, total });
      }, sig)
      .then((all) => {
        if (sig.cancelled) return;
        cache.set(path, all);
        setRows(all);
        setDone(true);
      })
      .catch((e) => !sig.cancelled && setError(String(e?.message ?? e)));
    return () => {
      sig.cancelled = true;
    };
  }, [path, enabled]);

  return { rows, progress, done, error };
}
