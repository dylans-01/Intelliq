"""Tableau workbook import: upload → analyse → map → preview → apply."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ...importer.archive import WorkbookFileError, read_workbook
from ...importer.convert import Converter, suggest_mapping
from ...importer.twb_parser import WorkbookParseError, parse_workbook
from ...platform.rbac import Cap
from ...platform.store import now
from ...semantic.model import Dataset, SemanticField
from ...semantic.resolver import SemanticModel
from ...spec.dashboard import Dashboard
from ..context import AppContext
from ..deps import Principal, client_ip, ctx, require

router = APIRouter(prefix="/api/imports", tags=["imports"])


class MappingBody(BaseModel):
    datasources: dict[str, str] = Field(default_factory=dict)
    fields: dict[str, dict[str, str]] = Field(default_factory=dict)


def _datasets(app: AppContext, workspace_id: str) -> dict[str, Dataset]:
    return {r["id"]: Dataset.model_validate(r["model"]) for r in app.store.list_datasets(workspace_id)}


def _convert(app: AppContext, principal: Principal, record: dict, mapping: dict) -> Any:
    return Converter(
        record["workbook"], mapping, _datasets(app, principal.workspace_id), import_id=record["id"],
        workbook_name=record["filename"], sha256=record["sha256"], imported_at=record["created_at"],
    ).convert()


def _inventory(workbook: dict) -> dict:
    return {
        "datasources": [{"name": d["name"], "caption": d["caption"], "connection": d.get("connection"),
                         "extract": d.get("extract"), "custom_sql": bool(d.get("custom_sql")),
                         "fields": [c["caption"] or c["name"] for c in d["columns"]] + [m["local_name"] for m in d["metadata"]]}
                        for d in workbook["datasources"]],
        "worksheets": [w["name"] for w in workbook["worksheets"]],
        "dashboards": [d["name"] for d in workbook["dashboards"]],
        "parameters": [p["caption"] for p in workbook["parameters"]],
        "actions": [a["caption"] for a in workbook["actions"]],
        "assets": workbook.get("assets", []),
        "version": workbook.get("version"),
    }


def _payload(record: dict, result: Any | None = None) -> dict:
    out = {k: record[k] for k in ("id", "filename", "sha256", "size", "status", "mapping", "dashboard_ids", "created_at")}
    out["report"] = record["report"]
    out["inventory"] = _inventory(record["workbook"])
    if result is not None:
        out["report"] = result.report
        out["preview"] = {"dashboards": result.dashboards, "new_fields": result.new_fields}
    return out


def _owned(app: AppContext, principal: Principal, iid: str) -> dict:
    record = app.store.get_import(iid)
    if record is None or record["workspace_id"] != principal.workspace_id:
        raise HTTPException(404, "Import not found")
    return record


@router.post("")
async def upload(request: Request, file: UploadFile = File(...), principal: Principal = Depends(require(Cap.IMPORT_WORKBOOKS)),
                 app: AppContext = Depends(ctx)):
    limit = app.settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    try:
        wf = read_workbook(file.filename or "workbook.twb", data, max_bytes=limit)
        workbook = parse_workbook(wf.twb_bytes)
    except (WorkbookFileError, WorkbookParseError) as e:
        app.store.audit("import.rejected", actor=principal.id, workspace=principal.workspace_id,
                        detail={"filename": file.filename, "reason": str(e)}, ip=client_ip(request))
        raise HTTPException(400, str(e)) from None
    workbook["assets"] = [a.__dict__ for a in wf.assets]
    mapping = suggest_mapping(workbook, list(_datasets(app, principal.workspace_id).values()))
    iid = app.store.create_import(principal.workspace_id, wf.filename, wf.sha256, wf.size, workbook, {}, principal.id)
    app.store.update_import(iid, mapping=mapping)
    record = app.store.get_import(iid)
    result = _convert(app, principal, record, mapping)  # type: ignore[arg-type]
    app.store.update_import(iid, report=result.report)
    app.store.audit("import.analyzed", actor=principal.id, workspace=principal.workspace_id, target_type="import",
                    target_id=iid, detail={"filename": wf.filename, "sha256": wf.sha256, "summary": result.report["summary"]},
                    ip=client_ip(request))
    return _payload(app.store.get_import(iid), result)  # type: ignore[arg-type]


@router.get("")
def list_imports(principal: Principal = Depends(require(Cap.IMPORT_WORKBOOKS)), app: AppContext = Depends(ctx)):
    return app.store.list_imports(principal.workspace_id)


@router.get("/{iid}")
def get_import(iid: str, principal: Principal = Depends(require(Cap.IMPORT_WORKBOOKS)), app: AppContext = Depends(ctx)):
    record = _owned(app, principal, iid)
    return _payload(record, _convert(app, principal, record, record["mapping"]))


@router.put("/{iid}/mapping")
def set_mapping(iid: str, body: MappingBody, principal: Principal = Depends(require(Cap.IMPORT_WORKBOOKS)),
                app: AppContext = Depends(ctx)):
    record = _owned(app, principal, iid)
    if record["status"] == "applied":
        raise HTTPException(409, "This import has already been applied")
    datasets = _datasets(app, principal.workspace_id)
    mapping = body.model_dump()
    for ds_name, did in mapping["datasources"].items():
        if did not in datasets:
            raise HTTPException(400, f"Unknown dataset '{did}'")
        valid = {f.id for f in datasets[did].fields}
        for src, target in mapping["fields"].get(ds_name, {}).items():
            if target not in valid:
                raise HTTPException(400, f"Field '{target}' does not exist in dataset '{did}' (mapping for {src})")
    app.store.update_import(iid, mapping=mapping)
    record = app.store.get_import(iid)
    result = _convert(app, principal, record, mapping)  # type: ignore[arg-type]
    app.store.update_import(iid, report=result.report)
    return _payload(app.store.get_import(iid), result)  # type: ignore[arg-type]


@router.post("/{iid}/apply")
def apply(iid: str, request: Request, principal: Principal = Depends(require(Cap.IMPORT_WORKBOOKS)),
          app: AppContext = Depends(ctx)):
    record = _owned(app, principal, iid)
    if record["status"] == "applied":
        raise HTTPException(409, "This import has already been applied")
    result = _convert(app, principal, record, record["mapping"])
    if not result.dashboards:
        raise HTTPException(400, "Nothing could be converted; review the mapping")
    if any(result.new_fields.values()) and not principal.can(Cap.MANAGE_DATASETS):
        raise HTTPException(403, "This import adds calculated fields to a dataset; an administrator must apply it")

    for did, fields in result.new_fields.items():
        if not fields:
            continue
        row = app.store.get_dataset(did)
        ds = Dataset.model_validate(row["model"])  # type: ignore[index]
        merged = Dataset.model_validate({**ds.model_dump(by_alias=True), "fields": [
            *[f.model_dump(by_alias=True) for f in ds.fields], *[SemanticField.model_validate(f).model_dump() for f in fields]]})
        problems = SemanticModel(merged).validate()
        if problems:
            raise HTTPException(422, {"message": "Imported calculations failed validation", "problems": problems})
        app.store.save_dataset_version(did, merged.name, merged.model_dump(by_alias=True, exclude_none=True),
                                       f"Tableau import {record['filename']}: {len(fields)} calculated field(s)", principal.id)

    created = []
    for spec_dict in result.dashboards:
        spec = Dashboard.model_validate(spec_dict)
        created.append(app.store.create_dashboard(principal.workspace_id, spec.title, spec.model_dump(mode="json"),
                                                  principal.id, source="tableau"))
    app.store.update_import(iid, status="applied", dashboard_ids=created, report=result.report)
    app.store.audit("import.applied", actor=principal.id, workspace=principal.workspace_id, target_type="import", target_id=iid,
                    detail={"dashboards": created, "fields_added": sum(len(v) for v in result.new_fields.values()),
                            "at": now()}, ip=client_ip(request))
    return _payload(app.store.get_import(iid), result)  # type: ignore[arg-type]
