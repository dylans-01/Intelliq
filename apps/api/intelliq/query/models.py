"""Query request contract between the dashboard layer and the query engine."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..semantic.model import Aggregation, DataType, Format

Scalar = bool | int | float | date | datetime | str | None
TimeUnit = Literal["year", "quarter", "month", "week", "day", "hour", "minute"]
DatePart = Literal["year", "quarter", "month", "week", "weekday", "day"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuickTableCalc(Strict):
    """Tableau-style quick table calculation applied to a measure."""

    type: Literal["running_total", "percent_of_total", "difference", "percent_difference", "rank", "rank_dense", "index"]
    # Output ids of dimensions that partition (restart) the calculation.
    # Remaining dimensions are addressed in sort order. Empty = "Table".
    partition_by: list[str] = Field(default_factory=list)
    direction: Literal["asc", "desc"] = "desc"


class FieldUse(Strict):
    """A field placed in a query: a semantic field or an ad-hoc formula, plus
    how it is used (aggregation, date granularity, table calculation)."""

    field: str | None = None
    formula: str | None = Field(default=None, max_length=20_000)
    aggregation: Aggregation | Literal["none"] | None = None
    time_unit: TimeUnit | None = None
    date_part: DatePart | None = None
    table_calc: QuickTableCalc | None = None
    label: str | None = Field(default=None, max_length=200)
    format: Format | None = None

    @model_validator(mode="after")
    def _one_source(self) -> FieldUse:
        if (self.field is None) == (self.formula is None):
            raise ValueError("Exactly one of 'field' or 'formula' is required")
        if self.time_unit and self.date_part:
            raise ValueError("Use either time_unit (continuous) or date_part (discrete), not both")
        return self


class RelativeDate(Strict):
    mode: Literal["current", "previous", "last_n", "to_date"] = "last_n"
    unit: Literal["year", "quarter", "month", "week", "day"] = "month"
    n: int = Field(default=1, ge=1, le=1000)
    anchor: date | None = None  # defaults to today (server time zone)


class TopN(Strict):
    n: int = Field(ge=1, le=10_000)
    by: FieldUse
    direction: Literal["top", "bottom"] = "top"


FilterOp = Literal[
    "in", "not_in", "eq", "neq", "gt", "gte", "lt", "lte", "between",
    "contains", "starts_with", "is_null", "not_null", "relative_date", "top_n",
]  # fmt: skip
FilterStage = Literal["datasource", "context", "dimension", "measure", "table_calc"]


class Filter(Strict):
    """A filter and the stage of Tableau's order of operations it runs in:

    datasource → context → (FIXED LOD) → dimension → (INCLUDE/EXCLUDE LOD)
    → measure → (table calculations) → table_calc
    """

    target: FieldUse | None = None
    column: str | None = None  # output id; for measure / table_calc stages
    op: FilterOp
    values: list[Scalar] = Field(default_factory=list, max_length=10_000)
    stage: FilterStage = "dimension"
    relative: RelativeDate | None = None
    top_n: TopN | None = None

    @model_validator(mode="after")
    def _shape(self) -> Filter:
        if (self.target is None) == (self.column is None):
            raise ValueError("A filter needs exactly one of 'target' or 'column'")
        if self.column is not None and self.stage not in ("measure", "table_calc"):
            raise ValueError("'column' filters are only valid in the measure or table_calc stage")
        if self.stage == "table_calc" and self.column is None:
            raise ValueError("table_calc filters must reference an output column")
        if self.op == "relative_date" and self.relative is None:
            raise ValueError("relative_date filters need 'relative'")
        if self.op == "top_n" and self.top_n is None:
            raise ValueError("top_n filters need 'top_n'")
        if self.op == "between" and len(self.values) != 2:
            raise ValueError("between filters need exactly two values (use null for an open end)")
        return self


class Sort(Strict):
    column: str
    direction: Literal["asc", "desc"] = "asc"


class ParameterValue(Strict):
    name: str
    data_type: DataType
    value: Scalar


class QueryRequest(Strict):
    dataset_id: str
    dimensions: list[FieldUse] = Field(default_factory=list, max_length=30)
    measures: list[FieldUse] = Field(default_factory=list, max_length=50)
    filters: list[Filter] = Field(default_factory=list, max_length=100)
    sort: list[Sort] = Field(default_factory=list, max_length=10)
    limit: int | None = Field(default=None, ge=1, le=1_000_000)
    parameters: list[ParameterValue] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _non_empty(self) -> QueryRequest:
        if not self.dimensions and not self.measures:
            raise ValueError("A query needs at least one dimension or measure")
        return self


class ColumnMeta(BaseModel):
    id: str
    label: str
    role: Literal["dimension", "measure"]
    data_type: str
    field_id: str | None = None
    format: Format | None = None
    time_unit: str | None = None
    date_part: str | None = None
    table_calc: bool = False


class QueryResult(BaseModel):
    columns: list[ColumnMeta]
    rows: list[list[Scalar]]
    row_count: int
    truncated: bool
    cache_hit: bool = False
    duration_ms: float
    query_id: str
    sql: str | None = None  # only returned to authors
