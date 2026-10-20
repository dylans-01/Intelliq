from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ...platform.auth import create_session, hash_password, token_hash, verify_password
from ...platform.store import now
from ..context import AppContext
from ..deps import SESSION_COOKIE, Principal, client_ip, ctx, current_principal

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class PasswordBody(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=10, max_length=1024)


def me_payload(app: AppContext, principal: Principal) -> dict:
    u = principal.user
    return {
        "user": {"id": u["id"], "email": u["email"], "name": u["name"]},
        "workspace": {"id": principal.workspace_id, "role": principal.role},
        "workspaces": [{"id": w["id"], "name": w["name"], "role": w["role"]} for w in app.store.workspaces_for(u["id"])],
        "capabilities": sorted(c.value for c in principal.caps),
        "csrf_token": principal.session["csrf_token"],
    }


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, app: AppContext = Depends(ctx)):
    ip = client_ip(request) or "unknown"
    if not app.login_limiter.allow(f"{ip}|{body.email.lower()}") or not app.login_limiter.allow(ip):
        raise HTTPException(429, "Too many sign-in attempts. Try again in a minute.")
    user = app.store.user_by_email(body.email)
    ok = verify_password(user["password_hash"] if user else None, body.password)
    if not ok or user is None or not user["is_active"]:
        app.store.audit("auth.login_failed", actor=user["id"] if user else None, detail={"email": body.email[:320]}, ip=ip)
        raise HTTPException(401, "Email or password is incorrect")
    token, _csrf = create_session(app.store, user["id"], app.settings.session_hours, ip, request.headers.get("user-agent"))
    app.store.update_user(user["id"], last_login_at=now())
    app.store.audit("auth.login", actor=user["id"], ip=ip)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", secure=app.settings.cookie_secure,
        max_age=app.settings.session_hours * 3600, path="/",
    )
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, principal: Principal = Depends(current_principal),
           app: AppContext = Depends(ctx)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        app.store.delete_session(token_hash(token))
    app.store.audit("auth.logout", actor=principal.id, ip=client_ip(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(current_principal), app: AppContext = Depends(ctx)):
    return me_payload(app, principal)


@router.post("/password")
def change_password(body: PasswordBody, request: Request, principal: Principal = Depends(current_principal),
                    app: AppContext = Depends(ctx)):
    if not verify_password(principal.user["password_hash"], body.current_password):
        raise HTTPException(400, "Current password is incorrect")
    try:
        new_hash = hash_password(body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    app.store.update_user(principal.id, password_hash=new_hash)
    app.store.delete_user_sessions(principal.id)
    app.store.audit("auth.password_changed", actor=principal.id, ip=client_ip(request))
    return {"ok": True, "signed_out": True}
