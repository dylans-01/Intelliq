"""SQL generation for non-DuckDB dialects.

These tests prove the generated SQL is syntactically valid for each target
(it re-parses with that dialect's grammar) and has the expected shape. They
do NOT prove execution semantics on live PostgreSQL / SQL Server / Snowflake /
Databricks; see tests/integration/test_connector_contract.py for that.
"""

from __future__ import annotations

import pytest
import sqlglot

from intelliq.connectors.base import render_positional
from intelliq.query.models import QueryRequest
from intelliq.query.planner import PlanError, Planner, UserContext
from intelliq.sample.model import sample_dataset

DIALECTS = {"postgres": "%s", "tsql": "?", "snowflake": "?", "databricks": "?", "duckdb": "?"}

QUERIES = {
    "aggregate": {"dimensions": [{"field": "region"}], "measures": [{"field": "sales"}, {"field": "profit_ratio"}]},
    "dates": {"dimensions": [{"field": "order_date", "time_unit": "week"}, {"field": "order_date", "date_part": "weekday"}],
              "measures": [{"field": "days_to_ship"}]},
    "lod": {"dimensions": [{"field": "segment"}],
            "measures": [{"field": "customer_lifetime_sales"},
                         {"formula": "SUM([Sales]) / SUM({EXCLUDE [Segment] : SUM([Sales])})"}]},
    "filters": {"dimensions": [{"field": "sub_category"}], "measures": [{"field": "sales"}],
                "filters": [{"target": {"field": "region"}, "op": "in", "values": ["East", "O'Brien"]},
                            {"target": {"field": "sub_category"}, "op": "top_n", "top_n": {"n": 5, "by": {"field": "sales"}}},
                            {"target": {"field": "customer_name"}, "op": "contains", "values": ["50%"]},
                            {"column": "m0", "op": "gt", "values": [10], "stage": "measure"}],
                "limit": 20},
    "strings": {"dimensions": [{"formula": "UPPER(LEFT([Region], 2)) + '-' + STR(YEAR([Order Date]))"}],
                "measures": [{"field": "order_count"}]},
}


@pytest.fixture(scope="module")
def ds():
    return sample_dataset("c")


@pytest.mark.parametrize("dialect", list(DIALECTS))
@pytest.mark.parametrize("name", list(QUERIES))
def test_generated_sql_parses_in_target_dialect(ds, dialect, name):
    req = QueryRequest.model_validate({"dataset_id": ds.id, **QUERIES[name]})
    plan = Planner(ds, dialect, UserContext("u", "admin")).plan(req)
    sql, params = render_positional(plan.sql_template, plan.token_prefix, plan.params, DIALECTS[dialect],
                                    escape_percent=dialect == "postgres")
    parse_sql = sql.replace("%s", "?").replace("%%", "%") if dialect == "postgres" else sql
    sqlglot.parse_one(parse_sql, read=dialect)
    assert plan.token_prefix not in sql
    assert sql.count(DIALECTS[dialect]) - (sql.count("%%s") if dialect == "postgres" else 0) == len(params)
    if name == "filters":
        assert "O'Brien" not in sql and "O'Brien" in params
        if dialect == "postgres":
            assert "50%" not in sql  # user values are bound, never inlined
        if dialect == "tsql":
            assert "TOP" in sql.upper()


def test_tsql_rejects_median(ds):
    req = QueryRequest.model_validate({"dataset_id": ds.id, "measures": [{"field": "sales", "aggregation": "median"}]})
    with pytest.raises(PlanError, match="MEDIAN"):
        Planner(ds, "tsql", UserContext("u", "admin")).plan(req)


def test_postgres_percent_escaping_is_consistent():
    sql, params = render_positional("SELECT '5%' AS a WHERE x = TOKEN0", "TOKEN", {"TOKEN0": 1}, "%s", escape_percent=True)
    assert sql == "SELECT '5%%' AS a WHERE x = %s" and params == [1]


def test_identifiers_are_quoted(ds):
    req = QueryRequest.model_validate({"dataset_id": ds.id, "dimensions": [{"field": "sub_category"}]})
    sql = Planner(ds, "snowflake", UserContext("u", "admin")).plan(req).sql_template
    assert '"o"."sub_category"' in sql and '"main"."orders"' in sql
