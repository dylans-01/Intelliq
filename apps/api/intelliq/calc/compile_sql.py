"""Compile bound calculation trees into sqlglot expressions.

The planner hands this module trees in which every ``FieldRef`` has been
bound to a ``ColumnRef`` (or inlined calculation) and every LOD has been
replaced with a reference to its subquery. Parameters arrive already
validated against their declared type and domain.

All values become sqlglot literal/placeholder nodes; identifiers become
quoted sqlglot identifiers. No SQL text is concatenated here.

Semantics follow Tableau where it differs from plain SQL:
- ``a / b`` is floating-point division and returns NULL when ``b = 0``.
- ``INT()`` truncates toward zero (a plain CAST rounds on some engines).
- ``MIN(a, b)``/``MAX(a, b)`` return NULL if either argument is NULL.
- ``SQRT``/``LN``/``LOG`` return NULL outside their domain instead of erroring.
- Weeks start on a configurable day (Tableau US default: Sunday) and
  ``DATEPART('weekday', d)`` returns 1 = first day of week.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from sqlglot import exp

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

# Reference dates used to compute weekday numbers portably across dialects.
_WEEK_REFERENCE = {"sunday": "1900-01-07", "monday": "1900-01-01"}

_UNIT = {
    "year": "YEAR",
    "quarter": "QUARTER",
    "month": "MONTH",
    "week": "WEEK",
    "day": "DAY",
    "hour": "HOUR",
    "minute": "MINUTE",
    "second": "SECOND",
    "dayofyear": "DOY",
}


def literal(value: Any) -> exp.Expression:
    """Typed, escaped literal. Strings are escaped by sqlglot per dialect."""

    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int | float):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise CalcError("Non-finite numbers are not allowed")
        return exp.Literal.number(value)
    if isinstance(value, datetime):
        return exp.cast(exp.Literal.string(value.isoformat(sep=" ")), exp.DataType.Type.TIMESTAMP)
    if isinstance(value, date):
        return exp.cast(exp.Literal.string(value.isoformat()), exp.DataType.Type.DATE)
    if isinstance(value, str):
        return exp.Literal.string(value)
    raise CalcError(f"Unsupported literal value of type {type(value).__name__}")


def _double(e: exp.Expression) -> exp.Expression:
    return exp.cast(e, exp.DataType.Type.DOUBLE)


def _trunc_to_int(e: exp.Expression) -> exp.Expression:
    # Truncate toward zero portably, then cast.
    return exp.cast(
        exp.Case(ifs=[exp.If(this=exp.LT(this=e, expression=exp.Literal.number(0)), true=exp.Ceil(this=e))],
                 default=exp.Floor(this=e)),
        exp.DataType.Type.BIGINT,
    )


def _null_if_outside(cond: exp.Expression, value: exp.Expression) -> exp.Expression:
    return exp.Case(ifs=[exp.If(this=cond, true=exp.Null())], default=value)


class SqlCompiler:
    def __init__(
        self,
        dialect: str,
        *,
        week_start: str = "sunday",
        params: dict[str, Any] | None = None,
        result_column: Callable[[str], exp.Expression] | None = None,
    ):
        self.dialect = dialect
        self.week_start = week_start
        self.params = params or {}
        self.result_column = result_column

    def compile(self, node: Node) -> exp.Expression:
        if isinstance(node, Literal):
            return literal(node.value)
        if isinstance(node, ColumnRef):
            return exp.column(node.column, table=node.table_alias, quoted=True)
        if isinstance(node, ParamRef):
            if node.name not in self.params:
                raise CalcError(f"No value supplied for parameter '{node.name}'", node.span, "unknown_parameter")
            return literal(self.params[node.name])
        if isinstance(node, ResultRef):
            if self.result_column is None:
                raise CalcError("Result references are only valid in the result stage", node.span)
            return self.result_column(node.column)
        if isinstance(node, FieldRef):
            raise CalcError(f"Field [{node.name}] was not bound before compilation", node.span, "internal")
        if isinstance(node, Lod):
            raise CalcError("Level of detail expression was not planned before compilation", node.span, "internal")
        if isinstance(node, Unary):
            inner = self.compile(node.operand)
            return exp.Not(this=exp.paren(inner)) if node.op == "NOT" else exp.Neg(this=exp.paren(inner))
        if isinstance(node, Binary):
            return self._binary(node)
        if isinstance(node, InList):
            expr = exp.In(this=self.compile(node.operand), expressions=[self.compile(i) for i in node.items])
            return exp.Not(this=expr) if node.negated else expr
        if isinstance(node, If):
            ifs = [exp.If(this=self.compile(c), true=self.compile(v)) for c, v in node.branches]
            return exp.Case(ifs=ifs, default=self.compile(node.else_) if node.else_ is not None else None)
        if isinstance(node, Case):
            subject = self.compile(node.subject)
            ifs = [exp.If(this=exp.EQ(this=subject.copy(), expression=self.compile(v)), true=self.compile(r))
                   for v, r in node.whens]
            return exp.Case(ifs=ifs, default=self.compile(node.else_) if node.else_ is not None else None)
        if isinstance(node, Func):
            return self._func(node)
        raise CalcError(f"Cannot compile {type(node).__name__} to SQL", getattr(node, "span", None))

    # -- operators --------------------------------------------------------------
    def _binary(self, node: Binary) -> exp.Expression:
        left, right = self.compile(node.left), self.compile(node.right)
        op = node.op
        if op == "AND":
            return exp.And(this=exp.paren(left), expression=exp.paren(right))
        if op == "OR":
            return exp.Or(this=exp.paren(left), expression=exp.paren(right))
        cmp = {"=": exp.EQ, "<>": exp.NEQ, "<": exp.LT, "<=": exp.LTE, ">": exp.GT, ">=": exp.GTE}
        if op in cmp:
            return cmp[op](this=left, expression=right)
        if op == "+" and self._is_string(node):
            return exp.DPipe(this=left, expression=right, safe=False)
        if op in ("+", "-") and self._is_temporal(node.left) and not self._is_temporal(node.right):
            amount = right if op == "+" else exp.Neg(this=exp.paren(right))
            return self._keep_temporal_type(exp.DateAdd(this=left, expression=amount, unit=exp.var("DAY")), node.left)
        if op == "-" and self._is_temporal(node.left) and self._is_temporal(node.right):
            return exp.DateDiff(this=left, expression=right, unit=exp.var("DAY"))
        if op == "+":
            return exp.Add(this=left, expression=right)
        if op == "-":
            return exp.Sub(this=left, expression=right)
        if op == "*":
            return exp.Mul(this=left, expression=right)
        if op == "/":
            return exp.Div(this=_double(left), expression=exp.Nullif(this=right, expression=exp.Literal.number(0)))
        if op == "%":
            return exp.Mod(this=exp.paren(left), expression=exp.paren(right))
        if op == "^":
            return exp.Pow(this=_double(left), expression=right)
        raise CalcError(f"Unsupported operator {op}", node.span)

    # Light-weight static type hints so operator lowering does not need the
    # full checker. The planner has already type-checked the tree.
    def _is_string(self, node: Node) -> bool:
        if isinstance(node, Literal):
            return node.kind == "string"
        if isinstance(node, ColumnRef):
            return node.data_type == "string"
        if isinstance(node, Binary) and node.op == "+":
            return self._is_string(node.left) or self._is_string(node.right)
        if isinstance(node, Func):
            return node.name in {"STR", "LEFT", "RIGHT", "MID", "UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM", "REPLACE"}
        if isinstance(node, ParamRef):
            return isinstance(self.params.get(node.name), str)
        if isinstance(node, If):
            return any(self._is_string(v) for _, v in node.branches)
        return False

    def _is_temporal(self, node: Node) -> bool:
        if isinstance(node, Literal):
            return node.kind in ("date", "datetime")
        if isinstance(node, ColumnRef):
            return node.data_type in ("date", "datetime")
        if isinstance(node, Func):
            return node.name in {"DATE", "DATETIME", "DATETRUNC", "DATEADD", "TODAY", "NOW", "MAKEDATE"}
        if isinstance(node, ParamRef):
            return isinstance(self.params.get(node.name), date)
        return False

    # -- dates -------------------------------------------------------------------
    def weekday(self, d: exp.Expression) -> exp.Expression:
        """1..7 where 1 is the configured first day of week."""

        ref = literal(date.fromisoformat(_WEEK_REFERENCE[self.week_start]))
        diff = exp.DateDiff(this=exp.cast(d, exp.DataType.Type.DATE), expression=ref, unit=exp.var("DAY"))
        seven = exp.Literal.number(7)
        return exp.Add(
            this=exp.Mod(this=exp.paren(exp.Add(this=exp.Mod(this=exp.paren(diff), expression=seven), expression=seven)),
                         expression=seven),
            expression=exp.Literal.number(1),
        )

    def datetrunc(self, part: str, d: exp.Expression, datetime_input: bool) -> exp.Expression:
        if part == "week":
            day_start = exp.cast(d, exp.DataType.Type.DATE)
            offset = exp.Neg(this=exp.paren(exp.Sub(this=self.weekday(d.copy()), expression=exp.Literal.number(1))))
            result: exp.Expression = exp.DateAdd(this=day_start, expression=offset, unit=exp.var("DAY"))
        else:
            result = exp.TimestampTrunc(this=d, unit=exp.var(_UNIT[part]))
        target = exp.DataType.Type.TIMESTAMP if datetime_input else exp.DataType.Type.DATE
        return exp.cast(result, target)

    def datepart(self, part: str, d: exp.Expression) -> exp.Expression:
        if part == "weekday":
            return self.weekday(d)
        if part == "week":
            # Week of year, week 1 contains January 1st (Tableau default).
            jan1 = exp.TimestampTrunc(this=d.copy(), unit=exp.var("YEAR"))
            doy = exp.Extract(this=exp.var("DOY"), expression=d)
            num = exp.Add(this=doy, expression=exp.Sub(this=self.weekday(jan1), expression=exp.Literal.number(2)))
            return exp.Add(
                this=exp.cast(exp.Floor(this=exp.Div(this=_double(num), expression=exp.Literal.number(7))),
                              exp.DataType.Type.BIGINT),
                expression=exp.Literal.number(1),
            )
        return exp.cast(exp.Extract(this=exp.var(_UNIT[part]), expression=d), exp.DataType.Type.BIGINT)

    # -- functions ----------------------------------------------------------------
    def _func(self, node: Func) -> exp.Expression:
        name = node.name
        if name in ("DATETRUNC", "DATEPART", "DATEADD", "DATEDIFF"):
            part = str(node.args[0].value).lower()  # type: ignore[attr-defined]
            rest = [self.compile(a) for a in node.args[1:]]
            if name == "DATETRUNC":
                return self.datetrunc(part, rest[0], self._datetime_arg(node.args[1]))
            if name == "DATEPART":
                return self.datepart(part, rest[0])
            if name == "DATEADD":
                unit, amount = _UNIT[part], rest[0]
                if part == "weekday" or part == "dayofyear":
                    unit = "DAY"
                if part == "quarter":
                    unit, amount = "MONTH", exp.Mul(this=exp.paren(amount), expression=exp.Literal.number(3))
                if part == "week":
                    unit, amount = "DAY", exp.Mul(this=exp.paren(amount), expression=exp.Literal.number(7))
                return self._keep_temporal_type(
                    exp.DateAdd(this=rest[1], expression=amount, unit=exp.var(unit)), node.args[2]
                )
            # DATEDIFF(part, start, end)
            start, end = rest
            if part == "week":
                return exp.cast(
                    exp.Div(
                        this=exp.DateDiff(this=self.datetrunc("week", end, False), expression=self.datetrunc("week", start, False),
                                          unit=exp.var("DAY")),
                        expression=exp.Literal.number(7),
                    ),
                    exp.DataType.Type.BIGINT,
                )
            unit = "DAY" if part in ("weekday", "dayofyear") else _UNIT[part]
            if self.dialect == "postgres" and part in ("year", "quarter", "month"):
                return self._pg_calendar_diff(part, start, end)
            return exp.DateDiff(this=end, expression=start, unit=exp.var(unit))

        args = [self.compile(a) for a in node.args]
        a0 = args[0] if args else None

        # Aggregates
        if name == "SUM":
            return exp.Sum(this=a0)
        if name == "AVG":
            return exp.Avg(this=_double(a0))
        if name in ("MIN", "MAX") and len(args) == 1:
            return (exp.Min if name == "MIN" else exp.Max)(this=a0)
        if name in ("MIN", "MAX"):
            a, b = args
            pick = exp.LT if name == "MIN" else exp.GT
            return exp.Case(
                ifs=[
                    exp.If(this=exp.Or(this=exp.Is(this=a.copy(), expression=exp.Null()),
                                       expression=exp.Is(this=b.copy(), expression=exp.Null())), true=exp.Null()),
                    exp.If(this=pick(this=a.copy(), expression=b.copy()), true=a.copy()),
                ],
                default=b.copy(),
            )
        if name == "COUNT":
            return exp.Count(this=a0)
        if name == "COUNTD":
            return exp.Count(this=exp.Distinct(expressions=[a0]))
        if name == "MEDIAN":
            if self.dialect == "tsql":
                raise CalcError("MEDIAN() is not supported on SQL Server", node.span, "unsupported_on_dialect")
            return exp.Median(this=a0)
        if name == "STDEV":
            return exp.StddevSamp(this=_double(a0))
        if name == "STDEVP":
            return exp.StddevPop(this=_double(a0))
        if name == "VAR":
            return exp.VarianceSamp(this=_double(a0)) if hasattr(exp, "VarianceSamp") else exp.Variance(this=_double(a0))
        if name == "VARP":
            return exp.VariancePop(this=_double(a0))
        if name == "ATTR":
            return exp.Case(ifs=[exp.If(this=exp.EQ(this=exp.Min(this=a0.copy()), expression=exp.Max(this=a0.copy())),
                                        true=exp.Min(this=a0.copy()))])

        # Number
        if name == "ABS":
            return exp.Abs(this=a0)
        if name == "ROUND":
            return exp.Round(this=_double(a0), decimals=args[1] if len(args) > 1 else exp.Literal.number(0))
        if name == "CEILING":
            return exp.cast(exp.Ceil(this=a0), exp.DataType.Type.BIGINT)
        if name == "FLOOR":
            return exp.cast(exp.Floor(this=a0), exp.DataType.Type.BIGINT)
        if name == "SQRT":
            return _null_if_outside(exp.LT(this=a0.copy(), expression=exp.Literal.number(0)), exp.Sqrt(this=_double(a0)))
        if name == "POWER":
            return exp.Pow(this=_double(a0), expression=args[1])
        if name == "EXP":
            return exp.Exp(this=_double(a0))
        if name == "LN":
            return _null_if_outside(exp.LTE(this=a0.copy(), expression=exp.Literal.number(0)), exp.Ln(this=_double(a0)))
        if name == "LOG":
            base = args[1] if len(args) > 1 else exp.Literal.number(10)
            value = exp.Div(this=exp.Ln(this=_double(a0)), expression=exp.Ln(this=_double(base)))
            return _null_if_outside(exp.LTE(this=a0.copy(), expression=exp.Literal.number(0)), value)
        if name == "SIGN":
            return exp.cast(exp.Sign(this=a0), exp.DataType.Type.BIGINT)
        if name == "DIV":
            return _trunc_to_int(exp.Div(this=_double(a0), expression=exp.Nullif(this=args[1], expression=exp.Literal.number(0))))
        if name == "ZN":
            return exp.Coalesce(this=a0, expressions=[exp.Literal.number(0)])
        if name == "PI":
            return exp.Anonymous(this="PI", expressions=[])

        # String
        if name == "LEN":
            return exp.Length(this=a0)
        if name == "LEFT":
            return exp.Substring(this=a0, start=exp.Literal.number(1), length=args[1])
        if name == "RIGHT":
            return exp.Right(this=a0, expression=args[1])
        if name == "MID":
            return exp.Substring(this=a0, start=args[1], length=args[2] if len(args) > 2 else exp.Length(this=a0.copy()))
        if name == "UPPER":
            return exp.Upper(this=a0)
        if name == "LOWER":
            return exp.Lower(this=a0)
        if name == "TRIM":
            return exp.Trim(this=a0)
        if name == "LTRIM":
            return exp.Trim(this=a0, position="LEADING")
        if name == "RTRIM":
            return exp.Trim(this=a0, position="TRAILING")
        if name == "CONTAINS":
            return exp.GT(this=exp.StrPosition(this=a0, substr=args[1]), expression=exp.Literal.number(0))
        if name == "STARTSWITH":
            return exp.EQ(this=exp.Substring(this=a0, start=exp.Literal.number(1), length=exp.Length(this=args[1].copy())),
                          expression=args[1])
        if name == "ENDSWITH":
            return exp.EQ(this=exp.Right(this=a0, expression=exp.Length(this=args[1].copy())), expression=args[1])
        if name == "REPLACE":
            return exp.Anonymous(this="REPLACE", expressions=args)
        if name == "FIND":
            return exp.StrPosition(this=a0, substr=args[1])

        # Type conversion
        if name == "STR":
            return exp.cast(a0, exp.DataType.Type.VARCHAR)
        if name == "INT":
            return _trunc_to_int(_double(a0))
        if name == "FLOAT":
            return _double(a0)
        if name == "DATE":
            return exp.cast(a0, exp.DataType.Type.DATE)
        if name == "DATETIME":
            return exp.cast(a0, exp.DataType.Type.TIMESTAMP)

        # Logical / null
        if name == "IIF":
            ifs = [exp.If(this=args[0], true=args[1]), exp.If(this=exp.Not(this=exp.paren(args[0].copy())), true=args[2])]
            return exp.Case(ifs=ifs, default=args[3] if len(args) > 3 else None)
        if name == "IFNULL":
            return exp.Coalesce(this=a0, expressions=[args[1]])
        if name == "ISNULL":
            return exp.Is(this=a0, expression=exp.Null())

        # Date
        if name in ("YEAR", "QUARTER", "MONTH", "DAY"):
            return self.datepart(name.lower(), a0)
        if name == "TODAY":
            return exp.CurrentDate()
        if name == "NOW":
            return exp.CurrentTimestamp()
        if name == "MAKEDATE":
            return exp.DateFromParts(year=args[0], month=args[1], day=args[2])

        raise CalcError(f"{name}() cannot be executed in the database", node.span, "not_sql_executable")

    def _keep_temporal_type(self, e: exp.Expression, source: Node) -> exp.Expression:
        """Date arithmetic returns a date for date input (engines often widen to timestamp)."""

        target = exp.DataType.Type.TIMESTAMP if self._datetime_arg(source) else exp.DataType.Type.DATE
        return exp.cast(e, target)

    def _datetime_arg(self, node: Node) -> bool:
        if isinstance(node, ColumnRef):
            return node.data_type == "datetime"
        if isinstance(node, Literal):
            return node.kind == "datetime"
        if isinstance(node, Func):
            return node.name in ("NOW", "DATETIME")
        return False

    def _pg_calendar_diff(self, part: str, start: exp.Expression, end: exp.Expression) -> exp.Expression:
        def ext(unit: str, e: exp.Expression) -> exp.Expression:
            return exp.Extract(this=exp.var(unit), expression=e.copy())

        years = exp.Sub(this=ext("YEAR", end), expression=ext("YEAR", start))
        if part == "year":
            return exp.cast(years, exp.DataType.Type.BIGINT)
        if part == "quarter":
            q = exp.Sub(this=ext("QUARTER", end), expression=ext("QUARTER", start))
            return exp.cast(exp.Add(this=exp.Mul(this=exp.paren(years), expression=exp.Literal.number(4)), expression=q),
                            exp.DataType.Type.BIGINT)
        m = exp.Sub(this=ext("MONTH", end), expression=ext("MONTH", start))
        return exp.cast(exp.Add(this=exp.Mul(this=exp.paren(years), expression=exp.Literal.number(12)), expression=m),
                        exp.DataType.Type.BIGINT)
