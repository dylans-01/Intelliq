from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

from intelliq.connectors import duckdb_connector
from intelliq.connectors.duckdb_connector import DuckDBConnector
from intelliq.query.executor import QueryExecutor
from intelliq.query.models import QueryRequest
from intelliq.query.planner import Planner, UserContext
from intelliq.sample.model import sample_dataset
from intelliq.sample.seed import build_sample_warehouse

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def warehouse(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("warehouse")
    duckdb_connector.EXTRA_ROOTS.append(root)
    return build_sample_warehouse(root / "sample.duckdb")


@pytest.fixture(scope="session")
def dataset():
    return sample_dataset("conn-test")


@pytest.fixture(scope="session")
def connector(warehouse) -> DuckDBConnector:
    return DuckDBConnector({"path": str(warehouse)}, {})


@pytest.fixture(scope="session")
def oracle(warehouse):
    """Independent read-only connection for hand-written reference SQL."""

    # Same config as the connector: DuckDB rejects mixed configs for one file in a process.
    con = duckdb.connect(str(warehouse), read_only=True, config={"enable_external_access": False})
    yield con
    con.close()


class Runner:
    def __init__(self, dataset, connector):
        self.dataset = dataset
        self.connector = connector
        self.executor = QueryExecutor()

    def plan(self, query: dict, user: UserContext | None = None, dataset=None):
        ds = dataset or self.dataset
        req = QueryRequest.model_validate({"dataset_id": ds.id, **query})
        return Planner(ds, "duckdb", user or UserContext("u", "admin")).plan(req)

    def run(self, query: dict, user: UserContext | None = None, dataset=None):
        planned = self.plan(query, user, dataset)
        result = self.executor.execute(planned, self.connector, cache_scope=("t",), ttl_seconds=0, timeout_s=30,
                                       owner="u", include_sql=True)
        return result

    def rows(self, query: dict, **kw) -> list[list]:
        return self.run(query, **kw).rows


@pytest.fixture(scope="session")
def runner(dataset, connector) -> Runner:
    return Runner(dataset, connector)


# ------------------------------------------------------------------------- API
PASSWORDS = {"admin": "admin-password-1", "author": "author-password-1", "viewer": "viewer-password-1",
             "west": "west-viewer-password"}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from intelliq.api.app import create_app
    from intelliq.config import Settings
    from intelliq.platform.auth import hash_password
    from intelliq.platform.bootstrap import DEFAULT_WORKSPACE, ensure_admin, seed_sample

    monkeypatch.setenv("INTELLIQ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INTELLIQ_DEV", "1")
    monkeypatch.setenv("INTELLIQ_LOGIN_RATE", "1000")
    os.environ.pop("INTELLIQ_SECRET_KEY", None)
    settings = Settings()
    app = create_app(settings)
    store = app.state.ctx.store
    admin = ensure_admin(store, "admin@example.com", PASSWORDS["admin"])
    for role in ("author", "viewer"):
        u = store.create_user(f"{role}@example.com", role.title(), hash_password(PASSWORDS[role]))
        store.set_membership(u["id"], DEFAULT_WORKSPACE, role)
    west = store.create_user("west@example.com", "West Viewer", hash_password(PASSWORDS["west"]), {"region": ["West"]})
    store.set_membership(west["id"], DEFAULT_WORKSPACE, "viewer")
    seed_sample(store, settings.warehouse_dir, admin["id"])
    # Row-level security on the sample dataset: viewers see only their region.
    row = store.get_dataset("superstore")
    model = row["model"]
    model["row_level_security"] = [{"field": "region", "user_attribute": "region", "exempt_roles": ["admin", "author"]}]
    store.save_dataset_version("superstore", model["name"], model, "Add RLS", admin["id"])

    class Api:
        def __init__(self):
            self.app = app
            self.store = store

        def client(self, role: str | None = None) -> TestClient:
            c = TestClient(app)
            if role:
                email = "west@example.com" if role == "west" else f"{role}@example.com"
                r = c.post("/api/auth/login", json={"email": email, "password": PASSWORDS[role]})
                assert r.status_code == 200, r.text
                csrf = c.get("/api/auth/me").json()["csrf_token"]
                c.headers["X-Intelliq-CSRF"] = csrf
            return c

    return Api()
