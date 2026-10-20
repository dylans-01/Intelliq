import { BarChart3, ChevronLeft, Code2, Download, MoreHorizontal, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { downloadPost } from "@/api/client";
import { useWorksheetData } from "@/api/hooks";
import type { DashboardSpec, RuntimeState, Scalar, Worksheet } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import { Dialog, ErrorState, IconButton, Menu, Skeleton, Spinner, useToast } from "@/design-system";
import { readPalette, type ColorRegistry } from "@/viz/colors";
import { bind, type Highlight, type Tuple } from "@/viz/model";
import { TableViz } from "@/viz/plugins/TableViz";
import { VizRenderer } from "@/viz/VizRenderer";

export function WorksheetTile({
  ws,
  spec,
  dashboardId,
  draft,
  state,
  colors,
  selected,
  highlight,
  onSelect,
  drillPath,
  onDrill,
  hierarchyLevels,
  preview,
  previewImportId,
}: {
  ws: Worksheet;
  spec: DashboardSpec;
  dashboardId?: string;
  draft: boolean;
  state: RuntimeState;
  colors: ColorRegistry;
  selected: Tuple[];
  highlight: Highlight | null;
  onSelect: (tuple: Tuple | null, additive: boolean) => void;
  drillPath: Scalar[];
  onDrill: (path: Scalar[]) => void;
  hierarchyLevels?: string[];
  preview?: boolean;
  previewImportId?: string;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const [view, setView] = useState<"chart" | "table">("chart");
  const [sqlOpen, setSqlOpen] = useState(false);
  const query = useWorksheetData({ dashboardId, worksheetId: ws.id, state, draft, previewSpec: preview ? spec : undefined, previewImportId });
  const result = query.data;

  const canDrill = !!ws.drill_hierarchy && !!hierarchyLevels && drillPath.length < hierarchyLevels.length - 1;
  const handleSelect = (tuple: Tuple | null, additive: boolean) => {
    if (tuple && canDrill && !additive) {
      // Drill into the clicked member of the hierarchy's current level.
      const dimField = ws.viz.fields.find((f) => f.role === "dimension" && f.use.field && hierarchyLevels!.includes(f.use.field));
      if (dimField && dimField.key in tuple) {
        onDrill([...drillPath, tuple[dimField.key]]);
        return;
      }
    }
    onSelect(tuple, additive);
  };

  const breadcrumb = useMemo(() => {
    if (!drillPath.length || !result) return null;
    return drillPath.map((v) => (v === null ? "Null" : String(v))).join(" › ");
  }, [drillPath, result]);

  const exportCsv = async () => {
    if (!dashboardId) return;
    try {
      await downloadPost(`/api/dashboards/${dashboardId}/worksheets/${ws.id}/export.csv`, { state, draft }, `${ws.title || ws.id}.csv`);
    } catch (e) {
      toast((e as Error).message, "critical");
    }
  };

  const showTitle = ws.show_title !== false && (ws.title || ws.subtitle);
  const isKpi = ws.viz.type === "kpi";
  const menuItems = [
    { label: view === "chart" ? "View as table" : "View as chart", icon: view === "chart" ? <Table2 /> : <BarChart3 />, onSelect: () => setView(view === "chart" ? "table" : "chart") },
    ...(dashboardId && can("export_data") ? [{ label: "Export CSV", icon: <Download />, onSelect: () => void exportCsv() }] : []),
    ...(can("view_sql") && result?.sql ? [{ label: "View generated SQL", icon: <Code2 />, onSelect: () => setSqlOpen(true) }] : []),
  ];

  return (
    <section className={`dash-tile${isKpi ? " dash-tile--kpi" : ""}`} aria-label={ws.title || ws.id} data-worksheet={ws.id}>
      <header className="dash-tile__header">
        <div className="dash-tile__titles">
          {showTitle && <h3 className="dash-tile__title">{ws.title}</h3>}
          {breadcrumb ? (
            <div className="dash-tile__crumb">
              <button className="dash-tile__up" onClick={() => onDrill(drillPath.slice(0, -1))} aria-label="Drill up">
                <ChevronLeft aria-hidden /> Up
              </button>
              <span>{breadcrumb}</span>
            </div>
          ) : (
            ws.subtitle && !isKpi && <p className="dash-tile__subtitle">{ws.subtitle}</p>
          )}
        </div>
        <div className="dash-tile__actions">
          {query.isFetching && result && <Spinner label="Refreshing" />}
          {!isKpi && (
            <Menu
              label={`${ws.title || "Worksheet"} options`}
              items={menuItems}
              trigger={(props) => <IconButton {...props} size="sm" label="Worksheet options" icon={<MoreHorizontal />} />}
            />
          )}
        </div>
      </header>
      <div className={`dash-tile__body${query.isFetching && result ? " is-refreshing" : ""}`}>
        {query.isError && !result ? (
          <ErrorState compact title="Couldn't load this view" body={(query.error as Error).message} />
        ) : !result ? (
          <TileSkeleton kind={ws.viz.type} />
        ) : view === "table" && !isKpi ? (
          <TableViz
            ctx={{ viz: ws.viz, fields: bind(ws.viz, result), rows: result.rows, palette: readPalette(), colors, selected, highlight }}
            result={result}
            onSelect={handleSelect}
          />
        ) : (
          <VizRenderer viz={ws.viz} result={result} colors={colors} selected={selected} highlight={highlight} onSelect={handleSelect} title={ws.title} />
        )}
      </div>
      {result?.truncated && (
        <footer className="dash-tile__footer">Showing the first {result.row_count.toLocaleString()} rows</footer>
      )}
      <Dialog open={sqlOpen} onClose={() => setSqlOpen(false)} title="Generated SQL" description={`${result?.duration_ms ?? 0} ms${result?.cache_hit ? " · served from cache" : ""}`} wide>
        <pre className="dash-sql">{result?.sql}</pre>
        {result && (
          <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
            {result.row_count.toLocaleString()} rows · columns: {result.columns.map((c) => c.label).join(", ")}
          </p>
        )}
      </Dialog>
    </section>
  );
}

function TileSkeleton({ kind }: { kind: string }) {
  if (kind === "kpi") return <Skeleton style={{ height: 28, width: "60%", marginTop: "auto" }} />;
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 10, height: "100%", padding: "8px 4px" }} aria-hidden>
      {[40, 65, 50, 80, 58, 72, 45].map((h, i) => (
        <Skeleton key={i} style={{ flex: 1, height: `${h}%`, maxWidth: 24 }} />
      ))}
    </div>
  );
}

export function TextTile({ text }: { text: string }) {
  return <div className="dash-text">{text}</div>;
}

