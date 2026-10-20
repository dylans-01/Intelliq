"""Connector contract.

A connector executes *planned* SQL (never user-authored SQL) against one
database, with bound parameters, a timeout, a row cap and cooperative
cancellation. Connectors also expose schema discovery so administrators can
build semantic models against real tables.

Credentials arrive as a separate ``secrets`` mapping decrypted by the
platform at call time; connectors must never log or echo them.
"""

from __future__ import annotations

import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, ClassVar


class ConnectorError(Exception):
    code = "connector_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class QueryTimeout(ConnectorError):
    code = "query_timeout"


class QueryCancelled(ConnectorError):
    code = "query_cancelled"


class DriverMissing(ConnectorError):
    code = "driver_missing"


@dataclass(frozen=True)
class TableInfo:
    schema: str | None
    name: str
    kind: str = "table"  # table | view


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str  # Intelliq type
    native_type: str
    nullable: bool = True


@dataclass
class RawResult:
    column_names: list[str]
    rows: list[tuple]


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            self._event.set()
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE001 - best-effort cancellation
                pass

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def on_cancel(self, cb) -> None:
        with self._lock:
            if self._event.is_set():
                run_now = True
            else:
                self._callbacks.append(cb)
                run_now = False
        if run_now:
            cb()


def render_positional(
    template: str, token_prefix: str, params: dict[str, Any], marker: str, escape_percent: bool = False
) -> tuple[str, list[Any]]:
    """Replace planner tokens with the driver's positional marker, returning
    the SQL and the parameter list in order of appearance."""

    if escape_percent:
        template = template.replace("%", "%%")
    ordered: list[Any] = []
    pattern = re.compile(re.escape(token_prefix) + r"(\d+)")

    def sub(m: re.Match) -> str:
        token = m.group(0)
        if token not in params:
            raise ConnectorError("Internal error: unbound query parameter")
        ordered.append(params[token])
        return marker

    sql = pattern.sub(sub, template)
    return sql, ordered


_TYPE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"bool|bit\b"), "boolean"),
    (re.compile(r"timestamp|datetime|smalldatetime|datetimeoffset"), "datetime"),
    (re.compile(r"^date$|^date\b"), "date"),
    (re.compile(r"tinyint|smallint|bigint|hugeint|\bint\b|integer|^int\d|long|ubigint|uinteger|usmallint|utinyint"), "integer"),
    (re.compile(r"decimal|numeric|number|float|double|real|money"), "real"),
    (re.compile(r"char|text|string|varchar|uuid|json|enum|clob|nvarchar"), "string"),
]


def map_native_type(native: str) -> str:
    t = native.lower().strip()
    if t.startswith(("number", "numeric", "decimal")) and re.search(r",\s*0\)", t):
        return "integer"
    for pattern, mapped in _TYPE_RULES:
        if pattern.search(t):
            return mapped
    return "string"


def normalise_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 else float(v)
    if isinstance(v, bytes | bytearray | memoryview):
        return None  # binary data is not exposed to visualizations
    return v


class Connector(ABC):
    kind: ClassVar[str]
    dialect: ClassVar[str]
    marker: ClassVar[str] = "?"
    escape_percent: ClassVar[bool] = False
    label: ClassVar[str]
    verified: ClassVar[bool] = False  # True only when exercised by the automated contract suite

    def __init__(self, config: dict[str, Any], secrets: dict[str, str]):
        self.config = config
        self.secrets = secrets

    # Configuration schema shown in the admin UI: (name, label, secret, required)
    config_fields: ClassVar[list[tuple[str, str, bool, bool]]] = []

    @abstractmethod
    def test(self) -> None: ...

    @abstractmethod
    def list_tables(self) -> list[TableInfo]: ...

    @abstractmethod
    def describe(self, schema: str | None, table: str) -> list[ColumnInfo]: ...

    @abstractmethod
    def _execute(
        self, sql: str, params: Sequence[Any], *, timeout_s: float, max_rows: int, cancel: CancelToken
    ) -> RawResult: ...

    def execute(
        self,
        sql_template: str,
        token_prefix: str,
        params: dict[str, Any],
        *,
        timeout_s: float,
        max_rows: int,
        cancel: CancelToken | None = None,
    ) -> RawResult:
        sql, ordered = render_positional(sql_template, token_prefix, params, self.marker, self.escape_percent)
        return self._execute(sql, ordered, timeout_s=timeout_s, max_rows=max_rows, cancel=cancel or CancelToken())

    def render(self, sql_template: str, token_prefix: str, params: dict[str, Any]) -> tuple[str, list[Any]]:
        return render_positional(sql_template, token_prefix, params, self.marker, self.escape_percent)

    def close(self) -> None:  # noqa: B027 - optional hook; default is a no-op
        pass


def run_with_timeout(fn, *, timeout_s: float, cancel: CancelToken, interrupt) -> Any:
    """Run ``fn`` in a worker thread; on timeout or cancellation call
    ``interrupt`` (driver-specific) and raise."""

    result: dict[str, Any] = {}

    def target():
        try:
            result["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised in caller thread
            result["error"] = e

    worker = threading.Thread(target=target, daemon=True, name="intelliq-query")
    worker.start()
    deadline = time.monotonic() + timeout_s
    cancel.on_cancel(interrupt)
    while worker.is_alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            interrupt()
            worker.join(5)
            raise QueryTimeout(f"Query exceeded the {timeout_s:g}s time limit")
        if cancel.cancelled:
            worker.join(5)
            raise QueryCancelled("Query was cancelled")
        worker.join(min(remaining, 0.05))
    if cancel.cancelled:
        raise QueryCancelled("Query was cancelled")
    if "error" in result:
        raise result["error"]
    return result.get("value")
