"""Compile a worksheet plus viewer runtime state into a QueryRequest.

Runtime state is the only input a viewer controls: dashboard filter
selections, parameter values, cross-filter selections and drill path. All of
it is validated here against the stored specification; viewers cannot add
fields, formulas or remove authored filters.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..query.models import FieldUse, Filter, ParameterValue, QueryRequest, RelativeDate, Scalar, Sort
from ..query.planner import PlanError, coerce_value
from ..semantic.model import Dataset
from .dashboard import Dashboard, Parameter, Worksheet


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilterState(Strict):
    values: list[Scalar] = Field(default_factory=list, max_length=10_000)
    relative: RelativeDate | None = None


class Selection(Strict):
    """Marks selected in a source worksheet: each tuple maps viz field key → value."""

    action_id: str
    tuples: list[dict[str, Scalar]] = Field(default_factory=list, max_length=1000)


class RuntimeState(Strict):
    filters: dict[str, FilterState] = Field(default_factory=dict)
    parameters: dict[str, Scalar] = Field(default_factory=dict)
    selections: list[Selection] = Field(default_factory=list, max_length=50)
    drill: list[Scalar] = Field(default_factory=list, max_length=10)


def parameter_value(p: Parameter, supplied: Any, has_value: bool) -> Any:
    value = supplied if has_value else p.default
    value = coerce_value(value, p.data_type)
    if value is None:
        return None
    if p.allowed.type == "list":
        allowed = [coerce_value(v, p.data_type) for v in p.allowed.values]
        if value not in allowed:
            raise PlanError(f"Value for parameter '{p.name}' is not one of the allowed values", "invalid_parameter")
    if p.allowed.type == "range":
        if p.allowed.min is not None and value < p.allowed.min:
            raise PlanError(f"Parameter '{p.name}' is below its minimum", "invalid_parameter")
        if p.allowed.max is not None and value > p.allowed.max:
            raise PlanError(f"Parameter '{p.name}' is above its maximum", "invalid_parameter")
    return value


def _applies(target: list[str] | str, ws_id: str) -> bool:
    return target == "all" or ws_id in target


def compile_worksheet(
    dashboard: Dashboard, ws: Worksheet, state: RuntimeState, dataset: Dataset
) -> tuple[QueryRequest, dict[str, str]]:
    # Parameters ---------------------------------------------------------------
    params = [
        ParameterValue(name=p.name, data_type=p.data_type,
                       value=parameter_value(p, state.parameters.get(p.id), p.id in state.parameters))
        for p in dashboard.parameters
    ]

    # Fields -----------------------------------------------------------------
    dims = [f for f in ws.viz.fields if f.role == "dimension"]
    measures = [f for f in ws.viz.fields if f.role == "measure"]
    key_map = {f.key: f"d{i}" for i, f in enumerate(dims)}
    key_map.update({f.key: f"m{i}" for i, f in enumerate(measures)})
    dim_uses = [f.use for f in dims]
    filters: list[Filter] = [wf.filter for wf in ws.filters]

    # Drill-down: replace the hierarchy's dimension with the next level and
    # filter the levels above it to the drilled path.
    if ws.drill_hierarchy and state.drill:
        hierarchy = next((h for h in dataset.hierarchies if h.id == ws.drill_hierarchy), None)
        if hierarchy is None:
            raise PlanError("Worksheet references an unknown hierarchy")
        idx = next((i for i, u in enumerate(dim_uses) if u.field in hierarchy.levels), None)
        if idx is not None:
            start = hierarchy.levels.index(dim_uses[idx].field)  # type: ignore[arg-type]
            depth = min(len(state.drill), len(hierarchy.levels) - 1 - start)
            for k in range(depth):
                filters.append(Filter(target=FieldUse(field=hierarchy.levels[start + k]), op="in",
                                      values=[state.drill[k]], stage="dimension"))
            dim_uses[idx] = FieldUse(field=hierarchy.levels[start + depth])

    # Dashboard filters --------------------------------------------------------
    for df in dashboard.filters:
        if not _applies(df.applies_to, ws.id) or df.dataset_id != ws.dataset_id:
            continue
        fs = state.filters.get(df.id)
        values = fs.values if fs is not None else df.default
        target = FieldUse(field=df.field)
        if df.control in ("multi_select", "single_select"):
            if not values:
                continue  # "(All)"
            if df.control == "single_select" and len(values) > 1:
                raise PlanError(f"Filter '{df.label or df.id}' accepts a single value", "invalid_filter")
            filters.append(Filter(target=target, op="in", values=values, stage=df.stage))
        elif df.control in ("range", "date_range"):
            if len(values) != 2 or (values[0] is None and values[1] is None):
                continue
            filters.append(Filter(target=target, op="between", values=values, stage=df.stage))
        elif df.control == "relative_date":
            if fs is None or fs.relative is None:
                continue
            filters.append(Filter(target=target, op="relative_date", relative=fs.relative, stage=df.stage))
        elif df.control == "search":
            if not values or not values[0]:
                continue
            filters.append(Filter(target=target, op="contains", values=values[:1], stage=df.stage))

    # Filter actions (cross-filtering) ---------------------------------------------
    actions = {a.id: a for a in dashboard.actions}
    for sel in state.selections:
        action = actions.get(sel.action_id)
        if action is None:
            raise PlanError(f"Unknown action '{sel.action_id}'", "invalid_selection")
        if action.type != "filter" or action.source == ws.id or not _applies(action.targets, ws.id):
            continue
        source = dashboard.worksheet(action.source)
        if source.dataset_id != ws.dataset_id or not sel.tuples:
            continue
        source_dims = {f.key: f.use for f in source.viz.fields if f.role == "dimension"}
        for key, use in source_dims.items():
            if action.fields and use.field not in action.fields:
                continue
            if use.field is None:
                continue  # ad-hoc formula dimensions are not passed between worksheets
            values = list(dict.fromkeys(t[key] for t in sel.tuples if key in t))
            if not values:
                continue
            # Exact for single-field or single-mark selections; for multi-field,
            # multi-mark selections this is a superset (see docs/compatibility).
            filters.append(Filter(
                target=FieldUse(field=use.field, time_unit=use.time_unit, date_part=use.date_part),
                op="in", values=values, stage="dimension",
            ))

    sort = [Sort(column=key_map[s.key], direction=s.direction) for s in ws.viz.sort]
    req = QueryRequest(
        dataset_id=ws.dataset_id,
        dimensions=dim_uses,
        measures=[f.use for f in measures],
        filters=filters,
        sort=sort,
        limit=ws.viz.limit,
        parameters=params,
    )
    return req, key_map
