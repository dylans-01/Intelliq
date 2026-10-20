"""Type and aggregation-level inference.

Every expression gets a data type and a *level*:

- ``const``  — literal or parameter; combines with anything.
- ``row``    — evaluated per source row (dimensions, row-level calcs, LOD results).
- ``agg``    — aggregate, evaluated per view mark in the database.
- ``table``  — table calculation, evaluated on the returned result set.

Mixing ``row`` and ``agg`` in one expression is an error, exactly as in
Tableau ("Cannot mix aggregate and non-aggregate arguments").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import functions as F
from .ast import (
    Binary,
    Case,
    ColumnRef,
    FieldRef,
    Func,
    If,
    InList,
    Literal,
    Lod,
    Node,
    ParamRef,
    ResultRef,
    Unary,
)
from .errors import CalcError


@dataclass(frozen=True)
class Typed:
    data_type: str
    level: str  # const | row | agg | table


# Resolver for references: returns Typed or raises CalcError.
FieldResolver = Callable[[str], Typed]
ParamResolver = Callable[[str], str]

_LEVEL_ORDER = {"const": 0, "row": 1, "agg": 2, "table": 3}
COMPARISON = {"=", "<>", "<", "<=", ">", ">="}


def _combine_levels(levels: list[str], node: Node) -> str:
    non_const = {lv for lv in levels if lv != "const"}
    if not non_const:
        return "const"
    if "row" in non_const and ("agg" in non_const or "table" in non_const):
        raise CalcError(
            "Cannot mix aggregate and non-aggregate arguments in this expression",
            node.span,
            code="mixed_aggregation",
        )
    return max(non_const, key=lambda lv: _LEVEL_ORDER[lv])


def _is_numeric(t: str) -> bool:
    return t in F.NUMERIC or t == "null"


class TypeChecker:
    def __init__(self, fields: FieldResolver, params: ParamResolver | None = None):
        self.fields = fields
        self.params = params or (lambda name: (_ for _ in ()).throw(CalcError(f"Unknown parameter '{name}'")))
        # Keyed by id() for speed; the node is stored alongside so it cannot be
        # garbage-collected and have its id reused by a different node.
        self._cache: dict[int, tuple[Node, Typed]] = {}

    def check(self, node: Node) -> Typed:
        key = id(node)
        cached = self._cache.get(key)
        if cached is None or cached[0] is not node:
            cached = (node, self._check(node))
            self._cache[key] = cached
        return cached[1]

    def _check(self, node: Node) -> Typed:
        if isinstance(node, Literal):
            return Typed(node.kind, "const")
        if isinstance(node, FieldRef):
            try:
                return self.fields(node.name)
            except CalcError as e:
                raise CalcError(e.message, node.span, e.code) from None
        if isinstance(node, ParamRef):
            try:
                return Typed(self.params(node.name), "const")
            except CalcError as e:
                raise CalcError(e.message, node.span, "unknown_parameter") from None
        if isinstance(node, ColumnRef):
            return Typed(node.data_type, "row")
        if isinstance(node, ResultRef):
            return Typed("null", "agg")
        if isinstance(node, Unary):
            inner = self.check(node.operand)
            if node.op == "NOT":
                if inner.data_type not in ("boolean", "null"):
                    raise CalcError("NOT requires a boolean expression", node.span, "type_mismatch")
                return Typed("boolean", inner.level)
            if not _is_numeric(inner.data_type):
                raise CalcError("Unary minus requires a number", node.span, "type_mismatch")
            return Typed(inner.data_type, inner.level)
        if isinstance(node, Binary):
            return self._binary(node)
        if isinstance(node, InList):
            operand = self.check(node.operand)
            items = [self.check(i) for i in node.items]
            for item, src in zip(items, node.items, strict=True):
                if not self._comparable(operand.data_type, item.data_type):
                    raise CalcError(f"Cannot compare {operand.data_type} with {item.data_type}", src.span, "type_mismatch")
            level = _combine_levels([operand.level] + [i.level for i in items], node)
            return Typed("boolean", level)
        if isinstance(node, Func):
            return self._func(node)
        if isinstance(node, If):
            levels, types = [], []
            for cond, value in node.branches:
                c = self.check(cond)
                if c.data_type not in ("boolean", "null"):
                    raise CalcError("IF condition must be a boolean expression", cond.span, "type_mismatch")
                v = self.check(value)
                levels += [c.level, v.level]
                types.append(v.data_type)
            if node.else_ is not None:
                e = self.check(node.else_)
                levels.append(e.level)
                types.append(e.data_type)
            return Typed(self._unify(types, node), _combine_levels(levels, node))
        if isinstance(node, Case):
            subj = self.check(node.subject)
            levels, types = [subj.level], []
            for value, result in node.whens:
                v, r = self.check(value), self.check(result)
                levels += [v.level, r.level]
                types.append(r.data_type)
            if node.else_ is not None:
                e = self.check(node.else_)
                levels.append(e.level)
                types.append(e.data_type)
            return Typed(self._unify(types, node), _combine_levels(levels, node))
        if isinstance(node, Lod):
            for d in node.dimensions:
                dt = self.check(d)
                if dt.level not in ("row", "const"):
                    raise CalcError("Level of detail dimensions must be row-level fields", d.span, "invalid_lod")
            inner = self.check(node.expr)
            if inner.level not in ("agg", "const"):
                raise CalcError(
                    "The expression inside a level of detail calculation must be an aggregate, e.g. SUM([Sales])",
                    node.expr.span,
                    "invalid_lod",
                )
            # An LOD result behaves as a row-level value that can itself be aggregated.
            return Typed(inner.data_type, "row")
        raise CalcError(f"Unsupported expression {type(node).__name__}", node.span)

    def _unify(self, types: list[str], node: Node) -> str:
        concrete = [t for t in types if t != "null"]
        if not concrete:
            return "null"
        if all(t in F.NUMERIC for t in concrete):
            return "real" if "real" in concrete else "integer"
        if all(t in F.TEMPORAL for t in concrete):
            return "datetime" if "datetime" in concrete else "date"
        first = concrete[0]
        if any(t != first for t in concrete):
            raise CalcError("All branches must return the same data type", node.span, "type_mismatch")
        return first

    def _binary(self, node: Binary) -> Typed:
        left, right = self.check(node.left), self.check(node.right)
        level = _combine_levels([left.level, right.level], node)
        lt, rt = left.data_type, right.data_type
        op = node.op
        if op in ("AND", "OR"):
            for side, t in ((node.left, lt), (node.right, rt)):
                if t not in ("boolean", "null"):
                    raise CalcError(f"{op} requires boolean operands", side.span, "type_mismatch")
            return Typed("boolean", level)
        if op in COMPARISON:
            if not self._comparable(lt, rt):
                raise CalcError(f"Cannot compare {lt} with {rt}", node.span, "type_mismatch")
            return Typed("boolean", level)
        if op == "+":
            if lt == "string" or rt == "string":
                if lt not in ("string", "null") or rt not in ("string", "null"):
                    raise CalcError("Cannot add a string and a non-string; use STR() to convert", node.span, "type_mismatch")
                return Typed("string", level)
            if lt in F.TEMPORAL and _is_numeric(rt):
                return Typed(lt, level)  # date + days
        if op == "-" and lt in F.TEMPORAL and rt in F.TEMPORAL:
            return Typed("real", level)  # difference in days
        if op == "-" and lt in F.TEMPORAL and _is_numeric(rt):
            return Typed(lt, level)
        if not (_is_numeric(lt) and _is_numeric(rt)):
            raise CalcError(f"Operator {op} requires numeric operands, got {lt} and {rt}", node.span, "type_mismatch")
        if op in ("/", "^"):
            return Typed("real", level)
        return Typed("real" if "real" in (lt, rt) else ("integer" if lt != "null" or rt != "null" else "null"), level)

    @staticmethod
    def _comparable(a: str, b: str) -> bool:
        if "null" in (a, b) or a == b:
            return True
        if a in F.NUMERIC and b in F.NUMERIC:
            return True
        if a in F.TEMPORAL and b in F.TEMPORAL:
            return True
        return False

    def _func(self, node: Func) -> Typed:
        fdef = F.get(node.name)
        if fdef is None:
            raise CalcError(f"Unknown or unsupported function {node.name}()", node.span, "unsupported_function")
        if not fdef.check_arity(len(node.args)):
            expected = (
                str(fdef.min_args)
                if fdef.min_args == fdef.max_args
                else f"{fdef.min_args}–{fdef.max_args if fdef.max_args is not None else 'n'}"
            )
            raise CalcError(
                f"{node.name}() expects {expected} argument(s), got {len(node.args)}", node.span, "arity"
            )

        # Date-part string literal arguments are validated, not type-checked as data.
        part_arg = node.name in ("DATETRUNC", "DATEPART", "DATEADD", "DATEDIFF")
        args = node.args
        if part_arg:
            part = args[0]
            if not (isinstance(part, Literal) and part.kind == "string"):
                raise CalcError(f"{node.name}() requires a literal date part such as 'month'", part.span, "invalid_argument")
            allowed = F.TRUNC_PARTS if node.name == "DATETRUNC" else F.DATE_PARTS
            if str(part.value).lower() not in allowed:
                raise CalcError(
                    f"Unsupported date part '{part.value}'. Use one of: {', '.join(allowed)}", part.span, "invalid_argument"
                )
            args = args[1:]
        if node.name in ("RANK", "RANK_DENSE", "RANK_UNIQUE", "RANK_PERCENTILE") and len(args) == 2:
            order = args[1]
            if not (isinstance(order, Literal) and order.kind == "string" and str(order.value).lower() in ("asc", "desc")):
                raise CalcError("Rank order must be 'asc' or 'desc'", order.span, "invalid_argument")
            args = args[:1]

        typed = [self.check(a) for a in args]
        types = [t.data_type for t in typed]
        levels = [t.level for t in typed]

        if fdef.kind == "aggregate" and not (node.name in ("MIN", "MAX") and len(node.args) == 2):
            for a, t in zip(args, typed, strict=True):
                if t.level in ("agg", "table"):
                    raise CalcError(
                        f"Argument to {node.name}() is already an aggregation and cannot be aggregated further",
                        a.span,
                        "nested_aggregation",
                    )
            if node.name in ("SUM", "AVG", "MEDIAN", "STDEV", "STDEVP", "VAR", "VARP") and not _is_numeric(types[0]):
                raise CalcError(f"{node.name}() requires a numeric argument", node.span, "type_mismatch")
            return Typed(fdef.returns(types), "agg")

        if fdef.kind == "table":
            for a, t in zip(args, typed, strict=True):
                if t.level == "row":
                    raise CalcError(
                        f"Argument to {node.name}() must be an aggregate, e.g. {node.name}(SUM([Sales]))",
                        a.span,
                        "table_calc_requires_aggregate",
                    )
            if node.name == "LOOKUP" and not _is_numeric(types[1]):
                raise CalcError("LOOKUP() offset must be an integer", args[1].span, "type_mismatch")
            if node.name == "LOOKUP" and typed[1].level != "const":
                raise CalcError("LOOKUP() offset must be a constant", args[1].span, "invalid_argument")
            return Typed(fdef.returns(types), "table")

        # Scalar function
        self._check_scalar_args(node, types, args)
        if part_arg:
            types = ["string"] + types
        return Typed(fdef.returns(types), _combine_levels(levels, node))

    def _check_scalar_args(self, node: Func, types: list[str], args: tuple[Node, ...]) -> None:
        name = node.name
        numeric_fns = {"ABS", "ROUND", "CEILING", "FLOOR", "SQRT", "POWER", "EXP", "LN", "LOG", "SIGN", "DIV", "ZN"}
        string_fns = {"LEN", "LEFT", "RIGHT", "MID", "UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM",
                      "CONTAINS", "STARTSWITH", "ENDSWITH", "REPLACE", "FIND"}  # fmt: skip
        date_fns = {"YEAR", "QUARTER", "MONTH", "DAY"}
        if name in numeric_fns:
            for a, t in zip(args, types, strict=True):
                if not _is_numeric(t):
                    raise CalcError(f"{name}() requires numeric arguments", a.span, "type_mismatch")
        elif name in string_fns:
            if types[0] not in ("string", "null"):
                raise CalcError(f"{name}() requires a string as its first argument", args[0].span, "type_mismatch")
            if name in ("LEFT", "RIGHT", "MID"):
                for a, t in zip(args[1:], types[1:], strict=True):
                    if not _is_numeric(t):
                        raise CalcError(f"{name}() position/length must be numeric", a.span, "type_mismatch")
        elif name in date_fns or name in ("DATETRUNC", "DATEPART"):
            if types[-1] not in (*F.TEMPORAL, "null"):
                raise CalcError(f"{name}() requires a date argument", args[-1].span, "type_mismatch")
        elif name == "DATEADD":
            if not _is_numeric(types[0]):
                raise CalcError("DATEADD() interval must be numeric", args[0].span, "type_mismatch")
            if types[1] not in (*F.TEMPORAL, "null"):
                raise CalcError("DATEADD() requires a date argument", args[1].span, "type_mismatch")
        elif name == "DATEDIFF":
            for a, t in zip(args, types, strict=True):
                if t not in (*F.TEMPORAL, "null"):
                    raise CalcError("DATEDIFF() requires date arguments", a.span, "type_mismatch")
        elif name in ("IIF",):
            if types[0] not in ("boolean", "null"):
                raise CalcError("IIF() test must be a boolean expression", args[0].span, "type_mismatch")
            self._unify(types[1:], node)
        elif name == "IFNULL":
            self._unify(types, node)
        elif name in ("MIN", "MAX"):
            self._unify(types, node)


def infer(node: Node, fields: FieldResolver, params: ParamResolver | None = None) -> Typed:
    return TypeChecker(fields, params).check(node)
