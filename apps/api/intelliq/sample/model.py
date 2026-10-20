"""Semantic model for the synthetic sample warehouse."""

from __future__ import annotations

from ..semantic.model import Dataset

CURRENCY = {"type": "currency", "decimals": 0, "currency": "USD"}


def sample_dataset(connection_id: str, dataset_id: str = "superstore") -> Dataset:
    def col(fid, name, role, dtype, column, **kw):
        return {"id": fid, "name": name, "role": role, "data_type": dtype, "column": column, **kw}

    def calc(fid, name, role, dtype, formula, **kw):
        return {"id": fid, "name": name, "role": role, "data_type": dtype, "formula": formula, **kw}

    return Dataset.model_validate({
        "id": dataset_id,
        "name": "Sample Superstore (synthetic)",
        "description": "Synthetic retail orders generated for development. Not real data.",
        "connection_id": connection_id,
        "base": {"schema": "main", "name": "orders"},
        "base_alias": "o",
        "joins": [
            {"alias": "p", "table": {"schema": "main", "name": "people"}, "type": "left",
             "on": [{"left": "o.region", "right": "p.region"}]},
            {"alias": "r", "table": {"schema": "main", "name": "returns"}, "type": "left",
             "on": [{"left": "o.order_id", "right": "r.order_id"}]},
        ],
        "fields": [
            col("order_id", "Order ID", "dimension", "string", "o.order_id", folder="Order"),
            col("order_date", "Order Date", "dimension", "date", "o.order_date", folder="Order"),
            col("ship_date", "Ship Date", "dimension", "date", "o.ship_date", folder="Order"),
            col("ship_mode", "Ship Mode", "dimension", "string", "o.ship_mode", folder="Order"),
            col("customer_name", "Customer Name", "dimension", "string", "o.customer_name", folder="Customer"),
            col("segment", "Segment", "dimension", "string", "o.segment", folder="Customer"),
            col("region", "Region", "dimension", "string", "o.region", folder="Location"),
            col("state", "State", "dimension", "string", "o.state", folder="Location"),
            col("city", "City", "dimension", "string", "o.city", folder="Location"),
            col("category", "Category", "dimension", "string", "o.category", folder="Product"),
            col("sub_category", "Sub-Category", "dimension", "string", "o.sub_category", folder="Product"),
            col("product_name", "Product Name", "dimension", "string", "o.product_name", folder="Product"),
            col("regional_manager", "Regional Manager", "dimension", "string", "p.person", folder="Location"),
            col("returned", "Returned", "dimension", "string", "r.returned", folder="Order"),
            col("sales", "Sales", "measure", "real", "o.sales", format=CURRENCY, certified=True),
            col("profit", "Profit", "measure", "real", "o.profit", format=CURRENCY, certified=True),
            col("quantity", "Quantity", "measure", "integer", "o.quantity", format={"type": "number", "decimals": 0}),
            col("discount", "Discount", "measure", "real", "o.discount", default_aggregation="avg",
                format={"type": "percent", "decimals": 1}),
            calc("profit_ratio", "Profit Ratio", "measure", "real", "SUM([Profit]) / SUM([Sales])",
                 format={"type": "percent", "decimals": 1}, certified=True,
                 description="Profit as a share of sales. Canonical definition used by every dashboard."),
            calc("order_count", "Orders", "measure", "integer", "COUNTD([Order ID])",
                 format={"type": "number", "decimals": 0}, certified=True),
            calc("days_to_ship", "Days to Ship", "measure", "integer", "DATEDIFF('day', [Order Date], [Ship Date])",
                 default_aggregation="avg", format={"type": "number", "decimals": 1}),
            calc("profitable", "Profitability", "dimension", "string",
                 "IF [Profit] > 0 THEN 'Profitable' ELSEIF [Profit] = 0 THEN 'Break-even' ELSE 'Unprofitable' END"),
            calc("is_returned", "Is Returned", "dimension", "boolean", "NOT ISNULL([Returned])"),
            calc("customer_lifetime_sales", "Customer Lifetime Sales", "measure", "real",
                 "{FIXED [Customer Name] : SUM([Sales])}", default_aggregation="avg", format=CURRENCY,
                 description="Total sales per customer across all time, ignoring dimension filters."),
        ],
        "hierarchies": [
            {"id": "product", "name": "Product", "levels": ["category", "sub_category", "product_name"]},
            {"id": "location", "name": "Location", "levels": ["region", "state", "city"]},
        ],
        "settings": {"week_start": "sunday", "cache_ttl_seconds": 300},
    })
