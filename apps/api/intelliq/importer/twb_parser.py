"""Parse Tableau workbook XML into a neutral, JSON-serialisable model.

Parsing uses ``defusedxml`` so external entities, DTD retrieval and entity
expansion attacks are rejected. Nothing in the workbook is executed; formulas
are kept as text and translated later by the converter.

The model captures what the converter needs: data sources (connections,
relations, columns, calculations), parameters, worksheets (shelves, marks,
encodings, filters, sorts), dashboards (zones) and actions. Anything not
understood is recorded, never silently dropped.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

_REF = re.compile(r"\[((?:[^\]]|\]\])+)\]\.\[((?:[^\]]|\]\])+)\]")


class WorkbookParseError(ValueError):
    pass


def _strip(name: str | None) -> str:
    if not name:
        return ""
    name = name.strip()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    return name.replace("]]", "]")


def _unquote(value: str | None) -> Any:
    """Tableau stores member values as literals: "East", 10, #2024-01-01#, true."""

    if value is None:
        return None
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('""', '"')
    if len(v) >= 2 and v[0] == v[-1] == "#":
        return v[1:-1]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v == "%null%":
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


DERIVATION_CODES = {
    "none", "sum", "avg", "min", "max", "cnt", "ctd", "med", "attr", "usr", "stdev", "stdevp", "var", "varp",
    "yr", "qr", "mn", "dy", "wk", "wd", "hr", "mt", "sc", "tyr", "tqr", "tmn", "twk", "tdy", "thr", "tmt", "tsc",
    "my", "mdy", "pctl", "collect",
}  # fmt: skip


def parse_instance(name: str) -> dict[str, Any] | None:
    """Split a column-instance name like ``sum:Sales:qk`` or ``pcto:sum:Sales:qk``.

    Format: ``[table-calc-prefix:]*derivation:Field Name:type[:n]``. Field
    names may themselves contain ':', so the derivation is located by code.
    """

    parts = _strip(name).split(":")
    if parts and parts[-1].isdigit():
        parts = parts[:-1]
    if len(parts) < 3 or not re.fullmatch(r"[a-z]+", parts[-1]):
        return None
    idx = next((i for i, p in enumerate(parts[:-2]) if p in DERIVATION_CODES), None)
    if idx is None:
        return None
    return {
        "table_calc_prefix": ":".join(parts[:idx]) or None,
        "derivation_code": parts[idx],
        "field": ":".join(parts[idx + 1 : -1]),
        "type": parts[-1],  # nk / ok discrete, qk continuous
    }


def shelf_refs(text: str | None) -> list[dict[str, str]]:
    """Field references on a rows/cols shelf, in order: ``[ds].[instance]``."""

    if not text:
        return []
    return [{"datasource": a.replace("]]", "]"), "instance": b.replace("]]", "]")} for a, b in _REF.findall(text)]


def parse_workbook(xml: bytes) -> dict[str, Any]:
    try:
        root = SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as e:
        raise WorkbookParseError(f"Workbook XML uses forbidden constructs: {type(e).__name__}") from None
    except SafeET.ParseError as e:
        raise WorkbookParseError(f"Workbook XML is malformed: {e}") from None
    if root.tag != "workbook":
        raise WorkbookParseError("Root element is not <workbook>")

    model: dict[str, Any] = {
        "version": root.get("version"),
        "source_build": root.get("source-build"),
        "datasources": [],
        "parameters": [],
        "worksheets": [],
        "dashboards": [],
        "actions": [],
        "unrecognised": [],
    }

    for ds in root.findall("./datasources/datasource"):
        if ds.get("name") == "Parameters":
            model["parameters"].extend(_parameters(ds))
            continue
        model["datasources"].append(_datasource(ds))

    for ws in root.findall("./worksheets/worksheet"):
        model["worksheets"].append(_worksheet(ws))

    for db in root.findall("./dashboards/dashboard"):
        if db.get("type") == "storyboard":
            model["unrecognised"].append({"kind": "story", "name": db.get("name"),
                                          "message": "Stories are not supported"})
            continue
        model["dashboards"].append(_dashboard(db))

    for action in root.findall("./actions/action"):
        model["actions"].append(_action(action))

    # <windows>, <thumbnails>, <preferences>, <style> etc. are presentation-only
    # metadata and are intentionally not modelled.
    return model


