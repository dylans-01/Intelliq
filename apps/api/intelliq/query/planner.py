"""Query planner: validated QueryRequest + semantic model → SQL plan.

Pipeline
--------
1. Resolve every field use to an expression tree and type-check it.
2. Wrap measures in their aggregation and quick table calculation.
3. Split table calculations: aggregate sub-expressions become hidden SQL
   result columns; the remainder is evaluated on the result set.
4. Plan level-of-detail expressions as grouped subqueries joined back to the
   row level (FIXED ignores dimension filters; INCLUDE/EXCLUDE respect them).
5. Bind fields to physical columns, culling unused LEFT joins.
6. Apply filters in Tableau's order of operations, row-level security in
   every scope, and emit one parameterised SELECT.

Every user-supplied *value* becomes a bound parameter; every *identifier*
comes from the administrator-authored semantic model.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlglot import exp

from ..calc import ast as A
from ..calc.compile_sql import SqlCompiler, literal
from ..calc.errors import CalcError
from ..calc.parser import parse
from ..calc.table_calc import Addressing
from ..calc.typecheck import TypeChecker, Typed
from ..semantic.model import Dataset, SemanticField
from ..semantic.resolver import SemanticModel
from .models import ColumnMeta, FieldUse, Filter, QueryRequest, QuickTableCalc, Sort


class PlanError(Exception):
    def __init__(self, message: str, code: str = "invalid_query", detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or {}


@dataclass
class UserContext:
    user_id: str
    role: str
    attributes: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class TableCalcStep:
    column: str
    tree: A.Node
    addressing: Addressing


@dataclass
class PostFilter:
    column: str
    op: str
    values: list[Any]


@dataclass
class PlannedQuery:
    sql_template: str  # contains parameter tokens, see ``param_token``
    token_prefix: str
    params: dict[str, Any]
    dialect: str
    columns: list[ColumnMeta]
    hidden: list[str]
    table_calcs: list[TableCalcStep]
    post_filters: list[PostFilter]
    post_sort: list[Sort]
    user_limit: int | None
    row_cap: int
    parameters: dict[str, Any]


AGG_FN = {
    "sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT", "countd": "COUNTD",
    "median": "MEDIAN", "attr": "ATTR", "stdev": "STDEV", "var": "VAR",
}  # fmt: skip
AGG_LABEL = {
    "sum": "Sum of", "avg": "Avg.", "min": "Min.", "max": "Max.", "count": "Count of", "countd": "Count Distinct of",
    "median": "Median", "attr": "", "stdev": "Std. Dev. of", "var": "Variance of",
}  # fmt: skip
TABLE_CALC_LABEL = {
    "running_total": "Running Sum of", "percent_of_total": "% of Total", "difference": "Difference in",
    "percent_difference": "% Difference in", "rank": "Rank of", "rank_dense": "Dense Rank of", "index": "Index of",
}  # fmt: skip


def _family(t: str) -> str:
    return {"integer": "number", "real": "number", "date": "temporal", "datetime": "temporal"}.get(t, t)


def coerce_value(value: Any, data_type: str) -> Any:
    """Coerce a user-supplied filter/parameter value to the field's type."""

    if value is None:
        return None
    try:
        if data_type == "integer":
            if isinstance(value, bool):
                raise ValueError
            f = float(value)
            return int(f) if f.is_integer() else f
        if data_type == "real":
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if data_type == "boolean":
            if isinstance(value, bool):
                return value
            if str(value).lower() in ("true", "1"):
                return True
            if str(value).lower() in ("false", "0"):
                return False
            raise ValueError
        if data_type == "date":
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            return date.fromisoformat(str(value)[:10])
        if data_type == "datetime":
            if isinstance(value, datetime):
                return value
            if isinstance(value, date):
                return datetime(value.year, value.month, value.day)
            return datetime.fromisoformat(str(value))
        if data_type == "string":
            if not isinstance(value, str | int | float):
                raise ValueError
            s = str(value)
            if len(s) > 4000:
                raise ValueError
            return s
    except (ValueError, TypeError):
        raise PlanError(f"Value {value!r} is not a valid {data_type}", "invalid_value") from None
    return value


