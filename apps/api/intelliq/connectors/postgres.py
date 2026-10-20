"""PostgreSQL connector (psycopg 3 + psycopg_pool).

Each query runs in a READ ONLY transaction with a server-side
``statement_timeout``; cancellation uses the protocol-level cancel request.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .base import (
    CancelToken,
    ColumnInfo,
    Connector,
    ConnectorError,
    DriverMissing,
    QueryCancelled,
    QueryTimeout,
    RawResult,
    TableInfo,
    map_native_type,
    normalise_value,
    run_with_timeout,
)


class PostgresConnector(Connector):
    kind = "postgres"
    dialect = "postgres"
    marker = "%s"
    escape_percent = True
    label = "PostgreSQL"
    config_fields = [
        ("host", "Host", False, True),
        ("port", "Port", False, False),
        ("database", "Database", False, True),
        ("user", "User", False, True),
        ("password", "Password", True, True),
        ("sslmode", "SSL mode", False, False),
    ]

    def __init__(self, config: dict[str, Any], secrets: dict[str, str]):
        super().__init__(config, secrets)
        self._pool = None

    def _pool_get(self):
        if self._pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError:
                raise DriverMissing("Install the 'postgres' extra to use PostgreSQL connections") from None
            kwargs = {
                "host": self.config.get("host"),
                "port": int(self.config.get("port") or 5432),
                "dbname": self.config.get("database"),
                "user": self.config.get("user"),
                "password": self.secrets.get("password"),
                "sslmode": self.config.get("sslmode") or "prefer",
                "application_name": "intelliq",
            }
            self._pool = ConnectionPool(kwargs=kwargs, min_size=0, max_size=int(self.config.get("pool_size") or 5), open=True)
        return self._pool

    def test(self) -> None:
        self._run("SELECT 1", [], timeout_s=15, max_rows=1, cancel=CancelToken())

    def list_tables(self) -> list[TableInfo]:
        res = self._run(
            "SELECT table_schema, table_name, table_type FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2",
            [], timeout_s=30, max_rows=20_000, cancel=CancelToken(),
        )
        return [TableInfo(s, n, "view" if "VIEW" in t else "table") for s, n, t in res.rows]

    def describe(self, schema: str | None, table: str) -> list[ColumnInfo]:
        res = self._run(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = %s AND (%s::text IS NULL OR table_schema = %s) ORDER BY ordinal_position",
            [table, schema, schema], timeout_s=30, max_rows=5_000, cancel=CancelToken(),
        )
        return [ColumnInfo(n, map_native_type(t), t, nullable == "YES") for n, t, nullable in res.rows]

    def _execute(self, sql: str, params: Sequence[Any], *, timeout_s: float, max_rows: int, cancel: CancelToken) -> RawResult:
        return self._run(sql, params, timeout_s=timeout_s, max_rows=max_rows, cancel=cancel)

    def _run(self, sql: str, params: Sequence[Any], *, timeout_s: float, max_rows: int, cancel: CancelToken) -> RawResult:
        import psycopg

        pool = self._pool_get()
        with pool.connection() as conn:
            def run() -> RawResult:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("SET TRANSACTION READ ONLY")
                        cur.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
                        cur.execute(sql, list(params))
                        names = [d.name for d in cur.description or []]
                        rows = cur.fetchmany(max_rows)
                        return RawResult(names, [tuple(normalise_value(v) for v in r) for r in rows])

            try:
                return run_with_timeout(run, timeout_s=timeout_s + 5, cancel=cancel, interrupt=conn.cancel)
            except (QueryTimeout, QueryCancelled):
                raise
            except psycopg.errors.QueryCanceled:
                raise QueryTimeout(f"Query exceeded the {timeout_s:g}s time limit") from None
            except psycopg.Error as e:
                raise ConnectorError(str(e).strip().splitlines()[0][:500]) from None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None
