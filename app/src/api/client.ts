import type { Envelope, Session } from "./types";

// The API key travels in a header, not the query parameter the reference
// documents. It ends up in the client bundle either way, which is acceptable
// only because this assignment publishes the key in submission.json anyway. In
// production this call would go through a small server that holds the key and
// the session, and the browser would never see either.
const BASE = "https://solve.ivy.homes";
const API_KEY = "IVY26-C947D32E59D7";

const STORAGE_KEY = "ivy.session";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// ---------------------------------------------------------------------------
// session storage
// ---------------------------------------------------------------------------
// localStorage, so the session survives a refresh. The trade-off is real: any
// script running on this origin can read the token. The alternative that
// actually fixes it is an httpOnly cookie set by a server we do not have, so
// the honest position is that this is a known weakness of a static frontend
// rather than a solved problem.

export function readSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return s.access_token && s.email ? s : null;
  } catch {
    return null;
  }
}

function writeSession(s: Session | null) {
  if (s) localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  else localStorage.removeItem(STORAGE_KEY);
}

let session: Session | null = readSession();
const listeners = new Set<(s: Session | null) => void>();

export function onSessionChange(fn: (s: Session | null) => void) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setSession(s: Session | null) {
  session = s;
  writeSession(s);
  listeners.forEach((fn) => fn(s));
}

export function currentSession() {
  return session;
}

// ---------------------------------------------------------------------------
// auth
// ---------------------------------------------------------------------------
// The access token lasts 900 seconds, not the documented 86400, and there is a
// refresh endpoint the reference says does not exist. Both matter: the brief
// requires the app to still work half an hour after login, which is twice the
// token's life, so refreshing is not optional.

function toSession(email: string, body: any): Session {
  return {
    access_token: body.access_token ?? body.token,
    refresh_token: body.refresh_token ?? "",
    email: body.user?.email ?? email,
    obtained_at: Date.now(),
    expires_in: body.expires_in ?? 900,
  };
}

export async function login(email: string, password: string) {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify({ email, password }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(res.status, detailOf(body) ?? "login failed");
  }
  setSession(toSession(email, body));
  return session!;
}

export function logout() {
  const s = session;
  setSession(null);
  if (s) {
    // Best effort. The server invalidating the token is good hygiene, but the
    // user is already logged out locally whether or not this call lands.
    fetch(`${BASE}/auth/logout`, {
      method: "POST",
      headers: {
        "X-API-Key": API_KEY,
        Authorization: `Bearer ${s.access_token}`,
      },
    }).catch(() => {});
  }
}

// A single refresh in flight at a time.
//
// Without this, a screen that fires six requests on mount and gets six 401s
// would start six refreshes. Five of them would present a refresh token the
// server has already rotated away, fail, and log the user out in the middle of
// a working session. Holding one promise and having every caller await it means
// the refresh happens once and the other five continue on its result.
let refreshInflight: Promise<Session> | null = null;

function refresh(): Promise<Session> {
  if (refreshInflight) return refreshInflight;
  const s = session;
  if (!s?.refresh_token) return Promise.reject(new ApiError(401, "no session"));

  refreshInflight = fetch(`${BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify({ refresh_token: s.refresh_token }),
  })
    .then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(res.status, detailOf(body) ?? "refresh failed");
      const next: Session = {
        ...s,
        access_token: body.access_token ?? body.token ?? s.access_token,
        refresh_token: body.refresh_token ?? s.refresh_token,
        obtained_at: Date.now(),
        expires_in: body.expires_in ?? s.expires_in,
      };
      setSession(next);
      return next;
    })
    .catch((err) => {
      setSession(null);
      throw err;
    })
    .finally(() => {
      refreshInflight = null;
    });

  return refreshInflight;
}

function detailOf(body: any): string | null {
  const d = body?.detail;
  if (typeof d === "string") return d;
  // 422 bodies put an array of validation objects in `detail`, not the string
  // the reference promises.
  if (Array.isArray(d)) return d.map((e: any) => e?.msg ?? "invalid").join("; ");
  return null;
}

// ---------------------------------------------------------------------------
// requests
// ---------------------------------------------------------------------------

async function raw(path: string, params?: Record<string, unknown>, retry = true): Promise<any> {
  const url = new URL(BASE + path);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  const headers: Record<string, string> = { "X-API-Key": API_KEY };
  if (session) headers.Authorization = `Bearer ${session.access_token}`;

  const res = await fetch(url.toString(), { headers });
  if (res.status === 401 && retry && session?.refresh_token) {
    await refresh();
    return raw(path, params, false);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailOf(body) ?? res.statusText);
  return body;
}

export function get<T>(path: string, params?: Record<string, unknown>) {
  return raw(path, params) as Promise<T>;
}

// Paging is by `offset`. `page` is accepted and silently ignored, so a client
// written against the documentation re-reads the first window forever. `limit`
// is clamped to 50 whatever is asked for, and `total` under-reports by about
// 8.6%, so `has_more` is the only trustworthy stop signal.
export const MAX_LIMIT = 50;

export async function page<T>(
  path: string,
  offset: number,
  params?: Record<string, unknown>
) {
  return get<Envelope<T>>(path, { ...params, limit: MAX_LIMIT, offset });
}

export async function crawl<T>(
  path: string,
  params: Record<string, unknown> | undefined,
  onProgress?: (got: number, total: number) => void,
  signal?: { cancelled: boolean }
): Promise<T[]> {
  const out: T[] = [];
  let offset = 0;
  for (let guard = 0; guard < 400; guard++) {
    if (signal?.cancelled) break;
    const env = await page<T>(path, offset, params);
    out.push(...env.results);
    onProgress?.(out.length, env.total);
    if (!env.has_more || env.results.length === 0) break;
    offset += env.results.length;
  }
  return out;
}
