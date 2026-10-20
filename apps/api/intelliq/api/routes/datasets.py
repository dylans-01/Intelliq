from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from ...calc.errors import CalcError
from ...connectors.base import ConnectorError
from ...platform.rbac import Cap
from ...query.models import FieldUse, Filter, QueryRequest
from ...semantic.model import Dataset
from ...semantic.resolver import SemanticModel
from ..context import AppContext
from ..deps import Principal, client_ip, ctx, require
from ..services import run_query

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


class DatasetBody(BaseModel):
    model: dict[str, Any]
    note: str = Field(default="", max_length=500)


def describe(app: AppContext, row: dict, *, full: bool) -> dict:
    ds = Dataset.model_validate(row["model"])
    sm = SemanticModel(ds)
    out: dict[str, Any] = {
        "id": ds.id, "name": ds.name, "description": ds.description, "version": row.get("current_version", row.get("version")),
        "connection_id": ds.connection_id, "updated_at": row.get("updated_at"),
        "field_count": len(ds.fields),
    }
    if full:
        problems = sm.validate()
        lineage = sm.lineage()
        fields = []
        for f in ds.fields:
            try:
                typed = sm.typed(f)
                level = typed.level
            except CalcError:
                level = "invalid"
            lin = lineage[f.id]
            fields.append({**f.model_dump(exclude_none=True), "level": level, "depends_on": lin.depends_on,
                           "used_by": lin.used_by, "columns": lin.columns})
        out.update({
            "model": ds.model_dump(by_alias=True, exclude_none=True),
            "fields": fields,
            "problems": problems,
            "hierarchies": [h.model_dump() for h in ds.hierarchies],
        })
    return out


def _owned(app: AppContext, principal: Principal, did: str) -> dict:
    row = app.store.get_dataset(did)
    if row is None or row["workspace_id"] != principal.workspace_id:
        raise HTTPException(404, "Dataset not found")
    return row


@router.get("")
def list_datasets(principal: Principal = Depends(require(Cap.VIEW_DATASETS)), app: AppContext = Depends(ctx)):
    return [describe(app, r, full=False) for r in app.store.list_datasets(principal.workspace_id)]


@router.get("/{did}")
def get_dataset(did: str, principal: Principal = Depends(require(Cap.VIEW_DATASETS)), app: AppContext = Depends(ctx)):
    return describe(app, _owned(app, principal, did), full=True)


@router.get("/{did}/versions")
def versions(did: str, principal: Principal = Depends(require(Cap.VIEW_DATASETS)), app: AppContext = Depends(ctx)):
    _owned(app, principal, did)
    return app.store.dataset_versions(did)


def _validate_model(app: AppContext, principal: Principal, model: dict) -> Dataset:
    try:
        ds = Dataset.model_validate(model)
    except ValidationError as e:
        raise HTTPException(422, {"message": "Invalid dataset definition", "errors": e.errors(include_url=False, include_context=False, include_input=False)}) from None
    conn = app.store.get_connection(ds.connection_id)
    if conn is None or conn["workspace_id"] != principal.workspace_id:
        raise HTTPException(400, "Dataset references an unknown connection")
    problems = SemanticModel(ds).validate()
    if problems:
        raise HTTPException(422, {"message": "Dataset has invalid fields", "problems": problems})
    return ds


def _check_columns(app: AppContext, principal: Principal, ds: Dataset) -> list[str]:
    """Verify referenced tables and columns exist in the connection."""

    warnings: list[str] = []
    try:
        connector = app.connector_for(ds.connection_id, principal.workspace_id)
        columns_by_alias = {}
        for alias, table in ds.tables_by_alias().items():
            cols = connector.describe(table.schema_, table.name)
            if not cols:
                warnings.append(f"Table {table.name} was not found or has no columns")
            columns_by_alias[alias] = {c.name.lower() for c in cols}
        for f in ds.fields:
            if f.column:
                alias, col = f.column.split(".", 1)
                if columns_by_alias.get(alias) and col.lower() not in columns_by_alias[alias]:
                    warnings.append(f"Column {f.column} for [{f.name}] does not exist")
    except ConnectorError as e:
        warnings.append(f"Could not verify columns: {e.message}")
    return warnings


@router.post("")
def create_dataset(body: DatasetBody, request: Request, principal: Principal = Depends(require(Cap.MANAGE_DATASETS)),
                   app: AppContext = Depends(ctx)):
    ds = _validate_model(app, principal, body.model)
    if app.store.get_dataset(ds.id) is not None:
        raise HTTPException(409, "A dataset with this id already exists")
    warnings = _check_columns(app, principal, ds)
    if any("does not exist" in w or "not found" in w for w in warnings):
        raise HTTPException(422, {"message": "Dataset references missing columns", "problems": warnings})
    app.store.create_dataset(principal.workspace_id, ds.id, ds.name, ds.connection_id,
                             ds.model_dump(by_alias=True, exclude_none=True), principal.id)
    app.store.audit("dataset.created", actor=principal.id, workspace=principal.workspace_id, target_type="dataset",
                    target_id=ds.id, ip=client_ip(request))
    return {**describe(app, _owned(app, principal, ds.id), full=True), "warnings": warnings}


@router.put("/{did}")
def update_dataset(did: str, body: DatasetBody, request: Request, principal: Principal = Depends(require(Cap.MANAGE_DATASETS)),
                   app: AppContext = Depends(ctx)):
    _owned(app, principal, did)
    ds = _validate_model(app, principal, body.model)
    if ds.id != did:
        raise HTTPException(400, "Dataset id cannot be changed")
    warnings = _check_columns(app, principal, ds)
    if any("does not exist" in w or "not found" in w for w in warnings):
        raise HTTPException(422, {"message": "Dataset references missing columns", "problems": warnings})
    version = app.store.save_dataset_version(did, ds.name, ds.model_dump(by_alias=True, exclude_none=True),
                                             body.note or "Updated", principal.id)
    app.store.audit("dataset.updated", actor=principal.id, workspace=principal.workspace_id, target_type="dataset",
                    target_id=did, detail={"version": version, "note": body.note}, ip=client_ip(request))
    return {**describe(app, _owned(app, principal, did), full=True), "warnings": warnings}


@router.get("/{did}/values/{field_id}")
def field_values(did: str, field_id: str, request: Request, search: str | None = None, limit: int = 200,
                 principal: Principal = Depends(require(Cap.ADHOC_QUERY)), app: AppContext = Depends(ctx)):
    """Distinct values for authoring filter controls (authors only; viewers use
    the dashboard-scoped endpoint, which only exposes filter fields)."""

    _owned(app, principal, did)
    filters = [Filter(target=FieldUse(field=field_id), op="contains", values=[search])] if search else []
    req = QueryRequest(dataset_id=did, dimensions=[FieldUse(field=field_id)], filters=filters, limit=min(limit, 1000))
    result = run_query(app, principal, req, allow_formulas=False, ip=client_ip(request))
    return {"values": [r[0] for r in result.rows], "truncated": result.truncated}