def relative_range(mode: str, unit: str, n: int, anchor: date) -> tuple[date, date]:
    """[start, end) date range for a relative date filter."""

    def period_start(d: date) -> date:
        if unit == "year":
            return date(d.year, 1, 1)
        if unit == "quarter":
            return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
        if unit == "month":
            return date(d.year, d.month, 1)
        if unit == "week":
            return d - timedelta(days=(d.weekday() + 1) % 7)  # Sunday start
        return d

    def shift(d: date, k: int) -> date:
        if unit in ("year", "quarter", "month"):
            months = {"year": 12, "quarter": 3, "month": 1}[unit] * k
            y, m = divmod(d.month - 1 + months, 12)
            return date(d.year + y, m + 1, 1)
        return d + timedelta(days=(7 if unit == "week" else 1) * k)

    start = period_start(anchor)
    if mode == "current":
        return start, shift(start, 1)
    if mode == "previous":
        return shift(start, -1), start
    if mode == "to_date":
        return start, anchor + timedelta(days=1)
    return shift(start, -(n - 1)), shift(start, 1)  # last_n, including the current period


class _Binder:
    """Collects bound values behind unguessable tokens; connectors replace the
    tokens with their driver's positional marker in order of appearance."""

    def __init__(self):
        self.prefix = "IQP" + secrets.token_hex(6).upper() + "X"
        self.values: dict[str, Any] = {}

    def bind(self, value: Any) -> exp.Expression:
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise PlanError("Non-finite numbers are not allowed", "invalid_value")
        token = f"{self.prefix}{len(self.values)}"
        self.values[token] = value
        return exp.var(token)


@dataclass
class _Lod:
    alias: str
    node: A.Lod
    data_type: str