# ---------------------------------------------------------------- data sources
def _datasource(ds: Element) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": ds.get("name"),
        "caption": ds.get("caption") or ds.get("name"),
        "connection": None,
        "relations": [],
        "custom_sql": [],
        "columns": [],
        "metadata": [],
        "extract": ds.find("./extract") is not None,
    }
    conn = ds.find("./connection")
    if conn is not None:
        inner = conn
        named = conn.find("./named-connections/named-connection/connection")
        if conn.get("class") == "federated" and named is not None:
            inner = named
        out["connection"] = {
            "class": inner.get("class"),
            "server": inner.get("server"),
            "dbname": inner.get("dbname"),
            "schema": inner.get("schema"),
            "warehouse": inner.get("warehouse"),
            "filename": inner.get("filename"),
        }
        for rel in conn.iter("relation"):
            rtype = rel.get("type")
            if rtype == "table":
                out["relations"].append({"name": rel.get("name"), "table": rel.get("table")})
            elif rtype == "text":
                out["custom_sql"].append({"name": rel.get("name"), "sql": (rel.text or "").strip()})
            elif rtype == "join":
                clause = rel.find("./clause/expression")
                out["relations"].append({"join": rel.get("join"), "clause": _expression_text(clause) if clause is not None else None})
    for obj in ds.iter("object"):  # 2020.2+ object model (relationships)
        rel = obj.find("./properties/relation")
        if rel is not None and rel.get("type") == "table":
            out["relations"].append({"name": obj.get("caption") or rel.get("name"), "table": rel.get("table"), "object": True})

    for rec in ds.iter("metadata-record"):
        if rec.get("class") != "column":
            continue
        out["metadata"].append({
            "remote_name": (rec.findtext("remote-name") or "").strip(),
            "local_name": _strip(rec.findtext("local-name")),
            "parent": _strip(rec.findtext("parent-name")),
            "local_type": (rec.findtext("local-type") or "").strip(),
        })

    for col in ds.findall("./column"):
        calc = col.find("./calculation")
        entry = {
            "name": _strip(col.get("name")),
            "caption": col.get("caption"),
            "datatype": col.get("datatype"),
            "role": col.get("role"),
            "type": col.get("type"),
            "hidden": col.get("hidden") == "true",
            "default_aggregation": (col.get("aggregation") or "").lower() or None,
            "formula": None,
            "calc_class": None,
        }
        if calc is not None:
            entry["calc_class"] = calc.get("class")
            entry["formula"] = calc.get("formula")
            if calc.get("class") == "categorical-bin":
                entry["formula"] = None
                entry["unsupported"] = "Groups (categorical bins) are not yet supported"
            elif calc.get("class") == "bin":
                entry["formula"] = None
                entry["unsupported"] = "Numeric bins are not yet supported"
        out["columns"].append(entry)
    return out


def _expression_text(expr: Element) -> str:
    op = expr.get("op", "")
    children = expr.findall("./expression")
    if not children:
        return op
    return f" {op} ".join(_expression_text(c) for c in children)


def _parameters(ds: Element) -> list[dict[str, Any]]:
    out = []
    for col in ds.findall("./column"):
        calc = col.find("./calculation")
        members = [_unquote(m.get("value")) for m in col.findall("./members/member")]
        rng = col.find("./range")
        out.append({
            "name": _strip(col.get("name")),
            "caption": col.get("caption") or _strip(col.get("name")),
            "datatype": col.get("datatype"),
            "domain": col.get("param-domain-type") or "all",
            "value": _unquote(col.get("value") if col.get("value") is not None else (calc.get("formula") if calc is not None else None)),
            "members": members,
            "range": {"min": _unquote(rng.get("min")), "max": _unquote(rng.get("max")), "step": _unquote(rng.get("granularity"))}
            if rng is not None else None,
        })
    return out


