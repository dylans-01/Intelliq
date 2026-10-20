import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, ApiError, session } from "@/api/client";
import type { Me } from "@/api/types";
import { PageLoader } from "@/design-system";

export type Capability =
  | "view_dashboards"
  | "view_drafts"
  | "edit_dashboards"
  | "publish_dashboards"
  | "export_data"
  | "adhoc_query"
  | "view_sql"
  | "view_datasets"
  | "manage_datasets"
  | "manage_connections"
  | "import_workbooks"
  | "manage_users"
  | "view_audit";

interface AuthValue {
  me: Me | null;
  loading: boolean;
  /** Mirrors server capabilities to shape the UI; the server still enforces every request. */
  can: (cap: Capability) => boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchWorkspace: (id: string) => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);
const WORKSPACE_KEY = "intelliq-workspace";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const queryClient = useQueryClient();

  const refresh = useCallback(async () => {
    try {
      const data = await api.get<Me>("/api/auth/me");
      session.setCsrf(data.csrf_token);
      session.setWorkspace(data.workspace.id);
      setMe(data);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401) console.error(e);
      session.setCsrf(null);
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    try {
      session.setWorkspace(localStorage.getItem(WORKSPACE_KEY));
    } catch {
      /* storage unavailable */
    }
    session.onUnauthorized(() => {
      setMe(null);
      queryClient.clear();
    });
    void refresh();
  }, [refresh, queryClient]);

  const value = useMemo<AuthValue>(
    () => ({
      me,
      loading,
      can: (cap) => !!me?.capabilities.includes(cap),
      login: async (email, password) => {
        await api.post("/api/auth/login", { email, password });
        await refresh();
      },
      logout: async () => {
        try {
          await api.post("/api/auth/logout");
        } finally {
          session.setCsrf(null);
          setMe(null);
          queryClient.clear();
        }
      },
      switchWorkspace: async (id) => {
        session.setWorkspace(id);
        try {
          localStorage.setItem(WORKSPACE_KEY, id);
        } catch {
          /* storage unavailable */
        }
        queryClient.clear();
        await refresh();
      },
    }),
    [me, loading, refresh, queryClient],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <PageLoader />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}
