"""First-run bootstrap and optional sample content (development only)."""

from __future__ import annotations

from pathlib import Path

from ..sample.model import sample_dataset
from ..sample.seed import build_sample_warehouse
from .auth import hash_password
from .store import Store

DEFAULT_WORKSPACE = "default"


def ensure_admin(store: Store, email: str, password: str, name: str = "Administrator") -> dict:
    """Create the default workspace and an administrator if they do not exist."""

    if not any(w["id"] == DEFAULT_WORKSPACE for w in _all_workspaces(store)):
        store.create_workspace("Default workspace", DEFAULT_WORKSPACE)
    user = store.user_by_email(email)
    if user is None:
        user = store.create_user(email, name, hash_password(password))
    store.set_membership(user["id"], DEFAULT_WORKSPACE, "admin")
    return user


def _all_workspaces(store: Store) -> list[dict]:
    with store.tx() as c:
        return [dict(r) for r in c.execute("SELECT id, name FROM workspaces").fetchall()]


def seed_sample(store: Store, warehouse_dir: Path, actor: str | None, workspace: str = DEFAULT_WORKSPACE) -> str | None:
    """Create the synthetic sample warehouse, its connection, dataset and an
    example dashboard. Returns the dashboard id, or None if already seeded."""

    if store.get_dataset("superstore") is not None:
        return None
    path = build_sample_warehouse(warehouse_dir / "sample_superstore.duckdb")
    conn = store.create_connection(workspace, "Sample warehouse (synthetic)", "duckdb", {"path": str(path)}, None, actor)
    ds = sample_dataset(conn["id"])
    store.create_dataset(workspace, ds.id, ds.name, conn["id"], ds.model_dump(by_alias=True, exclude_none=True), actor)
    did = store.create_dashboard(workspace, "Sales Performance (sample)", sample_dashboard_spec(), actor)
    store.publish_dashboard(did, 1)
    return did


