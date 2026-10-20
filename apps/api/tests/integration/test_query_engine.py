"""Query engine results checked against independently written SQL (oracle)."""

from __future__ import annotations

import time
from datetime import date

import pytest

from intelliq.connectors.base import CancelToken, QueryCancelled, QueryTimeout
from intelliq.query.planner import PlanError, UserContext
from intelliq.semantic.model import Dataset


def by_key(rows, key=0):
    return {r[key]: r for r in rows}


def approx_rows(actual, expected):
    assert len(actual) == len(expected)
    for a, e in zip(actual, expected, strict=True):
        assert [pytest.approx(x) if isinstance(x, float) else x for x in e] == a


def test_basic_aggregation_matches_oracle(runner, oracle):
    rows = runner.rows({"dimensions": [{"field": "region"}],
                        "measures": [{"field": "sales"}, {"field": "profit_ratio"}, {"field": "order_count"},
                                     {"field": "quantity", "aggregation": "avg"}]})
    expected = oracle.execute("""
        SELECT region, SUM(sales)::DOUBLE, SUM(profit)::DOUBLE / SUM(sales), COUNT(DISTINCT order_id), AVG(quantity)
        FROM orders GROUP BY region ORDER BY region""").fetchall()
    approx_rows(rows, [list(r) for r in expected])


def test_time_unit_and_date_part(runner, oracle):
    rows = runner.rows({"dimensions": [{"field": "order_date", "time_unit": "quarter"}],
                        "measures": [{"field": "sales"}]})
    expected = oracle.execute("SELECT date_trunc('quarter', order_date)::DATE, SUM(sales)::DOUBLE FROM orders "
                              "GROUP BY 1 ORDER BY 1").fetchall()
    approx_rows(rows, [[r[0].isoformat(), r[1]] for r in expected])
    rows = runner.rows({"dimensions": [{"field": "order_date", "date_part": "month"}], "measures": [{"field": "sales"}]})
    assert [r[0] for r in rows] == list(range(1, 13))


def test_join_is_culled_when_unused(runner):
    plan = runner.plan({"dimensions": [{"field": "region"}], "measures": [{"field": "sales"}]})
    assert "people" not in plan.sql_template and "returns" not in plan.sql_template
    plan = runner.plan({"dimensions": [{"field": "regional_manager"}], "measures": [{"field": "sales"}]})
    assert '"people"' in plan.sql_template and "returns" not in plan.sql_template


