"""Microsoft SQL Server connector (pyodbc).

Connections request ``ApplicationIntent=ReadOnly``; queries use the ODBC
query timeout and ``cursor.cancel()`` for cancellation. Pooling is provided
by the ODBC driver manager.
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


def _odbc_escape(value: str) -> str:
    # ODBC connection-string values containing ; or } must be braced, with } doubled.
    return "{" + value.replace("}", "}}") + "}"


class SqlServerConnector(Connector):
    kind = "sqlserver"
    dialect = "tsql"
    label = "Microsoft SQL Server"
    config_fields = [
        ("host", "Server", False, True),
        ("port", "Port", False, False),
        ("database", "Database", False, True),
        ("user", "User", False, True),
        ("password", "Password", True, True),
        ("driver", "ODBC driver", False, False),
        ("encrypt", "Encrypt (yes/no)", False, False),
    ]

    def _connect(self):
        try:
            import pyodbc
        except ImportError:
            raise DriverMissing("Install the 'sqlserver' extra and an ODBC driver to use SQL Server") from None
        parts = {
            "DRIVER": _odbc_escape(self.config.get("driver") or "ODBC Driver 18 for SQL Server"),
            "SERVER": _odbc_escape(f"{self.config.get('host')},{int(self.config.get('port') or 1433)}"),
            "DATABASE": _odbc_escape(str(self.config.get("database"))),
            "UID": _odbc_escape(str(self.config.get("user"))),
            "PWD": _odbc_escape(self.secrets.get("password", "")),
            "Encrypt": "yes" if str(self.config.get("encrypt", "yes")).lower() != "no" else "no",
            "ApplicationIntent": "ReadOnly",
            "APP": "intelliq",
        }
        conn_str = ";".join(f"{k}={v}" for k, v in parts.items())
        return pyodbc.connect(conn_str, autocommit=True, timeout=15)

    def test(self) -> None:
        self._execute("SELECT 1", [], timeout_s=15, max_rows=1, cancel=CancelToken())

    def list_tables(self) -> list[TableInfo]:
        res = self._execute(
            "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES ORDER BY 1, 2",
            [], timeout_s=30, max_rows=20_000, cancel=CancelToken(),
        )
        return [TableInfo(s, n, "view" if "VIEW" in t else "table") for s, n, t in res.rows]

    def describe(self, schema: str | None, table: str) -> list[ColumnInfo]:
        res = self._execute(
            "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = ? AND (? IS NULL OR TABLE_SCHEMA = ?) ORDER BY ORDINAL_POSITION",
            [table, schema, schema], timeout_s=30, max_rows=5_000, cancel=CancelToken(),
        )
        return [ColumnInfo(n, map_native_type(t), t, nullable == "YES") for n, t, nullable in res.rows]

    def _execute(self, sql: str, params: Sequence[Any], *, timeout_s: float, max_rows: int, cancel: CancelToken) -> RawResult:
        import pyodbc

        conn = self._connect()
        conn.timeout = int(timeout_s)
        cur = conn.cursor()

        def run() -> RawResult:
            cur.execute(sql, *params)
            names = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows)
            return RawResult(names, [tuple(normalise_value(v) for v in r) for r in rows])

        try:
            return run_with_timeout(run, timeout_s=timeout_s + 5, cancel=cancel, interrupt=cur.cancel)
        except (QueryTimeout, QueryCancelled):
            raise
        except pyodbc.OperationalError as e:
            if "HYT00" in str(e):
                raise QueryTimeout(f"Query exceeded the {timeout_s:g}s time limit") from None
            raise ConnectorError(str(e).splitlines()[0][:500]) from None
        except pyodbc.Error as e:
            raise ConnectorError(str(e).splitlines()[0][:500]) from None
        finally:
            conn.close()
