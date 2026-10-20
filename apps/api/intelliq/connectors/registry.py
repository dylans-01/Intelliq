"""Connector registry: maps a connection kind to its adapter class and keeps
one live adapter per (connection id, revision)."""

from __future__ import annotations

import threading
from typing import Any

from .base import Connector, ConnectorError
from .databricks import DatabricksConnector
from .duckdb_connector import DuckDBConnector
from .postgres import PostgresConnector
from .snowflake import SnowflakeConnector
from .sqlserver import SqlServerConnector

CONNECTOR_TYPES: dict[str, type[Connector]] = {
    c.kind: c
    for c in (DuckDBConnector, PostgresConnector, SqlServerConnector, SnowflakeConnector, DatabricksConnector)
}

_instances: dict[str, tuple[int, Connector]] = {}
_lock = threading.Lock()


def connector_class(kind: str) -> type[Connector]:
    try:
        return CONNECTOR_TYPES[kind]
    except KeyError:
        raise ConnectorError(f"Unsupported connection type '{kind}'") from None


def get_connector(connection_id: str, revision: int, kind: str, config: dict[str, Any], secrets: dict[str, str]) -> Connector:
    with _lock:
        cached = _instances.get(connection_id)
        if cached and cached[0] == revision:
            return cached[1]
        if cached:
            cached[1].close()
        instance = connector_class(kind)(config, secrets)
        _instances[connection_id] = (revision, instance)
        return instance


def describe_types() -> list[dict[str, Any]]:
    return [
        {
            "kind": c.kind,
            "label": c.label,
            "dialect": c.dialect,
            "verified": c.verified,
            "fields": [
                {"name": n, "label": label, "secret": secret, "required": required}
                for n, label, secret, required in c.config_fields
            ],
        }
        for c in CONNECTOR_TYPES.values()
    ]
