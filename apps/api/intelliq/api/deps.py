"""Request dependencies: authentication, CSRF, workspace and capability checks."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from ..platform.auth import csrf_matches, token_hash
from ..platform.rbac import Cap, capabilities
from .context import AppContext

SESSION_COOKIE = "iq_session"
CSRF_HEADER = "X-Intelliq-CSRF"
WORKSPACE_HEADER = "X-Intelliq-Workspace"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    user: dict
    session: dict
    workspace_id: str
    workspace_settings: dict
    role: str
    caps: frozenset[Cap]

    @property
    def id(self) -> str:
        return self.user["id"]

    def can(self, cap: Cap) -> bool:
        return cap in self.caps


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def current_principal(request: Request, app: AppContext = Depends(ctx)) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Not signed in")
    session = app.store.get_session(token_hash(token))
    if session is None:
        raise HTTPException(401, "Session expired")
    user = app.store.get_user(session["user_id"])
    if user is None or not user["is_active"]:
        raise HTTPException(401, "Account disabled")
    if request.method not in SAFE_METHODS and not csrf_matches(session["csrf_token"], request.headers.get(CSRF_HEADER)):
        raise HTTPException(403, "Missing or invalid CSRF token")

    workspaces = app.store.workspaces_for(user["id"])
    if not workspaces:
        raise HTTPException(403, "You are not a member of any workspace")
    requested = request.headers.get(WORKSPACE_HEADER)
    ws = next((w for w in workspaces if w["id"] == requested), None) if requested else workspaces[0]
    if ws is None:
        raise HTTPException(403, "You are not a member of this workspace")
    return Principal(user, session, ws["id"], ws["settings"], ws["role"], capabilities(ws["role"], ws["settings"]))


def require(*caps: Cap):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        missing = [c for c in caps if c not in principal.caps]
        if missing:
            raise HTTPException(403, "You do not have permission to do this")
        return principal

    return dependency
