from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...connectors.base import ConnectorError
from ...connectors.registry import connector_class, describe_types
from ...platform.rbac import Cap
from ..context import AppContext
from ..deps import Principal, client_ip, ctx, require

router = APIRouter(prefix="/api", tags=["connections"])


class ConnectionBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] | None = None  # write-only; omitted on update keeps existing secrets


def _split(kind: str, body: ConnectionBody) -> tuple[dict, dict | None]:
    cls = connector_class(kind)
    allowed = {name: secret for name, _label, secret, _req in cls.config_fields}
    config, secrets = {}, None
    for k, v in body.config.items():
        if k not in allowed:
            raise HTTPException(400, f"Unknown setting '{k}' for {cls.label}")
        if allowed[k]:
            raise HTTPException(400, f"'{k}' is a secret; send it in 'secrets'")
        config[k] = v
    if body.secrets is not None:
        secrets = {}
        for k, v in body.secrets.items():
            if not allowed.get(k):
                raise HTTPException(400, f"'{k}' is not a secret setting for {cls.label}")
            secrets[k] = v
    return config, secrets


@router.get("/connector-types")
def connector_types(principal: Principal = Depends(require(Cap.MANAGE_CONNECTIONS))):
    return describe_types()


@router.get("/connections")
def list_connections(principal: Principal = Depends(require(Cap.MANAGE_CONNECTIONS)), app: AppContext = Depends(ctx)):
    return app.store.list_connections(principal.workspace_id)


@router.post("/connections")
def create_connection(body: ConnectionBody, request: Request, principal: Principal = Depends(require(Cap.MANAGE_CONNECTIONS)),
                      app: AppContext = Depends(ctx)):
    try:
        config, secrets = _split(body.kind, body)
    except ConnectorError as e:
        raise HTTPException(400, e.message) from None
    sealed = app.secrets.seal(secrets) if secrets else None
    conn = app.store.create_connection(principal.workspace_id, body.name, body.kind, config, sealed, principal.id)
    app.store.audit("connection.created", actor=principal.id, workspace=principal.workspace_id, target_type="connection",
                    target_id=conn["id"], detail={"kind": body.kind, "name": body.name}, ip=client_ip(request))
    return conn


def _owned(app: AppContext, principal: Principal, cid: str) -> dict:
    conn = app.store.get_connection(cid)
    if conn is None or conn["workspace_id"] != principal.workspace_id:
        raise HTTPException(404, "Connection not found")
    return conn


@router.put("/connections/{cid}")
def update_connection(cid: str, body: ConnectionBody, request: Request,
                      principal: Principal = Depends(require(Cap.MANAGE_CONNECTIONS)), app: AppContext = Depends(ctx)):
    conn = _owned(app, principal, cid)
    if body.kind != conn["kind"]:
        raise HTTPException(400, "Connection type cannot be changed")
    config, secrets = _split(conn["kind"], body)
    app.store.update_connection(cid, name=body.name, config=config, secret_enc=app.secrets.seal(secrets) if secrets else None)
    app.store.audit("connection.updated", actor=principal.id, workspace=principal.workspace_id, target_type="connection",
                    target_id=cid, detail={"secrets_rotated": secrets is not None}, ip=client_ip(request))
    return app.store.get_connection(cid)


@router.post("/connections/{cid}/test")
def test_connection(cid: str, principal: Principal = Depends(require(Cap.MANAGE_CONNECTIONS)), app: AppContext = Depends(ctx)):
    _owned(app, principal, cid)
    try:
        app.connector_for(cid, principal.workspace_id).test()
    except ConnectorError as e:
        return {"ok": False, "message": e.message}
    return {"ok": True, "message": "Connected"}


@router.get("/connections/{cid}/tables")
def tables(cid: str, principal: Principal = Depends(require(Cap.MANAGE_CONNECTIONS)), app: AppContext = Depends(ctx)):
    _owned(app, principal, cid)
    try:
        return [t.__dict__ for t in app.connector_for(cid, principal.workspace_id).list_tables()]
    except ConnectorError as e:
        raise HTTPException(502, e.message) from None


@router.get("/connections/{cid}/columns")
def columns(cid: str, table: str, schema: str | None = None,
            principal: Principal = Depends(require(Cap.MANAGE_CONNECTIONS)), app: AppContext = Depends(ctx)):
    _owned(app, principal, cid)
    try:
        return [c.__dict__ for c in app.connector_for(cid, principal.workspace_id).describe(schema, table)]
    except ConnectorError as e:
        raise HTTPException(502, e.message) from None
