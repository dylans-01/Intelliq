"""Convert a parsed Tableau workbook into Intelliq dashboards and semantic fields.

Every element of the workbook produces a report item with one of four
statuses — never a silent omission:

- ``supported``   reproduced as-is.
- ``adjusted``    reproduced with a documented approximation.
- ``manual``      needs a person to map or rebuild it (e.g. unmapped field).
- ``unsupported`` cannot currently be reproduced (e.g. map marks, URL actions).

Calculated fields are parsed into expression trees, their field and parameter
references are rewritten to the target dataset, and the result is
type-checked against that dataset before being proposed as a new field.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..calc import ast as A
from ..calc import functions as F
from ..calc.errors import CalcError
from ..calc.parser import parse
from ..semantic.model import Dataset, SemanticField
from ..semantic.resolver import SemanticModel
from ..spec.dashboard import Dashboard
from .twb_parser import parse_instance

SUPPORTED, ADJUSTED, MANUAL, UNSUPPORTED = "supported", "adjusted", "manual", "unsupported"

AGG_DERIVATIONS = {
    "sum": "sum", "avg": "avg", "min": "min", "max": "max", "count": "count", "countd": "countd",
    "median": "median", "attr": "attr", "stdev": "stdev", "var": "var",
}  # fmt: skip
# Short codes used in column-instance names when no derivation attribute is present.
DERIVATION_CODE_NAMES = {
    "cnt": "count", "ctd": "countd", "med": "median", "usr": "user", "yr": "year", "qr": "quarter", "mn": "month",
    "dy": "day", "wk": "week", "wd": "weekday", "tyr": "year-trunc", "tqr": "quarter-trunc", "tmn": "month-trunc",
    "twk": "week-trunc", "tdy": "day-trunc",
}  # fmt: skip
DATE_PARTS = {"year": "year", "quarter": "quarter", "month": "month", "day": "day", "week": "week", "weekday": "weekday"}
DATE_TRUNCS = {"year-trunc": "year", "quarter-trunc": "quarter", "month-trunc": "month", "week-trunc": "week", "day-trunc": "day"}
TYPE_MAP = {"real": "real", "integer": "integer", "string": "string", "date": "date", "datetime": "datetime", "boolean": "boolean"}
QUICK_TABLE_CALCS = {"PctTotal": "percent_of_total", "RunningTotal": "running_total", "Difference": "difference",
                     "PctDiff": "percent_difference", "Rank": "rank"}
GRID_COLUMNS = 12
PX_PER_ROW = 44


@dataclass
class Report:
    items: list[dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, ref: str, status: str, message: str, **extra: Any) -> None:
        self.items.append({"kind": kind, "ref": ref, "status": status, "message": message, **extra})

    def to_dict(self) -> dict[str, Any]:
        summary = {s: 0 for s in (SUPPORTED, ADJUSTED, MANUAL, UNSUPPORTED)}
        for item in self.items:
            summary[item["status"]] += 1
        total = sum(summary.values()) or 1
        summary["fidelity"] = round((summary[SUPPORTED] + 0.5 * summary[ADJUSTED]) / total, 3)
        return {"summary": summary, "items": self.items}


def slug(text: str, used: set[str] | None = None) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "field"
    if not s[0].isalpha():
        s = "f_" + s
    s = s[:56]
    if used is not None:
        base, i = s, 2
        while s in used:
            s = f"{base}_{i}"
            i += 1
        used.add(s)
    return s


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


# =============================================================================
# Source field catalogue
@dataclass
class SourceField:
    name: str  # Tableau internal name, e.g. "Sales" or "Calculation_1234"
    caption: str
    datatype: str | None
    role: str | None
    formula: str | None = None
    remote_name: str | None = None
    unsupported: str | None = None
    hidden: bool = False


def source_fields(ds: dict[str, Any]) -> dict[str, SourceField]:
    out: dict[str, SourceField] = {}
    for md in ds.get("metadata", []):
        name = md["local_name"]
        out[name] = SourceField(name, name, md.get("local_type"), None, remote_name=md.get("remote_name"))
    for col in ds.get("columns", []):
        name = col["name"]
        existing = out.get(name)
        out[name] = SourceField(
            name=name,
            caption=col.get("caption") or (existing.caption if existing else name),
            datatype=col.get("datatype") or (existing.datatype if existing else None),
            role=col.get("role"),
            formula=col.get("formula"),
            remote_name=existing.remote_name if existing else None,
            unsupported=col.get("unsupported"),
            hidden=col.get("hidden", False),
        )
    return out


def suggest_mapping(workbook: dict[str, Any], datasets: list[Dataset]) -> dict[str, Any]:
    """Pick the dataset with the best raw-field overlap for each data source and
    auto-match fields by caption / remote name."""

    mapping: dict[str, Any] = {"datasources": {}, "fields": {}}
    for ds in workbook["datasources"]:
        raw = [f for f in source_fields(ds).values() if not f.formula]
        best, best_score = None, 0
        for target in datasets:
            names = {_norm(f.name) for f in target.fields} | {_norm(f.id) for f in target.fields}
            score = sum(1 for f in raw if _norm(f.caption) in names or _norm(f.remote_name or "") in names)
            if score > best_score:
                best, best_score = target, score
        if best is not None:
            mapping["datasources"][ds["name"]] = best.id
            mapping["fields"][ds["name"]] = auto_field_map(ds, best)
    return mapping


def auto_field_map(ds: dict[str, Any], target: Dataset) -> dict[str, str]:
    by_norm: dict[str, str] = {}
    for f in target.fields:
        by_norm.setdefault(_norm(f.name), f.id)
        by_norm.setdefault(_norm(f.id), f.id)
    out = {}
    for f in source_fields(ds).values():
        if f.formula:
            continue
        for candidate in (f.caption, f.remote_name or "", f.name):
            hit = by_norm.get(_norm(candidate)) if candidate else None
            if hit:
                out[f.name] = hit
                break
    return out


# =============================================================================
@dataclass
class ConversionResult:
    dashboards: list[dict[str, Any]]
    new_fields: dict[str, list[dict[str, Any]]]
    report: dict[str, Any]


class Converter:
    def __init__(self, workbook: dict[str, Any], mapping: dict[str, Any], datasets: dict[str, Dataset], *,
                 import_id: str | None = None, workbook_name: str | None = None, sha256: str | None = None,
                 imported_at: str | None = None):
        self.wb = workbook
        self.mapping = mapping
        self.datasets = datasets
        self.report = Report()
        self.provenance = {"source": "tableau", "workbook_name": workbook_name, "workbook_sha256": sha256,
                           "import_id": import_id, "imported_at": imported_at}
        self.ds_by_name = {d["name"]: d for d in workbook["datasources"]}
        self.params = {p["name"]: p for p in workbook["parameters"]}
        # Per Tableau data source: internal field name → ("field", target id) | ("formula", text)
        self.resolved: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
        self.new_fields: dict[str, list[SemanticField]] = defaultdict(list)
        self.used_params: set[str] = set()
        self._refs: dict[str, str] = {}  # provenance refs for the dashboard being converted

    # ------------------------------------------------------------------ main
    def convert(self) -> ConversionResult:
        for asset in self.wb.get("assets", []):
            if asset["kind"] == "extract":
                self.report.add("asset", asset["path"], ADJUSTED,
                                "Packaged extract data is not imported; dashboards query the mapped live dataset instead.")
            elif asset["kind"] == "image":
                self.report.add("asset", asset["path"], MANUAL, "Image assets are inventoried but not yet placed on dashboards.")
        for item in self.wb.get("unrecognised", []):
            self.report.add(item["kind"], item.get("name") or item["kind"], UNSUPPORTED, item["message"])

        for ds in self.wb["datasources"]:
            self._map_datasource(ds)

        dashboards: list[dict[str, Any]] = []
        placed: set[str] = set()
        for db in self.wb["dashboards"]:
            spec = self._dashboard(db, placed)
            if spec is not None:
                dashboards.append(spec)
        orphans = [ws for ws in self.wb["worksheets"] if ws["name"] not in placed]
        if not self.wb["dashboards"]:
            for ws in orphans:
                spec = self._standalone(ws)
                if spec is not None:
                    dashboards.append(spec)
        else:
            for ws in orphans:
                self.report.add("worksheet", ws["name"], ADJUSTED,
                                "Worksheet is not placed on any dashboard and was not imported as its own dashboard.")

        for p in self.wb["parameters"]:
            if p["name"] not in self.used_params:
                self.report.add("parameter", p["caption"], SUPPORTED, "Parameter is not referenced by imported content.")

        new_fields = {did: [f.model_dump(exclude_none=True) for f in fields] for did, fields in self.new_fields.items()}
        return ConversionResult(dashboards, new_fields, self.report.to_dict())

    # ---------------------------------------------------------- data sources
    def _map_datasource(self, ds: dict[str, Any]) -> None:
        name = ds["name"]
        caption = ds["caption"]
        conn = ds.get("connection") or {}
        target_id = self.mapping.get("datasources", {}).get(name)
        detail = f"Connection class '{conn.get('class')}'" if conn.get("class") else "No connection details"
        if ds.get("custom_sql"):
            self.report.add("datasource", caption, MANUAL,
                            "Custom SQL relations are not executed from workbooks; recreate them as an administrator-defined dataset.",
                            sql=[c["sql"][:2000] for c in ds["custom_sql"]])
        if target_id is None or target_id not in self.datasets:
            self.report.add("datasource", caption, MANUAL, f"{detail}. Map this data source to an Intelliq dataset.")
            return
        target = self.datasets[target_id]
        self.report.add("datasource", caption, SUPPORTED, f"{detail}. Mapped to dataset '{target.name}'.", target=target_id)

        fmap: dict[str, str] = self.mapping.get("fields", {}).get(name, {})
        fields = source_fields(ds)
        for f in fields.values():
            if f.formula is None and f.unsupported is None:
                tid = fmap.get(f.name)
                if tid and any(tf.id == tid for tf in target.fields):
                    self.resolved[name][f.name] = ("field", tid)
                elif not f.hidden:
                    self.report.add("field", f"{caption} › {f.caption}", MANUAL,
                                    "No matching field in the target dataset; map it manually.", source=f.name)
        self._translate_calcs(ds, fields, target)

    def _translate_calcs(self, ds: dict[str, Any], fields: dict[str, SourceField], target: Dataset) -> None:
        name, caption = ds["name"], ds["caption"]
        calcs = {f.name: f for f in fields.values() if f.formula is not None or f.unsupported}
        trees: dict[str, A.Node] = {}
        for f in calcs.values():
            ref = f"{caption} › {f.caption}"
            if f.unsupported:
                self.report.add("calculation", ref, UNSUPPORTED, f.unsupported, source=f.name)
                continue
            try:
                trees[f.name] = parse(f.formula or "")
            except CalcError as e:
                self.report.add("calculation", ref, UNSUPPORTED, f"Formula could not be parsed: {e.message}",
                                source=f.name, formula=f.formula)
                continue
            unknown = sorted({n.name for n in trees[f.name].walk() if isinstance(n, A.Func) and F.get(n.name) is None})
            if unknown:
                self.report.add("calculation", ref, UNSUPPORTED, f"Uses unsupported function(s): {', '.join(unknown)}",
                                source=f.name, formula=f.formula)
                del trees[f.name]

        # Resolve in dependency order so calcs can reference other calcs.
        pending = dict(trees)
        model_fields = list(target.fields)
        used_ids = {tf.id for tf in target.fields}
        used_names = {tf.name.lower() for tf in target.fields}
        progress = True
        while pending and progress:
            progress = False
            for internal, tree in list(pending.items()):
                deps = [n.name for n in tree.walk() if isinstance(n, A.FieldRef)]
                if any(d in pending and d != internal for d in deps):
                    continue
                del pending[internal]
                progress = True
                f = calcs[internal]
                ref = f"{caption} › {f.caption}"
                missing = [d for d in deps if d not in self.resolved[name]]
                if missing:
                    labels = ", ".join(fields[d].caption if d in fields else d for d in missing)
                    self.report.add("calculation", ref, MANUAL, f"References unmapped field(s): {labels}",
                                    source=internal, formula=f.formula)
                    continue
                params_used: set[str] = set()

                def rewrite(n: A.Node, params_used: set[str] = params_used) -> A.Node:
                    if isinstance(n, A.FieldRef):
                        kind, value = self.resolved[name][n.name]
                        if kind == "field":
                            return A.FieldRef(next(tf.name for tf in model_fields if tf.id == value), n.span)
                        return parse(value)  # parameter-dependent calc: inline its formula
                    if isinstance(n, A.ParamRef):
                        p = self.params.get(n.name)
                        if p is None:
                            raise CalcError(f"Unknown parameter '{n.name}'")
                        params_used.add(p["name"])
                        return A.ParamRef(p["caption"], n.span)
                    return n

                try:
                    translated = A.transform(tree, rewrite)
                except CalcError as e:
                    self.report.add("calculation", ref, MANUAL, e.message, source=internal, formula=f.formula)
                    continue
                formula = A.to_formula(translated)
                param_types = {p["caption"]: TYPE_MAP.get(p["datatype"], "string") for p in self.params.values()}
                candidate = Dataset.model_validate({**target.model_dump(by_alias=True), "fields": [
                    mf.model_dump(by_alias=True) for mf in model_fields]})
                try:
                    _, typed = SemanticModel(candidate, param_types).check_formula(formula)
                except CalcError as e:
                    self.report.add("calculation", ref, MANUAL, f"Translated formula does not validate: {e.message}",
                                    source=internal, formula=f.formula, translated=formula)
                    continue

                inline_params = bool(params_used) or any(
                    self.resolved[name].get(d, ("", ""))[0] == "formula" for d in deps
                )
                if inline_params:
                    # Datasets stay parameter-free: keep this calc as a worksheet-level formula.
                    self.used_params |= params_used
                    self.resolved[name][internal] = ("formula", formula)
                    self.report.add("calculation", ref, ADJUSTED,
                                    "Uses parameters; imported as a worksheet-level calculation rather than a dataset field.",
                                    source=internal, formula=f.formula, translated=formula)
                    continue

                existing = next((tf for tf in model_fields if tf.name.lower() == f.caption.lower()), None)
                if existing is not None and existing.formula is not None and _same_formula(existing.formula, formula):
                    self.resolved[name][internal] = ("field", existing.id)
                    self.report.add("calculation", ref, SUPPORTED,
                                    f"Matches the existing canonical definition [{existing.name}].",
                                    source=internal, formula=f.formula, target=existing.id)
                    continue
                new_name = f.caption
                status, note = SUPPORTED, "Translated and validated."
                if new_name.lower() in used_names:
                    new_name = f"{f.caption} (Tableau)"
                    status, note = ADJUSTED, f"A different field named [{f.caption}] already exists; imported as [{new_name}]."
                level = typed.level
                role = "measure" if level in ("agg", "table") or (f.role == "measure") else "dimension"
                dtype = typed.data_type if typed.data_type != "null" else TYPE_MAP.get(f.datatype or "", "string")
                if level == "table":
                    status = ADJUSTED
                    note += " Table calculation addressing defaults to 'Table (across)'; verify partitioning."
                new = SemanticField(
                    id=slug(f.caption, used_ids), name=new_name, role=role, data_type=dtype,  # type: ignore[arg-type]
                    formula=formula, description=f"Imported from Tableau calculation [{f.caption}].",
                    hidden=f.hidden,
                )
                used_names.add(new_name.lower())
                model_fields.append(new)
                self.new_fields[target.id].append(new)
                self.resolved[name][internal] = ("field", new.id)
                self.report.add("calculation", ref, status, note, source=internal, formula=f.formula,
                                translated=formula, target=new.id)

        for internal in pending:
            f = calcs[internal]
            self.report.add("calculation", f"{caption} › {f.caption}", UNSUPPORTED,
                            "Circular reference between calculated fields.", source=internal)

    def _target_dataset(self, ds_name: str) -> Dataset | None:
        did = self.mapping.get("datasources", {}).get(ds_name)
        return self.datasets.get(did) if did else None

    # ----------------------------------------------------------- worksheets
    def _field_use(self, ws: dict[str, Any], ref: dict[str, str], where: str) -> tuple[dict | None, str | None]:
        """Return (viz field dict without key/channel, role) or (None, None) after reporting why."""

        ds_name, instance = ref["datasource"], ref["instance"]
        label = f"{ws['name']} › {where}"
        if ds_name == "Parameters":
            return None, None
        if ws["datasources"] and ds_name != ws["datasources"][0]:
            self.report.add("field", label, UNSUPPORTED, "Data blending (secondary data source) is not supported.",
                            source=instance)
            return None, None
        if instance.startswith(":"):
            self.report.add("field", label, MANUAL,
                            "Measure Names / Measure Values are not supported; add the measures individually.", source=instance)
            return None, None
        info = ws["instances"].get(f"{ds_name}::{instance}")
        parsed = parse_instance(instance)
        column = info["column"] if info else (parsed["field"] if parsed else None)
        derivation = (info.get("derivation") if info else None) or (parsed["derivation_code"] if parsed else "none")
        derivation = (derivation or "none").lower()
        derivation = DERIVATION_CODE_NAMES.get(derivation, derivation)
        resolved = self.resolved.get(ds_name, {}).get(column or "")
        if resolved is None:
            self.report.add("field", label, MANUAL, f"Field [{column}] is not mapped to the target dataset.", source=instance)
            return None, None
        kind, value = resolved
        use: dict[str, Any] = {"field": value} if kind == "field" else {"formula": value}
        target = self._target_dataset(ds_name)
        target_field = next((f for f in target.fields if f.id == value), None) if (target and kind == "field") else None

        if derivation in ("none", ""):
            role = "dimension"
            if target_field is not None and target_field.role == "measure":
                if target_field.formula and _is_aggregate(target_field.formula):
                    role = "measure"
                else:
                    self.report.add("field", label, ADJUSTED, "Disaggregated measure placed as a dimension.", source=instance)
        elif derivation in AGG_DERIVATIONS:
            role, use["aggregation"] = "measure", AGG_DERIVATIONS[derivation]
        elif derivation == "user":
            role = "measure"
        elif derivation in DATE_PARTS:
            role, use["date_part"] = "dimension", DATE_PARTS[derivation]
        elif derivation in DATE_TRUNCS:
            role, use["time_unit"] = "dimension", DATE_TRUNCS[derivation]
        else:
            self.report.add("field", label, UNSUPPORTED, f"Field derivation '{derivation}' is not supported.", source=instance)
            return None, None

        tc = info.get("table_calc") if info else None
        if tc:
            qtc = QUICK_TABLE_CALCS.get(tc.get("type") or "")
            if qtc is None:
                self.report.add("table_calc", label, UNSUPPORTED, f"Quick table calculation '{tc.get('type')}' is not supported.",
                                source=instance)
            else:
                use["table_calc"] = {"type": qtc}
                ordering = tc.get("ordering_type") or "Table"
                if ordering not in ("Table", "Rows", "Columns"):
                    self.report.add("table_calc", label, ADJUSTED,
                                    f"Addressing '{ordering}' approximated as 'Table'; verify partitioning.", source=instance)
                else:
                    self.report.add("table_calc", label, SUPPORTED, f"Converted to quick table calculation '{qtc}'.",
                                    source=instance)
        return use, role

    def _viz(self, ws: dict[str, Any], skip_filter_fields: set[tuple[str, str]]) -> tuple[dict | None, str | None, list]:
        ref_label = ws["name"]
        if not ws["datasources"]:
            self.report.add("worksheet", ref_label, UNSUPPORTED, "Worksheet has no data source.")
            return None, None, []
        ds_name = ws["datasources"][0]
        target = self._target_dataset(ds_name)
        if target is None:
            self.report.add("worksheet", ref_label, MANUAL, "Worksheet's data source is not mapped to a dataset.")
            return None, None, []

        fields: list[dict[str, Any]] = []
        keys: dict[str, str] = {}  # instance → key

        def place(ref: dict[str, str], channel: str, where: str) -> dict | None:
            use, role = self._field_use(ws, ref, where)
            if use is None:
                return None
            key = keys.get(ref["instance"])
            if key is not None:
                return next(f for f in fields if f["key"] == key)
            key = f"f{len(fields) + 1}"
            keys[ref["instance"]] = key
            entry = {"key": key, "role": role, "channel": channel, "use": use}
            fields.append(entry)
            return entry

        x = [e for r in ws["cols"] if (e := place(r, "x", "Columns")) is not None]
        y = [e for r in ws["rows"] if (e := place(r, "y", "Rows")) is not None]
        enc_channels = {"color": "color", "size": "size", "text": "label", "label": "label", "tooltip": "tooltip",
                        "lod": "detail", "wedge-size": "size", "shape": "detail"}
        for enc in ws["encodings"]:
            channel = enc_channels.get(enc["channel"])
            if channel is None:
                self.report.add("encoding", f"{ws['name']} › {enc['channel']}", UNSUPPORTED,
                                f"Encoding '{enc['channel']}' is not supported.")
                continue
            place(enc, channel, enc["channel"].capitalize())

        mark = (ws.get("mark") or "Automatic").lower()
        viz_type, options, note = self._infer_type(mark, x, y, fields)
        status = SUPPORTED if note is None else ADJUSTED
        if viz_type == "table" and mark in ("map", "polygon", "multipolygon", "ganttbar"):
            status = UNSUPPORTED
        self.report.add("worksheet", ref_label, status,
                        note or f"Converted to a {viz_type} chart.", mark=ws.get("mark"), viz_type=viz_type)

        # Re-assign channels to match the chosen renderer.
        dims = [f for f in fields if f["role"] == "dimension"]
        measures = [f for f in fields if f["role"] == "measure"]
        if viz_type == "table":
            for f in fields:
                f["channel"] = "column"
        elif viz_type == "kpi":
            for f in measures:
                f["channel"] = "value"
        elif viz_type in ("bar", "line", "area"):
            primary = [f for f in dims if f["channel"] in ("x", "y")]
            for i, f in enumerate(primary):
                f["channel"] = "x" if i == 0 else "detail"
            for f in measures:
                if f["channel"] in ("x", "y", "size"):
                    f["channel"] = "y"
            if len(primary) > 1:
                self.report.add("worksheet", ref_label, ADJUSTED, "Nested dimension headers were flattened.")
        elif viz_type == "pie":
            for f in measures:
                f["channel"] = "y"
            for f in dims:
                if f["channel"] in ("x", "y", "detail"):
                    f["channel"] = "color"

        sort = []
        for s in ws.get("sorts", []):
            if s.get("class") == "computed" and s.get("using") and s["using"]["instance"] in keys:
                sort.append({"key": keys[s["using"]["instance"]], "direction": "desc" if s["direction"] == "DESC" else "asc"})
            elif s.get("class") == "manual":
                self.report.add("sort", ref_label, UNSUPPORTED, "Manual sort orders are not supported.")
            elif s.get("column") and s["column"]["instance"] in keys and s.get("class") in ("natural", "alphabetic"):
                sort.append({"key": keys[s["column"]["instance"]], "direction": "desc" if s["direction"] == "DESC" else "asc"})
            else:
                self.report.add("sort", ref_label, ADJUSTED, "Sort could not be reproduced exactly; default ordering applies.")

        filters = []
        for flt in ws.get("filters", []):
            converted = self._filter(ws, flt, skip_filter_fields)
            if converted is not None:
                filters.append({"filter": converted})

        if not fields:
            self.report.add("worksheet", ref_label, MANUAL, "No fields could be converted.")
            return None, None, []
        viz = {"type": viz_type, "fields": fields, "sort": sort, "options": options}
        return viz, target.id, filters

    def _infer_type(self, mark: str, x: list[dict], y: list[dict], fields: list[dict]) -> tuple[str, dict, str | None]:
        mx = [f for f in x if f["role"] == "measure"]
        my = [f for f in y if f["role"] == "measure"]
        dims = [f for f in fields if f["role"] == "dimension"]
        on_axes_dims = [f for f in x + y if f["role"] == "dimension"]
        measures = [f for f in fields if f["role"] == "measure"]
        has_color_dim = any(f["channel"] == "color" and f["role"] == "dimension" for f in fields)
        temporal = any("time_unit" in f["use"] or "date_part" in f["use"] for f in on_axes_dims)
        horizontal = bool(mx) and not my
        opts: dict[str, Any] = {}
        if horizontal:
            opts["orientation"] = "horizontal"

        if mark in ("map", "polygon", "multipolygon", "ganttbar"):
            return "table", {}, f"Mark type '{mark}' is not supported; rendered as a table."
        if mark == "pie":
            return "pie", {"donut": False}, None
        if mark == "bar":
            if has_color_dim:
                opts["stacked"] = True
            return "bar", opts, None
        if mark == "line":
            return "line", opts, None
        if mark == "area":
            return "area", {**opts, "stacked": has_color_dim}, None
        if mark in ("circle", "shape") and mx and my:
            return "scatter", {}, None
        if mark == "square" and len(on_axes_dims) >= 2 and any(f["channel"] == "color" and f["role"] == "measure" for f in fields):
            return "heatmap", {}, None
        if mark == "text" and not mx and not my:
            if len(measures) == 1 and not dims:
                return "kpi", {}, None
            return "table", {}, None
        # Automatic (or a mark used in a way we approximate)
        note = None if mark == "automatic" else f"Mark '{mark}' approximated by an automatic chart choice."
        if mx and my:
            return "scatter", {}, note
        if (mx or my) and on_axes_dims:
            if temporal and any("time_unit" in f["use"] for f in on_axes_dims):
                return "line", {}, note
            return "bar", {**opts, "stacked": has_color_dim}, note
        if measures and not dims:
            return ("kpi" if len(measures) == 1 else "table"), {}, note
        return "table", {}, note

    def _filter(self, ws: dict[str, Any], flt: dict[str, Any], skip: set[tuple[str, str]]) -> dict | None:
        col = flt.get("column")
        if not col:
            return None
        label = f"{ws['name']} › filter {col['instance']}"
        if (col["datasource"], col["instance"]) in skip:
            self.report.add("filter", label, SUPPORTED, "Promoted to an interactive dashboard filter.")
            return None
        use, role = self._field_use(ws, col, "Filter")
        if use is None:
            return None
        stage = "context" if flt.get("context") else ("measure" if role == "measure" else "dimension")
        cls = flt.get("class")
        if cls == "categorical":
            if flt.get("raw_function") == "top_n":
                self.report.add("filter", label, MANUAL, "Top N filters must be recreated (rank-by measure is not stored portably).")
                return None
            members = flt.get("members", [])
            if not members:
                self.report.add("filter", label, SUPPORTED, "Filter keeps all values; nothing to apply.")
                return None
            self.report.add("filter", label, SUPPORTED, f"{'Exclude' if flt.get('except') else 'Include'} {len(members)} value(s).")
            return {"target": use, "op": "not_in" if flt.get("except") else "in", "values": members, "stage": stage}
        if cls == "quantitative":
            if flt.get("min") is None and flt.get("max") is None:
                return None
            self.report.add("filter", label, SUPPORTED, "Range filter.")
            return {"target": use, "op": "between", "values": [flt.get("min"), flt.get("max")], "stage": stage}
        if cls == "relative-date":
            rel = flt.get("relative") or {}
            period = rel.get("period") or "month"
            first, last = rel.get("first"), rel.get("last")
            relative = None
            if first == 0 and last == 0:
                relative = {"mode": "current", "unit": period, "n": 1}
            elif first == -1 and last == -1:
                relative = {"mode": "previous", "unit": period, "n": 1}
            elif isinstance(first, int) and last == 0 and first < 0:
                relative = {"mode": "last_n", "unit": period, "n": 1 - first}
            if relative is None or period not in ("year", "quarter", "month", "week", "day"):
                self.report.add("filter", label, MANUAL, "Relative date range could not be mapped.")
                return None
            if rel.get("anchor"):
                relative["anchor"] = str(rel["anchor"])[:10]
            self.report.add("filter", label, SUPPORTED, f"Relative date filter ({relative['mode']} {period}).")
            return {"target": use, "op": "relative_date", "relative": relative, "stage": stage}
        self.report.add("filter", label, UNSUPPORTED, f"Filter class '{cls}' is not supported.")
        return None

    # ------------------------------------------------------------ dashboards
    def _worksheet_spec(self, ws: dict[str, Any], wid: str, skip: set[tuple[str, str]]) -> dict | None:
        viz, dataset_id, filters = self._viz(ws, skip)
        if viz is None:
            return None
        return {"id": wid, "title": ws.get("title") or ws["name"], "dataset_id": dataset_id, "viz": viz, "filters": filters}

    def _parameters_spec(self) -> list[dict[str, Any]]:
        out = []
        for name in sorted(self.used_params):
            p = self.params[name]
            dtype = TYPE_MAP.get(p["datatype"], "string")
            allowed: dict[str, Any] = {"type": "all"}
            control = "input"
            if p["domain"] == "list" and p["members"]:
                allowed, control = {"type": "list", "values": p["members"]}, "dropdown"
            elif p["domain"] == "range" and p.get("range"):
                r = p["range"]
                allowed = {"type": "range", "min": r.get("min"), "max": r.get("max"), "step": r.get("step")}
                control = "slider"
            out.append({"id": slug(p["caption"]), "name": p["caption"], "data_type": dtype, "default": p["value"],
                        "allowed": allowed, "control": control})
            self.report.add("parameter", p["caption"], SUPPORTED, f"Converted ({p['domain']} domain).")
        return out

    def _layout_box(self, zone: dict[str, Any], height_px: int) -> dict[str, int]:
        x = round(zone["x"] * GRID_COLUMNS / 100_000)
        w = max(1, round(zone["w"] * GRID_COLUMNS / 100_000))
        x = min(x, GRID_COLUMNS - 1)
        w = min(w, GRID_COLUMNS - x)
        y = round(zone["y"] / 100_000 * height_px / PX_PER_ROW)
        h = max(2, round(zone["h"] / 100_000 * height_px / PX_PER_ROW))
        return {"x": x, "y": y, "w": w, "h": h}

    def _dashboard(self, db: dict[str, Any], placed: set[str]) -> dict | None:
        ws_by_name = {w["name"]: w for w in self.wb["worksheets"]}
        height = db["size"]["height"]
        name = db["name"]
        # Fields shown as dashboard quick filters become interactive filters.
        promoted: set[tuple[str, str]] = set()
        for z in db["zones"]:
            if z["type"] == "filter" and z.get("param"):
                ref = _split_ref(z["param"])
                if ref:
                    promoted.add(ref)

        worksheets: list[dict] = []
        items: list[dict] = []
        filters: list[dict] = []
        used_ids: set[str] = set()
        ws_ids: dict[str, str] = {}
        for z in db["zones"]:
            ztype = z["type"]
            ref = f"{name} › zone {z.get('name') or ztype}"
            box = self._layout_box(z, height)
            if z["floating"]:
                self.report.add("zone", ref, ADJUSTED, "Floating zone converted to a tiled position.")
            if ztype in ("worksheet", None) and z.get("name") in ws_by_name:
                ws = ws_by_name[z["name"]]
                wid = slug(ws["name"], used_ids)
                spec = self._worksheet_spec(ws, wid, promoted)
                placed.add(ws["name"])
                if spec is None:
                    continue
                spec_ref = f"worksheet:{ws['name']}"
                self._refs[wid] = spec_ref
                worksheets.append(spec)
                ws_ids[ws["name"]] = wid
                items.append({"id": f"i_{wid}", "kind": "worksheet", "ref": wid, **box})
            elif ztype == "filter":
                flt = self._dashboard_filter(z, used_ids, ref)
                if flt is not None:
                    filters.append(flt)
                    items.append({"id": f"i_{flt['id']}", "kind": "filter", "ref": flt["id"], **box})
            elif ztype == "paramctrl":
                pref = _split_ref(z.get("param") or "")
                if pref and pref[0] == "Parameters" and pref[1] in self.params:
                    self.used_params.add(pref[1])
                    pid = slug(self.params[pref[1]]["caption"])
                    items.append({"id": f"i_p_{pid}", "kind": "parameter", "ref": pid, **box})
                    self.report.add("zone", ref, SUPPORTED, "Parameter control.")
                else:
                    self.report.add("zone", ref, MANUAL, "Parameter control references an unknown parameter.")
            elif ztype == "text":
                items.append({"id": f"i_t{len(items)}", "kind": "text", "text": z.get("text") or "", **box})
                self.report.add("zone", ref, ADJUSTED, "Text converted without rich formatting.")
            elif ztype in ("title", "layout-basic", "layout-flow", "empty", "blank"):
                continue
            elif ztype in ("color", "legend", "size", "shape"):
                self.report.add("zone", ref, ADJUSTED, "Legends are rendered inside each chart.")
            else:
                self.report.add("zone", ref, UNSUPPORTED, f"Dashboard object '{ztype}' is not supported.")

        # Filters apply to worksheets on the same dataset ("all using this data source").
        for flt in filters:
            flt["applies_to"] = [w["id"] for w in worksheets if w["dataset_id"] == flt["dataset_id"]] or "all"

        actions = self._actions(name, ws_ids)
        if not worksheets:
            self.report.add("dashboard", name, MANUAL, "No worksheets on this dashboard could be converted.")
            return None
        spec = {
            "spec_version": 1,
            "title": name,
            "description": f"Imported from Tableau workbook {self.provenance.get('workbook_name') or ''}".strip(),
            "worksheets": worksheets,
            "filters": filters,
            "parameters": self._parameters_spec(),
            "actions": actions,
            "layout": {"columns": GRID_COLUMNS, "row_height": 36, "items": _pack(items)},
            "provenance": {**self.provenance, "refs": {**self._refs, "dashboard": f"dashboard:{name}"}},
        }
        Dashboard.model_validate(spec)
        self.report.add("dashboard", name, SUPPORTED, f"Converted with {len(worksheets)} worksheet(s).")
        self._refs = {}
        return spec

    def _standalone(self, ws: dict[str, Any]) -> dict | None:
        wid = slug(ws["name"])
        spec_ws = self._worksheet_spec(ws, wid, set())
        if spec_ws is None:
            return None
        spec = {
            "spec_version": 1, "title": ws["name"], "worksheets": [spec_ws],
            "parameters": self._parameters_spec(),
            "layout": {"columns": GRID_COLUMNS, "row_height": 36,
                       "items": [{"id": f"i_{wid}", "kind": "worksheet", "ref": wid, "x": 0, "y": 0, "w": 12, "h": 10}]},
            "provenance": {**self.provenance, "refs": {wid: f"worksheet:{ws['name']}"}},
        }
        Dashboard.model_validate(spec)
        return spec

    def _dashboard_filter(self, zone: dict[str, Any], used_ids: set[str], ref: str) -> dict | None:
        split = _split_ref(zone.get("param") or "")
        if not split:
            self.report.add("zone", ref, MANUAL, "Filter control without a field reference.")
            return None
        ds_name, instance = split
        parsed = parse_instance(instance)
        column = parsed["field"] if parsed else instance
        resolved = self.resolved.get(ds_name, {}).get(column)
        if resolved is None or resolved[0] != "field":
            self.report.add("zone", ref, MANUAL, f"Filter field [{column}] is not mapped.")
            return None
        target = self._target_dataset(ds_name)
        tf = next(f for f in target.fields if f.id == resolved[1]) if target else None  # type: ignore[union-attr]
        mode = (zone.get("mode") or "").lower()
        control = {"checkdropdown": "multi_select", "checklist": "multi_select", "dropdown": "single_select",
                   "radiolist": "single_select", "typein": "search", "pattern": "search", "slider": "single_select"}.get(mode)
        if control is None:
            if tf is not None and tf.data_type in ("date", "datetime"):
                control = "date_range"
            elif tf is not None and tf.data_type in ("integer", "real"):
                control = "range"
            else:
                control = "multi_select"
        # Seed defaults from the worksheet filter that the control displays.
        defaults: list[Any] = []
        for ws in self.wb["worksheets"]:
            for f in ws.get("filters", []):
                if f.get("column") and (f["column"]["datasource"], f["column"]["instance"]) == split and f.get("members"):
                    defaults = list(f["members"]) if not f.get("except") else []
        self.report.add("zone", ref, SUPPORTED if mode in ("checkdropdown", "checklist", "dropdown", "radiolist", "typein", "")
                        else ADJUSTED, f"Interactive filter ({control}) on [{tf.name if tf else column}].")
        return {"id": slug(f"filter_{column}", used_ids), "label": tf.name if tf else column,
                "dataset_id": target.id if target else "", "field": resolved[1], "control": control, "default": defaults}

    def _actions(self, dashboard: str, ws_ids: dict[str, str]) -> list[dict]:
        out = []
        used: set[str] = set()
        for a in self.wb["actions"]:
            if a.get("source_dashboard") not in (dashboard, None):
                continue
            ref = f"{dashboard} › action {a['caption']}"
            cmd = (a.get("command") or "").lower()
            sources = [a["source_worksheet"]] if a.get("source_worksheet") else list(ws_ids)
            sources = [s for s in sources if s in ws_ids]
            if not sources:
                continue
            if cmd == "tsc:tsl-filter" or cmd == "tsc:brush":
                params = a.get("params") or {}
                if params.get("target") and params["target"] != dashboard:
                    self.report.add("action", ref, MANUAL, "Actions that filter another dashboard are not supported yet.")
                    continue
                excluded = set((params.get("exclude") or "").split(",")) if params.get("exclude") else set()
                targets = [wid for name, wid in ws_ids.items() if name not in excluded]
                for src in sources:
                    out.append({"id": slug(f"{a['name']}_{src}", used), "name": a["caption"],
                                "type": "filter" if cmd == "tsc:tsl-filter" else "highlight", "source": ws_ids[src],
                                "targets": [t for t in targets if t != ws_ids[src]] or "all"})
                status = SUPPORTED if a.get("activation") in ("on-select", None) else ADJUSTED
                msg = "Converted." if status == SUPPORTED else f"Activation '{a.get('activation')}' converted to on-select."
                self.report.add("action", ref, status, msg)
            elif cmd == "tsc:url":
                self.report.add("action", ref, UNSUPPORTED, "URL actions are not imported (external links are not trusted).")
            else:
                self.report.add("action", ref, UNSUPPORTED, f"Action type '{a.get('command')}' is not supported.")
        return out


def _split_ref(text: str) -> tuple[str, str] | None:
    m = re.match(r"^\[((?:[^\]]|\]\])+)\]\.\[((?:[^\]]|\]\])+)\]$", text.strip())
    if not m:
        return None
    return m.group(1).replace("]]", "]"), m.group(2).replace("]]", "]")


def _same_formula(a: str, b: str) -> bool:
    try:
        return A.to_formula(parse(a)).lower() == A.to_formula(parse(b)).lower()
    except CalcError:
        return False


def _is_aggregate(formula: str) -> bool:
    try:
        tree = parse(formula)
    except CalcError:
        return False
    for n in tree.walk():
        if isinstance(n, A.Func):
            fdef = F.get(n.name)
            if fdef is not None and fdef.kind in ("aggregate", "table"):
                return True
    return False


def _pack(items: list[dict]) -> list[dict]:
    """Resolve overlaps produced by rounding: push colliding items down."""

    placed: list[dict] = []
    for item in sorted(items, key=lambda i: (i["y"], i["x"])):
        while any(_overlap(item, p) for p in placed):
            item["y"] += 1
        placed.append(item)
    return placed


def _overlap(a: dict, b: dict) -> bool:
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]
