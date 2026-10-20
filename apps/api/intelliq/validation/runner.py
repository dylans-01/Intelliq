"""Run a parity validation case.

A case file (JSON) names a dataset definition, a connection, a dashboard
spec and, per worksheet, a reference CSV (e.g. a Tableau crosstab export)
with the columns to match on and the columns to compare::

    {
      "name": "Executive Overview vs Tableau 2023.3",
      "connection": {"kind": "duckdb", "config": {"path": "warehouse.duckdb"}},
      "dataset": "dataset.json",
      "dashboard": "dashboard.json",
      "tolerance": {"absolute": 0.005, "relative": 1e-9},
      "checks": [
        {"worksheet": "by_region", "state": {}, "reference": "by_region.csv",
         "keys": {"Region": "d0"}, "values": {"Sales": "m0"}}
      ]
    }

``keys``/``values`` map reference column headers to result column ids.
Paths are relative to the case file.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..connectors.registry import connector_class
from ..query.executor import QueryExecutor
from ..query.planner import Planner, UserContext
from ..semantic.model import Dataset
from ..spec.compile import RuntimeState, compile_worksheet
from ..spec.migrations import load
from .compare import Tolerance, compare


def _read_json(base: Path, ref: Any) -> Any:
    if isinstance(ref, str):
        return json.loads((base / ref).read_text())
    return ref


def run_case(case_path: Path) -> dict[str, Any]:
    base = case_path.parent
    case = json.loads(case_path.read_text())
    dataset = Dataset.model_validate(_read_json(base, case["dataset"]))
    spec = load(_read_json(base, case["dashboard"]))
    conn_cfg = dict(case["connection"]["config"])
    if "path" in conn_cfg:
        conn_cfg["path"] = str((base / conn_cfg["path"]).resolve())
        from ..connectors import duckdb_connector

        duckdb_connector.EXTRA_ROOTS.append(Path(conn_cfg["path"]).parent)
    connector = connector_class(case["connection"]["kind"])(conn_cfg, case["connection"].get("secrets", {}))
    tol = Tolerance(**case.get("tolerance", {}))
    executor = QueryExecutor()
    user = UserContext("validation", "admin")

    checks = []
    for check in case["checks"]:
        ws = spec.worksheet(check["worksheet"])
        req, _ = compile_worksheet(spec, ws, RuntimeState.model_validate(check.get("state", {})), dataset)
        planned = Planner(dataset, connector.dialect, user).plan(req)
        result = executor.execute(planned, connector, cache_scope=("validation",), ttl_seconds=0,
                                  timeout_s=dataset.settings.query_timeout_seconds, owner="validation")
        ids = [c.id for c in result.columns]
        actual = []
        for row in result.rows:
            by_id = dict(zip(ids, row, strict=True))
            actual.append({**{ref: by_id[cid] for ref, cid in check["keys"].items()},
                           **{ref: by_id[cid] for ref, cid in check["values"].items()}})
        with (base / check["reference"]).open(newline="", encoding="utf-8-sig") as fh:
            reference = list(csv.DictReader(fh))
        outcome = compare(actual, reference, keys=list(check["keys"]), values=list(check["values"]), tolerance=tol)
        checks.append({"worksheet": check["worksheet"], "reference": check["reference"], **outcome.to_dict()})

    return {"name": case.get("name", case_path.stem), "passed": all(c["passed"] for c in checks), "checks": checks,
            "tolerance": tol.__dict__}


def main(case: str, report: str | None) -> int:
    outcome = run_case(Path(case))
    for c in outcome["checks"]:
        status = "PASS" if c["passed"] else "FAIL"
        print(f"{status}  {c['worksheet']}: {c['matched_rows']} rows, {c['compared_values']} values, "
              f"{c['discrepancy_count']} discrepancies")
        for d in c["discrepancies"][:10]:
            print(f"      {d['kind']} {d['key']} {d.get('column') or ''} expected={d.get('expected')} actual={d.get('actual')}")
    if report:
        Path(report).write_text(json.dumps(outcome, indent=2, default=str))
    print("PASSED" if outcome["passed"] else "FAILED")
    return 0 if outcome["passed"] else 1
