import pytest

from intelliq.calc import ast as A
from intelliq.calc.parser import parse
from intelliq.calc.table_calc import Addressing, TableCalcEvaluator


def bind(formula: str) -> A.Node:
    """Replace aggregate SUM([x]) with a ResultRef to column x."""

    def rewrite(n):
        if isinstance(n, A.Func) and n.name == "SUM" and isinstance(n.args[0], A.FieldRef):
            return A.ResultRef(n.args[0].name)
        return n

    return A.transform(parse(formula), rewrite)


ROWS = [
    {"seg": "A", "m": 1, "v": 10},
    {"seg": "A", "m": 2, "v": 30},
    {"seg": "A", "m": 3, "v": 30},
    {"seg": "B", "m": 1, "v": 5},
    {"seg": "B", "m": 2, "v": None},
    {"seg": "B", "m": 3, "v": 15},
]


def ev(formula, partition=("seg",), order=(("m", False),), rows=ROWS):
    return TableCalcEvaluator(rows, Addressing(list(partition), list(order))).evaluate(bind(formula))


def test_running_sum_restarts_per_partition_and_skips_nulls():
    assert ev("RUNNING_SUM(SUM([v]))") == [10, 40, 70, 5, 5, 20]


def test_running_avg_and_count():
    assert ev("RUNNING_COUNT(SUM([v]))") == [1, 2, 3, 1, 1, 2]
    assert ev("RUNNING_AVG(SUM([v]))") == [10, 20, pytest.approx(70 / 3), 5, 5, 10]


def test_total_and_percent_of_total():
    assert ev("TOTAL(SUM([v]))") == [70, 70, 70, 20, 20, 20]
    out = ev("SUM([v]) / TOTAL(SUM([v]))")
    assert out[0] == pytest.approx(10 / 70) and out[4] is None


def test_whole_table_when_unpartitioned():
    assert ev("WINDOW_SUM(SUM([v]))", partition=())[0] == 90
    assert ev("SIZE()", partition=()) == [6] * 6


def test_rank_variants_with_ties_and_nulls():
    assert ev("RANK(SUM([v]))") == [3, 1, 1, 2, None, 1]
    assert ev("RANK_DENSE(SUM([v]))") == [2, 1, 1, 2, None, 1]
    assert ev("RANK(SUM([v]), 'asc')") == [1, 2, 2, 1, None, 2]
    unique = ev("RANK_UNIQUE(SUM([v]))")
    assert sorted(unique[:3]) == [1, 2, 3]


def test_rank_percentile():
    out = ev("RANK_PERCENTILE(SUM([v]), 'asc')", partition=())
    assert out[0] == pytest.approx(0.25)  # 10 is above 5 only: 1/(5-1)
    assert out[3] == 0.0 and out[4] is None


def test_lookup_and_difference():
    assert ev("LOOKUP(SUM([v]), -1)") == [None, 10, 30, None, 5, None]
    assert ev("SUM([v]) - LOOKUP(SUM([v]), -1)") == [None, 20, 0, None, None, None]
    assert ev("LOOKUP(SUM([v]), 1)") == [30, 30, None, None, 15, None]


def test_index_first_last():
    assert ev("INDEX()") == [1, 2, 3, 1, 2, 3]
    assert ev("FIRST()") == [0, -1, -2, 0, -1, -2]
    assert ev("LAST()") == [2, 1, 0, 2, 1, 0]


def test_ordering_respects_addressing_direction():
    out = ev("RUNNING_SUM(SUM([v]))", order=(("m", True),))
    assert out[:3] == [70, 60, 30]


def test_scalar_functions_inside_table_calcs():
    assert ev("ZN(LOOKUP(SUM([v]), -1))")[0] == 0
    assert ev("IIF(RANK(SUM([v])) = 1, 'top', 'other')")[:3] == ["other", "top", "top"]
    assert ev("ROUND(SUM([v]) / 3, 1)")[0] == 3.3
