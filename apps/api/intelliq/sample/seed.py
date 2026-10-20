"""Deterministic *synthetic* sample warehouse for development and tests.

The schema mirrors the shape of Tableau's well-known "Sample - Superstore"
data source (orders / people / returns) so that Superstore-style workbooks
can be imported and mapped, but every row here is generated from a fixed
random seed. It is not Tableau's data and must not be used to claim numeric
parity with Tableau outputs.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

REGIONS = {
    "East": [("New York", ["New York City", "Buffalo", "Rochester"]), ("Pennsylvania", ["Philadelphia", "Pittsburgh"]),
             ("Massachusetts", ["Boston", "Cambridge"]), ("Ohio", ["Columbus", "Cleveland"])],
    "West": [("California", ["Los Angeles", "San Francisco", "San Diego"]), ("Washington", ["Seattle", "Spokane"]),
             ("Oregon", ["Portland", "Salem"]), ("Colorado", ["Denver", "Boulder"])],
    "Central": [("Texas", ["Houston", "Dallas", "Austin"]), ("Illinois", ["Chicago", "Springfield"]),
                ("Michigan", ["Detroit", "Lansing"]), ("Minnesota", ["Minneapolis"])],
    "South": [("Florida", ["Miami", "Orlando", "Tampa"]), ("Georgia", ["Atlanta", "Savannah"]),
              ("North Carolina", ["Charlotte", "Raleigh"]), ("Tennessee", ["Nashville", "Memphis"])],
}
MANAGERS = {"East": "Chuck Magee", "West": "Sadie Pawthorne", "Central": "Roxanne Rodriguez", "South": "Fred Suzuki"}
CATEGORIES = {
    "Furniture": {"Bookcases": (90, 900), "Chairs": (60, 1200), "Furnishings": (8, 300), "Tables": (150, 1500)},
    "Office Supplies": {"Appliances": (20, 700), "Art": (3, 60), "Binders": (3, 120), "Envelopes": (5, 70),
                        "Fasteners": (1, 25), "Labels": (2, 30), "Paper": (5, 60), "Storage": (15, 400),
                        "Supplies": (3, 90)},
    "Technology": {"Accessories": (10, 400), "Copiers": (300, 3000), "Machines": (100, 2500), "Phones": (40, 1100)},
}
MARGIN = {"Furniture": 0.06, "Office Supplies": 0.17, "Technology": 0.16}
SEGMENTS = [("Consumer", 0.52), ("Corporate", 0.30), ("Home Office", 0.18)]
SHIP_MODES = [("Standard Class", 0.6, 4, 7), ("Second Class", 0.2, 2, 5), ("First Class", 0.15, 1, 3), ("Same Day", 0.05, 0, 0)]
FIRST = ["Alex", "Blake", "Casey", "Dana", "Elliot", "Frankie", "Gray", "Harper", "Indy", "Jordan", "Kai", "Logan",
         "Morgan", "Noel", "Oakley", "Parker", "Quinn", "Reese", "Sawyer", "Taylor", "Umi", "Val", "Wren", "Yael"]
LAST = ["Abbott", "Barker", "Chen", "Diaz", "Ellis", "Foster", "Garcia", "Hughes", "Iyer", "Jensen", "Kowalski",
        "Lopez", "Mendes", "Novak", "Okafor", "Patel", "Quinlan", "Rossi", "Sato", "Turner", "Usman", "Vance"]


def _weighted(rng: random.Random, items):
    r, acc = rng.random(), 0.0
    for item in items:
        acc += item[1]
        if r <= acc:
            return item
    return items[-1]


def build_sample_warehouse(path: Path, *, seed: int = 20240601, orders: int = 5000) -> Path:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    customers = []
    for i in range(800):
        seg = _weighted(rng, SEGMENTS)[0]
        region = rng.choice(list(REGIONS))
        state, cities = rng.choice(REGIONS[region])
        customers.append((f"CU-{10000 + i}", f"{rng.choice(FIRST)} {rng.choice(LAST)}", seg, region, state, rng.choice(cities)))

    products = []
    pid = 1000
    for cat, subs in CATEGORIES.items():
        for sub, (lo, hi) in subs.items():
            for k in range(12):
                pid += 1
                base = round(rng.uniform(lo, hi), 2)
                products.append((f"{cat[:3].upper()}-{sub[:2].upper()}-{pid}", f"{sub} {chr(65 + k % 26)}{pid % 97:02d}",
                                 cat, sub, base))

    rows = []
    returned = set()
    start = date(2021, 1, 1)
    span = (date(2024, 12, 31) - start).days
    row_id = 0
    for o in range(orders):
        # Gentle growth and Q4 seasonality.
        while True:
            d = start + timedelta(days=rng.randrange(span + 1))
            weight = 0.65 + 0.35 * ((d - start).days / span) + (0.25 if d.month in (9, 11, 12) else 0)
            if rng.random() < weight / 1.25:
                break
        cust = rng.choice(customers)
        mode, _, lo, hi = _weighted(rng, SHIP_MODES)
        ship = d + timedelta(days=rng.randint(lo, hi))
        order_id = f"US-{d.year}-{100000 + o}"
        if rng.random() < 0.08:
            returned.add(order_id)
        for _ in range(rng.choice([1, 1, 1, 2, 2, 3, 4])):
            row_id += 1
            prod = rng.choice(products)
            qty = rng.choice([1, 2, 2, 3, 3, 4, 5, 6, 7, 9])
            discount = rng.choice([0, 0, 0, 0, 0.1, 0.2, 0.2, 0.3, 0.4, 0.5, 0.7]) if prod[2] != "Technology" else rng.choice([0, 0, 0, 0.2, 0.4])
            sales = round(prod[4] * qty * (1 - discount), 2)
            profit = round(sales * (MARGIN[prod[2]] + rng.gauss(0.05, 0.12) - discount * 0.9), 2)
            rows.append((row_id, order_id, d, ship, mode, cust[0], cust[1], cust[2], "United States", cust[5], cust[4],
                         cust[3], prod[0], prod[2], prod[3], prod[1], sales, qty, discount, profit))

    con = duckdb.connect(str(path))
    try:
        con.execute("""
            CREATE TABLE orders (
              row_id INTEGER, order_id VARCHAR, order_date DATE, ship_date DATE, ship_mode VARCHAR,
              customer_id VARCHAR, customer_name VARCHAR, segment VARCHAR, country VARCHAR, city VARCHAR,
              state VARCHAR, region VARCHAR, product_id VARCHAR, category VARCHAR, sub_category VARCHAR,
              product_name VARCHAR, sales DECIMAL(12, 2), quantity INTEGER, discount DOUBLE, profit DECIMAL(12, 2))
        """)
        # Bulk load through CSV: row-by-row inserts are slow in DuckDB.
        staging = path.with_suffix(".orders.csv")
        with staging.open("w", newline="") as fh:
            csv.writer(fh).writerows(rows)
        try:
            con.execute("COPY orders FROM ? (HEADER false)", [str(staging)])
        finally:
            staging.unlink()
        con.execute("CREATE TABLE people (person VARCHAR, region VARCHAR)")
        con.executemany("INSERT INTO people VALUES (?, ?)", [(m, r) for r, m in MANAGERS.items()])
        con.execute("CREATE TABLE returns (returned VARCHAR, order_id VARCHAR)")
        con.executemany("INSERT INTO returns VALUES ('Yes', ?)", [(o,) for o in sorted(returned)])
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return path
