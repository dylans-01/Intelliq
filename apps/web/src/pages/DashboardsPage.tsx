import { FileInput, LayoutDashboard, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDashboards } from "@/api/hooks";
import type { DashboardSummary } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import { Badge, Button, DataTable, EmptyState, ErrorState, PageLoader, SearchInput, Tabs } from "@/design-system";
import { PageHeader } from "@/shell/AppShell";
import { relativeTime } from "@/lib/time";

type Tab = "all" | "published" | "draft";

export function DashboardsPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const { data, isLoading, error } = useDashboards();
  const [tab, setTab] = useState<Tab>("all");
  const [q, setQ] = useState("");

  const rows = useMemo(
    () =>
      (data ?? [])
        .filter((d) => tab === "all" || d.status === tab)
        .filter((d) => d.title.toLowerCase().includes(q.trim().toLowerCase())),
    [data, tab, q],
  );

  if (isLoading) return <PageLoader />;
  if (error) return <ErrorState body={(error as Error).message} />;

  const counts = { all: data!.length, published: data!.filter((d) => d.status === "published").length, draft: data!.filter((d) => d.status === "draft").length };

  return (
    <div className="page">
      <PageHeader
        title="Dashboards"
        description="Analytical workspaces built on certified datasets and metrics."
        actions={
          <>
            {can("import_workbooks") && (
              <Button icon={<FileInput />} onClick={() => navigate("/import")}>
                Import from Tableau
              </Button>
            )}
            {can("edit_dashboards") && (
              <Button variant="primary" icon={<Plus />} onClick={() => navigate("/dashboards/new")}>
                New dashboard
              </Button>
            )}
          </>
        }
      />
      {data!.length === 0 ? (
        <div className="iq-card">
          <EmptyState
            icon={<LayoutDashboard />}
            title="No dashboards yet"
            body={can("edit_dashboards") ? "Create a dashboard from a dataset, or import an existing Tableau workbook." : "Dashboards appear here once they are published."}
            action={can("edit_dashboards") ? <Button variant="primary" icon={<Plus />} onClick={() => navigate("/dashboards/new")}>New dashboard</Button> : undefined}
          />
        </div>
      ) : (
        <div className="iq-card">
          <div className="list-toolbar">
            {can("view_drafts") ? (
              <Tabs<Tab>
                label="Filter dashboards"
                value={tab}
                onChange={setTab}
                tabs={[
                  { value: "all", label: "All", count: counts.all },
                  { value: "published", label: "Published", count: counts.published },
                  { value: "draft", label: "Drafts", count: counts.draft },
                ]}
              />
            ) : (
              <span />
            )}
            <div style={{ width: 260 }}>
              <SearchInput size_="sm" placeholder="Filter by title" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter dashboards by title" />
            </div>
          </div>
          <DataTable<DashboardSummary>
            rows={rows}
            rowKey={(r) => r.id}
            onRowClick={(r) => navigate(`/dashboards/${r.id}`)}
            initialSort={{ key: "updated", dir: "desc" }}
            caption="Dashboards"
            columns={[
              {
                key: "title",
                header: "Title",
                value: (r) => r.title,
                render: (r) => (
                  <span className="cell-title">
                    <LayoutDashboard aria-hidden />
                    <span>{r.title}</span>
                    {r.source === "tableau" && <Badge>Tableau import</Badge>}
                  </span>
                ),
              },
              {
                key: "status",
                header: "Status",
                value: (r) => r.status,
                width: 140,
                render: (r) =>
                  r.status === "published" ? (
                    <Badge tone="good">Published{r.published_version !== r.current_version ? " · edits pending" : ""}</Badge>
                  ) : (
                    <Badge>Draft</Badge>
                  ),
              },
              { key: "version", header: "Version", value: (r) => r.current_version, numeric: true, width: 90 },
              { key: "updated", header: "Updated", value: (r) => r.updated_at, width: 140, render: (r) => <span className="muted">{relativeTime(r.updated_at)}</span> },
            ]}
            empty={<EmptyState compact title="No dashboards match" />}
          />
        </div>
      )}
    </div>
  );
}
