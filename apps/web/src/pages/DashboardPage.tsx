import { Copy, Eye, EyeOff, History, MoreHorizontal, Pencil, RotateCcw, Trash2, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "@/api/client";
import { useDashboard, useDashboardMutation, useDashboardVersions } from "@/api/hooks";
import type { DashboardDetail } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import { DashboardCanvas, useDashboardRuntime } from "@/dashboard/DashboardCanvas";
import { isPristine } from "@/dashboard/runtime";
import { Badge, Button, DataTable, Dialog, EmptyState, ErrorState, IconButton, Menu, PageLoader, useToast } from "@/design-system";
import { relativeTime } from "@/lib/time";
import { PageHeader } from "@/shell/AppShell";

export function DashboardPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const { can } = useAuth();
  const draft = can("view_drafts") && params.get("draft") === "1";
  const { data, isLoading, error } = useDashboard(id, draft);

  if (isLoading) return <PageLoader />;
  if (error) {
    if (error instanceof ApiError && error.status === 404) return <EmptyState title="Dashboard not found" body="It may have been deleted or not yet published." />;
    return <ErrorState body={(error as Error).message} />;
  }
  return <DashboardView key={`${data!.id}:${data!.version}`} detail={data!} draft={draft} setDraft={(d) => setParams(d ? { draft: "1" } : {})} />;
}

function DashboardView({ detail, draft, setDraft }: { detail: DashboardDetail; draft: boolean; setDraft: (d: boolean) => void }) {
  const { can } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const spec = detail.spec;
  const [state, dispatch] = useDashboardRuntime(spec);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const hasPendingEdits = detail.published_version !== null && detail.published_version !== detail.current_version;

  useEffect(() => {
    document.title = `${spec.title} · Intelliq`;
    return () => {
      document.title = "Intelliq";
    };
  }, [spec.title]);

  const publish = useDashboardMutation((version?: number) => api.post(`/api/dashboards/${detail.id}/publish`, { version }));
  const unpublish = useDashboardMutation(() => api.post(`/api/dashboards/${detail.id}/unpublish`));
  const duplicate = useDashboardMutation(() => api.post<DashboardDetail>(`/api/dashboards/${detail.id}/duplicate`));
  const remove = useDashboardMutation(() => api.delete(`/api/dashboards/${detail.id}`));

  const run = async (p: Promise<unknown>, success: string) => {
    try {
      await p;
      toast(success, "good");
    } catch (e) {
      toast((e as Error).message, "critical");
    }
  };

  const menu = [
    ...(can("edit_dashboards")
      ? [{
          label: "Duplicate",
          icon: <Copy />,
          onSelect: async () => {
            try {
              const copy = await duplicate.mutateAsync(undefined);
              navigate(`/dashboards/${copy.id}?draft=1`);
            } catch (e) {
              toast((e as Error).message, "critical");
            }
          },
        }]
      : []),
    ...(can("view_drafts") ? [{ label: "Version history", icon: <History />, onSelect: () => setVersionsOpen(true) }] : []),
    ...(can("publish_dashboards") && detail.status === "published"
      ? [{ label: "Unpublish", icon: <EyeOff />, onSelect: () => void run(unpublish.mutateAsync(undefined), "Dashboard unpublished") }]
      : []),
    ...(can("edit_dashboards") ? ["separator" as const, { label: "Delete", icon: <Trash2 />, danger: true, onSelect: () => setConfirmDelete(true) }] : []),
  ];

  return (
    <div className="page page--wide">
      <PageHeader
        title={spec.title}
        meta={
          <>
            {detail.status === "published" ? <Badge tone="good">Published</Badge> : <Badge>Draft</Badge>}
            {spec.provenance?.source === "tableau" && <Badge tone="accent">Imported from {spec.provenance.workbook_name ?? "Tableau"}</Badge>}
            <span>
              Version {detail.version} · updated {relativeTime(detail.updated_at)}
            </span>
          </>
        }
        description={spec.description || undefined}
        actions={
          <>
            {!isPristine(spec, state) && (
              <Button variant="ghost" icon={<RotateCcw />} onClick={() => dispatch({ type: "reset", spec })}>
                Reset view
              </Button>
            )}
            {can("view_drafts") && hasPendingEdits && (
              <Button variant="ghost" icon={<Eye />} onClick={() => setDraft(!draft)}>
                {draft ? "View published" : "View latest draft"}
              </Button>
            )}
            {can("publish_dashboards") && (detail.status === "draft" || (draft && hasPendingEdits)) && (
              <Button icon={<Upload />} loading={publish.isPending} onClick={() => void run(publish.mutateAsync(detail.current_version), `Version ${detail.current_version} published`)}>
                Publish
              </Button>
            )}
            {can("edit_dashboards") && (
              <Button variant="primary" icon={<Pencil />} onClick={() => navigate(`/dashboards/${detail.id}/edit`)}>
                Edit
              </Button>
            )}
            {menu.length > 0 && <Menu items={menu} trigger={(p) => <IconButton {...p} variant="secondary" label="More actions" icon={<MoreHorizontal />} />} />}
          </>
        }
      />
      {spec.worksheets.length === 0 ? (
        <div className="iq-card">
          <EmptyState title="This dashboard is empty" body={can("edit_dashboards") ? "Open the editor to add visualizations." : undefined} />
        </div>
      ) : (
        <DashboardCanvas spec={spec} dashboardId={detail.id} draft={draft || detail.status === "draft"} state={state} dispatch={dispatch} drillLevels={detail.drill_levels} />
      )}
      {versionsOpen && <VersionsDialog detail={detail} onClose={() => setVersionsOpen(false)} />}
      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete dashboard?"
        description="This permanently removes the dashboard and all of its versions."
        footer={
          <>
            <Button onClick={() => setConfirmDelete(false)}>Cancel</Button>
            <Button
              variant="danger"
              loading={remove.isPending}
              onClick={async () => {
                await run(remove.mutateAsync(undefined), "Dashboard deleted");
                navigate("/dashboards");
              }}
            >
              Delete
            </Button>
          </>
        }
      >
        <p style={{ fontSize: 13 }}>
          <strong>{spec.title}</strong> will no longer be available to anyone in this workspace.
        </p>
      </Dialog>
    </div>
  );
}

