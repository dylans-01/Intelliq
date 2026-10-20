"""The parity validation runner, end to end.

References here are produced by independently written SQL over the synthetic
warehouse. This proves the *framework* (case loading, worksheet execution,
matching, tolerances, discrepancy reporting) — it is not evidence of parity
with Tableau, which requires reference exports from Tableau itself.
"""

from __future__ import annotations

import csv
import json

from intelliq.platform.bootstrap import sample_dashboard_spec
from intelliq.validation.runner import run_case


def _write_case(tmp_path, warehouse, dataset, references: dict[str, list[dict]], checks: list[dict]) -> str:
    (tmp_path / "dataset.json").write_text(json.dumps(dataset.model_dump(by_alias=True, exclude_none=True)))
    (tmp_path / "dashboard.json").write_text(json.dumps(sample_dashboard_spec()))
    for name, rows in references.items():
        with (tmp_path / name).open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    case = {
        "name": "Sample dashboard vs oracle SQL",
        "connection": {"kind": "duckdb", "config": {"path": str(warehouse)}},
        "dataset": "dataset.json",
        "dashboard": "dashboard.json",
        "tolerance": {"absolute": 0.005, "relative": 1e-9},
        "checks": checks,
    }
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case))
    return str(path)


def test_runner_passes_matching_references_and_reports_discrepancies(tmp_path, warehouse, dataset, oracle):
    by_region = [
        {"Region": r, "Sales": f"${s:,.2f}", "Profit Ratio": f"{p * 100:.4f}%"}
        for r, s, p in oracle.execute(
            "SELECT region, SUM(sales)::DOUBLE, SUM(profit)::DOUBLE / SUM(sales) FROM orders GROUP BY 1").fetchall()
    ]
    by_segment_corporate = [
        {"Month": m.strftime("%m/%d/%Y"), "Segment": seg, "Sales": round(s, 2)}
        for m, seg, s in oracle.execute(
            "SELECT date_trunc('month', order_date)::DATE, segment, SUM(sales)::DOUBLE FROM orders "
            "WHERE segment = 'Corporate' GROUP BY 1, 2").fetchall()
    ]
    checks = [
        {"worksheet": "by_region", "reference": "by_region.csv", "keys": {"Region": "d0"},
         "values": {"Sales": "m0", "Profit Ratio": "m1"}},
        {"worksheet": "trend", "reference": "trend.csv", "state": {"filters": {"f_segment": {"values": ["Corporate"]}}},
         "keys": {"Month": "d0", "Segment": "d1"}, "values": {"Sales": "m0"}},
    ]
    case = _write_case(tmp_path, warehouse, dataset, {"by_region.csv": by_region, "trend.csv": by_segment_corporate}, checks)
    outcome = run_case(__import__("pathlib").Path(case))
    assert outcome["passed"], json.dumps(outcome, default=str)[:2000]
    assert outcome["checks"][0]["compared_values"] == 8
    assert outcome["checks"][1]["matched_rows"] == 48

    # Tamper with one reference value and drop a row: both must be reported.
    by_region[0]["Sales"] = "$1.00"
    tampered = by_segment_corporate[1:]
    case = _write_case(tmp_path, warehouse, dataset, {"by_region.csv": by_region, "trend.csv": tampered}, checks)
    outcome = run_case(__import__("pathlib").Path(case))
    assert not outcome["passed"]
    kinds = [d["kind"] for c in outcome["checks"] for d in c["discrepancies"]]
    assert kinds.count("value") == 1 and kinds.count("unexpected_row") == 1
