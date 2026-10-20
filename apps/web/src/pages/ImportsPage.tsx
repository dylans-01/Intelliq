import { useQueryClient } from "@tanstack/react-query";
import { FileInput, UploadCloud } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { keys, useImports } from "@/api/hooks";
import type { ImportPayload, ImportSummary } from "@/api/types";
import { Badge, DataTable, EmptyState, ErrorState, PageLoader, Spinner, useToast } from "@/design-system";
import { bytes, relativeTime } from "@/lib/time";
import { PageHeader } from "@/shell/AppShell";

export default function ImportsPage() {
  const { data, isLoading, error } = useImports();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(false);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!/\.twbx?$/i.test(file.name)) {
      toast("Choose a Tableau workbook (.twb or .twbx)", "critical");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    setUploading(true);
    try {
      const res = await api.upload<ImportPayload>("/api/imports", form);
      await qc.invalidateQueries({ queryKey: keys.imports });
      navigate(`/import/${res.id}`);
    } catch (e) {
      toast((e as Error).message, "critical");
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    void upload(e.dataTransfer.files[0]);
  };

  return (
    <div className="page">
      <PageHeader
        title="Tableau import"
        description="Convert Tableau workbooks into Intelliq dashboards. Every worksheet, calculation, filter and action is reported as supported, adjusted, needing manual mapping, or unsupported — nothing is dropped silently."
      />
      <div
        className="dropzone"
        role="button"
        tabIndex={0}
        data-active={drag}
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
      >
        {uploading ? <Spinner label="Analyzing workbook" /> : <UploadCloud aria-hidden />}
        <strong>{uploading ? "Analyzing workbook…" : "Drop a .twb or .twbx file, or click to browse"}</strong>
        <span style={{ fontSize: 12 }}>Files are parsed safely on the server. Nothing inside a workbook is executed; packaged extracts are inventoried but not loaded.</span>
        <input ref={input} type="file" accept=".twb,.twbx" hidden onChange={(e) => void upload(e.target.files?.[0])} />
      </div>

      <h2 className="section-title">Previous imports</h2>
      {isLoading ? (
        <PageLoader />
      ) : error ? (
        <ErrorState body={(error as Error).message} />
      ) : !data?.length ? (
        <div className="iq-card">
          <EmptyState icon={<FileInput />} title="No imports yet" body="Uploaded workbooks and their compatibility reports appear here." />
        </div>
      ) : (
        <div className="iq-card">
          <DataTable<ImportSummary>
            rows={data}
            rowKey={(r) => r.id}
            onRowClick={(r) => navigate(`/import/${r.id}`)}
            columns={[
              { key: "file", header: "Workbook", value: (r) => r.filename, render: (r) => <span className="cell-title"><FileInput aria-hidden />{r.filename}</span> },
              { key: "size", header: "Size", value: (r) => r.size, render: (r) => <span className="muted">{bytes(r.size)}</span>, width: 100, numeric: true },
              { key: "fidelity", header: "Fidelity", value: (r) => r.summary.fidelity ?? 0, render: (r) => `${Math.round((r.summary.fidelity ?? 0) * 100)}%`, width: 100, numeric: true },
              {
                key: "issues",
                header: "Needs attention",
                value: (r) => (r.summary.manual ?? 0) + (r.summary.unsupported ?? 0),
                width: 160,
                render: (r) => (
                  <span className="row" style={{ gap: 6 }}>
                    {(r.summary.manual ?? 0) > 0 && <Badge tone="warning">{r.summary.manual} manual</Badge>}
                    {(r.summary.unsupported ?? 0) > 0 && <Badge tone="critical">{r.summary.unsupported} unsupported</Badge>}
                    {!(r.summary.manual ?? 0) && !(r.summary.unsupported ?? 0) && <span className="muted">—</span>}
                  </span>
                ),
              },
              { key: "status", header: "Status", value: (r) => r.status, width: 110, render: (r) => (r.status === "applied" ? <Badge tone="good">Applied</Badge> : <Badge>Analyzed</Badge>) },
              { key: "when", header: "Uploaded", value: (r) => r.created_at, render: (r) => <span className="muted">{relativeTime(r.created_at)}</span>, width: 120 },
            ]}
          />
        </div>
      )}
    </div>
  );
}
