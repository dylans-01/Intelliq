"""Result-stage evaluation of table calculations.

Table calculations (RUNNING_SUM, RANK, TOTAL, LOOKUP, ...) operate on the
aggregated result a visualization displays, not on source rows. The planner
pushes their aggregate arguments to the database as hidden result columns
(``ResultRef``) and this module evaluates the remaining expression over the
returned rows, partition by partition, in addressing order.

Partitioning and addressing follow Tableau's model: ``partition_by`` fields
split the result into independent windows; within a window rows are ordered
by ``order_by`` (the addressing fields).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .ast import Binary, Case, Func, If, InList, Literal, Node, ParamRef, ResultRef, Unary
from .errors import CalcError

Row = dict[str, Any]


@dataclass
class Addressing:
    partition_by: list[str] = field(default_factory=list)  # result column names
    order_by: list[tuple[str, bool]] = field(default_factory=list)  # (column, descending)


def _sort_key(value: Any):
    # NULLs sort last; mixed types never occur within one column.
    if value is None:
        return (1, 0)
    if isinstance(value, datetime | date):
        return (0, value.isoformat())
    return (0, value)


def _partitions(rows: Sequence[Row], addressing: Addressing) -> list[list[int]]:
    groups: dict[tuple, list[int]] = {}
    for i, row in enumerate(rows):
        key = tuple(row.get(c) for c in addressing.partition_by)
        groups.setdefault(key, []).append(i)
    ordered = []
    for idxs in groups.values():
        for col, desc in reversed(addressing.order_by):
            idxs.sort(key=lambda i, c=col: _sort_key(rows[i].get(c)), reverse=desc)
        ordered.append(idxs)
    return ordered


def _null(*values: Any) -> bool:
    return any(v is None for v in values)


def _arith(op: str, a: Any, b: Any) -> Any:
    if _null(a, b):
        return None
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        return None if b == 0 else a / b
    if op == "%":
        return None if b == 0 else math.fmod(a, b)
    if op == "^":
        try:
            return float(a) ** b
        except (OverflowError, ValueError, ZeroDivisionError):
            return None
    raise CalcError(f"Unsupported operator {op} in table calculation")


def _compare(op: str, a: Any, b: Any) -> bool | None:
    if _null(a, b):
        return None
    return {"=": a == b, "<>": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


class TableCalcEvaluator:
    def __init__(self, rows: Sequence[Row], addressing: Addressing, params: dict[str, Any] | None = None):
        self.rows = rows
        self.n = len(rows)
        self.addressing = addressing
        self.params = params or {}
        self.parts = _partitions(rows, addressing)

    def evaluate(self, node: Node) -> list[Any]:
        if isinstance(node, Literal):
            return [node.value] * self.n
        if isinstance(node, ResultRef):
            return [r.get(node.column) for r in self.rows]
        if isinstance(node, ParamRef):
            return [self.params.get(node.name)] * self.n
        if isinstance(node, Unary):
            vals = self.evaluate(node.operand)
            if node.op == "NOT":
                return [None if v is None else (not v) for v in vals]
            return [None if v is None else -v for v in vals]
        if isinstance(node, Binary):
            left, right = self.evaluate(node.left), self.evaluate(node.right)
            if node.op == "AND":
                return [None if (a is None or b is None) and not (a is False or b is False) else bool(a and b)
                        for a, b in zip(left, right, strict=True)]
            if node.op == "OR":
                return [True if (a is True or b is True) else (None if a is None or b is None else False)
                        for a, b in zip(left, right, strict=True)]
            if node.op in ("=", "<>", "<", "<=", ">", ">="):
                return [_compare(node.op, a, b) for a, b in zip(left, right, strict=True)]
            if node.op == "+" and any(isinstance(v, str) for v in left + right):
                return [None if _null(a, b) else f"{a}{b}" for a, b in zip(left, right, strict=True)]
            return [_arith(node.op, a, b) for a, b in zip(left, right, strict=True)]
        if isinstance(node, InList):
            operand = self.evaluate(node.operand)
            items = [self.evaluate(i) for i in node.items]
            out = []
            for i, v in enumerate(operand):
                if v is None:
                    out.append(None)
                    continue
                hit = any(col[i] == v for col in items)
                out.append(not hit if node.negated else hit)
            return out
        if isinstance(node, If):
            conds = [(self.evaluate(c), self.evaluate(v)) for c, v in node.branches]
            default = self.evaluate(node.else_) if node.else_ is not None else [None] * self.n
            out = []
            for i in range(self.n):
                for cvals, vvals in conds:
                    if cvals[i] is True:
                        out.append(vvals[i])
                        break
                else:
                    out.append(default[i])
            return out
        if isinstance(node, Case):
            subject = self.evaluate(node.subject)
            whens = [(self.evaluate(v), self.evaluate(r)) for v, r in node.whens]
            default = self.evaluate(node.else_) if node.else_ is not None else [None] * self.n
            out = []
            for i in range(self.n):
                for vvals, rvals in whens:
                    if subject[i] is not None and subject[i] == vvals[i]:
                        out.append(rvals[i])
                        break
                else:
                    out.append(default[i])
            return out
        if isinstance(node, Func):
            return self._func(node)
        raise CalcError(f"{type(node).__name__} cannot be evaluated in a table calculation", getattr(node, "span", None))

    # ------------------------------------------------------------------------
    def _per_partition(self, values: list[Any], fn) -> list[Any]:
        out: list[Any] = [None] * self.n
        for idxs in self.parts:
            part_vals = [values[i] for i in idxs]
            results = fn(part_vals)
            for i, r in zip(idxs, results, strict=True):
                out[i] = r
        return out

    def _func(self, node: Func) -> list[Any]:
        name = node.name
        if name in ("INDEX", "FIRST", "LAST", "SIZE"):
            def positional(part: list[Any]) -> list[Any]:
                size = len(part)
                return {
                    "INDEX": list(range(1, size + 1)),
                    "FIRST": [-i for i in range(size)],
                    "LAST": [size - 1 - i for i in range(size)],
                    "SIZE": [size] * size,
                }[name]

            return self._per_partition([None] * self.n, positional)

        if name in ("RANK", "RANK_DENSE", "RANK_UNIQUE", "RANK_PERCENTILE"):
            values = self.evaluate(node.args[0])
            descending = name != "RANK_PERCENTILE"  # Tableau defaults: RANK* desc, RANK_PERCENTILE asc
            if len(node.args) > 1:
                descending = str(node.args[1].value).lower() == "desc"  # type: ignore[attr-defined]
            return self._per_partition(values, lambda part: _rank(part, name, descending))

        if name == "LOOKUP":
            values = self.evaluate(node.args[0])
            offset = int(self.evaluate(node.args[1])[0] or 0)

            def lookup(part: list[Any]) -> list[Any]:
                return [part[i + offset] if 0 <= i + offset < len(part) else None for i in range(len(part))]

            return self._per_partition(values, lookup)

        if name.startswith("RUNNING_"):
            values = self.evaluate(node.args[0])
            return self._per_partition(values, lambda part: _running(part, name))

        if name.startswith("WINDOW_") or name == "TOTAL":
            values = self.evaluate(node.args[0])
            kind = "SUM" if name == "TOTAL" else name.split("_", 1)[1]
            return self._per_partition(values, lambda part: [_window(part, kind)] * len(part))

        args = [self.evaluate(a) for a in node.args]
        return [_scalar(name, [a[i] for a in args]) for i in range(self.n)]


def _non_null(values: list[Any]) -> list[Any]:
    return [v for v in values if v is not None]


def _window(values: list[Any], kind: str) -> Any:
    vals = _non_null(values)
    if kind == "COUNT":
        return len(vals)
    if not vals:
        return None
    if kind == "SUM":
        return sum(vals)
    if kind == "AVG":
        return sum(vals) / len(vals)
    if kind == "MIN":
        return min(vals)
    if kind == "MAX":
        return max(vals)
    raise CalcError(f"Unsupported window aggregate {kind}")


def _running(values: list[Any], name: str) -> list[Any]:
    out, acc, count = [], None, 0
    for v in values:
        if v is not None:
            count += 1
            if name in ("RUNNING_SUM", "RUNNING_AVG"):
                acc = v if acc is None else acc + v
            elif name == "RUNNING_MIN":
                acc = v if acc is None else min(acc, v)
            elif name == "RUNNING_MAX":
                acc = v if acc is None else max(acc, v)
        if name == "RUNNING_COUNT":
            out.append(count)
        elif name == "RUNNING_AVG":
            out.append(None if acc is None else acc / count)
        else:
            out.append(acc)
    return out


def _rank(values: list[Any], name: str, descending: bool) -> list[Any]:
    present = [(v, i) for i, v in enumerate(values) if v is not None]
    present.sort(key=lambda p: p[0], reverse=descending)
    ranks: list[Any] = [None] * len(values)
    if name == "RANK_UNIQUE":
        for r, (_, i) in enumerate(present, start=1):
            ranks[i] = r
        return ranks
    dense, prev, competition = 0, object(), 0
    for pos, (v, i) in enumerate(present, start=1):
        if v != prev:
            dense += 1
            competition = pos
            prev = v
        ranks[i] = dense if name == "RANK_DENSE" else competition
    if name == "RANK_PERCENTILE":
        n = len(present)
        # Tableau: percentile rank in [0, 1], ascending by value by default.
        asc_sorted = sorted(v for v, _ in present)
        for _v, i in present:
            below = sum(1 for x in asc_sorted if x < values[i])
            ranks[i] = 0.0 if n <= 1 else below / (n - 1)
            if descending:
                ranks[i] = 1.0 - ranks[i] if n > 1 else 0.0
    return ranks


def _scalar(name: str, args: list[Any]) -> Any:
    a = args[0] if args else None
    if name == "ABS":
        return None if a is None else abs(a)
    if name == "ROUND":
        if a is None:
            return None
        digits = int(args[1]) if len(args) > 1 and args[1] is not None else 0
        q = 10**digits
        return math.floor(abs(a) * q + 0.5) / q * (1 if a >= 0 else -1)  # half away from zero
    if name == "ZN":
        return 0 if a is None else a
    if name == "IFNULL":
        return args[1] if a is None else a
    if name == "ISNULL":
        return a is None
    if name == "IIF":
        if a is True:
            return args[1]
        if a is False:
            return args[2]
        return args[3] if len(args) > 3 else None
    if name in ("MIN", "MAX") and len(args) == 2:
        if _null(*args):
            return None
        return min(args) if name == "MIN" else max(args)
    if name == "FLOAT":
        return None if a is None else float(a)
    if name == "INT":
        return None if a is None else int(a)
    if name == "STR":
        return None if a is None else str(a)
    if name == "SQRT":
        return None if a is None or a < 0 else math.sqrt(a)
    if name == "SIGN":
        return None if a is None else (a > 0) - (a < 0)
    raise CalcError(f"{name}() is not supported inside a table calculation", code="unsupported_in_table_calc")
