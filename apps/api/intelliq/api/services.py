"""Query execution on behalf of a principal, with audit, rate limiting and
error sanitisation."""

from __future__ import annotations

import logging

from fastapi import HTTPException

from ..calc.errors import CalcError
from ..connectors.base import ConnectorError, DriverMissing, QueryCancelled, QueryTimeout
from ..platform.rbac import Cap
from ..query.models import QueryRequest, QueryResult
from ..query.planner import PlanError, Planner, UserContext
from ..semantic.model import Dataset
from .context import AppContext
from .deps import Principal

log = logging.getLogger("intelliq.query")


def user_context(principal: Principal) -> UserContext:
    attrs: dict[str, list[str]] = {}
    for key, value in (principal.user.get("attributes") or {}).items():
        if isinstance(value, list):
            attrs[key] = [str(v) for v in value]
        elif value is not None:
            attrs[key] = [str(value)]
    return UserContext(principal.id, principal.role, attrs)


def run_query(
    app: AppContext,
    principal: Principal,
    req: QueryRequest,
    *,
    allow_formulas: bool,
    audit_target: tuple[str, str] | None = None,
    ip: str | None = None,
    query_id: str | None = None,
    dataset_override: Dataset | None = None,
) -> QueryResult:
    """``dataset_override`` lets authoring previews plan against an unsaved
    variant of a dataset (e.g. an import's pending calculated fields)."""

    if not app.query_limiter.allow(principal.id):
        raise HTTPException(429, "Too many queries; slow down and try again shortly")
    try:
        dataset, version = app.dataset(req.dataset_id, principal.workspace_id)
    except LookupError:
        raise HTTPException(404, "Dataset not found") from None
    if dataset_override is not None:
        if dataset_override.id != dataset.id or dataset_override.connection_id != dataset.connection_id:
            raise HTTPException(400, "Dataset override does not match the stored dataset")
        dataset, version = dataset_override, f"{version}-preview"
    try:
        connector = app.connector_for(dataset.connection_id, principal.workspace_id)
        planned = Planner(dataset, connector.dialect, user_context(principal), allow_formulas=allow_formulas).plan(req)
        conn = app.store.get_connection(dataset.connection_id) or {}
        result = app.executor.execute(
            planned,
            connector,
            cache_scope=(dataset.id, version, dataset.connection_id, conn.get("revision")),
            ttl_seconds=dataset.settings.cache_ttl_seconds,
            timeout_s=dataset.settings.query_timeout_seconds,
            owner=principal.id,
            include_sql=principal.can(Cap.VIEW_SQL),
            query_id=query_id,
        )
    except PlanError as e:
        raise HTTPException(400, {"code": e.code, "message": e.message, "detail": e.detail}) from None
    except CalcError as e:
        raise HTTPException(400, {"code": e.code, "message": e.message, "detail": e.to_dict()}) from None
    except QueryTimeout as e:
        raise HTTPException(504, {"code": e.code, "message": e.message}) from None
    except QueryCancelled as e:
        raise HTTPException(409, {"code": e.code, "message": e.message}) from None
    except DriverMissing as e:
        raise HTTPException(501, {"code": e.code, "message": e.message}) from None
    except ConnectorError as e:
        log.warning("data source error on dataset %s: %s", dataset.id, e.message)
        message = e.message if principal.can(Cap.VIEW_SQL) else "The data source returned an error"
        raise HTTPException(502, {"code": e.code, "message": message}) from None

    app.store.audit(
        "query.run", actor=principal.id, workspace=principal.workspace_id,
        target_type=audit_target[0] if audit_target else "dataset",
        target_id=audit_target[1] if audit_target else dataset.id,
        detail={"dataset": dataset.id, "rows": result.row_count, "ms": result.duration_ms, "cache_hit": result.cache_hit},
        ip=ip,
    )
    return result
