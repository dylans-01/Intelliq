"""Connector contract suite.

Every connector must pass the same contract: connectivity, schema discovery,
type mapping, bound parameters, timeouts, and — most importantly — returning
the same answers as DuckDB for the same semantic queries, which exercises the
dialect-specific SQL the planner generates.

DuckDB always runs. Other engines run when configured through environment
variables, e.g.::

    INTELLIQ_TEST_POSTGRES='{"host": "...", "port": 5432, "database": "...", "user": "...", "password": "..."}'

Engines that are not configured are skipped (reported, never silently passed).
"""

from __future__ import annotations

import json
import os

import duckdb
import pytest

from intelliq.connectors.base import CancelToken, ConnectorError, QueryTimeout
from intelliq.connectors.registry import connector_class
from intelliq.query.executor import QueryExecutor
from intelliq.query.models import QueryRequest
from intelliq.query.planner import Planner, UserContext

TABLES = {
    "orders": [
        ("row_id", "INTEGER"), ("order_id", "VARCHAR(32)"), ("order_date", "DATE"), ("ship_date", "DATE"),
        ("ship_mode", "VARCHAR(32)"), ("customer_id", "VARCHAR(32)"), ("customer_name", "VARCHAR(64)"),
        ("segment", "VARCHAR(32)"), ("country", "VARCHAR(32)"), ("city", "VARCHAR(64)"), ("state", "VARCHAR(64)"),
        ("region", "VARCHAR(16)"), ("product_id", "VARCHAR(32)"), ("category", "VARCHAR(32)"),
        ("sub_category", "VARCHAR(32)"), ("product_name", "VARCHAR(64)"), ("sales", "DECIMAL(12,2)"),
        ("quantity", "INTEGER"), ("discount", "DOUBLE PRECISION"), ("profit", "DECIMAL(12,2)"),
    ],
    "people": [("person", "VARCHAR(64)"), ("region", "VARCHAR(16)")],
    "returns": [("returned", "VARCHAR(8)"), ("order_id", "VARCHAR(32)")],
}

QUERIES = {
    "aggregates": {"dimensions": [{"field": "region"}],
                   "measures": [{"field": "sales"}, {"field": "profit_ratio"}, {"field": "order_count"},
                                {"field": "discount"}, {"field": "quantity", "aggregation": "median"}]},
    "joins": {"dimensions": [{"field": "regional_manager"}, {"field": "is_returned"}], "measures": [{"field": "sales"}]},
    "month_trunc": {"dimensions": [{"field": "order_date", "time_unit": "month"}], "measures": [{"field": "sales"}],
                    "filters": [{"target": {"field": "order_date"}, "op": "between", "values": ["2023-01-01", "2023-12-31"]}]},
    "week_and_weekday": {"dimensions": [{"field": "order_date", "time_unit": "week"}, {"field": "order_date", "date_part": "weekday"}],
                         "measures": [{"field": "order_count"}],
                         "filters": [{"target": {"field": "order_date"}, "op": "between", "values": ["2024-02-01", "2024-03-15"]}]},
    "date_math": {"dimensions": [{"field": "ship_mode"}],
                  "measures": [{"field": "days_to_ship"},
                               {"formula": "AVG(DATEDIFF('month', [Order Date], #2025-01-15#))"},
                               {"formula": "MAX(DATEADD('quarter', 1, [Order Date]))"},
                               {"formula": "MIN(DATEPART('week', [Order Date]))"}]},
    "lods": {"dimensions": [{"field": "segment"}],
             "measures": [{"field": "customer_lifetime_sales"},
                          {"formula": "AVG({INCLUDE [Customer Name] : SUM([Sales])})"},
                          {"formula": "SUM([Sales]) / SUM({EXCLUDE [Segment] : SUM([Sales])})"}],
             "filters": [{"target": {"field": "region"}, "op": "in", "values": ["West", "East"]}]},
    "top_n_and_having": {"dimensions": [{"field": "sub_category"}], "measures": [{"field": "sales"}],
                         "filters": [{"target": {"field": "sub_category"}, "op": "top_n", "top_n": {"n": 5, "by": {"field": "sales"}}},
                                     {"column": "m0", "op": "gt", "values": [100], "stage": "measure"}],
                         "sort": [{"column": "m0", "direction": "desc"}]},
    "strings_and_logic": {"dimensions": [{"formula": "UPPER(LEFT([Region], 3)) + '-' + STR(YEAR([Order Date]))"},
                                         {"field": "profitable"}],
                          "measures": [{"formula": "SUM(IIF(CONTAINS([Product Name], 'A'), 1, 0))"},
                                       {"formula": "SUM(ZN([Profit]) * 2)"}]},
    "hostile_values": {"dimensions": [{"field": "region"}], "measures": [{"field": "sales"}],
                       "filters": [{"target": {"field": "customer_name"}, "op": "contains", "values": ["50% O'Brien\\"]}]},
    "table_calcs": {"dimensions": [{"field": "order_date", "time_unit": "year"}],
                    "measures": [{"field": "sales", "table_calc": {"type": "running_total"}},
                                 {"field": "sales", "table_calc": {"type": "percent_difference"}}]},
}


