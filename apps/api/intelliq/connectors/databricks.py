"""Databricks SQL connector (databricks-sql-connector, native ``?`` parameters)."""

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


class DatabricksConnector(Connector):
    kind = "databricks"
    dialect = "databricks"
    label = "Databricks SQL"
    config_fields = [
        ("server_hostname", "Server hostname", False, True),
        ("http_path", "HTTP path", False, True),
        ("access_token", "Access token", True, True),
        ("catalog", "Catalog", False, False),
        ("schema", "Schema", False, False),
    ]

    def _connect(self):
        try:
            from databricks import sql as dbsql
        except ImportError:
            raise DriverMissing("Install the 'databricks' extra to use Databricks connections") from None
        return dbsql.connect(
            server_hostname=self.config.get("server_hostname"),
            http_path=self.config.get("http_path"),
            access_token=self.secrets.get("access_token"),
            catalog=self.config.get("catalog"),
            schema=self.config.get("schema"),
            user_agent_entry="intelliq",
        )

    def test(self) -> None:
        self._execute("SELECT 1", [], timeout_s=60, max_rows=1, cancel=CancelToken())

    def list_tables(self) -> list[TableInfo]:
        res = self._execute(
            "SELECT table_schema, table_name, table_type FROM information_schema.tables "
            "WHERE table_schema <> 'information_schema' ORDER BY 1, 2",
            [], timeout_s=120, max_rows=20_000, cancel=CancelToken(),
        )
        return [TableInfo(s, n, "view" if "VIEW" in (t or "") else "table") for s, n, t in res.rows]

    def describe(self, schema: str | None, table: str) -> list[ColumnInfo]:
        res = self._execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = ? AND (? IS NULL OR table_schema = ?) ORDER BY ordinal_position",
            [table, schema, schema], timeout_s=120, max_rows=5_000, cancel=CancelToken(),
        )
        return [ColumnInfo(n, map_native_type(t), t, nullable == "YES") for n, t, nullable in res.rows]

    def _execute(self, sql: str, params: Sequence[Any], *, timeout_s: float, max_rows: int, cancel: CancelToken) -> RawResult:
        conn = self._connect()
        cur = conn.cursor()

        def run() -> RawResult:
            cur.execute(sql, list(params))
            names = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows)
            return RawResult(names, [tuple(normalise_value(v) for v in r) for r in rows])

        try:
            return run_with_timeout(run, timeout_s=timeout_s, cancel=cancel, interrupt=cur.cancel)
        except (QueryTimeout, QueryCancelled):
            raise
        except Exception as e:  # noqa: BLE001 - the driver has no single error base class
            raise ConnectorError(str(e).splitlines()[0][:500] if str(e) else "Databricks error") from None
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