class Planner:
    def __init__(
        self,
        dataset: Dataset,
        dialect: str,
        user: UserContext,
        *,
        allow_formulas: bool = True,
        today: date | None = None,
    ):
        self.dataset = dataset
        self.dialect = dialect
        self.user = user
        self.allow_formulas = allow_formulas
        self.today = today or date.today()
        self.binder = _Binder()
        self._lod_counter = 0

    # =====================================================================
    def plan(self, req: QueryRequest) -> PlannedQuery:
        if req.dataset_id != self.dataset.id:
            raise PlanError("Query dataset does not match planner dataset")
        param_types = {p.name: p.data_type for p in req.parameters}
        self.params = {p.name: coerce_value(p.value, p.data_type) for p in req.parameters}
        self.model = SemanticModel(self.dataset, param_types)
        self.checker = self.model.checker()
        self.compiler = SqlCompiler(self.dialect, week_start=self.dataset.settings.week_start, params=self.params)

        try:
            return self._plan(req)
        except CalcError as e:
            raise PlanError(e.message, e.code, e.to_dict()) from None

    def _plan(self, req: QueryRequest) -> PlannedQuery:
        columns: list[ColumnMeta] = []
        dim_trees: list[tuple[str, A.Node]] = []
        sql_measures: list[tuple[str, A.Node]] = []
        hidden: dict[A.Node, str] = {}
        table_calcs: list[TableCalcStep] = []
        output_trees: dict[str, A.Node] = {}

        # 1. Dimensions -------------------------------------------------------
        for i, fu in enumerate(req.dimensions):
            cid = f"d{i}"
            tree, typed, f = self._dimension_tree(fu)
            dim_trees.append((cid, tree))
            output_trees[cid] = tree
            columns.append(ColumnMeta(
                id=cid, label=fu.label or self._dim_label(fu, f), role="dimension",
                data_type=typed.data_type if typed.data_type != "null" else "string",
                field_id=f.id if f else None, format=fu.format or (f.format if f else None),
                time_unit=fu.time_unit, date_part=fu.date_part,
            ))

        # 2. Measures ----------------------------------------------------------
        for i, fu in enumerate(req.measures):
            cid = f"m{i}"
            tree, typed, f = self._measure_tree(fu)
            if typed.level == "table":
                partition = fu.table_calc.partition_by if fu.table_calc else []
                addressed = [t for cid_, t in dim_trees if cid_ not in partition]
                tree = self._rewrite_totals(tree, addressed)
                typed = self.checker.check(tree)
            is_table = typed.level == "table"
            if is_table:
                split = self._split_table_calc(tree, hidden)
                partition = fu.table_calc.partition_by if fu.table_calc else []
                table_calcs.append(TableCalcStep(cid, split, self._addressing(partition, dim_trees, req.sort)))
            else:
                sql_measures.append((cid, tree))
                output_trees[cid] = tree
            data_type = typed.data_type if typed.data_type != "null" else "real"
            if fu.table_calc and fu.table_calc.type in ("percent_of_total", "percent_difference"):
                data_type = "real"
            columns.append(ColumnMeta(
                id=cid, label=fu.label or self._measure_label(fu, f), role="measure", data_type=data_type,
                field_id=f.id if f else None, format=self._measure_format(fu, f), table_calc=is_table,
            ))
        hidden_cols = [(alias, tree) for tree, alias in hidden.items()]

        # 3. Filters ------------------------------------------------------------
        output_ids = {c.id for c in columns}
        table_calc_ids = {s.column for s in table_calcs}
        post_filters: list[PostFilter] = []
        scoped: dict[str, list[Filter]] = {"datasource": [], "context": [], "dimension": [], "measure": []}
        for flt in req.filters:
            if flt.stage == "table_calc":
                if flt.column not in output_ids:
                    raise PlanError(f"Filter references unknown column '{flt.column}'")
                post_filters.append(PostFilter(flt.column, flt.op, list(flt.values)))
            elif flt.stage == "measure" and flt.column in table_calc_ids:
                post_filters.append(PostFilter(flt.column, flt.op, list(flt.values)))
            else:
                scoped[flt.stage].append(flt)

        view_dims = [t for _, t in dim_trees]
        scope = _Scope(self, view_dims=view_dims, include_dimension_filters=True, filters=scoped, grouped=True)
        select_items: list[exp.Expression] = []
        group_by: list[exp.Expression] = []
        for cid, tree in dim_trees:
            e = scope.compile(tree)
            select_items.append(exp.alias_(e, cid, quoted=True))
            group_by.append(e.copy())
        measure_exprs: dict[str, exp.Expression] = {}
        for cid, tree in sql_measures + hidden_cols:
            e = scope.compile(tree)
            measure_exprs[cid] = e
            select_items.append(exp.alias_(e, cid, quoted=True))

        having: list[exp.Expression] = []
        for flt in scoped["measure"]:
            if flt.column is not None:
                if flt.column not in measure_exprs:
                    raise PlanError(f"Measure filter references unknown column '{flt.column}'")
                target_expr = measure_exprs[flt.column].copy()
                target_type = next(c.data_type for c in columns if c.id == flt.column)
            else:
                mtree, mtyped, _ = self._measure_tree(flt.target)  # type: ignore[arg-type]
                if mtyped.level == "table":
                    raise PlanError("Measure filters on table calculations must use the table_calc stage")
                target_expr = scope.compile(mtree)
                target_type = mtyped.data_type
            having.append(self._predicate(target_expr, flt, target_type))

        where = scope.where()
        query = exp.select(*select_items).from_(scope.from_clause())
        for j in scope.join_clauses():
            query = query.join(j)
        if where is not None:
            query = query.where(where)
        if group_by and (sql_measures or hidden_cols):
            query = query.group_by(*group_by)
        elif group_by:
            query = query.distinct()
        if having:
            query = query.having(exp.and_(*having))

        # 4. Sorting and limits ----------------------------------------------------
        sort = list(req.sort)
        for s in sort:
            if s.column not in output_ids:
                raise PlanError(f"Sort references unknown column '{s.column}'")
        sql_sort = [s for s in sort if s.column not in table_calc_ids]
        post_sort = sort if any(s.column in table_calc_ids for s in sort) else []
        order_cols = sql_sort or [Sort(column=cid) for cid, _ in dim_trees]
        if order_cols:
            query = query.order_by(*[
                exp.Ordered(this=exp.column(s.column, quoted=True), desc=s.direction == "desc", nulls_first=False)
                for s in order_cols
            ])

        max_rows = self.dataset.settings.max_rows
        if table_calcs or post_filters:
            sql_limit = max_rows + 1  # table calcs need every mark; user limit applied afterwards
        else:
            sql_limit = min(req.limit or max_rows, max_rows) + 1
        query = query.limit(sql_limit)

        return PlannedQuery(
            sql_template=query.sql(dialect=self.dialect),
            token_prefix=self.binder.prefix,
            params=self.binder.values,
            dialect=self.dialect,
            columns=columns,
            hidden=[alias for alias, _ in hidden_cols],
            table_calcs=table_calcs,
            post_filters=post_filters,
            post_sort=post_sort,
            user_limit=req.limit,
            row_cap=max_rows,
            parameters=self.params,
        )

    # =====================================================================
    # Field resolution
    def _base_tree(self, fu: FieldUse) -> tuple[A.Node, SemanticField | None]:
        if fu.field is not None:
            f = self.model.field(fu.field)
            return A.FieldRef(f.name), f
        if not self.allow_formulas:
            raise PlanError("Ad-hoc formulas are not permitted for this user", "forbidden")
        return parse(fu.formula or ""), None

    def _dimension_tree(self, fu: FieldUse) -> tuple[A.Node, Typed, SemanticField | None]:
        if fu.aggregation not in (None, "none") or fu.table_calc is not None:
            raise PlanError("Dimensions cannot be aggregated; place the field in measures instead")
        tree, f = self._base_tree(fu)
        typed = self.checker.check(tree)
        if fu.time_unit or fu.date_part:
            if _family(typed.data_type) != "temporal":
                raise PlanError("Date granularity can only be applied to date fields")
            if fu.time_unit:
                tree = A.Func("DATETRUNC", (A.Literal(fu.time_unit, "string"), tree))
            else:
                tree = A.Func("DATEPART", (A.Literal(fu.date_part, "string"), tree))
            typed = self.checker.check(tree)
        if typed.level not in ("row", "const"):
            raise PlanError(
                f"'{fu.label or (f.name if f else fu.formula)}' is an aggregate and cannot be used as a dimension",
                "aggregate_dimension",
            )
        return tree, typed, f

    def _measure_tree(self, fu: FieldUse) -> tuple[A.Node, Typed, SemanticField | None]:
        tree, f = self._base_tree(fu)
        typed = self.checker.check(tree)
        if typed.level in ("row", "const"):
            agg = fu.aggregation if fu.aggregation not in (None, "none") else (f.default_aggregation if f else None)
            if agg is None:
                agg = "sum" if _family(typed.data_type) == "number" else "count"
            if fu.time_unit and _family(typed.data_type) == "temporal":
                tree = A.Func("DATETRUNC", (A.Literal(fu.time_unit, "string"), tree))
            tree = A.Func(AGG_FN[agg], (tree,))
        elif fu.aggregation not in (None, "none"):
            raise PlanError(
                f"'{f.name if f else fu.formula}' is already aggregated; remove the aggregation", "nested_aggregation"
            )
        if fu.table_calc is not None:
            tree = _quick_table_calc(tree, fu.table_calc)
        typed = self.checker.check(tree)
        return tree, typed, f

    def _rewrite_totals(self, tree: A.Node, addressed_dims: list[A.Node]) -> A.Node:
        """Make TOTAL() exact for every aggregate.

        Additive or idempotent aggregates re-aggregate on the result set
        (SUM/COUNT → WINDOW_SUM, MIN/MAX → WINDOW_MIN/MAX). Others (AVG, COUNTD,
        MEDIAN, …) cannot be derived from view-level values, so they are computed
        in the database over the underlying rows at the partition's grain:
        TOTAL(AGG(x)) → MIN({EXCLUDE <addressed dimensions> : AGG(x)}).
        """

        window = {"SUM": "WINDOW_SUM", "COUNT": "WINDOW_SUM", "MIN": "WINDOW_MIN", "MAX": "WINDOW_MAX"}

        def visit(node: A.Node) -> A.Node:
            if isinstance(node, A.Func) and node.name == "TOTAL" and len(node.args) == 1:
                inner = node.args[0]
                if isinstance(inner, A.Func) and inner.name in window and len(inner.args) == 1:
                    return A.Func(window[inner.name], (inner,), node.span)
                if isinstance(inner, A.Func) and inner.name in AGG_FN.values():
                    excluded = tuple(self.model.expand(d) for d in addressed_dims)
                    return A.Func("MIN", (A.Lod(A.LodKind.EXCLUDE, excluded, inner, node.span),), node.span)
                return node  # e.g. TOTAL(SUM(x) / SUM(y)): sum of view values, as documented
            return A.map_children(node, visit)

        return visit(tree)

    def _split_table_calc(self, tree: A.Node, hidden: dict[A.Node, str]) -> A.Node:
        def split(node: A.Node) -> A.Node:
            level = self.checker.check(node).level
            if level == "agg":
                if node not in hidden:
                    hidden[node] = f"h{len(hidden)}"
                return A.ResultRef(hidden[node])
            if level == "const":
                return node
            return A.map_children(node, split)

        return split(tree)

    def _addressing(self, partition_by: list[str], dims: list[tuple[str, A.Node]], sort: list[Sort]) -> Addressing:
        ids = [cid for cid, _ in dims]
        for p in partition_by:
            if p not in ids:
                raise PlanError(f"Table calculation partitions by unknown dimension '{p}'")
        address = [cid for cid in ids if cid not in partition_by]
        return Addressing(partition_by=list(partition_by), order_by=[(cid, False) for cid in address])

    # =====================================================================
    # Labels and formats
    def _dim_label(self, fu: FieldUse, f: SemanticField | None) -> str:
        name = f.name if f else (fu.formula or "")
        unit = fu.time_unit or fu.date_part
        return f"{unit.capitalize()} of {name}" if unit else name

    def _measure_label(self, fu: FieldUse, f: SemanticField | None) -> str:
        name = f.name if f else (fu.formula or "Calculation")
        agg = fu.aggregation if fu.aggregation not in (None, "none") else None
        if agg and f and agg != (f.default_aggregation or "sum"):
            name = f"{AGG_LABEL[agg]} {name}".strip()
        elif agg and not f:
            name = f"{AGG_LABEL[agg]} {name}".strip()
        if fu.table_calc:
            return f"{TABLE_CALC_LABEL[fu.table_calc.type]} {name}"
        return name

    def _measure_format(self, fu: FieldUse, f: SemanticField | None):
        from ..semantic.model import Format

        if fu.format:
            return fu.format
        if fu.table_calc and fu.table_calc.type in ("percent_of_total", "percent_difference"):
            return Format(type="percent", decimals=1)
        if fu.table_calc and fu.table_calc.type in ("rank", "rank_dense", "index"):
            return Format(type="number", decimals=0)
        if fu.aggregation in ("count", "countd"):
            return Format(type="number", decimals=0)
        return f.format if f else None

    # =====================================================================
    # Predicates
    def _predicate(self, target: exp.Expression, flt: Filter, data_type: str) -> exp.Expression:
        op = flt.op
        b = self.binder.bind
        values = [coerce_value(v, data_type) for v in flt.values] if op not in ("relative_date",) else []

        if op in ("in", "not_in"):
            non_null = [v for v in values if v is not None]
            has_null = len(non_null) != len(values)
            parts: list[exp.Expression] = []
            if non_null:
                parts.append(exp.In(this=target.copy(), expressions=[b(v) for v in non_null]))
            if has_null:
                parts.append(exp.Is(this=target.copy(), expression=exp.Null()))
            if not parts:  # empty selection: "in" matches nothing, "not_in" everything
                return exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0 if op == "in" else 1))
            cond = exp.or_(*parts) if len(parts) > 1 else parts[0]
            if op == "not_in":
                # NOT IN keeps NULL rows unless NULL itself is excluded (Tableau semantics).
                negated = exp.Not(this=exp.paren(cond))
                if has_null:
                    return negated
                return exp.or_(negated, exp.Is(this=target.copy(), expression=exp.Null()))
            return cond
        cmp = {"eq": exp.EQ, "neq": exp.NEQ, "gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}
        if op in cmp:
            if len(values) != 1 or values[0] is None:
                raise PlanError(f"'{op}' filters need exactly one non-null value")
            return cmp[op](this=target, expression=b(values[0]))
        if op == "between":
            lo, hi = values
            parts = []
            if lo is not None:
                parts.append(exp.GTE(this=target.copy(), expression=b(lo)))
            if hi is not None:
                parts.append(exp.LTE(this=target.copy(), expression=b(hi)))
            if not parts:
                return exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(1))
            return exp.and_(*parts)
        if op in ("contains", "starts_with"):
            if len(values) != 1 or not isinstance(values[0], str):
                raise PlanError(f"'{op}' filters need one text value")
            needle = values[0].lower()
            lowered = exp.Lower(this=exp.cast(target, exp.DataType.Type.VARCHAR))
            if op == "contains":
                return exp.GT(this=exp.StrPosition(this=lowered, substr=b(needle)), expression=exp.Literal.number(0))
            return exp.EQ(
                this=exp.Substring(this=lowered, start=exp.Literal.number(1), length=exp.Literal.number(len(needle))),
                expression=b(needle),
            )
        if op == "is_null":
            return exp.Is(this=target, expression=exp.Null())
        if op == "not_null":
            return exp.Not(this=exp.Is(this=target, expression=exp.Null()))
        if op == "relative_date":
            if _family(data_type) != "temporal":
                raise PlanError("Relative date filters require a date field")
            rel = flt.relative
            start, end = relative_range(rel.mode, rel.unit, rel.n, rel.anchor or self.today)  # type: ignore[union-attr]
            lo: Any = start if data_type == "date" else datetime(start.year, start.month, start.day)
            hi: Any = end if data_type == "date" else datetime(end.year, end.month, end.day)
            return exp.and_(exp.GTE(this=target.copy(), expression=b(lo)), exp.LT(this=target.copy(), expression=b(hi)))
        raise PlanError(f"Unsupported filter operator '{op}'")


