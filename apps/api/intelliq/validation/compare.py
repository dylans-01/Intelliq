"""Numeric and categorical comparison of query results against references.

Rules (documented in docs/06-validation.md):
- Rows are matched on key columns after normalising keys (trimmed strings,
  ISO dates, ``NULL``/empty → None).
- Numbers match when ``|a - b| <= absolute + relative * |b|``.
- NULL only equals NULL. A reference ``%null%``/empty cell is NULL.
- Missing and unexpected rows are discrepancies, never ignored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

NULL_TOKENS = {"", "null", "%null%", "none", "nan"}


@dataclass
class Tolerance:
    absolute: float = 0.005
    relative: float = 1e-9


@dataclass
class Discrepancy:
    kind: str  # missing_row | unexpected_row | value
    key: tuple
    column: str | None = None
    expected: Any = None
    actual: Any = None
    delta: float | None = None


@dataclass
class ComparisonResult:
    matched_rows: int = 0
    compared_values: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.discrepancies

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "matched_rows": self.matched_rows,
            "compared_values": self.compared_values,
            "discrepancies": [d.__dict__ for d in self.discrepancies[:500]],
            "discrepancy_count": len(self.discrepancies),
        }


def normalise_key(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    if s.lower() in NULL_TOKENS:
        return None
    # Tableau exports dates as e.g. 2024-01-01 00:00:00 or 1/1/2024
    for fmt in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%m/%d/%Y %I:%M:%S %p"):
        try:
            d = datetime.strptime(s, fmt)
            return d.date().isoformat() if d.time() == datetime.min.time() else d.isoformat(sep=" ")
        except ValueError:
            pass
    return s


def to_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, int | float):
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    if s.lower() in NULL_TOKENS:
        return None
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    return n / 100 if pct else n


def compare(
    actual: list[dict[str, Any]],
    reference: list[dict[str, Any]],
    *,
    keys: list[str],
    values: list[str],
    tolerance: Tolerance | None = None,
) -> ComparisonResult:
    tol = tolerance or Tolerance()
    result = ComparisonResult()

    def index(rows: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
        out: dict[tuple, dict[str, Any]] = {}
        for r in rows:
            out[tuple(normalise_key(r.get(k)) for k in keys)] = r
        return out

    a_idx, r_idx = index(actual), index(reference)
    for key, ref_row in r_idx.items():
        act_row = a_idx.get(key)
        if act_row is None:
            result.discrepancies.append(Discrepancy("missing_row", key))
            continue
        result.matched_rows += 1
        for col in values:
            result.compared_values += 1
            exp_v, act_v = to_number(ref_row.get(col)), to_number(act_row.get(col))
            if exp_v is None and act_v is None:
                # Both NULL, or both non-numeric: compare as normalised text.
                if normalise_key(ref_row.get(col)) != normalise_key(act_row.get(col)):
                    result.discrepancies.append(Discrepancy("value", key, col, ref_row.get(col), act_row.get(col)))
                continue
            if exp_v is None or act_v is None:
                result.discrepancies.append(Discrepancy("value", key, col, ref_row.get(col), act_row.get(col)))
                continue
            delta = abs(act_v - exp_v)
            if delta > tol.absolute + tol.relative * abs(exp_v):
                result.discrepancies.append(Discrepancy("value", key, col, exp_v, act_v, delta))
    for key in a_idx:
        if key not in r_idx:
            result.discrepancies.append(Discrepancy("unexpected_row", key))
    return result
