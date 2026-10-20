"""Dashboards: CRUD, versioning, publication, data and export.

Viewers can only read *published* versions and can only influence a query
through RuntimeState (filter selections, parameters, cross-filter selections,
drill path), validated against the stored spec in ``spec.compile``.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from ...platform.rbac import Cap
from ...query.models import FieldUse, Filter, QueryRequest
from ...query.planner import PlanError
from ...semantic.model import Dataset
from ...spec.compile import RuntimeState, compile_worksheet
from ...spec.dashboard import Dashboard
from ...spec.migrations import SpecMigrationError, load
from ..context import AppContext
from ..deps import Principal, client_ip, ctx, require
from ..services import run_query

router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])


class SpecBody(BaseModel):
    spec: dict[str, Any]
    note: str = Field(default="", max_length=500)


class DataBody(BaseModel):
    state: RuntimeState = Field(default_factory=RuntimeState)
    draft: bool = False  # authors: use the latest saved version instead of the published one


class PreviewBody(BaseModel):
    spec: dict[str, Any]
    worksheet_id: str
    state: RuntimeState = Field(default_factory=RuntimeState)
    import_id: str | None = None  # overlay this import's pending calculated fields


class PublishBody(BaseModel):
    version: int | None = None


def _validate(app: AppContext, principal: Principal, raw: dict[str, Any]) -> Dashboard:
    try:
        spec = load(raw)
    except (ValidationError, SpecMigrationError) as e:
        errors = e.errors(include_url=False, include_context=False, include_input=False) if isinstance(e, ValidationError) else [{"msg": str(e)}]
        raise HTTPException(422, {"message": "Invalid dashboard specification", "errors": errors}) from None
    for ws in spec.worksheets:
        row = app.store.get_dataset(ws.dataset_id)
        if row is None or row["workspace_id"] != principal.workspace_id:
            raise HTTPException(422, {"message": f"Worksheet '{ws.id}' references an unknown dataset"})
    return spec


def _row(app: AppContext, principal: Principal, did: str, *, version: int | None = None, draft: bool = False) -> dict:
    head = app.store.get_dashboard(did)
    if head is None or head["workspace_id"] != principal.workspace_id:
        raise HTTPException(404, "Dashboard not found")
    can_drafts = principal.can(Cap.VIEW_DRAFTS)
    if version is not None:
        if not can_drafts and version != head["published_version"]:
            raise HTTPException(404, "Dashboard not found")
        row = app.store.get_dashboard(did, version)
    elif draft and can_drafts:
        row = head
    elif head["published_version"] is not None:
        row = app.store.get_dashboard(did, head["published_version"])
    elif can_drafts:
        row = head
    else:
        raise HTTPException(404, "Dashboard not found")
    if row is None:
        raise HTTPException(404, "Version not found")
    return row


def _summary(row: dict) -> dict:
    return {k: row.get(k) for k in ("id", "title", "status", "current_version", "published_version", "source",
                                    "created_by", "created_at", "updated_at")}


@router.get("")
def list_dashboards(principal: Principal = Depends(require(Cap.VIEW_DASHBOARDS)), app: AppContext = Depends(ctx)):
    rows = app.store.list_dashboards(principal.workspace_id, published_only=not principal.can(Cap.VIEW_DRAFTS))
    return [_summary(r) for r in rows]


@router.post("")
def create(body: SpecBody, request: Request, principal: Principal = Depends(require(Cap.EDIT_DASHBOARDS)),
           app: AppContext = Depends(ctx)):
    spec = _validate(app, principal, body.spec)
    did = app.store.create_dashboard(principal.workspace_id, spec.title, spec.model_dump(mode="json"), principal.id,
                                     source=spec.provenance.source)
    app.store.audit("dashboard.created", actor=principal.id, workspace=principal.workspace_id, target_type="dashboard",
                    target_id=did, ip=client_ip(request))
    return _detail(app.store.get_dashboard(did))


def _detail(row: dict | None) -> dict:
    assert row is not None
    return {**_summary(row), "version": row["version"], "spec": load(row["spec"]).model_dump(mode="json")}


def _drill_levels(app: AppContext, principal: Principal, spec: Dashboard) -> dict[str, list[str]]:
    """Hierarchy levels (field ids) for drillable worksheets. Viewers cannot read
    dataset definitions, so the dashboard exposes just what drilling needs."""

    out: dict[str, list[str]] = {}
    for ws in spec.worksheets:
        if not ws.drill_hierarchy:
            continue
        try:
            dataset, _ = app.dataset(ws.dataset_id, principal.workspace_id)
        except LookupError:
            continue
        h = next((h for h in dataset.hierarchies if h.id == ws.drill_hierarchy), None)
        if h is not None:
            out[ws.id] = list(h.levels)
    return out


@router.get("/{did}")
def get(did: str, version: int | None = None, draft: bool = False,
        principal: Principal = Depends(require(Cap.VIEW_DASHBOARDS)), app: AppContext = Depends(ctx)):
    detail = _detail(_row(app, principal, did, version=version, draft=draft))
    detail["drill_levels"] = _drill_levels(app, principal, load(detail["spec"]))
    return detail


@router.put("/{did}")
def save(did: str, body: SpecBody, request: Request, principal: Principal = Depends(require(Cap.EDIT_DASHBOARDS)),
         app: AppContext = Depends(ctx)):
    _row(app, principal, did, draft=True)
    spec = _validate(app, principal, body.spec)
    version = app.store.save_dashboard_version(did, spec.title, spec.model_dump(mode="json"), body.note or "Saved", principal.id)
    app.store.audit("dashboard.saved", actor=principal.id, workspace=principal.workspace_id, target_type="dashboard",
                    target_id=did, detail={"version": version}, ip=client_ip(request))
    return _detail(app.store.get_dashboard(did))


@router.get("/{did}/versions")
def versions(did: str, principal: Principal = Depends(require(Cap.VIEW_DRAFTS)), app: AppContext = Depends(ctx)):
    _row(app, principal, did, draft=True)
    return app.store.dashboard_versions(did)


@router.post("/{did}/publish")
def publish(did: str, body: PublishBody, request: Request, principal: Principal = Depends(require(Cap.PUBLISH_DASHBOARDS)),
            app: AppContext = Depends(ctx)):
    head = _row(app, principal, did, draft=True)
    version = body.version or head["current_version"]
    if app.store.get_dashboard(did, version) is None:
        raise HTTPException(404, "Version not found")
    app.store.publish_dashboard(did, version)
    app.store.audit("dashboard.published", actor=principal.id, workspace=principal.workspace_id, target_type="dashboard",
                    target_id=did, detail={"version": version}, ip=client_ip(request))
    return _summary(app.store.get_dashboard(did))  # type: ignore[arg-type]


@router.post("/{did}/unpublish")
def unpublish(did: str, request: Request, principal: Principal = Depends(require(Cap.PUBLISH_DASHBOARDS)),
              app: AppContext = Depends(ctx)):
    _row(app, principal, did, draft=True)
    app.store.unpublish_dashboard(did)
    app.store.audit("dashboard.unpublished", actor=principal.id, workspace=principal.workspace_id, target_type="dashboard",
                    target_id=did, ip=client_ip(request))
    return _summary(app.store.get_dashboard(did))  # type: ignore[arg-type]


@router.post("/{did}/duplicate")
def duplicate(did: str, request: Request, principal: Principal = Depends(require(Cap.EDIT_DASHBOARDS)),
              app: AppContext = Depends(ctx)):
    row = _row(app, principal, did, draft=True)
    spec = load(row["spec"])
    spec.title = f"{spec.title} (copy)"[:300]
    new_id = app.store.create_dashboard(principal.workspace_id, spec.title, spec.model_dump(mode="json"), principal.id,
                                        source=row["source"])
    app.store.audit("dashboard.duplicated", actor=principal.id, workspace=principal.workspace_id, target_type="dashboard",
                    target_id=new_id, detail={"from": did}, ip=client_ip(request))
    return _detail(app.store.get_dashboard(new_id))


@router.delete("/{did}")
def delete(did: str, request: Request, principal: Principal = Depends(require(Cap.EDIT_DASHBOARDS)),
           app: AppContext = Depends(ctx)):
    _row(app, principal, did, draft=True)
    app.store.delete_dashboard(did)
    app.store.audit("dashboard.deleted", actor=principal.id, workspace=principal.workspace_id, target_type="dashboard",
                    target_id=did, ip=client_ip(request))
    return {"ok": True}


# ----------------------------------------------------------------------------- data
def _worksheet_result(app: AppContext, principal: Principal, spec: Dashboard, wid: str, state: RuntimeState,
                      request: Request, target: tuple[str, str], allow_formulas: bool,
                      overrides: dict[str, Dataset] | None = None) -> dict:
    try:
        ws = spec.worksheet(wid)
    except KeyError:
        raise HTTPException(404, "Worksheet not found") from None
    try:
        dataset, _ = app.dataset(ws.dataset_id, principal.workspace_id)
        override = (overrides or {}).get(ws.dataset_id)
        req, key_map = compile_worksheet(spec, ws, state, override or dataset)
    except LookupError:
        raise HTTPException(404, "Dataset not found") from None
    except PlanError as e:
        raise HTTPException(400, {"code": e.code, "message": e.message}) from None
    # Formulas in a stored spec were written by an author and are part of the
    # published artifact, so viewers may execute them; ad-hoc formulas from
    # viewers are impossible because viewers only send RuntimeState.
    result = run_query(app, principal, req, allow_formulas=allow_formulas, audit_target=target, ip=client_ip(request),
                       query_id=request.headers.get("X-Intelliq-Query-Id"), dataset_override=override)
    return {**result.model_dump(mode="json"), "key_map": key_map}


@router.post("/{did}/worksheets/{wid}/data")
def worksheet_data(did: str, wid: str, body: DataBody, request: Request,
                   principal: Principal = Depends(require(Cap.VIEW_DASHBOARDS)), app: AppContext = Depends(ctx)):
    row = _row(app, principal, did, draft=body.draft)
    return _worksheet_result(app, principal, load(row["spec"]), wid, body.state, request, ("dashboard", did), True)


@router.post("/preview/data")
def preview_data(body: PreviewBody, request: Request, principal: Principal = Depends(require(Cap.EDIT_DASHBOARDS, Cap.ADHOC_QUERY)),
                 app: AppContext = Depends(ctx)):
    spec = _validate(app, principal, body.spec)
    overrides = _import_overrides(app, principal, body.import_id) if body.import_id else None
    return _worksheet_result(app, principal, spec, body.worksheet_id, body.state, request, ("preview", body.worksheet_id), True,
                             overrides)


def _import_overrides(app: AppContext, principal: Principal, import_id: str) -> dict[str, Dataset]:
    """Datasets with an unapplied import's calculated fields merged in, for preview."""

    from .imports import _convert

    record = app.store.get_import(import_id)
    if record is None or record["workspace_id"] != principal.workspace_id:
        raise HTTPException(404, "Import not found")
    result = _convert(app, principal, record, record["mapping"])
    out: dict[str, Dataset] = {}
    for did, fields in result.new_fields.items():
        if not fields:
            continue
        base, _ = app.dataset(did, principal.workspace_id)
        out[did] = Dataset.model_validate({**base.model_dump(by_alias=True),
                                           "fields": [*[f.model_dump(by_alias=True) for f in base.fields], *fields]})
    return out


