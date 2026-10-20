from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from intelliq.importer.archive import WorkbookFileError, read_workbook
from intelliq.importer.convert import Converter, suggest_mapping
from intelliq.importer.twb_parser import WorkbookParseError, parse_instance, parse_workbook, shelf_refs
from intelliq.sample.model import sample_dataset
from intelliq.spec.dashboard import Dashboard

FIXTURE = Path(__file__).parent.parent / "fixtures" / "superstore_overview.twb"


def twbx(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ----------------------------------------------------------------- archive safety
def test_twbx_reads_root_workbook_and_inventories_assets():
    data = twbx({"book.twb": FIXTURE.read_bytes(), "Data/Extracts/orders.hyper": b"\0" * 100, "Image/logo.png": b"png"})
    wf = read_workbook("book.twbx", data, max_bytes=10**8)
    assert wf.packaged and wf.twb_name == "book.twb"
    assert {(a.path, a.kind) for a in wf.assets} == {("Data/Extracts/orders.hyper", "extract"), ("Image/logo.png", "image")}
    assert len(wf.sha256) == 64


@pytest.mark.parametrize("entries, message", [
    ({"../evil.twb": b"<workbook/>"}, "unsafe path"),
    ({"book.twb": b"<workbook/>", "/abs/path": b"x"}, "unsafe path"),
    ({"book.twb": b"<workbook/>", "a/../../b": b"x"}, "unsafe path"),
    ({"Data/x.csv": b"x"}, "does not contain a workbook"),
    ({"a.twb": b"<workbook/>", "b.twb": b"<workbook/>"}, "more than one"),
    ({"book.twb": b"not xml"}, "not XML"),
])
def test_twbx_rejections(entries, message):
    with pytest.raises(WorkbookFileError, match=message):
        read_workbook("book.twbx", twbx(entries), max_bytes=10**8)


def test_zip_bomb_and_symlink_rejected():
    bomb = twbx({"book.twb": b"<workbook/>", "pad.bin": b"\0" * 50_000_000})
    with pytest.raises(WorkbookFileError, match="compression ratio"):
        read_workbook("book.twbx", bomb, max_bytes=10**8)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("book.twb", b"<workbook/>")
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(WorkbookFileError, match="symbolic link"):
        read_workbook("book.twbx", buf.getvalue(), max_bytes=10**8)


def test_size_and_type_limits():
    with pytest.raises(WorkbookFileError, match="upload limit"):
        read_workbook("a.twb", b"<workbook/>" * 100, max_bytes=50)
    with pytest.raises(WorkbookFileError, match="Only Tableau"):
        read_workbook("a.ipynb", b"{}", max_bytes=1000)
    with pytest.raises(WorkbookFileError, match="not a valid package"):
        read_workbook("a.twbx", b"PK garbage", max_bytes=1000)


# ------------------------------------------------------------------- XML safety
def test_external_entities_are_rejected():
    xxe = b"""<?xml version="1.0"?><!DOCTYPE w [<!ENTITY x SYSTEM "file:///etc/passwd">]><workbook>&x;</workbook>"""
    with pytest.raises(WorkbookParseError, match="forbidden"):
        parse_workbook(xxe)
    bomb = b"""<?xml version="1.0"?><!DOCTYPE w [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><workbook>&b;</workbook>"""
    with pytest.raises(WorkbookParseError):
        parse_workbook(bomb)
    with pytest.raises(WorkbookParseError, match="not <workbook>"):
        parse_workbook(b"<html/>")


# ------------------------------------------------------------------------ parsing
def test_instance_and_shelf_parsing():
    assert parse_instance("[sum:Sales:qk]")["derivation_code"] == "sum"
    assert parse_instance("[pcto:sum:Sales:qk]")["table_calc_prefix"] == "pcto"
    assert parse_instance("[none:Sub-Category:nk]")["field"] == "Sub-Category"
    refs = shelf_refs("([ds.1].[none:Category:nk] / [ds.1].[none:Sub-Category:nk])")
    assert [r["instance"] for r in refs] == ["none:Category:nk", "none:Sub-Category:nk"]


def test_fixture_model():
    wb = parse_workbook(FIXTURE.read_bytes())
    assert [p["caption"] for p in wb["parameters"]] == ["Top N", "Profit Threshold"]
    assert wb["parameters"][0]["range"] == {"min": 1, "max": 20, "step": 1}
    ds = wb["datasources"][0]
    assert ds["connection"]["class"] == "sqlserver" and ds["connection"]["server"] == "warehouse.example.com"
    assert {r.get("name") for r in ds["relations"] if r.get("name")} == {"Orders", "People"}
    assert len(wb["worksheets"]) == 6 and wb["dashboards"][0]["name"] == "Executive Overview"
    trend = next(w for w in wb["worksheets"] if w["name"] == "Sales Trend")
    assert trend["mark"] == "Line" and trend["filters"][0]["relative"]["first"] == -11
    assert {a["command"] for a in wb["actions"]} == {"tsc:tsl-filter", "tsc:brush", "tsc:url"}


# --------------------------------------------------------------------- conversion
@pytest.fixture()
def converted():
    wb = parse_workbook(FIXTURE.read_bytes())
    wb["assets"] = [{"path": "Data/Extracts/x.hyper", "size": 1, "kind": "extract"}]
    ds = sample_dataset("c")
    mapping = suggest_mapping(wb, [ds])
    return Converter(wb, mapping, {ds.id: ds}, workbook_name="fixture.twb", sha256="abc").convert(), mapping


def _item(result, kind, ref_part):
    return next(i for i in result.report["items"] if i["kind"] == kind and ref_part in i["ref"])


def test_auto_mapping(converted):
    _, mapping = converted
    fields = mapping["fields"]["federated.0a1b2c3"]
    assert fields["Sub-Category"] == "sub_category" and fields["Regional Manager"] == "regional_manager"
    assert "Postal Code" not in fields


def test_every_item_is_classified_never_dropped(converted):
    result, _ = converted
    statuses = {i["status"] for i in result.report["items"]}
    assert statuses <= {"supported", "adjusted", "manual", "unsupported"}
    summary = result.report["summary"]
    assert summary["unsupported"] >= 3 and 0 < summary["fidelity"] < 1
    assert _item(result, "calculation", "Order Prefix")["status"] == "unsupported"
    assert "REGEXP_EXTRACT" in _item(result, "calculation", "Order Prefix")["message"]
    assert _item(result, "action", "Open CRM")["status"] == "unsupported"
    assert _item(result, "zone", "zone web")["status"] == "unsupported"
    assert _item(result, "asset", "x.hyper")["status"] == "adjusted"
    assert _item(result, "worksheet", "State Map")["status"] == "adjusted"


def test_calculations_translate_and_reuse_canonical_metrics(converted):
    result, _ = converted
    assert "existing canonical definition" in _item(result, "calculation", "Profit Ratio")["message"]
    names = {f["name"]: f for f in result.new_fields["superstore"]}
    assert names["Sales per Customer"]["formula"] == "{FIXED [Customer Name] : SUM([Sales])}"
    assert names["Ship Days"]["formula"] == "DATEDIFF('day', [Order Date], [Ship Date])"
    assert "Above Threshold" not in names  # parameter-dependent → worksheet formula
    assert _item(result, "calculation", "Above Threshold")["status"] == "adjusted"


def test_dashboard_structure(converted):
    result, _ = converted
    assert len(result.dashboards) == 1
    spec = Dashboard.model_validate(result.dashboards[0])
    types = {w.id: w.viz.type for w in spec.worksheets}
    assert types == {"sales_by_region": "bar", "sales_trend": "line", "category_mix": "pie",
                     "profit_vs_sales": "scatter", "customer_detail": "table"}
    trend = spec.worksheet("sales_trend")
    assert trend.viz.fields[0].use.time_unit == "month"
    assert trend.filters[0].filter.relative.n == 12
    mix = spec.worksheet("category_mix")
    assert any(f.use.table_calc and f.use.table_calc.type == "percent_of_total" for f in mix.viz.fields)
    region = spec.worksheet("sales_by_region")
    assert region.viz.sort[0].direction == "desc"
    detail = spec.worksheet("customer_detail")
    assert not any(f.filter.target and f.filter.target.field == "region" for f in detail.filters)  # promoted
    assert spec.filters[0].field == "region" and spec.filters[0].default == ["East", "West"]
    assert spec.parameters[0].name == "Profit Threshold"
    assert {a.type for a in spec.actions} == {"filter", "highlight"}
    assert spec.provenance.source == "tableau" and spec.provenance.refs["sales_trend"] == "worksheet:Sales Trend"
    # Layout is on the grid and non-overlapping
    boxes = [(i.x, i.y, i.w, i.h) for i in spec.layout.items]
    for a in boxes:
        for b in boxes:
            if a is not b:
                assert not (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def test_unmapped_datasource_requires_manual_mapping():
    wb = parse_workbook(FIXTURE.read_bytes())
    ds = sample_dataset("c")
    result = Converter(wb, {"datasources": {}, "fields": {}}, {ds.id: ds}).convert()
    assert _item(result, "datasource", "Superstore")["status"] == "manual"
    assert result.dashboards == []
