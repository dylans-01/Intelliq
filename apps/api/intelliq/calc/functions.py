"""Catalogue of supported calculation functions.

Each function declares its kind, arity and return-type rule. SQL compilation
lives in ``compile_sql.py`` and result-stage evaluation in ``table_calc.py``;
this module is the single source of truth for *what* is supported and is
also used to generate the published compatibility matrix.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

NUMERIC = ("integer", "real")
TEMPORAL = ("date", "datetime")

DATE_PARTS = ("year", "quarter", "month", "week", "day", "weekday", "dayofyear", "hour", "minute", "second")
TRUNC_PARTS = ("year", "quarter", "month", "week", "day", "hour", "minute", "second")


def _num(args: list[str]) -> str:
    return "real" if "real" in args else ("integer" if all(a in ("integer", "null") for a in args) else "real")


def _same_as(i: int) -> Callable[[list[str]], str]:
    return lambda args: args[i] if i < len(args) else "null"


def _const(t: str) -> Callable[[list[str]], str]:
    return lambda _args: t


def _first_non_null(args: list[str]) -> str:
    """Common type of alternative branches: numbers widen to real."""

    concrete = [a for a in args if a != "null"]
    if concrete and all(a in NUMERIC for a in concrete):
        return "real" if "real" in concrete else "integer"
    return concrete[0] if concrete else "null"


@dataclass(frozen=True)
class FunctionDef:
    name: str
    kind: str  # "scalar" | "aggregate" | "table"
    min_args: int
    max_args: int | None
    returns: Callable[[list[str]], str]
    category: str
    description: str
    notes: str = ""  # documented semantic differences vs. Tableau

    def check_arity(self, n: int) -> bool:
        return n >= self.min_args and (self.max_args is None or n <= self.max_args)


_DEFS: list[FunctionDef] = [
    # ---- Aggregates ---------------------------------------------------------
    FunctionDef("SUM", "aggregate", 1, 1, lambda a: _num(a), "Aggregate", "Sum of values; NULLs ignored."),
    FunctionDef("AVG", "aggregate", 1, 1, _const("real"), "Aggregate", "Arithmetic mean; NULLs ignored."),
    FunctionDef("MIN", "aggregate", 1, 2, _same_as(0), "Aggregate",
                "Minimum. With two arguments acts as a row-level LEAST().",
                "Two-argument form returns NULL if either argument is NULL, matching Tableau."),
    FunctionDef("MAX", "aggregate", 1, 2, _same_as(0), "Aggregate",
                "Maximum. With two arguments acts as a row-level GREATEST().",
                "Two-argument form returns NULL if either argument is NULL, matching Tableau."),
    FunctionDef("COUNT", "aggregate", 1, 1, _const("integer"), "Aggregate", "Count of non-NULL values."),
    FunctionDef("COUNTD", "aggregate", 1, 1, _const("integer"), "Aggregate", "Count of distinct non-NULL values."),
    FunctionDef("MEDIAN", "aggregate", 1, 1, _const("real"), "Aggregate", "Median (continuous).",
                "SQL Server lacks an aggregate MEDIAN; not supported on that dialect."),
    FunctionDef("STDEV", "aggregate", 1, 1, _const("real"), "Aggregate", "Sample standard deviation."),
    FunctionDef("STDEVP", "aggregate", 1, 1, _const("real"), "Aggregate", "Population standard deviation."),
    FunctionDef("VAR", "aggregate", 1, 1, _const("real"), "Aggregate", "Sample variance."),
    FunctionDef("VARP", "aggregate", 1, 1, _const("real"), "Aggregate", "Population variance."),
    FunctionDef("ATTR", "aggregate", 1, 1, _same_as(0), "Aggregate",
                "Value if all rows share it, otherwise NULL.",
                "Tableau displays '*' for mixed values; Intelliq returns NULL and the renderer shows '*'."),
    # ---- Number -----------------------------------------------------------
    FunctionDef("ABS", "scalar", 1, 1, _same_as(0), "Number", "Absolute value."),
    FunctionDef("ROUND", "scalar", 1, 2, _const("real"), "Number", "Round to N decimal places (default 0).",
                "Rounding of exact .5 ties follows the database (half away from zero on all targets tested)."),
    FunctionDef("CEILING", "scalar", 1, 1, _const("integer"), "Number", "Smallest integer >= value."),
    FunctionDef("FLOOR", "scalar", 1, 1, _const("integer"), "Number", "Largest integer <= value."),
    FunctionDef("SQRT", "scalar", 1, 1, _const("real"), "Number", "Square root; NULL for negatives."),
    FunctionDef("POWER", "scalar", 2, 2, _const("real"), "Number", "Exponentiation."),
    FunctionDef("EXP", "scalar", 1, 1, _const("real"), "Number", "e raised to the value."),
    FunctionDef("LN", "scalar", 1, 1, _const("real"), "Number", "Natural logarithm; NULL for <= 0."),
    FunctionDef("LOG", "scalar", 1, 2, _const("real"), "Number", "Logarithm (base 10 by default)."),
    FunctionDef("SIGN", "scalar", 1, 1, _const("integer"), "Number", "-1, 0 or 1."),
    FunctionDef("DIV", "scalar", 2, 2, _const("integer"), "Number", "Integer division; NULL on divide by zero."),
    FunctionDef("ZN", "scalar", 1, 1, _same_as(0), "Number", "Replace NULL with zero."),
    FunctionDef("PI", "scalar", 0, 0, _const("real"), "Number", "The constant pi."),
    # ---- String ---------------------------------------------------------------
    FunctionDef("LEN", "scalar", 1, 1, _const("integer"), "String", "Character length."),
    FunctionDef("LEFT", "scalar", 2, 2, _const("string"), "String", "Leftmost N characters."),
    FunctionDef("RIGHT", "scalar", 2, 2, _const("string"), "String", "Rightmost N characters."),
    FunctionDef("MID", "scalar", 2, 3, _const("string"), "String", "Substring from 1-based position."),
    FunctionDef("UPPER", "scalar", 1, 1, _const("string"), "String", "Upper case."),
    FunctionDef("LOWER", "scalar", 1, 1, _const("string"), "String", "Lower case."),
    FunctionDef("TRIM", "scalar", 1, 1, _const("string"), "String", "Strip leading and trailing spaces."),
    FunctionDef("LTRIM", "scalar", 1, 1, _const("string"), "String", "Strip leading spaces."),
    FunctionDef("RTRIM", "scalar", 1, 1, _const("string"), "String", "Strip trailing spaces."),
    FunctionDef("CONTAINS", "scalar", 2, 2, _const("boolean"), "String", "True if substring occurs."),
    FunctionDef("STARTSWITH", "scalar", 2, 2, _const("boolean"), "String", "True if string starts with prefix."),
    FunctionDef("ENDSWITH", "scalar", 2, 2, _const("boolean"), "String", "True if string ends with suffix."),
    FunctionDef("REPLACE", "scalar", 3, 3, _const("string"), "String", "Replace all occurrences."),
    FunctionDef("FIND", "scalar", 2, 2, _const("integer"), "String", "1-based position of substring, 0 if absent."),
    # ---- Type conversion --------------------------------------------------------
    FunctionDef("STR", "scalar", 1, 1, _const("string"), "Type conversion", "Convert to string.",
                "Number-to-string formatting follows the database's default text rendering."),
    FunctionDef("INT", "scalar", 1, 1, _const("integer"), "Type conversion", "Convert to integer (truncates toward zero)."),
    FunctionDef("FLOAT", "scalar", 1, 1, _const("real"), "Type conversion", "Convert to floating point."),
    FunctionDef("DATE", "scalar", 1, 1, _const("date"), "Type conversion", "Convert to date."),
    FunctionDef("DATETIME", "scalar", 1, 1, _const("datetime"), "Type conversion", "Convert to datetime."),
    # ---- Logical / null -----------------------------------------------------------
    FunctionDef("IIF", "scalar", 3, 4, lambda a: _first_non_null(a[1:3]), "Logical",
                "IIF(test, then, else[, unknown])."),
    FunctionDef("IFNULL", "scalar", 2, 2, _first_non_null, "Logical", "First argument unless NULL."),
    FunctionDef("ISNULL", "scalar", 1, 1, _const("boolean"), "Logical", "True if NULL."),
    # ---- Date ---------------------------------------------------------------------
    FunctionDef("DATETRUNC", "scalar", 2, 2, lambda a: "datetime" if a[1] == "datetime" else "date", "Date",
                "Truncate a date to the given part.",
                "Weeks start on the dataset's configured week start (default Sunday, as Tableau's US locale)."),
    FunctionDef("DATEPART", "scalar", 2, 2, _const("integer"), "Date", "Integer part of a date.",
                "'weekday' returns 1=Sunday..7=Saturday as Tableau does."),
    FunctionDef("DATEADD", "scalar", 3, 3, lambda a: a[2], "Date", "Add an interval to a date."),
    FunctionDef("DATEDIFF", "scalar", 3, 3, _const("integer"), "Date", "Number of part boundaries crossed.",
                "Boundary-crossing semantics are exact on DuckDB, SQL Server, Snowflake and Databricks; "
                "PostgreSQL output for month/quarter/year is computed from calendar fields."),
    FunctionDef("YEAR", "scalar", 1, 1, _const("integer"), "Date", "Year of a date."),
    FunctionDef("QUARTER", "scalar", 1, 1, _const("integer"), "Date", "Quarter of a date."),
    FunctionDef("MONTH", "scalar", 1, 1, _const("integer"), "Date", "Month of a date."),
    FunctionDef("DAY", "scalar", 1, 1, _const("integer"), "Date", "Day of month."),
    FunctionDef("TODAY", "scalar", 0, 0, _const("date"), "Date", "Current date (server time zone)."),
    FunctionDef("NOW", "scalar", 0, 0, _const("datetime"), "Date", "Current timestamp (server time zone)."),
    FunctionDef("MAKEDATE", "scalar", 3, 3, _const("date"), "Date", "Date from year, month, day."),
    # ---- Table calculations (evaluated on the result set) ---------------------------
    FunctionDef("RUNNING_SUM", "table", 1, 1, lambda a: _num(a), "Table calculation", "Cumulative sum along the partition."),
    FunctionDef("RUNNING_AVG", "table", 1, 1, _const("real"), "Table calculation", "Cumulative average."),
    FunctionDef("RUNNING_MIN", "table", 1, 1, _same_as(0), "Table calculation", "Cumulative minimum."),
    FunctionDef("RUNNING_MAX", "table", 1, 1, _same_as(0), "Table calculation", "Cumulative maximum."),
    FunctionDef("RUNNING_COUNT", "table", 1, 1, _const("integer"), "Table calculation", "Cumulative count of non-NULL."),
    FunctionDef("WINDOW_SUM", "table", 1, 1, lambda a: _num(a), "Table calculation", "Sum over the whole partition.",
                "Optional start/end offsets are not yet supported."),
    FunctionDef("WINDOW_AVG", "table", 1, 1, _const("real"), "Table calculation", "Average over the partition.",
                "Optional start/end offsets are not yet supported."),
    FunctionDef("WINDOW_MIN", "table", 1, 1, _same_as(0), "Table calculation", "Minimum over the partition."),
    FunctionDef("WINDOW_MAX", "table", 1, 1, _same_as(0), "Table calculation", "Maximum over the partition."),
    FunctionDef("WINDOW_COUNT", "table", 1, 1, _const("integer"), "Table calculation", "Count over the partition."),
    FunctionDef("TOTAL", "table", 1, 1, lambda a: _num(a), "Table calculation",
                "Total of the expression over the partition.",
                "Exact for every aggregate: SUM/COUNT/MIN/MAX re-aggregate the view; AVG, COUNTD, MEDIAN… "
                "are computed in the database at the partition grain. Interaction with measure filters: validation pending."),
    FunctionDef("INDEX", "table", 0, 0, _const("integer"), "Table calculation", "1-based row index in the partition."),
    FunctionDef("FIRST", "table", 0, 0, _const("integer"), "Table calculation", "Offset to the first row (<= 0)."),
    FunctionDef("LAST", "table", 0, 0, _const("integer"), "Table calculation", "Offset to the last row (>= 0)."),
    FunctionDef("SIZE", "table", 0, 0, _const("integer"), "Table calculation", "Number of rows in the partition."),
    FunctionDef("RANK", "table", 1, 2, _const("integer"), "Table calculation", "Competition rank (1,2,2,4). Default 'desc'."),
    FunctionDef("RANK_DENSE", "table", 1, 2, _const("integer"), "Table calculation", "Dense rank (1,2,2,3)."),
    FunctionDef("RANK_UNIQUE", "table", 1, 2, _const("integer"), "Table calculation", "Unique rank (1,2,3,4)."),
    FunctionDef("RANK_PERCENTILE", "table", 1, 2, _const("real"), "Table calculation",
                "Percentile rank 0..1. Default 'asc'."),
    FunctionDef("LOOKUP", "table", 2, 2, _same_as(0), "Table calculation", "Value at a relative offset."),
]

FUNCTIONS: dict[str, FunctionDef] = {f.name: f for f in _DEFS}


def get(name: str) -> FunctionDef | None:
    return FUNCTIONS.get(name.upper())
