"""Query execution: runs a planned query on a connector, applies the result
stage (table calculations, table-calc filters, final sort and limit) and
returns a serialisable result."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import date, datetime
from typing import Any

from ..calc.errors import CalcError
from ..calc.table_calc import TableCalcEvaluator, _sort_key
from ..connectors.base import CancelToken, Connector
from .cache import ResultCache, cache_key
from .models import QueryResult
from .planner import PlanError, PlannedQuery, PostFilter


class RunningQueries:
    """Registry of in-flight queries so their owners can cancel them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, tuple[str, CancelToken]] = {}

    def register(self, query_id: str, owner: str) -> CancelToken:
        token = CancelToken()
        with self._lock:
            self._running[query_id] = (owner, token)
        return token

    def finish(self, query_id: str) -> None:
        with self._lock:
            self._running.pop(query_id, None)

    def cancel(self, query_id: str, owner: str, is_admin: bool = False) -> bool:
        with self._lock:
            entry = self._running.get(query_id)
        if entry is None or (entry[0] != owner and not is_admin):
            return False
        entry[1].cancel()
        return True


def _serialise(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    return v


def _matches(op: str, value: Any, values: list[Any]) -> bool:
    if op == "in":
        return value in values
    if op == "not_in":
        return value not in values
    if op == "is_null":
        return value is None
    if op == "not_null":
        return value is not None
    if value is None:
        return False
    if op == "between":
        lo, hi = values
        return (lo is None or value >= lo) and (hi is None or value <= hi)
    target = values[0] if values else None
    if target is None:
        return False
    return {
        "eq": value == target, "neq": value != target, "gt": value > target,
        "gte": value >= target, "lt": value < target, "lte": value <= target,
    }.get(op, True)  # fmt: skip


class QueryExecutor:
    def __init__(self, cache: ResultCache | None = None, running: RunningQueries | None = None):
        self.cache = cache or ResultCache()
        self.running = running or RunningQueries()

    def execute(
        self,
        planned: PlannedQuery,
        connector: Connector,
        *,
        cache_scope: tuple,
        ttl_seconds: float,
        timeout_s: float,
        owner: str,
        include_sql: bool = False,
        query_id: str | None = None,
    ) -> QueryResult:
        query_id = query_id or uuid.uuid4().hex
        started = time.perf_counter()
        sql, ordered = connector.render(planned.sql_template, planned.token_prefix, planned.params)
        key = cache_key(cache_scope, sql, ordered, planned.dialect)

        cached = self.cache.get(key)
        cache_hit = cached is not None
        if cached is None:
            token = self.running.register(query_id, owner)
            try:
                raw = connector.execute(
                    planned.sql_template, planned.token_prefix, planned.params,
                    timeout_s=timeout_s, max_rows=planned.row_cap + 1, cancel=token,
                )
            finally:
                self.running.finish(query_id)
            cached = (raw.column_names, raw.rows)
            self.cache.put(key, cached, ttl_seconds, cells=len(raw.rows) * max(1, len(raw.column_names)))

        names, raw_rows = cached
        truncated = len(raw_rows) > planned.row_cap
        rows = [dict(zip(names, r, strict=True)) for r in raw_rows[: planned.row_cap]]
        rows = self._result_stage(planned, rows)

        if planned.user_limit is not None and len(rows) > planned.user_limit:
            rows = rows[: planned.user_limit]
            truncated = truncated or not (planned.table_calcs or planned.post_filters)
        elif planned.user_limit is None and len(rows) > planned.row_cap:
            truncated = True

        visible = [c for c in planned.columns]
        out_rows = [[_serialise(r.get(c.id)) for c in visible] for r in rows]
        return QueryResult(
            columns=visible,
            rows=out_rows,
            row_count=len(out_rows),
            truncated=truncated,
            cache_hit=cache_hit,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            query_id=query_id,
            sql=sql if include_sql else None,
        )

    def _result_stage(self, planned: PlannedQuery, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if planned.table_calcs:
            for step in planned.table_calcs:
                try:
                    values = TableCalcEvaluator(rows, step.addressing, planned.parameters).evaluate(step.tree)
                except CalcError as e:
                    raise PlanError(e.message, e.code) from None
                for r, v in zip(rows, values, strict=True):
                    r[step.column] = v
        for pf in planned.post_filters:
            rows = [r for r in rows if _matches(pf.op, r.get(pf.column), _coerce_post(pf))]
        if planned.post_sort:
            for s in reversed(planned.post_sort):
                rows.sort(key=lambda r, c=s.column: _sort_key(r.get(c)), reverse=s.direction == "desc")
        return rows


def _coerce_post(pf: PostFilter) -> list[Any]:
    out = []
    for v in pf.values:
        if isinstance(v, str):
            try:
                out.append(float(v))
                continue
            except ValueError:
                pass
        out.append(v)
    return out
