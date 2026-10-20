"""Intelliq Dashboard Specification (IDS), version 1.

The platform-owned, renderer-independent representation of a dashboard.
Natively authored and Tableau-imported dashboards share this format and the
same runtime. Tableau XML is never used at runtime.

A dashboard is a set of *worksheets* (one visualization + its query intent),
dashboard-level *filters* and *parameters*, *actions* (cross-filtering,
highlighting, navigation) and a responsive grid *layout*.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..query.models import FieldUse, Scalar
from ..query.models import Filter as QueryFilter
from ..semantic.model import DataType

SPEC_VERSION = 1
_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _key(v: str) -> str:
    if not _KEY.match(v):
        raise ValueError(f"Invalid key {v!r}: use letters, digits, '_' or '-' (max 64)")
    return v


VizType = Literal["bar", "line", "area", "scatter", "pie", "table", "kpi", "heatmap"]
Channel = Literal["x", "y", "color", "size", "label", "detail", "tooltip", "column", "value"]


class VizField(Strict):
    """A field placed on a channel ("shelf") of a visualization."""

    key: str
    role: Literal["dimension", "measure"]
    channel: Channel
    use: FieldUse

    _k = field_validator("key")(_key)


class VizSort(Strict):
    key: str
    direction: Literal["asc", "desc"] = "asc"


class VizOptions(Strict):
    orientation: Literal["vertical", "horizontal"] = "vertical"
    stacked: bool = False
    show_labels: bool = False
    show_legend: bool = True
    smooth: bool = False
    show_points: bool = False
    donut: bool = True
    comparison: Literal["none", "previous_row"] = "none"  # KPI delta vs previous period
    page_size: int = Field(default=25, ge=5, le=500)  # tables
    palette: str | None = None


class VizSpec(Strict):
    type: VizType
    fields: list[VizField] = Field(default_factory=list, max_length=40)
    sort: list[VizSort] = Field(default_factory=list, max_length=5)
    limit: int | None = Field(default=None, ge=1, le=100_000)
    options: VizOptions = Field(default_factory=VizOptions)

    @model_validator(mode="after")
    def _keys_unique(self) -> VizSpec:
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("Visualization field keys must be unique")
        for s in self.sort:
            if s.key not in keys:
                raise ValueError(f"Sort references unknown field key '{s.key}'")
        for f in self.fields:
            if f.role == "dimension" and (f.use.aggregation not in (None, "none") or f.use.table_calc):
                raise ValueError(f"Dimension '{f.key}' cannot be aggregated")
        return self


class WorksheetFilter(Strict):
    """A filter authored into a worksheet. Always applied; viewers cannot remove it."""

    filter: QueryFilter


class Worksheet(Strict):
    id: str
    title: str = Field(default="", max_length=300)
    subtitle: str = Field(default="", max_length=500)
    show_title: bool = True
    dataset_id: str
    viz: VizSpec
    filters: list[WorksheetFilter] = Field(default_factory=list, max_length=50)
    drill_hierarchy: str | None = None  # hierarchy id enabling drill-down on the first dimension

    _k = field_validator("id")(_key)


class AllowedValues(Strict):
    type: Literal["all", "list", "range"] = "all"
    values: list[Scalar] = Field(default_factory=list, max_length=1000)
    min: float | None = None
    max: float | None = None
    step: float | None = None


class Parameter(Strict):
    id: str
    name: str = Field(min_length=1, max_length=128)
    data_type: DataType
    default: Scalar = None
    allowed: AllowedValues = Field(default_factory=AllowedValues)
    control: Literal["dropdown", "slider", "input"] = "dropdown"

    _k = field_validator("id")(_key)


class DashboardFilter(Strict):
    """An interactive filter control. Viewer selections only narrow results."""

    id: str
    label: str = Field(default="", max_length=200)
    dataset_id: str
    field: str  # semantic field id
    control: Literal["multi_select", "single_select", "range", "date_range", "relative_date", "search"] = "multi_select"
    stage: Literal["context", "dimension"] = "dimension"
    default: list[Scalar] = Field(default_factory=list, max_length=1000)
    applies_to: list[str] | Literal["all"] = "all"

    _k = field_validator("id")(_key)


class Action(Strict):
    id: str
    name: str = Field(default="", max_length=200)
    type: Literal["filter", "highlight", "navigate"]
    source: str  # worksheet id
    targets: list[str] | Literal["all"] = "all"
    fields: list[str] = Field(default_factory=list)  # semantic field ids to pass; empty = all source dimensions
    target_dashboard: str | None = None  # navigate
    clear: Literal["show_all", "keep"] = "show_all"

    _k = field_validator("id")(_key)

    @model_validator(mode="after")
    def _nav(self) -> Action:
        if self.type == "navigate" and not self.target_dashboard:
            raise ValueError("Navigate actions need target_dashboard")
        return self


class LayoutItem(Strict):
    id: str
    kind: Literal["worksheet", "filter", "parameter", "text"]
    ref: str | None = None  # worksheet / filter / parameter id
    text: str | None = Field(default=None, max_length=5000)
    x: int = Field(ge=0, le=23)
    y: int = Field(ge=0, le=1000)
    w: int = Field(ge=1, le=24)
    h: int = Field(ge=1, le=60)

    _k = field_validator("id")(_key)


class Layout(Strict):
    columns: Literal[12, 24] = 12
    row_height: int = Field(default=36, ge=16, le=200)
    items: list[LayoutItem] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _fits(self) -> Layout:
        for item in self.items:
            if item.x + item.w > self.columns:
                raise ValueError(f"Layout item '{item.id}' overflows the {self.columns}-column grid")
        return self


class Theme(Strict):
    mode: Literal["system", "light", "dark"] = "system"
    palette: str = "intelliq"
    density: Literal["comfortable", "compact"] = "comfortable"


class Provenance(Strict):
    source: Literal["native", "tableau"] = "native"
    workbook_name: str | None = None
    workbook_sha256: str | None = None
    import_id: str | None = None
    imported_at: str | None = None
    # component id → Tableau reference (e.g. "worksheet:Sales by Region")
    refs: dict[str, str] = Field(default_factory=dict)


class Dashboard(Strict):
    spec_version: Literal[1] = SPEC_VERSION
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    worksheets: list[Worksheet] = Field(default_factory=list, max_length=100)
    filters: list[DashboardFilter] = Field(default_factory=list, max_length=100)
    parameters: list[Parameter] = Field(default_factory=list, max_length=100)
    actions: list[Action] = Field(default_factory=list, max_length=100)
    layout: Layout = Field(default_factory=Layout)
    theme: Theme = Field(default_factory=Theme)
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def _references(self) -> Dashboard:
        ws_ids = [w.id for w in self.worksheets]
        if len(ws_ids) != len(set(ws_ids)):
            raise ValueError("Worksheet ids must be unique")
        flt_ids = {f.id for f in self.filters}
        par_ids = {p.id for p in self.parameters}
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("Parameter names must be unique")
        for item in self.layout.items:
            valid = {"worksheet": set(ws_ids), "filter": flt_ids, "parameter": par_ids}.get(item.kind)
            if valid is not None and item.ref not in valid:
                raise ValueError(f"Layout item '{item.id}' references unknown {item.kind} '{item.ref}'")
        for a in self.actions:
            if a.source not in ws_ids:
                raise ValueError(f"Action '{a.id}' has unknown source worksheet '{a.source}'")
            if a.targets != "all":
                for t in a.targets:
                    if t not in ws_ids:
                        raise ValueError(f"Action '{a.id}' targets unknown worksheet '{t}'")
        for f in self.filters:
            if f.applies_to != "all":
                for t in f.applies_to:
                    if t not in ws_ids:
                        raise ValueError(f"Filter '{f.id}' applies to unknown worksheet '{t}'")
        return self

    def worksheet(self, wid: str) -> Worksheet:
        for w in self.worksheets:
            if w.id == wid:
                return w
        raise KeyError(wid)


def json_schema() -> dict[str, Any]:
    schema = Dashboard.model_json_schema()
    schema["$id"] = "https://intelliq.dev/schema/dashboard.v1.json"
    schema["title"] = "Intelliq Dashboard Specification v1"
    return schema