def test_join_results(runner, oracle):
    rows = runner.rows({"dimensions": [{"field": "regional_manager"}, {"field": "is_returned"}],
                        "measures": [{"field": "sales"}]})
    expected = oracle.execute("""
        SELECT p.person, r.returned IS NOT NULL, SUM(o.sales)::DOUBLE FROM orders o
        LEFT JOIN people p ON o.region = p.region LEFT JOIN returns r ON o.order_id = r.order_id
        GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
    approx_rows(rows, [list(r) for r in expected])


def test_filters_bind_values_never_inline(runner):
    hostile = "East' OR '1'='1"
    plan = runner.plan({"dimensions": [{"field": "region"}], "measures": [{"field": "sales"}],
                        "filters": [{"target": {"field": "region"}, "op": "in", "values": [hostile]}]})
    assert hostile not in plan.sql_template
    assert hostile in plan.params.values()
    assert runner.rows({"dimensions": [{"field": "region"}], "measures": [{"field": "sales"}],
                        "filters": [{"target": {"field": "region"}, "op": "in", "values": [hostile]}]}) == []


def test_fixed_lod_ignores_dimension_filters_but_respects_context(runner, oracle):
    base = {"dimensions": [{"field": "segment"}], "measures": [{"formula": "SUM({FIXED : SUM([Sales])})", "label": "x"}]}
    # Dimension filter: FIXED total ignores it
    dim = runner.rows({**base, "measures": [{"formula": "MIN({FIXED : SUM([Sales])})"}],
                       "filters": [{"target": {"field": "region"}, "op": "in", "values": ["West"]}]})
    grand = oracle.execute("SELECT SUM(sales)::DOUBLE FROM orders").fetchone()[0]
    assert all(r[1] == pytest.approx(grand) for r in dim)
    # Context filter: FIXED total respects it
    ctx = runner.rows({**base, "measures": [{"formula": "MIN({FIXED : SUM([Sales])})"}],
                       "filters": [{"target": {"field": "region"}, "op": "in", "values": ["West"], "stage": "context"}]})
    west = oracle.execute("SELECT SUM(sales)::DOUBLE FROM orders WHERE region = 'West'").fetchone()[0]
    assert all(r[1] == pytest.approx(west) for r in ctx)


def test_aggregate_over_finer_fixed_lod_uses_lod_grain(runner, oracle):
    """AVG of a per-customer FIXED value averages customers, not order lines."""

    rows = runner.rows({"dimensions": [{"field": "segment"}], "measures": [{"field": "customer_lifetime_sales"}]})
    expected = oracle.execute("""
        WITH c AS (SELECT customer_name, SUM(sales)::DOUBLE AS v FROM orders GROUP BY 1)
        SELECT s.segment, AVG(c.v) FROM (SELECT DISTINCT segment, customer_name FROM orders) s
        JOIN c USING (customer_name) GROUP BY 1 ORDER BY 1""").fetchall()
    approx_rows(rows, [list(r) for r in expected])


def test_include_lod(runner, oracle):
    rows = runner.rows({"dimensions": [{"field": "region"}],
                        "measures": [{"formula": "AVG({INCLUDE [Customer Name] : SUM([Sales])})"}]})
    expected = oracle.execute("""
        SELECT region, AVG(v) FROM (SELECT region, customer_name, SUM(sales)::DOUBLE v FROM orders GROUP BY 1, 2)
        GROUP BY 1 ORDER BY 1""").fetchall()
    approx_rows(rows, [list(r) for r in expected])


def test_exclude_lod_percent_of_region(runner, oracle):
    rows = runner.rows({"dimensions": [{"field": "region"}, {"field": "segment"}],
                        "measures": [{"formula": "SUM([Sales]) / SUM({EXCLUDE [Segment] : SUM([Sales])})"}]})
    expected = oracle.execute("""
        SELECT region, segment, SUM(sales)::DOUBLE / SUM(SUM(sales)) OVER (PARTITION BY region)
        FROM orders GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
    approx_rows(rows, [list(r) for r in expected])
    for region in {r[0] for r in rows}:
        assert sum(r[2] for r in rows if r[0] == region) == pytest.approx(1.0)


def test_row_level_lod_comparison(runner, oracle):
    """LOD inside a row-level expression joins at row level."""

    rows = runner.rows({"dimensions": [{"field": "region"}],
                        "measures": [{"formula": "SUM(IIF([Sales] > {FIXED [Region] : AVG([Sales])}, 1, 0))"}]})
    expected = oracle.execute("""
        SELECT o.region, SUM(CASE WHEN o.sales > a.v THEN 1 ELSE 0 END) FROM orders o
        JOIN (SELECT region, AVG(sales::DOUBLE) v FROM orders GROUP BY 1) a USING (region) GROUP BY 1 ORDER BY 1""").fetchall()
    approx_rows(rows, [list(r) for r in expected])


def test_top_n_computed_after_context_only(runner, oracle):
    q = {"dimensions": [{"field": "sub_category"}], "measures": [{"field": "sales"}],
         "filters": [{"target": {"field": "sub_category"}, "op": "top_n", "top_n": {"n": 3, "by": {"field": "sales"}}}],
         "sort": [{"column": "m0", "direction": "desc"}]}
    top = [r[0] for r in runner.rows(q)]
    expected = [r[0] for r in oracle.execute(
        "SELECT sub_category FROM orders GROUP BY 1 ORDER BY SUM(sales) DESC LIMIT 3").fetchall()]
    assert top == expected
    # A dimension filter does not change which members are in the top N (Tableau order of operations)...
    q_dim = {**q, "filters": q["filters"] + [{"target": {"field": "region"}, "op": "in", "values": ["South"]}]}
    assert {r[0] for r in runner.rows(q_dim)} <= set(expected)
    # ...but a context filter does.
    q_ctx = {**q, "filters": q["filters"] + [{"target": {"field": "region"}, "op": "in", "values": ["South"], "stage": "context"}]}
    south = [r[0] for r in oracle.execute(
        "SELECT sub_category FROM orders WHERE region = 'South' GROUP BY 1 ORDER BY SUM(sales) DESC LIMIT 3").fetchall()]
    assert [r[0] for r in runner.rows(q_ctx)] == south


def test_measure_filter_is_having(runner, oracle):
    rows = runner.rows({"dimensions": [{"field": "state"}], "measures": [{"field": "sales"}],
                        "filters": [{"column": "m0", "op": "gt", "values": [1_000_000], "stage": "measure"}]})
    expected = oracle.execute("SELECT state, SUM(sales)::DOUBLE FROM orders GROUP BY 1 HAVING SUM(sales) > 1000000 ORDER BY 1").fetchall()
    approx_rows(rows, [list(r) for r in expected])


def test_not_in_keeps_nulls_like_tableau(runner, oracle):
    rows = runner.rows({"dimensions": [{"field": "returned"}], "measures": [{"field": "sales"}],
                        "filters": [{"target": {"field": "returned"}, "op": "not_in", "values": ["Yes"]}]})
    assert [r[0] for r in rows] == [None]


def test_quick_table_calcs(runner, oracle):
    rows = runner.rows({"dimensions": [{"field": "order_date", "time_unit": "year"}, {"field": "segment"}],
                        "measures": [{"field": "sales"},
                                     {"field": "sales", "table_calc": {"type": "running_total", "partition_by": ["d1"]}},
                                     {"field": "sales", "table_calc": {"type": "percent_of_total", "partition_by": ["d0"]}},
                                     {"field": "sales", "table_calc": {"type": "rank", "partition_by": ["d0"]}}]})
    expected = oracle.execute("""
        SELECT y, segment, s,
               SUM(s) OVER (PARTITION BY segment ORDER BY y),
               s / SUM(s) OVER (PARTITION BY y),
               RANK() OVER (PARTITION BY y ORDER BY s DESC)
        FROM (SELECT date_trunc('year', order_date)::DATE y, segment, SUM(sales)::DOUBLE s FROM orders GROUP BY 1, 2)
        ORDER BY 1, 2""").fetchall()
    approx_rows(rows, [[r[0].isoformat(), *r[1:]] for r in expected])


def test_table_calc_filter_and_sort(runner):
    res = runner.run({"dimensions": [{"field": "sub_category"}],
                      "measures": [{"field": "sales"}, {"field": "sales", "table_calc": {"type": "rank"}}],
                      "filters": [{"column": "m1", "op": "lte", "values": [5], "stage": "table_calc"}],
                      "sort": [{"column": "m1", "direction": "asc"}]})
    assert [r[2] for r in res.rows] == [1, 2, 3, 4, 5]
    assert res.rows[0][1] == max(r[1] for r in res.rows)


def test_parameters(runner, oracle):
    rows = runner.rows({"dimensions": [{"field": "region"}],
                        "measures": [{"formula": "SUM(IIF([Sales] > [Parameters].[Min], 1, 0))"}],
                        "parameters": [{"name": "Min", "data_type": "real", "value": "500"}]})
    expected = oracle.execute("SELECT region, SUM(CASE WHEN sales > 500 THEN 1 ELSE 0 END) FROM orders GROUP BY 1 ORDER BY 1").fetchall()
    approx_rows(rows, [list(r) for r in expected])
    with pytest.raises(PlanError, match="not a valid real"):
        runner.plan({"measures": [{"field": "sales"}], "parameters": [{"name": "Min", "data_type": "real", "value": "abc"}]})


def test_relative_date_filter(runner, oracle):
    rows = runner.rows({"measures": [{"field": "sales"}],
                        "filters": [{"target": {"field": "order_date"}, "op": "relative_date",
                                     "relative": {"mode": "last_n", "unit": "month", "n": 3, "anchor": "2024-06-15"}}]})
    expected = oracle.execute("SELECT SUM(sales)::DOUBLE FROM orders WHERE order_date >= DATE '2024-04-01' "
                              "AND order_date < DATE '2024-07-01'").fetchone()[0]
    assert rows[0][0] == pytest.approx(expected)


def test_row_level_security(runner, oracle, dataset):
    secured = Dataset.model_validate({**dataset.model_dump(by_alias=True), "row_level_security": [
        {"field": "region", "user_attribute": "region"}]})
    west = UserContext("w", "viewer", {"region": ["West"]})
    rows = runner.rows({"dimensions": [{"field": "region"}], "measures": [{"field": "sales"}]}, user=west, dataset=secured)
    assert [r[0] for r in rows] == ["West"]
    # FIXED LOD subqueries are secured too: grand total equals the West total.
    rows = runner.rows({"measures": [{"formula": "MIN({FIXED : SUM([Sales])})"}]}, user=west, dataset=secured)
    west_total = oracle.execute("SELECT SUM(sales)::DOUBLE FROM orders WHERE region = 'West'").fetchone()[0]
    assert rows[0][0] == pytest.approx(west_total)
    # No attribute → no rows; admin exempt.
    assert runner.rows({"measures": [{"field": "sales"}]}, user=UserContext("n", "viewer"), dataset=secured) == [[None]]
    assert len(runner.rows({"dimensions": [{"field": "region"}], "measures": [{"field": "sales"}]},
                           user=UserContext("a", "admin"), dataset=secured)) == 4


def test_limit_and_truncation(runner):
    res = runner.run({"dimensions": [{"field": "customer_name"}], "measures": [{"field": "sales"}], "limit": 10})
    assert res.row_count == 10 and res.truncated
    small = runner.run({"dimensions": [{"field": "region"}], "measures": [{"field": "sales"}], "limit": 10})
    assert small.row_count == 4 and not small.truncated


def test_formulas_can_be_disallowed(runner, dataset):
    from intelliq.query.models import QueryRequest
    from intelliq.query.planner import Planner

    req = QueryRequest.model_validate({"dataset_id": dataset.id, "measures": [{"formula": "SUM([Sales])"}]})
    with pytest.raises(PlanError) as e:
        Planner(dataset, "duckdb", UserContext("v", "viewer"), allow_formulas=False).plan(req)
    assert e.value.code == "forbidden"


@pytest.mark.parametrize("query, code", [
    ({"dimensions": [{"field": "profit_ratio"}]}, "aggregate_dimension"),
    ({"measures": [{"field": "profit_ratio", "aggregation": "sum"}]}, "nested_aggregation"),
    ({"measures": [{"field": "nope"}]}, "unknown_field"),
    ({"measures": [{"formula": "SUM([Sales]"}]}, "invalid_formula"),
])
def test_plan_errors(runner, query, code):
    with pytest.raises(PlanError) as e:
        runner.plan(query)
    assert e.value.code == code


def test_cache_hits_for_identical_queries(runner, dataset, connector):
    from intelliq.query.executor import QueryExecutor

    ex = QueryExecutor()
    plan = lambda: runner.plan({"dimensions": [{"field": "segment"}], "measures": [{"field": "sales"}]})  # noqa: E731
    first = ex.execute(plan(), connector, cache_scope=("c",), ttl_seconds=60, timeout_s=10, owner="u")
    second = ex.execute(plan(), connector, cache_scope=("c",), ttl_seconds=60, timeout_s=10, owner="u")
    assert not first.cache_hit and second.cache_hit and first.rows == second.rows
    other_scope = ex.execute(plan(), connector, cache_scope=("other",), ttl_seconds=60, timeout_s=10, owner="u")
    assert not other_scope.cache_hit


def test_timeout_and_cancellation(connector):
    heavy = "SELECT COUNT(*) FROM range(10000000000) a"
    started = time.monotonic()
    with pytest.raises(QueryTimeout):
        connector._execute(heavy, [], timeout_s=0.3, max_rows=1, cancel=CancelToken())
    assert time.monotonic() - started < 5
    token = CancelToken()
    import threading

    threading.Timer(0.2, token.cancel).start()
    with pytest.raises(QueryCancelled):
        connector._execute(heavy, [], timeout_s=30, max_rows=1, cancel=token)


def test_duckdb_external_access_is_disabled(connector):
    from intelliq.connectors.base import ConnectorError

    with pytest.raises(ConnectorError):
        connector._execute("SELECT * FROM read_csv('/etc/passwd')", [], timeout_s=5, max_rows=1, cancel=CancelToken())


def test_week_start_setting(runner, dataset):
    monday = Dataset.model_validate({**dataset.model_dump(by_alias=True), "settings": {"week_start": "monday"}})
    q = {"dimensions": [{"field": "order_date", "time_unit": "week"}], "measures": [{"field": "sales"}],
         "filters": [{"target": {"field": "order_date"}, "op": "between", "values": ["2024-03-04", "2024-03-10"]}]}
    sunday_rows = runner.rows(q)
    monday_rows = runner.rows(q, dataset=monday)
    assert {date.fromisoformat(r[0]).weekday() for r in sunday_rows} == {6}
    assert {date.fromisoformat(r[0]).weekday() for r in monday_rows} == {0}
