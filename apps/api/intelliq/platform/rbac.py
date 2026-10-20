"""Role-based access control.

Roles are granted per workspace. Every API route declares the capability it
needs and the check runs on the server; the web client only *reflects* these
capabilities to hide controls, it never enforces them.
"""

from __future__ import annotations

from enum import StrEnum


class Cap(StrEnum):
    VIEW_DASHBOARDS = "view_dashboards"  # published dashboards
    VIEW_DRAFTS = "view_drafts"
    EDIT_DASHBOARDS = "edit_dashboards"
    PUBLISH_DASHBOARDS = "publish_dashboards"
    EXPORT_DATA = "export_data"
    ADHOC_QUERY = "adhoc_query"  # builder queries and formula validation
    VIEW_SQL = "view_sql"
    VIEW_DATASETS = "view_datasets"
    MANAGE_DATASETS = "manage_datasets"
    MANAGE_CONNECTIONS = "manage_connections"
    IMPORT_WORKBOOKS = "import_workbooks"
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT = "view_audit"


ROLE_CAPS: dict[str, frozenset[Cap]] = {
    "viewer": frozenset({Cap.VIEW_DASHBOARDS}),
    "author": frozenset({
        Cap.VIEW_DASHBOARDS, Cap.VIEW_DRAFTS, Cap.EDIT_DASHBOARDS, Cap.PUBLISH_DASHBOARDS, Cap.EXPORT_DATA,
        Cap.ADHOC_QUERY, Cap.VIEW_SQL, Cap.VIEW_DATASETS, Cap.IMPORT_WORKBOOKS,
    }),
    "admin": frozenset(Cap),
}

ROLES = tuple(ROLE_CAPS)


def capabilities(role: str | None, workspace_settings: dict | None = None) -> frozenset[Cap]:
    caps = set(ROLE_CAPS.get(role or "", frozenset()))
    if role == "viewer" and (workspace_settings or {}).get("viewers_can_export"):
        caps.add(Cap.EXPORT_DATA)
    return frozenset(caps)


def has(role: str | None, cap: Cap, workspace_settings: dict | None = None) -> bool:
    return cap in capabilities(role, workspace_settings)