def _csv_cell(v: Any) -> Any:
    # Neutralise spreadsheet formula injection (=, +, -, @, tab, CR).
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


@router.post("/{did}/worksheets/{wid}/export.csv")
def export_csv(did: str, wid: str, body: DataBody, request: Request,
               principal: Principal = Depends(require(Cap.VIEW_DASHBOARDS, Cap.EXPORT_DATA)), app: AppContext = Depends(ctx)):
    row = _row(app, principal, did, draft=body.draft)
    data = _worksheet_result(app, principal, load(row["spec"]), wid, body.state, request, ("dashboard", did), True)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([c["label"] for c in data["columns"]])
    for r in data["rows"]:
        writer.writerow([_csv_cell(v) for v in r])
    app.store.audit("data.exported", actor=principal.id, workspace=principal.workspace_id, target_type="dashboard",
                    target_id=did, detail={"worksheet": wid, "rows": data["row_count"]}, ip=client_ip(request))
    filename = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in wid)[:60] or "export"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})


@router.get("/{did}/filters/{fid}/values")
def filter_values(did: str, fid: str, request: Request, search: str | None = None, draft: bool = False,
                  principal: Principal = Depends(require(Cap.VIEW_DASHBOARDS)), app: AppContext = Depends(ctx)):
    """Distinct values for a filter control. Only fields exposed as dashboard
    filters can be enumerated, and row-level security applies."""

    spec = load(_row(app, principal, did, draft=draft)["spec"])
    flt = next((f for f in spec.filters if f.id == fid), None)
    if flt is None:
        raise HTTPException(404, "Filter not found")
    filters = [Filter(target=FieldUse(field=flt.field), op="contains", values=[search])] if search else []
    req = QueryRequest(dataset_id=flt.dataset_id, dimensions=[FieldUse(field=flt.field)], filters=filters, limit=1000)
    result = run_query(app, principal, req, allow_formulas=False, audit_target=("dashboard", did), ip=client_ip(request))
    return {"values": [r[0] for r in result.rows], "truncated": result.truncated}
