"""DuckDB connector — local development, testing and embedded analytics.

Databases are opened read-only with external access disabled, so a query can
never read files, attach other databases, or install extensions. Database
paths must live under one of the configured data roots.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from .base import (
    CancelToken,
    ColumnInfo,
    Connector,
    ConnectorError,
    QueryCancelled,
    QueryTimeout,
    RawResult,
    TableInfo,
    map_native_type,
    normalise_value,
    run_with_timeout,
)

_shared: dict[str, duckdb.DuckDBPyConnection] = {}
_shared_lock = threading.Lock()

# Set by the application at startup (the platform's own warehouse directory).
EXTRA_ROOTS: list[Path] = []


def allowed_roots() -> list[Path]:
    raw = os.environ.get("INTELLIQ_DUCKDB_ROOTS", "")
    roots = [Path(p).resolve() for p in raw.split(os.pathsep) if p]
    roots.extend(p.resolve() for p in EXTRA_ROOTS)
    return roots


class DuckDBConnector(Connector):
    kind = "duckdb"
    dialect = "duckdb"
    label = "DuckDB"
    verified = True
    config_fields = [("path", "Database file path", False, True)]

    def _path(self) -> str:
        path = self.config.get("path")
        if not path or not isinstance(path, str):
            raise ConnectorError("DuckDB connections need a database file path")
        resolved = Path(path).resolve()
        roots = allowed_roots()
        if not any(resolved == r or r in resolved.parents for r in roots):
            raise ConnectorError("Database path is outside the allowed data directories")
        if not resolved.exists():
            raise ConnectorError("Database file does not exist")
        return str(resolved)

    def _connection(self) -> duckdb.DuckDBPyConnection:
        path = self._path()
        with _shared_lock:
            con = _shared.get(path)
            if con is None:
                con = duckdb.connect(path, read_only=True, config={"enable_external_access": False})
                _shared[path] = con
            return con

    def test(self) -> None:
        cur = self._connection().cursor()
        try:
            cur.execute("SELECT 1").fetchall()
        finally:
            cur.close()

    def list_tables(self) -> list[TableInfo]:
        cur = self._connection().cursor()
        try:
            rows = cur.execute(
                "SELECT table_schema, table_name, table_type FROM information_schema.tables "
                "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') ORDER BY 1, 2"
            ).fetchall()
        finally:
            cur.close()
        return [TableInfo(s, n, "view" if "VIEW" in (t or "").upper() else "table") for s, n, t in rows]

    def describe(self, schema: str | None, table: str) -> list[ColumnInfo]:
        cur = self._connection().cursor()
        try:
            rows = cur.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = ? AND (? IS NULL OR table_schema = ?) ORDER BY ordinal_position",
                [table, schema, schema],
            ).fetchall()
        finally:
            cur.close()
        return [ColumnInfo(n, map_native_type(t), t, (nullable or "YES") == "YES") for n, t, nullable in rows]

    def _execute(
        self, sql: str, params: Sequence[Any], *, timeout_s: float, max_rows: int, cancel: CancelToken
    ) -> RawResult:
        cur = self._connection().cursor()

        def run() -> RawResult:
            cur.execute(sql, list(params))
            names = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows)
            return RawResult(names, [tuple(normalise_value(v) for v in r) for r in rows])

        try:
            return run_with_timeout(run, timeout_s=timeout_s, cancel=cancel, interrupt=cur.interrupt)
        except (QueryTimeout, QueryCancelled):
            raise
        except duckdb.InterruptException:
            raise QueryCancelled("Query was cancelled") from None
        except duckdb.Error as e:
            raise ConnectorError(_clean(str(e))) from None
        finally:
            try:
                cur.close()
            except duckdb.Error:
                pass


def _clean(message: str) -> str:
    # Driver messages can be long; keep the first line, which carries the cause.
    return message.strip().splitlines()[0][:500] if message.strip() else "Database error"


def reset_shared_connections() -> None:
    """Close cached connections (tests and connection updates)."""

    with _shared_lock:
        for con in _shared.values():
            try:
                con.close()
            except duckdb.Error:
                pass
        _shared.clear()
