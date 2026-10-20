from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...calc import ast as A
from ...calc import functions as F
from ...calc.errors import CalcError
from ...platform.rbac import Cap
from ...query.models import QueryRequest
from ...semantic.model import DataType
from ...semantic.resolver import SemanticModel
from ..context import AppContext
from ..deps import Principal, client_ip, ctx, require
from ..services import run_query

router = APIRouter(prefix="/api", tags=["query"])


class ParamType(BaseModel):
    name: str
    data_type: DataType


class ValidateBody(BaseModel):
    dataset_id: str
    formula: str = Field(max_length=20_000)
    parameters: list[ParamType] = Field(default_factory=list)


@router.post("/query")
def query(req: QueryRequest, request: Request, principal: Principal = Depends(require(Cap.ADHOC_QUERY)),
          app: AppContext = Depends(ctx)):
    return run_query(app, principal, req, allow_formulas=True, ip=client_ip(request),
                     query_id=request.headers.get("X-Intelliq-Query-Id"))


@router.post("/query/{query_id}/cancel")
def cancel(query_id: str, principal: Principal = Depends(require(Cap.VIEW_DASHBOARDS)), app: AppContext = Depends(ctx)):
    return {"cancelled": app.executor.running.cancel(query_id, principal.id, is_admin=principal.role == "admin")}


@router.post("/calc/validate")
def validate(body: ValidateBody, principal: Principal = Depends(require(Cap.ADHOC_QUERY)), app: AppContext = Depends(ctx)):
    try:
        ds, _ = app.dataset(body.dataset_id, principal.workspace_id)
    except LookupError:
        raise HTTPException(404, "Dataset not found") from None
    model = SemanticModel(ds, {p.name: p.data_type for p in body.parameters})
    try:
        tree, typed = model.check_formula(body.formula)
    except CalcError as e:
        return {"ok": False, "error": e.to_dict()}
    deps = [model.field(r).id for r in A.field_refs(tree) if model.has_field(r)]
    return {
        "ok": True,
        "data_type": typed.data_type,
        "level": typed.level,
        "role": "measure" if typed.level in ("agg", "table") else "dimension",
        "dependencies": deps,
        "parameters": A.param_refs(tree),
        "canonical": A.to_formula(tree),
    }


@router.get("/calc/functions")
def functions(principal: Principal = Depends(require(Cap.VIEW_DASHBOARDS))):
    return [
        {"name": f.name, "kind": f.kind, "category": f.category, "min_args": f.min_args, "max_args": f.max_args,
         "description": f.description, "notes": f.notes}
        for f in F.FUNCTIONS.values()
    ]