def _quick_table_calc(tree: A.Node, tc: QuickTableCalc) -> A.Node:
    if tc.type == "running_total":
        return A.Func("RUNNING_SUM", (tree,))
    if tc.type == "percent_of_total":
        return A.Binary("/", tree, A.Func("TOTAL", (tree,)))
    if tc.type == "difference":
        return A.Binary("-", tree, A.Func("LOOKUP", (tree, A.Literal(-1, "integer"))))
    if tc.type == "percent_difference":
        prev = A.Func("LOOKUP", (tree, A.Literal(-1, "integer")))
        return A.Binary("/", A.Binary("-", tree, prev), A.Func("ABS", (prev,)))
    if tc.type in ("rank", "rank_dense"):
        return A.Func("RANK" if tc.type == "rank" else "RANK_DENSE", (tree, A.Literal(tc.direction, "string")))
    if tc.type == "index":
        return A.Func("INDEX", ())
    raise PlanError(f"Unsupported table calculation '{tc.type}'")


# =========================================================================
class _Scope:
    """One SELECT scope: tracks used table aliases, planned LOD joins, and the
    filters that apply at this point in the order of operations."""

    def __init__(
        self,
        planner: Planner,
        *,
        view_dims: list[A.Node],
        include_dimension_filters: bool,
        filters: dict[str, list[Filter]],
        depth: int = 0,
        grouped: bool = False,
    ):
        if depth > 4:
            raise PlanError("Level of detail expressions are nested too deeply")
        self.p = planner
        # Expanded so INCLUDE/EXCLUDE dimensions (already expanded) compare equal.
        self.view_dims = [planner.model.expand(d) for d in view_dims]
        self.include_dimension_filters = include_dimension_filters
        self.filters = filters
        self.depth = depth
        # True for the view-level scope, whose GROUP BY is ``view_dims``.
        self.grouped = grouped
        self.aliases: set[str] = set()
        self.lods: list[tuple[_Lod, exp.Expression, list[exp.Expression]]] = []
        self._lod_cache: dict[A.Lod, _Lod] = {}
        self._lod_agg_cache: dict[A.Func, str] = {}

    # -- compile ------------------------------------------------------------
    def compile(self, tree: A.Node) -> exp.Expression:
        expanded = self.p.model.expand(tree)
        bound = self._bind(expanded)
        return self.p.compiler.compile(bound)

    def _bind(self, tree: A.Node) -> A.Node:
        def visit(node: A.Node) -> A.Node:
            if (
                self.grouped
                and isinstance(node, A.Func)
                and len(node.args) == 1
                and isinstance(node.args[0], A.Lod)
                and node.name in AGG_FN.values()
            ):
                return self._plan_lod_aggregate(node)
            if isinstance(node, A.Lod):
                return self._plan_lod(node)
            if isinstance(node, A.FieldRef):
                f = self.p.model.field(node.name)
                alias, column = f.column.split(".", 1)  # type: ignore[union-attr]
                self.aliases.add(alias)
                return A.ColumnRef(alias, column, f.data_type, node.span)
            return A.map_children(node, visit)

        return visit(tree)

    # -- LOD planning ----------------------------------------------------------
    def _plan_lod(self, node: A.Lod) -> A.Node:
        if node in self._lod_cache:
            lod = self._lod_cache[node]
            return A.ColumnRef(lod.alias, "v", lod.data_type)
        typed = TypeChecker(self.p.model.type_of_ref, self.p.model.param_type).check(node)
        self.p._lod_counter += 1
        lod = _Lod(f"lod{self.p._lod_counter}", node, typed.data_type if typed.data_type != "null" else "real")
        self._lod_cache[node] = lod

        if node.kind == A.LodKind.FIXED:
            grain = list(node.dimensions)
            include_dim_filters = False
        elif node.kind == A.LodKind.INCLUDE:
            grain = list(self.view_dims) + [d for d in node.dimensions if d not in self.view_dims]
            include_dim_filters = self.include_dimension_filters
        else:
            excluded = set(node.dimensions)
            grain = [d for d in self.view_dims if d not in excluded]
            include_dim_filters = self.include_dimension_filters

        sub = _Scope(self.p, view_dims=grain, include_dimension_filters=include_dim_filters,
                     filters=self.filters, depth=self.depth + 1)
        keys = [sub.compile(d) for d in grain]
        value = sub.compile(node.expr)
        select = exp.select(*[exp.alias_(k, f"k{i}", quoted=True) for i, k in enumerate(keys)],
                            exp.alias_(value, "v", quoted=True)).from_(sub.from_clause())
        for j in sub.join_clauses():
            select = select.join(j)
        where = sub.where()
        if where is not None:
            select = select.where(where)
        if keys:
            select = select.group_by(*[k.copy() for k in keys])

        # Join condition: the same grain expressions evaluated in this scope.
        outer_keys = [self.compile(d) for d in grain]
        self.lods.append((lod, select, outer_keys))
        return A.ColumnRef(lod.alias, "v", lod.data_type)

    def _plan_lod_aggregate(self, node: A.Func) -> A.Node:
        """``AGG({LOD})`` in the view: aggregate the LOD values over the distinct
        combinations of (view dimensions ∪ LOD grain), not over source rows.

        This reproduces Tableau's documented behaviour: an LOD coarser than the
        view is replicated once per mark, and an LOD finer than the view is
        aggregated at its own grain (e.g. AVG of per-customer totals)."""

        if node in self._lod_agg_cache:
            alias = self._lod_agg_cache[node]
        else:
            lod: A.Lod = node.args[0]  # type: ignore[assignment]
            self.p._lod_counter += 1
            alias = f"lagg{self.p._lod_counter}"
            self._lod_agg_cache[node] = alias

            sub = _Scope(self.p, view_dims=self.view_dims, include_dimension_filters=self.include_dimension_filters,
                         filters=self.filters, depth=self.depth + 1)
            view_keys = [sub.compile(d) for d in self.view_dims]
            extra = lod.dimensions if lod.kind != A.LodKind.EXCLUDE else ()
            lod_keys = [sub.compile(d) for d in extra if d not in self.view_dims]
            value = sub.compile(lod)
            domain = exp.select(
                *[exp.alias_(k, f"k{i}", quoted=True) for i, k in enumerate(view_keys)],
                *[exp.alias_(k, f"l{i}", quoted=True) for i, k in enumerate(lod_keys)],
                exp.alias_(value, "v", quoted=True),
            ).distinct().from_(sub.from_clause())
            for j in sub.join_clauses():
                domain = domain.join(j)
            where = sub.where()
            if where is not None:
                domain = domain.where(where)
            agg_value = self.p.compiler.compile(A.Func(node.name, (A.ColumnRef("x", "v", "real"),)))
            outer = exp.select(
                *[exp.column(f"k{i}", table="x", quoted=True) for i in range(len(view_keys))],
                exp.alias_(agg_value, "v", quoted=True),
            ).from_(exp.Subquery(this=domain, alias=exp.TableAlias(this=exp.to_identifier("x", quoted=True))))
            if view_keys:
                outer = outer.group_by(*[exp.column(f"k{i}", table="x", quoted=True) for i in range(len(view_keys))])
            outer_keys = [self.compile(d) for d in self.view_dims]
            self.lods.append((_Lod(alias, lod, "real"), outer, outer_keys))
        # Constant within each mark, so any aggregate recovers it in the grouped query.
        return A.Func("MIN", (A.ColumnRef(alias, "v", "real"),))

    # -- FROM / JOIN --------------------------------------------------------
    def _needed_aliases(self) -> list[str]:
        ds = self.p.dataset
        needed = set(self.aliases) | {ds.base_alias}
        needed |= {j.alias for j in ds.joins if j.type == "inner"}  # inner joins change row sets; never cull
        changed = True
        while changed:
            changed = False
            for j in ds.joins:
                if j.alias in needed:
                    for key in j.on:
                        for side in (key.left, key.right):
                            a = side.split(".", 1)[0]
                            if a not in needed:
                                needed.add(a)
                                changed = True
        return [ds.base_alias] + [j.alias for j in ds.joins if j.alias in needed]

    def _table(self, alias: str) -> exp.Table:
        t = self.p.dataset.tables_by_alias()[alias]
        return exp.table_(t.name, db=t.schema_, catalog=t.database, alias=alias, quoted=True)

    def from_clause(self) -> exp.Table:
        # Make sure filters and RLS have bound their aliases before FROM is rendered.
        self._predicates()
        return self._table(self.p.dataset.base_alias)

    def join_clauses(self) -> list[exp.Join]:
        self._predicates()
        ds = self.p.dataset
        joins: list[exp.Join] = []
        needed = self._needed_aliases()
        for j in ds.joins:
            if j.alias not in needed:
                continue
            on = exp.and_(*[
                exp.EQ(this=_qcol(k.left), expression=_qcol(k.right)) for k in j.on
            ])
            joins.append(exp.Join(this=self._table(j.alias), on=on, side="LEFT" if j.type == "left" else None,
                                  kind="INNER" if j.type == "inner" else None))
        for lod, select, outer_keys in self.lods:
            sub = exp.Subquery(this=select, alias=exp.TableAlias(this=exp.to_identifier(lod.alias, quoted=True)))
            if outer_keys:
                conds = [_null_safe_eq(k, exp.column(f"k{i}", table=lod.alias, quoted=True))
                         for i, k in enumerate(outer_keys)]
                joins.append(exp.Join(this=sub, on=exp.and_(*conds), side="LEFT"))
            else:
                joins.append(exp.Join(this=sub, kind="CROSS"))
        return joins

    # -- WHERE ------------------------------------------------------------
    def _predicates(self) -> list[exp.Expression]:
        if hasattr(self, "_preds"):
            return self._preds  # type: ignore[has-type]
        preds: list[exp.Expression] = list(self._rls())
        stages = ["datasource", "context"] + (["dimension"] if self.include_dimension_filters else [])
        for stage in stages:
            for flt in self.filters.get(stage, []):
                preds.append(self._filter(flt))
        self._preds = preds
        return preds

    def where(self) -> exp.Expression | None:
        preds = self._predicates()
        return exp.and_(*preds) if preds else None

    def _filter(self, flt: Filter) -> exp.Expression:
        fu = flt.target
        assert fu is not None
        if flt.op == "top_n":
            return self._top_n(flt)
        tree, typed, _ = self.p._dimension_tree(fu) if not fu.table_calc else (None, None, None)
        if tree is None or typed is None:
            raise PlanError("Table calculations can only be filtered in the table_calc stage")
        return self.p._predicate(self.compile(tree), flt, typed.data_type)

    def _top_n(self, flt: Filter) -> exp.Expression:
        top = flt.top_n
        assert top is not None and flt.target is not None
        dim_tree, _, _ = self.p._dimension_tree(flt.target)
        by_tree, by_typed, _ = self.p._measure_tree(top.by)
        if by_typed.level != "agg":
            raise PlanError("Top N must rank by an aggregate measure")
        # Top N is computed after context filters only (Tableau order of operations).
        sub = _Scope(self.p, view_dims=[dim_tree], include_dimension_filters=False,
                     filters={"datasource": self.filters.get("datasource", []),
                              "context": self.filters.get("context", [])},
                     depth=self.depth + 1)
        key = sub.compile(dim_tree)
        rank_by = sub.compile(by_tree)
        select = exp.select(key).from_(sub.from_clause())
        for j in sub.join_clauses():
            select = select.join(j)
        where = sub.where()
        if where is not None:
            select = select.where(where)
        select = (
            select.group_by(key.copy())
            .order_by(exp.Ordered(this=rank_by, desc=top.direction == "top", nulls_first=False),
                      exp.Ordered(this=key.copy()))
            .limit(top.n)
        )
        # Wrap so engines that disallow LIMIT inside IN (...) still accept it.
        wrapped = exp.select(exp.column("k", table="topn", quoted=True)).from_(
            exp.Subquery(this=select.select(exp.alias_(key.copy(), "k", quoted=True), append=False),
                         alias=exp.TableAlias(this=exp.to_identifier("topn", quoted=True)))
        )
        return exp.In(this=self.compile(dim_tree), query=exp.Subquery(this=wrapped))

    def _rls(self) -> list[exp.Expression]:
        out = []
        for rule in self.p.dataset.row_level_security:
            if self.p.user.role in rule.exempt_roles:
                continue
            f = self.p.model.field(rule.field)
            values = self.p.user.attributes.get(rule.user_attribute) or []
            if not values:
                out.append(exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0)))
                continue
            target = self.compile(A.FieldRef(f.name))
            out.append(exp.In(this=target, expressions=[self.p.binder.bind(coerce_value(v, f.data_type)) for v in values]))
        return out


def _qcol(qualified: str) -> exp.Column:
    alias, column = qualified.split(".", 1)
    return exp.column(column, table=alias, quoted=True)


def _null_safe_eq(a: exp.Expression, b: exp.Expression) -> exp.Expression:
    return exp.or_(
        exp.EQ(this=a, expression=b),
        exp.and_(exp.Is(this=a.copy(), expression=exp.Null()), exp.Is(this=b.copy(), expression=exp.Null())),
    )


__all__ = ["Planner", "PlannedQuery", "PlanError", "UserContext", "coerce_value", "relative_range", "literal"]
