"""Snowflake connector (snowflake-connector-python, qmark parameters)."""

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


class SnowflakeConnector(Connector):
    kind = "snowflake"
    dialect = "snowflake"
    label = "Snowflake"
    config_fields = [
        ("account", "Account identifier", False, True),
        ("user", "User", False, True),
        ("password", "Password", True, False),
        ("private_key", "Private key (PEM)", True, False),
        ("warehouse", "Warehouse", False, True),
        ("database", "Database", False, True),
        ("schema", "Schema", False, False),
        ("role", "Role", False, False),
    ]

    def _connect(self):
        try:
            import snowflake.connector
        except ImportError:
            raise DriverMissing("Install the 'snowflake' extra to use Snowflake connections") from None
        kwargs: dict[str, Any] = {
            "account": self.config.get("account"),
            "user": self.config.get("user"),
            "warehouse": self.config.get("warehouse"),
            "database": self.config.get("database"),
            "schema": self.config.get("schema"),
            "role": self.config.get("role"),
            "application": "intelliq",
            "paramstyle": "qmark",
            "client_session_keep_alive": False,
        }
        if self.secrets.get("private_key"):
            from cryptography.hazmat.primitives import serialization

            key = serialization.load_pem_private_key(self.secrets["private_key"].encode(), password=None)
            kwargs["private_key"] = key.private_bytes(
                serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        else:
            kwargs["password"] = self.secrets.get("password")
        return snowflake.connector.connect(**{k: v for k, v in kwargs.items() if v is not None})

    def test(self) -> None:
        self._execute("SELECT 1", [], timeout_s=30, max_rows=1, cancel=CancelToken())

    def list_tables(self) -> list[TableInfo]:
        res = self._execute(
            "SELECT table_schema, table_name, table_type FROM information_schema.tables "
            "WHERE table_schema <> 'INFORMATION_SCHEMA' ORDER BY 1, 2",
            [], timeout_s=60, max_rows=20_000, cancel=CancelToken(),
        )
        return [TableInfo(s, n, "view" if "VIEW" in t else "table") for s, n, t in res.rows]

    def describe(self, schema: str | None, table: str) -> list[ColumnInfo]:
        res = self._execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = ? AND (? IS NULL OR table_schema = ?) ORDER BY ordinal_position",
            [table, schema, schema], timeout_s=60, max_rows=5_000, cancel=CancelToken(),
        )
        return [ColumnInfo(n, map_native_type(t), t, nullable == "YES") for n, t, nullable in res.rows]

    def _execute(self, sql: str, params: Sequence[Any], *, timeout_s: float, max_rows: int, cancel: CancelToken) -> RawResult:
        import snowflake.connector

        conn = self._connect()
        cur = conn.cursor()

        def interrupt():
            if cur.sfqid:
                conn.cursor().execute("SELECT SYSTEM$CANCEL_QUERY(?)", [cur.sfqid])

        def run() -> RawResult:
            cur.execute(sql, list(params), timeout=int(timeout_s))
            names = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(max_rows)
            return RawResult(names, [tuple(normalise_value(v) for v in r) for r in rows])

        try:
            return run_with_timeout(run, timeout_s=timeout_s + 10, cancel=cancel, interrupt=interrupt)
        except (QueryTimeout, QueryCancelled):
            raise
        except snowflake.connector.errors.Error as e:
            raise ConnectorError(str(e).splitlines()[0][:500]) from None
        finally:
            conn.close()
