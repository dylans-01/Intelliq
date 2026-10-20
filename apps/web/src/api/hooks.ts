import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AuditEntry,
  ConnectionInfo,
  ConnectorType,
  DashboardDetail,
  DashboardSpec,
  DashboardSummary,
  DatasetDetail,
  DatasetSummary,
  ImportPayload,
  ImportSummary,
  QueryResult,
  RuntimeState,
  Scalar,
  UserRow,
} from "./types";

export const keys = {
  dashboards: ["dashboards"] as const,
  dashboard: (id: string, draft: boolean) => ["dashboard", id, draft] as const,
  versions: (id: string) => ["dashboard-versions", id] as const,
  datasets: ["datasets"] as const,
  dataset: (id: string) => ["dataset", id] as const,
  connections: ["connections"] as const,
  imports: ["imports"] as const,
  import: (id: string) => ["import", id] as const,
  users: ["users"] as const,
  audit: ["audit"] as const,
};

export const useDashboards = () => useQuery({ queryKey: keys.dashboards, queryFn: () => api.get<DashboardSummary[]>("/api/dashboards") });

export const useDashboard = (id: string, draft: boolean) =>
  useQuery({
    queryKey: keys.dashboard(id, draft),
    queryFn: () => api.get<DashboardDetail>(`/api/dashboards/${id}${draft ? "?draft=true" : ""}`),
  });

export const useDashboardVersions = (id: string, enabled: boolean) =>
  useQuery({
    queryKey: keys.versions(id),
    queryFn: () => api.get<{ version: number; note: string; created_at: string; created_by: string | null }[]>(`/api/dashboards/${id}/versions`),
    enabled,
  });

function newQueryId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Run a query; if React Query aborts it (superseded or unmounted), cancel it on the server too. */
async function cancellable<T>(path: string, body: unknown, signal: AbortSignal): Promise<T> {
  const id = newQueryId();
  const onAbort = () => void api.post(`/api/query/${id}/cancel`).catch(() => undefined);
  signal.addEventListener("abort", onAbort, { once: true });
  try {
    return await api.post<T>(path, body, { signal, headers: { "X-Intelliq-Query-Id": id } });
  } finally {
    signal.removeEventListener("abort", onAbort);
  }
}

export function useWorksheetData(args: {
  dashboardId?: string;
  worksheetId: string;
  state: RuntimeState;
  draft: boolean;
  /** Unsaved spec (builder preview) — runs through the preview endpoint. */
  previewSpec?: DashboardSpec;
  previewImportId?: string;
  enabled?: boolean;
}) {
  const { dashboardId, worksheetId, state, draft, previewSpec, previewImportId, enabled = true } = args;
  return useQuery({
    queryKey: previewSpec
      ? ["preview-data", previewImportId, worksheetId, JSON.stringify(previewSpec.worksheets.find((w) => w.id === worksheetId)), JSON.stringify(previewSpec.parameters), JSON.stringify(previewSpec.filters), JSON.stringify(previewSpec.actions), state]
      : ["worksheet-data", dashboardId, draft, worksheetId, state],
    queryFn: ({ signal }) =>
      previewSpec
        ? cancellable<QueryResult>("/api/dashboards/preview/data", { spec: previewSpec, worksheet_id: worksheetId, state, import_id: previewImportId }, signal)
        : cancellable<QueryResult>(`/api/dashboards/${dashboardId}/worksheets/${worksheetId}/data`, { state, draft }, signal),
    placeholderData: keepPreviousData, // keep the previous frame while refetching
    enabled,
    staleTime: 30_000,
    retry: (count, err) => count < 1 && !(err as { status?: number }).status,
  });
}

export const useFilterValues = (dashboardId: string | undefined, filterId: string, draft: boolean, enabled: boolean) =>
  useQuery({
    queryKey: ["filter-values", dashboardId, filterId, draft],
    queryFn: () => api.get<{ values: Scalar[]; truncated: boolean }>(`/api/dashboards/${dashboardId}/filters/${filterId}/values${draft ? "?draft=true" : ""}`),
    enabled: enabled && !!dashboardId,
    staleTime: 5 * 60_000,
  });

export const useFieldValues = (datasetId: string, fieldId: string, enabled: boolean) =>
  useQuery({
    queryKey: ["field-values", datasetId, fieldId],
    queryFn: () => api.get<{ values: Scalar[]; truncated: boolean }>(`/api/datasets/${datasetId}/values/${fieldId}`),
    enabled,
    staleTime: 5 * 60_000,
  });

export const useDatasets = (enabled = true) =>
  useQuery({ queryKey: keys.datasets, queryFn: () => api.get<DatasetSummary[]>("/api/datasets"), enabled });

export const useDataset = (id: string | undefined) =>
  useQuery({ queryKey: keys.dataset(id ?? ""), queryFn: () => api.get<DatasetDetail>(`/api/datasets/${id}`), enabled: !!id });

export const useConnections = (enabled = true) =>
  useQuery({ queryKey: keys.connections, queryFn: () => api.get<ConnectionInfo[]>("/api/connections"), enabled });

export const useConnectorTypes = (enabled = true) =>
  useQuery({ queryKey: ["connector-types"], queryFn: () => api.get<ConnectorType[]>("/api/connector-types"), enabled });

export const useImports = () => useQuery({ queryKey: keys.imports, queryFn: () => api.get<ImportSummary[]>("/api/imports") });
export const useImport = (id: string) => useQuery({ queryKey: keys.import(id), queryFn: () => api.get<ImportPayload>(`/api/imports/${id}`) });

export const useUsers = () => useQuery({ queryKey: keys.users, queryFn: () => api.get<UserRow[]>("/api/admin/users") });
export const useAudit = (action?: string) =>
  useQuery({
    queryKey: [...keys.audit, action ?? ""],
    queryFn: () => api.get<AuditEntry[]>(`/api/admin/audit?limit=300${action ? `&action=${encodeURIComponent(action)}` : ""}`),
  });

/** Mutations that refresh dashboard lists afterwards. */
export function useDashboardMutation<TVars, TResult>(fn: (vars: TVars) => Promise<TResult>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.dashboards });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      void qc.invalidateQueries({ queryKey: ["dashboard-versions"] });
    },
  });
}