def sample_dashboard_spec() -> dict:
    def f(key, role, channel, **use):
        return {"key": key, "role": role, "channel": channel, "use": use}

    ws = [
        {"id": "kpi_sales", "title": "Sales", "dataset_id": "superstore",
         "viz": {"type": "kpi", "fields": [f("s", "measure", "value", field="sales")]}},
        {"id": "kpi_profit", "title": "Profit", "dataset_id": "superstore",
         "viz": {"type": "kpi", "fields": [f("p", "measure", "value", field="profit")]}},
        {"id": "kpi_ratio", "title": "Profit ratio", "dataset_id": "superstore",
         "viz": {"type": "kpi", "fields": [f("r", "measure", "value", field="profit_ratio")]}},
        {"id": "kpi_orders", "title": "Orders", "dataset_id": "superstore",
         "viz": {"type": "kpi", "fields": [f("o", "measure", "value", field="order_count")]}},
        {"id": "trend", "title": "Monthly sales", "subtitle": "By customer segment", "dataset_id": "superstore",
         "viz": {"type": "line", "fields": [
             f("month", "dimension", "x", field="order_date", time_unit="month"),
             f("sales", "measure", "y", field="sales"),
             f("segment", "dimension", "color", field="segment")]}},
        {"id": "by_region", "title": "Sales by region", "subtitle": "Select a bar to filter the dashboard",
         "dataset_id": "superstore",
         "viz": {"type": "bar", "fields": [
             f("region", "dimension", "x", field="region"),
             f("sales", "measure", "y", field="sales"),
             f("ratio", "measure", "tooltip", field="profit_ratio")],
             "sort": [{"key": "sales", "direction": "desc"}]}},
        {"id": "by_category", "title": "Sales by sub-category", "subtitle": "Drill from category to product",
         "dataset_id": "superstore", "drill_hierarchy": "product",
         "viz": {"type": "bar", "options": {"orientation": "horizontal"}, "fields": [
             f("cat", "dimension", "x", field="category"),
             f("sales", "measure", "y", field="sales")],
             "sort": [{"key": "sales", "direction": "desc"}], "limit": 25}},
        {"id": "segment_mix", "title": "Segment mix", "dataset_id": "superstore",
         "viz": {"type": "pie", "options": {"donut": True}, "fields": [
             f("segment", "dimension", "color", field="segment"),
             f("sales", "measure", "y", field="sales")]}},
        {"id": "profitability", "title": "Profit vs. sales", "subtitle": "One point per sub-category",
         "dataset_id": "superstore",
         "viz": {"type": "scatter", "fields": [
             f("sales", "measure", "x", field="sales"),
             f("profit", "measure", "y", field="profit"),
             f("sub", "dimension", "detail", field="sub_category"),
             f("cat", "dimension", "color", field="category")]}},
        {"id": "managers", "title": "Regional performance", "dataset_id": "superstore",
         "viz": {"type": "table", "fields": [
             f("region", "dimension", "column", field="region"),
             f("manager", "dimension", "column", field="regional_manager"),
             f("sales", "measure", "column", field="sales"),
             f("profit", "measure", "column", field="profit"),
             f("ratio", "measure", "column", field="profit_ratio"),
             f("orders", "measure", "column", field="order_count"),
             f("ship", "measure", "column", field="days_to_ship")],
             "sort": [{"key": "sales", "direction": "desc"}]}},
    ]
    items = [
        {"id": "i_f_year", "kind": "filter", "ref": "f_year", "x": 0, "y": 0, "w": 3, "h": 2},
        {"id": "i_f_segment", "kind": "filter", "ref": "f_segment", "x": 3, "y": 0, "w": 3, "h": 2},
        {"id": "i_f_category", "kind": "filter", "ref": "f_category", "x": 6, "y": 0, "w": 3, "h": 2},
        {"id": "i_kpi_sales", "kind": "worksheet", "ref": "kpi_sales", "x": 0, "y": 2, "w": 3, "h": 3},
        {"id": "i_kpi_profit", "kind": "worksheet", "ref": "kpi_profit", "x": 3, "y": 2, "w": 3, "h": 3},
        {"id": "i_kpi_ratio", "kind": "worksheet", "ref": "kpi_ratio", "x": 6, "y": 2, "w": 3, "h": 3},
        {"id": "i_kpi_orders", "kind": "worksheet", "ref": "kpi_orders", "x": 9, "y": 2, "w": 3, "h": 3},
        {"id": "i_trend", "kind": "worksheet", "ref": "trend", "x": 0, "y": 5, "w": 8, "h": 8},
        {"id": "i_by_region", "kind": "worksheet", "ref": "by_region", "x": 8, "y": 5, "w": 4, "h": 8},
        {"id": "i_by_category", "kind": "worksheet", "ref": "by_category", "x": 0, "y": 13, "w": 5, "h": 9},
        {"id": "i_segment_mix", "kind": "worksheet", "ref": "segment_mix", "x": 5, "y": 13, "w": 3, "h": 9},
        {"id": "i_profitability", "kind": "worksheet", "ref": "profitability", "x": 8, "y": 13, "w": 4, "h": 9},
        {"id": "i_managers", "kind": "worksheet", "ref": "managers", "x": 0, "y": 22, "w": 12, "h": 7},
    ]
    return {
        "spec_version": 1,
        "title": "Sales Performance (sample)",
        "description": "Example dashboard over the synthetic sample warehouse.",
        "worksheets": ws,
        "filters": [
            {"id": "f_year", "label": "Order year", "dataset_id": "superstore", "field": "order_date",
             "control": "date_range", "default": []},
            {"id": "f_segment", "label": "Segment", "dataset_id": "superstore", "field": "segment", "default": []},
            {"id": "f_category", "label": "Category", "dataset_id": "superstore", "field": "category", "default": []},
        ],
        "actions": [
            {"id": "region_filter", "name": "Filter by region", "type": "filter", "source": "by_region", "targets": "all"},
            {"id": "segment_highlight", "name": "Highlight segment", "type": "highlight", "source": "segment_mix",
             "targets": "all"},
        ],
        "layout": {"columns": 12, "row_height": 36, "items": items},
    }