function VersionsDialog({ detail, onClose }: { detail: DashboardDetail; onClose: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const { data, isLoading } = useDashboardVersions(detail.id, true);
  const publish = useDashboardMutation((version: number) => api.post(`/api/dashboards/${detail.id}/publish`, { version }));
  return (
    <Dialog open onClose={onClose} title="Version history" description="Every save creates a version. Viewers always see the published one." wide>
      {isLoading ? (
        <PageLoader />
      ) : (
        <div className="iq-card" style={{ boxShadow: "none" }}>
          <DataTable
            rows={data ?? []}
            rowKey={(r) => String(r.version)}
            dense
            columns={[
              { key: "version", header: "Version", value: (r) => r.version, numeric: true, width: 80 },
              { key: "note", header: "Note", value: (r) => r.note },
              { key: "created", header: "Saved", value: (r) => r.created_at, render: (r) => relativeTime(r.created_at), width: 120 },
              {
                key: "state",
                header: "",
                value: (r) => r.version,
                sortable: false,
                width: 150,
                render: (r) =>
                  r.version === detail.published_version ? (
                    <Badge tone="good">Published</Badge>
                  ) : can("publish_dashboards") ? (
                    <Button
                      size="sm"
                      onClick={async () => {
                        try {
                          await publish.mutateAsync(r.version);
                          toast(`Version ${r.version} published`, "good");
                          onClose();
                        } catch (e) {
                          toast((e as Error).message, "critical");
                        }
                      }}
                    >
                      Publish this version
                    </Button>
                  ) : null,
              },
            ]}
          />
        </div>
      )}
    </Dialog>
  );
}
