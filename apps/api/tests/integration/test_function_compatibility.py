"""Executable compatibility matrix: every catalogued function is evaluated on
DuckDB and compared with the value Tableau's documented semantics give.

The same cases generate the support matrix in docs/05-calculation-compatibility.md
(see scripts/gen_compat_matrix.py). A function without a case here fails
``test_every_function_has_a_case``.
"""

from __future__ import annotations

from datetime import date, datetime

import duckdb
import pytest
from sqlglot import exp

from intelliq.calc import ast as A
from intelliq.calc import functions as F
from intelliq.calc.compile_sql import SqlCompiler
from intelliq.calc.parser import parse
from intelliq.calc.table_calc import Addressing, TableCalcEvaluator

# One fixture row; columns referenced as fields.
COLUMNS = {
    "N": ("n", "real", 7.5), "I": ("i", "integer", 7), "Neg": ("neg", "real", -2.7), "Zero": ("zero", "integer", 0),
    "S": ("s", "string", "  Tableau  "), "T": ("t", "string", "Superstore"), "Null": ("nul", "real", None),
    "D": ("d", "date", date(2024, 3, 6)), "D2": ("d2", "date", date(2024, 5, 1)),
    "DT": ("dt", "datetime", datetime(2024, 3, 6, 14, 30, 0)),
}

ROW_CASES: list[tuple[str, object]] = [
    ("ABS([Neg])", 2.7), ("ROUND([N])", 8.0), ("ROUND(2.345, 2)", 2.35), ("CEILING([N])", 8), ("FLOOR([N])", 7),
    ("SQRT(16)", 4.0), ("SQRT(-1)", None), ("POWER(2, 10)", 1024.0), ("EXP(0)", 1.0), ("LN(1)", 0.0), ("LN(0)", None),
    ("LOG(1000)", 3.0), ("LOG(8, 2)", 3.0), ("SIGN([Neg])", -1), ("DIV(7, 2)", 3), ("DIV(-7, 2)", -3), ("DIV(1, 0)", None),
    ("ZN([Null])", 0), ("ROUND(PI(), 4)", 3.1416),
    ("LEN([T])", 10), ("LEFT([T], 5)", "Super"), ("RIGHT([T], 5)", "store"), ("MID([T], 6)", "store"),
    ("MID([T], 2, 3)", "upe"), ("UPPER([T])", "SUPERSTORE"), ("LOWER([T])", "superstore"), ("TRIM([S])", "Tableau"),
    ("LTRIM([S])", "Tableau  "), ("RTRIM([S])", "  Tableau"), ("CONTAINS([T], 'store')", True),
    ("STARTSWITH([T], 'Super')", True), ("ENDSWITH([T], 'xyz')", False), ("REPLACE([T], 'store', 'market')", "Supermarket"),
    ("FIND([T], 'store')", 6), ("FIND([T], 'zzz')", 0),
    ("STR([I])", "7"), ("INT([Neg])", -2), ("INT('42')", 42), ("FLOAT([I]) / 2", 3.5), ("DATE('2024-01-31')", date(2024, 1, 31)),
    ("DATETIME('2024-01-31 08:00:00')", datetime(2024, 1, 31, 8, 0)),
    ("IIF([I] > 5, 'big', 'small')", "big"), ("IIF([Null] > 5, 'big', 'small')", None),
    ("IIF([Null] > 5, 'big', 'small', 'unknown')", "unknown"), ("IFNULL([Null], 1)", 1.0), ("ISNULL([Null])", True),
    ("MIN([I], 3)", 3), ("MAX([I], 3)", 7), ("MIN([I], [Null])", None),
    ("DATETRUNC('month', [D])", date(2024, 3, 1)), ("DATETRUNC('quarter', [D])", date(2024, 1, 1)),
    ("DATETRUNC('week', [D])", date(2024, 3, 3)), ("DATETRUNC('year', [D])", date(2024, 1, 1)),
    ("DATETRUNC('hour', [DT])", datetime(2024, 3, 6, 14, 0)),
    ("DATEPART('year', [D])", 2024), ("DATEPART('quarter', [D])", 1), ("DATEPART('month', [D])", 3),
    ("DATEPART('day', [D])", 6), ("DATEPART('weekday', [D])", 4), ("DATEPART('week', [D])", 10),
    ("DATEPART('dayofyear', [D])", 66), ("DATEPART('hour', [DT])", 14),
    ("DATEADD('month', 1, [D])", date(2024, 4, 6)), ("DATEADD('quarter', 1, [D])", date(2024, 6, 6)),
    ("DATEADD('week', -1, [D])", date(2024, 2, 28)), ("DATEADD('day', 30, [D])", date(2024, 4, 5)),
    ("DATEDIFF('day', [D], [D2])", 56), ("DATEDIFF('month', [D], [D2])", 2), ("DATEDIFF('year', #2023-12-31#, #2024-01-01#)", 1),
    ("DATEDIFF('week', [D], [D2])", 8), ("YEAR([D])", 2024), ("QUARTER([D])", 1), ("MONTH([D])", 3), ("DAY([D])", 6),
    ("MAKEDATE(2024, 2, 29)", date(2024, 2, 29)), ("[D] + 1", date(2024, 3, 7)), ("[D2] - [D]", 56),
    ("TODAY() <= TODAY()", True), ("NOW() <= NOW()", True),
    # Operators
    ("[I] / 2", 3.5), ("[I] / [Zero]", None), ("[I] % 4", 3), ("2 ^ 3", 8.0), ("[T] + '!'", "Superstore!"),
    ("[Null] + 1", None), ("NOT ([I] > 5)", False), ("[I] IN (1, 7)", True),
    ("CASE [I] WHEN 7 THEN 'seven' ELSE 'other' END", "seven"),
    ("IF [Null] > 0 THEN 'pos' ELSE 'else' END", "else"),
]