# ------------------------------------------------------------------ worksheets
def _worksheet(ws: Element) -> dict[str, Any]:
    table = ws.find("./table")
    view = table.find("./view") if table is not None else None
    out: dict[str, Any] = {
        "name": ws.get("name"),
        "title": "",
        "datasources": [],
        "instances": {},
        "rows": [],
        "cols": [],
        "mark": "Automatic",
        "encodings": [],
        "filters": [],
        "sorts": [],
        "unrecognised": [],
    }
    title = ws.find("./layout-options/title/formatted-text")
    if title is not None:
        out["title"] = "".join((r.text or "") for r in title.iter("run")).strip()
    if table is None or view is None:
        return out

    out["datasources"] = [d.get("name") for d in view.findall("./datasources/datasource")]
    for deps in view.findall("./datasource-dependencies"):
        ds_name = deps.get("datasource")
        for ci in deps.findall("./column-instance"):
            tc = ci.find("./table-calc")
            key = f"{ds_name}::{_strip(ci.get('name'))}"
            out["instances"][key] = {
                "datasource": ds_name,
                "column": _strip(ci.get("column")),
                "derivation": ci.get("derivation"),
                "pivot": ci.get("pivot"),
                "type": ci.get("type"),
                "table_calc": {
                    "type": tc.get("type"),
                    "ordering_type": tc.get("ordering-type"),
                    "ordering_fields": [_strip(o.text) for o in tc.findall("./order")],
                } if tc is not None else None,
            }

    out["rows"] = shelf_refs(table.findtext("./rows"))
    out["cols"] = shelf_refs(table.findtext("./cols"))
    pane = table.find("./panes/pane")
    if pane is not None:
        mark = pane.find("./mark")
        if mark is not None:
            out["mark"] = mark.get("class") or "Automatic"
        enc = pane.find("./encodings")
        if enc is not None:
            for e in enc:
                for ref in shelf_refs(e.get("column")):
                    out["encodings"].append({"channel": e.tag, **ref})

    for flt in view.findall("./filter"):
        out["filters"].append(_filter(flt))
    for sort in view.findall("./sort"):
        out["sorts"].append({
            "class": sort.get("class"),
            "column": shelf_refs(sort.get("column"))[0] if shelf_refs(sort.get("column")) else None,
            "direction": (sort.get("direction") or "ASC").upper(),
            "using": shelf_refs(sort.get("using"))[0] if shelf_refs(sort.get("using")) else None,
        })
    return out


def _filter(flt: Element) -> dict[str, Any]:
    refs = shelf_refs(flt.get("column"))
    out: dict[str, Any] = {
        "class": flt.get("class"),
        "column": refs[0] if refs else None,
        "context": flt.get("context") == "true",
        "members": [],
        "except": False,
        "min": None,
        "max": None,
        "relative": None,
        "raw_function": None,
    }
    if flt.get("class") == "categorical":
        top = flt.find("./groupfilter")
        if top is not None:
            out["raw_function"] = top.get("function")
            if top.get("function") == "except":
                out["except"] = True
            for gf in top.iter("groupfilter"):
                if gf.get("function") == "member":
                    out["members"].append(_unquote(gf.get("member")))
                elif gf.get("function") == "end":
                    out["raw_function"] = "top_n"
                    out["top_n"] = {"count": _unquote(gf.get("count")), "end": gf.get("end")}
    elif flt.get("class") == "quantitative":
        out["min"] = _unquote(flt.findtext("./min"))
        out["max"] = _unquote(flt.findtext("./max"))
    elif flt.get("class") == "relative-date":
        out["relative"] = {
            "period": (flt.get("period-type-v2") or flt.get("period-type") or "").lower(),
            "first": _unquote(flt.get("first-period")),
            "last": _unquote(flt.get("last-period")),
            "anchor": _unquote(flt.get("anchor-date")),
        }
    return out


# ------------------------------------------------------------------ dashboards
def _dashboard(db: Element) -> dict[str, Any]:
    size = db.find("./size")
    out: dict[str, Any] = {
        "name": db.get("name"),
        "size": {
            "width": int(size.get("maxwidth") or size.get("minwidth") or 1200) if size is not None else 1200,
            "height": int(size.get("maxheight") or size.get("minheight") or 800) if size is not None else 800,
        },
        "zones": [],
    }
    for zone in db.iter("zone"):
        ztype = zone.get("type-v2") or zone.get("type")
        if ztype in ("layout-basic", "layout-flow") and zone.get("name") is None:
            continue
        text = None
        ft = zone.find("./formatted-text")
        if ft is not None:
            text = "".join((r.text or "") for r in ft.iter("run")).strip()
        out["zones"].append({
            "id": zone.get("id"),
            "type": ztype or "worksheet",
            "name": zone.get("name"),
            "param": zone.get("param"),
            "mode": zone.get("mode"),
            "floating": zone.get("is-floating") == "true",
            "text": text,
            "x": int(zone.get("x") or 0), "y": int(zone.get("y") or 0),
            "w": int(zone.get("w") or 0), "h": int(zone.get("h") or 0),
        })
    return out


def _action(action: Element) -> dict[str, Any]:
    source = action.find("./source")
    cmd = action.find("./command")
    activation = action.find("./activation")
    params = {p.get("name"): p.get("value") for p in (cmd.findall("./param") if cmd is not None else [])}
    return {
        "name": _strip(action.get("name")),
        "caption": action.get("caption") or _strip(action.get("name")),
        "command": cmd.get("command") if cmd is not None else None,
        "activation": activation.get("type") if activation is not None else None,
        "auto_clear": activation.get("auto-clear") == "true" if activation is not None else True,
        "source_dashboard": source.get("dashboard") if source is not None else None,
        "source_worksheet": source.get("worksheet") if source is not None else None,
        "params": params,
    }
