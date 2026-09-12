import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import * as api from "../api/client";
import type { Session } from "../api/types";

interface AuthValue {
  session: Session | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(api.currentSession);

  // The client owns the session because it is the thing that refreshes it on a
  // 401. React subscribes rather than duplicating it, so a refresh triggered
  // deep inside a data fetch still updates the header without a round trip
  // through component state.
  useEffect(() => api.onSessionChange(setSession) as unknown as () => void, []);

  const value = useMemo<AuthValue>(
    () => ({
      session,
      login: async (email, password) => {
        await api.login(email, password);
      },
      logout: () => api.logout(),
    }),
    [session]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