AGG_ROWS = [3.0, 1.0, None, 5.0, 3.0]
AGG_CASES: list[tuple[str, object]] = [
    ("SUM([V])", 12.0), ("AVG([V])", 3.0), ("MIN([V])", 1.0), ("MAX([V])", 5.0), ("COUNT([V])", 4), ("COUNTD([V])", 3),
    ("MEDIAN([V])", 3.0), ("ROUND(STDEV([V]), 6)", 1.632993), ("ROUND(STDEVP([V]), 6)", 1.414214),
    ("ROUND(VAR([V]), 6)", 2.666667), ("VARP([V])", 2.0), ("ATTR([V])", None), ("ATTR(1)", 1),
]

# A NULL mark carries the running value forward (running aggregate of the
# non-NULL values so far). Marked "validation pending" in the support matrix.
TABLE_CASES: list[tuple[str, list]] = [
    ("RUNNING_SUM(SUM([V]))", [3, 4, 4, 9, 12]),
    ("RUNNING_AVG(SUM([V]))", [3, 2, 2, 3, 3]),
    ("RUNNING_MIN(SUM([V]))", [3, 1, 1, 1, 1]),
    ("RUNNING_MAX(SUM([V]))", [3, 3, 3, 5, 5]),
    ("RUNNING_COUNT(SUM([V]))", [1, 2, 2, 3, 4]),
    ("WINDOW_SUM(SUM([V]))", [12] * 5), ("WINDOW_AVG(SUM([V]))", [3] * 5), ("WINDOW_MIN(SUM([V]))", [1] * 5),
    ("WINDOW_MAX(SUM([V]))", [5] * 5), ("WINDOW_COUNT(SUM([V]))", [4] * 5), ("TOTAL(SUM([V]))", [12] * 5),
    ("INDEX()", [1, 2, 3, 4, 5]), ("FIRST()", [0, -1, -2, -3, -4]), ("LAST()", [4, 3, 2, 1, 0]), ("SIZE()", [5] * 5),
    ("RANK(SUM([V]))", [2, 4, None, 1, 2]), ("RANK_DENSE(SUM([V]))", [2, 3, None, 1, 2]),
    ("RANK_UNIQUE(SUM([V]))", [2, 4, None, 1, 3]), ("RANK_PERCENTILE(SUM([V]))", [1 / 3, 0, None, 1, 1 / 3]),
    ("LOOKUP(SUM([V]), -1)", [None, 3, 1, None, 5]),
]


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    cols = ", ".join(f"{phys} {'DOUBLE' if t == 'real' else {'integer': 'INTEGER', 'string': 'VARCHAR', 'date': 'DATE', 'datetime': 'TIMESTAMP'}[t]}"
                     for phys, t, _ in COLUMNS.values())
    c.execute(f"CREATE TABLE t ({cols})")
    c.execute(f"INSERT INTO t VALUES ({', '.join('?' * len(COLUMNS))})", [v for _, _, v in COLUMNS.values()])
    c.execute("CREATE TABLE a (v DOUBLE)")
    c.executemany("INSERT INTO a VALUES (?)", [[v] for v in AGG_ROWS])
    yield c
    c.close()


def _bind(formula: str, columns: dict) -> A.Node:
    def rewrite(n):
        if isinstance(n, A.FieldRef):
            phys, dtype, _ = columns[n.name]
            return A.ColumnRef("x", phys, dtype)
        return n

    return A.transform(parse(formula), rewrite)


def _approx(v):
    return pytest.approx(v) if isinstance(v, float) else v


@pytest.mark.parametrize("formula, expected", ROW_CASES, ids=[c[0] for c in ROW_CASES])
def test_row_level(con, formula, expected):
    e = SqlCompiler("duckdb").compile(_bind(formula, COLUMNS))
    value = con.execute(exp.select(e.as_("v")).from_(exp.table_("t", alias="x")).sql("duckdb")).fetchone()[0]
    assert value == _approx(expected)


@pytest.mark.parametrize("formula, expected", AGG_CASES, ids=[c[0] for c in AGG_CASES])
def test_aggregate(con, formula, expected):
    e = SqlCompiler("duckdb").compile(_bind(formula, {"V": ("v", "real", None)}))
    value = con.execute(exp.select(e.as_("v")).from_(exp.table_("a", alias="x")).sql("duckdb")).fetchone()[0]
    assert value == _approx(expected)


@pytest.mark.parametrize("formula, expected", TABLE_CASES, ids=[c[0] for c in TABLE_CASES])
def test_table_calculation(formula, expected):
    def rewrite(n):
        if isinstance(n, A.Func) and n.name == "SUM":
            return A.ResultRef("v")
        return n

    rows = [{"k": i, "v": v} for i, v in enumerate(AGG_ROWS)]
    out = TableCalcEvaluator(rows, Addressing([], [("k", False)])).evaluate(A.transform(parse(formula), rewrite))
    assert out == [None if x is None else pytest.approx(x) for x in expected]


def test_every_function_has_a_case():
    covered = set()
    for formula, _ in ROW_CASES + AGG_CASES + TABLE_CASES:
        covered |= {n.name for n in parse(formula).walk() if isinstance(n, A.Func)}
    missing = sorted(set(F.FUNCTIONS) - covered)
    assert not missing, f"Functions without compatibility cases: {missing}"