def _engines():
    engines = [("duckdb", None)]
    for kind in ("postgres", "sqlserver", "snowflake", "databricks"):
        raw = os.environ.get(f"INTELLIQ_TEST_{kind.upper()}")
        engines.append((kind, json.loads(raw) if raw else None))
    return engines


def _seed(kind: str, cfg: dict, warehouse) -> None:
    """Copy the synthetic warehouse into the target engine (schema 'main')."""

    src = duckdb.connect(str(warehouse), read_only=True, config={"enable_external_access": False})
    if kind != "postgres":
        pytest.skip(f"Seeding is implemented for PostgreSQL only; seed {kind} manually")
    import psycopg

    with psycopg.connect(host=cfg["host"], port=int(cfg.get("port") or 5432), dbname=cfg["database"],
                         user=cfg["user"], password=cfg.get("password") or None, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS main CASCADE")
        conn.execute("CREATE SCHEMA main")
        for table, cols in TABLES.items():
            conn.execute(f"CREATE TABLE main.{table} ({', '.join(f'{c} {t}' for c, t in cols)})")
            rows = src.execute(f"SELECT {', '.join(c for c, _ in cols)} FROM main.{table}").fetchall()
            with conn.cursor().copy(f"COPY main.{table} ({', '.join(c for c, _ in cols)}) FROM STDIN") as copy:
                for r in rows:
                    copy.write_row(r)
    src.close()


@pytest.fixture(scope="module", params=_engines(), ids=lambda e: e[0])
def engine(request, warehouse):
    kind, cfg = request.param
    if kind == "duckdb":
        return kind, connector_class("duckdb")({"path": str(warehouse)}, {})
    if cfg is None:
        pytest.skip(f"{kind}: set INTELLIQ_TEST_{kind.upper()} to run the contract suite against it")
    _seed(kind, cfg, warehouse)
    secrets = {k: cfg[k] for k in ("password", "access_token", "private_key") if k in cfg}
    config = {k: v for k, v in cfg.items() if k not in secrets}
    return kind, connector_class(kind)(config, secrets)


@pytest.fixture(scope="module")
def reference(warehouse):
    return connector_class("duckdb")({"path": str(warehouse)}, {})


def _run(connector, dataset, query):
    req = QueryRequest.model_validate({"dataset_id": dataset.id, **query})
    plan = Planner(dataset, connector.dialect, UserContext("contract", "admin")).plan(req)
    return QueryExecutor().execute(plan, connector, cache_scope=("contract", connector.kind), ttl_seconds=0,
                                   timeout_s=60, owner="contract").rows


def _normalise(rows):
    out = []
    for r in rows:
        out.append(tuple(round(v, 6) if isinstance(v, float) else (float(v) if isinstance(v, int) and not isinstance(v, bool) else v)
                         for v in r))
    return out


def test_connectivity_and_discovery(engine):
    _, c = engine
    c.test()
    names = {t.name.lower() for t in c.list_tables()}
    assert {"orders", "people", "returns"} <= names
    cols = {col.name.lower(): col.data_type for col in c.describe("main", "orders")}
    assert cols["order_date"] == "date" and cols["sales"] == "real" and cols["quantity"] == "integer"
    assert cols["region"] == "string"


@pytest.mark.parametrize("name", list(QUERIES))
def test_same_answers_as_reference(engine, reference, dataset, name):
    kind, c = engine
    if name == "aggregates" and c.dialect == "tsql":
        pytest.skip("MEDIAN is not supported on SQL Server (documented)")
    expected = _normalise(_run(reference, dataset, QUERIES[name]))
    actual = _normalise(_run(c, dataset, QUERIES[name]))
    if kind == "duckdb":
        assert actual == expected
        return
    # Row order for ties may differ between engines; compare as multisets with tolerance.
    assert len(actual) == len(expected), f"{kind}/{name}: {len(actual)} rows vs {len(expected)}"
    for a, e in zip(sorted(actual, key=repr), sorted(expected, key=repr), strict=True):
        assert [pytest.approx(x, rel=1e-6, abs=1e-6) if isinstance(x, float) else x for x in e] == list(a), f"{kind}/{name}"


def test_timeout_is_enforced(engine):
    _, c = engine
    heavy = {
        "duckdb": "SELECT COUNT(*) FROM range(10000000000)",
        "postgres": "SELECT COUNT(*) FROM generate_series(1, 2000000000)",
    }.get(c.kind)
    if heavy is None:
        pytest.skip("No heavy query defined for this engine")
    with pytest.raises(QueryTimeout):
        c._execute(heavy, [], timeout_s=0.5, max_rows=1, cancel=CancelToken())


def test_errors_are_sanitised(engine):
    _, c = engine
    with pytest.raises(ConnectorError) as e:
        c._execute("SELECT * FROM main.does_not_exist", [], timeout_s=10, max_rows=1, cancel=CancelToken())
    assert "\n" not in e.value.message and len(e.value.message) <= 500
