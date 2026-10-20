import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAuth, useAuth, type Capability } from "./auth/AuthProvider";
import { EmptyState, ForbiddenState, PageLoader } from "./design-system";
import { DashboardPage } from "./pages/DashboardPage";
import { DashboardsPage } from "./pages/DashboardsPage";
import { LoginPage } from "./pages/LoginPage";
import { AppShell } from "./shell/AppShell";

// Authoring and admin surfaces load on demand; viewers never download them.
const BuilderPage = lazy(() => import("./builder/BuilderPage"));
const MetricsPage = lazy(() => import("./pages/MetricsPage"));
const DataSourcesPage = lazy(() => import("./pages/DataSourcesPage"));
const ImportsPage = lazy(() => import("./pages/ImportsPage"));
const ImportDetailPage = lazy(() => import("./pages/ImportDetailPage"));
const AdminPage = lazy(() => import("./pages/AdminPage"));

function Gate({ cap, children }: { cap: Capability; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(cap)) return <ForbiddenState />;
  return <Suspense fallback={<PageLoader />}>{children}</Suspense>;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/dashboards/:id/edit"
        element={
          <RequireAuth>
            <Gate cap="edit_dashboards">
              <BuilderPage />
            </Gate>
          </RequireAuth>
        }
      />
      <Route
        path="/dashboards/new"
        element={
          <RequireAuth>
            <Gate cap="edit_dashboards">
              <BuilderPage />
            </Gate>
          </RequireAuth>
        }
      />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/dashboards" replace />} />
        <Route path="/dashboards" element={<DashboardsPage />} />
        <Route path="/dashboards/:id" element={<DashboardPage />} />
        <Route path="/metrics" element={<Gate cap="view_datasets"><MetricsPage /></Gate>} />
        <Route path="/metrics/:datasetId" element={<Gate cap="view_datasets"><MetricsPage /></Gate>} />
        <Route path="/data" element={<Gate cap="manage_connections"><DataSourcesPage /></Gate>} />
        <Route path="/import" element={<Gate cap="import_workbooks"><ImportsPage /></Gate>} />
        <Route path="/import/:id" element={<Gate cap="import_workbooks"><ImportDetailPage /></Gate>} />
        <Route path="/admin" element={<Gate cap="manage_users"><AdminPage /></Gate>} />
        <Route path="*" element={<EmptyState title="Page not found" body="The page you were looking for doesn't exist." />} />
      </Route>
    </Routes>
  );
}
