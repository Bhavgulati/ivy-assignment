import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useAuth } from "./auth";

// /v1/favourites is documented with GET, POST and DELETE. All three return 404,
// in both the British and American spellings, so there is no server side to
// save to and this has to live in the browser.
//
// The brief requires saved listings to be per user and to survive both a reload
// and a re-login, so the key is scoped by email rather than global. Two demo
// users on the same machine keep separate lists, and logging out and back in
// restores the list rather than clearing it.
//
// The limitation is worth stating plainly: this is per browser, not per
// account. The same user on a second device sees an empty list. Fixing that
// needs the endpoint the documentation claims already exists.

const keyFor = (email: string) => `ivy.saved.${email.toLowerCase()}`;

interface SavedValue {
  ids: string[];
  has: (id: string) => boolean;
  toggle: (id: string) => void;
  remove: (id: string) => void;
}

const Ctx = createContext<SavedValue | null>(null);

function read(email: string | null): string[] {
  if (!email) return [];
  try {
    const raw = localStorage.getItem(keyFor(email));
    const v = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function SavedProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const email = session?.email ?? null;
  const [ids, setIds] = useState<string[]>(() => read(email));

  useEffect(() => setIds(read(email)), [email]);

  const persist = useCallback(
    (next: string[]) => {
      setIds(next);
      if (email) localStorage.setItem(keyFor(email), JSON.stringify(next));
    },
    [email]
  );

  const value: SavedValue = {
    ids,
    has: (id) => ids.includes(id),
    toggle: (id) =>
      persist(ids.includes(id) ? ids.filter((x) => x !== id) : [id, ...ids]),
    remove: (id) => persist(ids.filter((x) => x !== id)),
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSaved() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSaved outside SavedProvider");
  return v;
}
