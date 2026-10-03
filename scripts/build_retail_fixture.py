"""Generate data/eval/retail.sql: the small retail database used by the SQL agent.

The SQL agent's system prompt contains this schema, and the evaluation checker
executes both the agent's SQL and a reference query against an in-memory SQLite
copy of this database. Generation is seeded, so the output is byte-for-byte
reproducible:

    uv run python scripts/build_retail_fixture.py
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "eval" / "retail.sql"

SCHEMA = """\
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    segment TEXT NOT NULL CHECK (segment IN ('consumer', 'business')),
    signup_date DATE NOT NULL
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_price REAL NOT NULL
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled'))
);
CREATE TABLE order_items (
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price REAL NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    title TEXT NOT NULL,
    manager_id INTEGER REFERENCES employees(id),
    hire_date DATE NOT NULL,
    salary INTEGER NOT NULL
);
"""

FIRST = [
    "Ava", "Liam", "Mia", "Noah", "Zoe", "Ethan", "Isla", "Lucas", "Aria", "Omar",
    "Priya", "Kenji", "Sofia", "Mateo", "Hana", "Felix", "Nora", "Ravi", "Elena", "Jonas",
]  # fmt: skip
LAST = [
    "Smith", "Garcia", "Chen", "Muller", "Dubois", "Sato", "Silva", "Patel", "Brown", "Kowalski",
    "Novak", "Rossi", "Kim", "Okafor", "Larsen",
]  # fmt: skip
COUNTRIES = ["USA", "Canada", "UK", "Germany", "France", "India", "Japan", "Brazil"]
PRODUCTS = [
    ("Mechanical Keyboard", "Electronics", 89.0),
    ("Wireless Mouse", "Electronics", 25.5),
    ("USB-C Hub", "Electronics", 39.99),
    ("27-inch Monitor", "Electronics", 249.0),
    ("Noise-Cancelling Headphones", "Electronics", 199.0),
    ("Webcam HD", "Electronics", 59.0),
    ("Standing Desk", "Furniture", 420.0),
    ("Ergonomic Chair", "Furniture", 310.0),
    ("Bookshelf", "Furniture", 120.0),
    ("Desk Lamp", "Furniture", 35.0),
    ("Notebook A5", "Stationery", 4.5),
    ("Gel Pens (10 pack)", "Stationery", 8.99),
    ("Sticky Notes", "Stationery", 3.25),
    ("Planner 2024", "Stationery", 18.0),
    ("Fountain Pen", "Stationery", 45.0),
    ("Coffee Beans 1kg", "Grocery", 22.0),
    ("Green Tea (50 bags)", "Grocery", 9.5),
    ("Dark Chocolate", "Grocery", 3.99),
    ("Protein Bars (12)", "Grocery", 24.0),
    ("Sparkling Water (24)", "Grocery", 14.0),
    ("Yoga Mat", "Sports", 29.0),
    ("Dumbbell Set", "Sports", 85.0),
    ("Running Shoes", "Sports", 110.0),
    ("Water Bottle", "Sports", 15.0),
    ("Resistance Bands", "Sports", 19.99),
]
DEPARTMENTS = {
    "Engineering": ["Software Engineer", "Senior Software Engineer", "Staff Engineer"],
    "Sales": ["Account Executive", "Sales Manager"],
    "Marketing": ["Marketing Specialist", "Content Lead"],
    "Support": ["Support Agent", "Support Lead"],
}
STATUSES = ["pending", "shipped", "delivered", "delivered", "delivered", "cancelled"]


def q(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def insert(table: str, rows: list[tuple[object, ...]]) -> str:
    values = ",\n".join("(" + ", ".join(q(v) for v in row) + ")" for row in rows)
    return f"INSERT INTO {table} VALUES\n{values};\n"


def build() -> str:
    rng = random.Random(42)

    customers = []
    for cid in range(1, 41):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        signup = date(2022, 1, 1) + timedelta(days=rng.randrange(0, 900))
        customers.append(
            (
                cid,
                name,
                rng.choice(COUNTRIES),
                rng.choice(["consumer", "consumer", "business"]),
                signup.isoformat(),
            )
        )

    products = [(i + 1, n, c, p) for i, (n, c, p) in enumerate(PRODUCTS)]

    orders, items = [], []
    for oid in range(1, 201):
        cust = rng.choice(customers)
        signup = date.fromisoformat(str(cust[4]))
        start = max(signup, date(2023, 1, 1))
        order_date = start + timedelta(
            days=rng.randrange(0, max(1, (date(2024, 12, 31) - start).days))
        )
        orders.append((oid, cust[0], order_date.isoformat(), rng.choice(STATUSES)))
        for prod in rng.sample(products, k=rng.randint(1, 4)):
            # occasional discount so order_items.unit_price != products.unit_price
            price = round(float(prod[3]) * rng.choice([1.0, 1.0, 1.0, 0.9]), 2)
            items.append((oid, prod[0], rng.randint(1, 5), price))

    employees: list[tuple[object, ...]] = [
        (1, "Grace Hopper", "Engineering", "CTO", None, "2019-03-01", 210000)
    ]
    eid = 2
    for dept, titles in DEPARTMENTS.items():
        lead_id = eid
        employees.append(
            (lead_id, f"{rng.choice(FIRST)} {rng.choice(LAST)}", dept, f"Head of {dept}", 1,
             (date(2019, 6, 1) + timedelta(days=rng.randrange(0, 500))).isoformat(),
             rng.randrange(150, 190) * 1000)
        )  # fmt: skip
        eid += 1
        for _ in range(rng.randint(3, 5)):
            employees.append(
                (eid, f"{rng.choice(FIRST)} {rng.choice(LAST)}", dept, rng.choice(titles), lead_id,
                 (date(2020, 1, 1) + timedelta(days=rng.randrange(0, 1500))).isoformat(),
                 rng.randrange(55, 140) * 1000)
            )  # fmt: skip
            eid += 1

    return (
        "-- Generated by scripts/build_retail_fixture.py (seed=42). Do not edit by hand.\n"
        + SCHEMA
        + insert("customers", customers)
        + insert("products", products)
        + insert("orders", orders)
        + insert("order_items", items)
        + insert("employees", employees)
    )


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"wrote {OUT}")
