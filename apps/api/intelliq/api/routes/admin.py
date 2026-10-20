from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...connectors.registry import describe_types
from ...platform.auth import hash_password
from ...platform.rbac import ROLES, Cap
from ..context import AppContext
from ..deps import Principal, client_ip, ctx, require

router = APIRouter(prefix="/api/admin", tags=["admin"])


class NewUser(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=1024)
    role: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class UserPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role: str | None = None
    is_active: bool | None = None
    attributes: dict[str, Any] | None = None


def _public(u: dict) -> dict:
    return {k: u[k] for k in ("id", "email", "name", "role", "is_active", "attributes", "created_at", "last_login_at") if k in u}


@router.get("/users")
def list_users(principal: Principal = Depends(require(Cap.MANAGE_USERS)), app: AppContext = Depends(ctx)):
    return [_public(u) for u in app.store.list_users(principal.workspace_id)]


@router.post("/users")
def create_user(body: NewUser, request: Request, principal: Principal = Depends(require(Cap.MANAGE_USERS)),
                app: AppContext = Depends(ctx)):
    if body.role not in ROLES:
        raise HTTPException(400, f"Role must be one of {', '.join(ROLES)}")
    existing = app.store.user_by_email(body.email)
    if existing is None:
        try:
            user = app.store.create_user(body.email, body.name, hash_password(body.password), body.attributes)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    else:
        user = existing  # add an existing account to this workspace
    app.store.set_membership(user["id"], principal.workspace_id, body.role)
    app.store.audit("user.created", actor=principal.id, workspace=principal.workspace_id, target_type="user",
                    target_id=user["id"], detail={"email": body.email, "role": body.role}, ip=client_ip(request))
    return _public({**user, "role": body.role})


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, request: Request,
                principal: Principal = Depends(require(Cap.MANAGE_USERS)), app: AppContext = Depends(ctx)):
    role = app.store.role_in(user_id, principal.workspace_id)
    if role is None:
        raise HTTPException(404, "User not found in this workspace")
    demoting_admin = role == "admin" and ((body.role and body.role != "admin") or body.is_active is False)
    if demoting_admin and app.store.count_admins(principal.workspace_id) <= 1:
        raise HTTPException(400, "A workspace needs at least one active administrator")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, f"Role must be one of {', '.join(ROLES)}")
        app.store.set_membership(user_id, principal.workspace_id, body.role)
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.is_active is not None:
        fields["is_active"] = int(body.is_active)
    if body.attributes is not None:
        fields["attributes"] = body.attributes
    app.store.update_user(user_id, **fields)
    if body.is_active is False:
        app.store.delete_user_sessions(user_id)
    app.store.audit("user.updated", actor=principal.id, workspace=principal.workspace_id, target_type="user",
                    target_id=user_id, detail=body.model_dump(exclude_none=True), ip=client_ip(request))
    user = app.store.get_user(user_id)
    return _public({**user, "role": app.store.role_in(user_id, principal.workspace_id)})  # type: ignore[dict-item]


@router.get("/audit")
def audit(before: int | None = None, action: str | None = None, limit: int = 200,
          principal: Principal = Depends(require(Cap.VIEW_AUDIT)), app: AppContext = Depends(ctx)):
    return app.store.audit_entries(principal.workspace_id, limit=limit, before_id=before, action=action)


@router.get("/system")
def system(principal: Principal = Depends(require(Cap.MANAGE_CONNECTIONS)), app: AppContext = Depends(ctx)):
    return {"cache": app.executor.cache.stats(), "connector_types": describe_types()}
