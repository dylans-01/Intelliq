"""End-to-end API tests: authentication, authorization, data access and import."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent.parent / "fixtures" / "superstore_overview.twb"


def sample_dashboard_id(client) -> str:
    return next(d["id"] for d in client.get("/api/dashboards").json() if d["title"].startswith("Sales Performance"))


# ------------------------------------------------------------------ authentication
def test_login_logout_and_session(api):
    c = api.client()
    assert c.get("/api/auth/me").status_code == 401
    bad = c.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong-password"})
    assert bad.status_code == 401 and bad.json()["message"] == "Email or password is incorrect"
    unknown = c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever-123"})
    assert unknown.status_code == 401 and unknown.json() == bad.json()  # no account enumeration
    ok = c.post("/api/auth/login", json={"email": "ADMIN@example.com", "password": "admin-password-1"})
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    me = c.get("/api/auth/me").json()
    assert me["workspace"]["role"] == "admin" and "manage_users" in me["capabilities"]
    c.headers["X-Intelliq-CSRF"] = me["csrf_token"]
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_state_changing_requests_require_csrf(api):
    c = api.client("author")
    del c.headers["X-Intelliq-CSRF"]
    r = c.post("/api/query", json={"dataset_id": "superstore", "measures": [{"field": "sales"}]})
    assert r.status_code == 403 and "CSRF" in r.json()["message"]
    c.headers["X-Intelliq-CSRF"] = "forged"
    assert c.post("/api/query", json={"dataset_id": "superstore", "measures": [{"field": "sales"}]}).status_code == 403


def test_login_rate_limit(api, monkeypatch):
    from intelliq.platform.ratelimit import RateLimiter

    api.app.state.ctx.login_limiter = RateLimiter(3)
    c = api.client()
    codes = [c.post("/api/auth/login", json={"email": "admin@example.com", "password": "nope-nope-nope"}).status_code
             for _ in range(4)]
    assert codes[-1] == 429


def test_security_headers(api):
    r = api.client().get("/api/health")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


# ------------------------------------------------------------------- authorization
@pytest.mark.parametrize("method, path, body", [
    ("post", "/api/query", {"dataset_id": "superstore", "measures": [{"field": "sales"}]}),
    ("post", "/api/calc/validate", {"dataset_id": "superstore", "formula": "SUM([Sales])"}),
    ("get", "/api/datasets", None),
    ("get", "/api/datasets/superstore/values/customer_name", None),
    ("get", "/api/connections", None),
    ("get", "/api/admin/users", None),
    ("get", "/api/admin/audit", None),
    ("post", "/api/dashboards", {"spec": {"title": "x"}}),
    ("get", "/api/imports", None),
])
def test_viewer_cannot_reach_authoring_or_admin_endpoints(api, method, path, body):
    c = api.client("viewer")
    r = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
    assert r.status_code == 403


def test_author_cannot_administer(api):
    c = api.client("author")
    assert c.get("/api/connections").status_code == 403
    assert c.get("/api/admin/users").status_code == 403
    assert c.post("/api/query", json={"dataset_id": "superstore", "measures": [{"field": "sales"}]}).status_code == 200


def test_viewers_only_see_published_dashboards(api):
    author = api.client("author")
    draft = author.post("/api/dashboards", json={"spec": {"title": "Draft only", "worksheets": []}}).json()
    viewer = api.client("viewer")
    titles = [d["title"] for d in viewer.get("/api/dashboards").json()]
    assert "Draft only" not in titles and any(t.startswith("Sales Performance") for t in titles)
    assert viewer.get(f"/api/dashboards/{draft['id']}").status_code == 404
    author.post(f"/api/dashboards/{draft['id']}/publish", json={})
    assert viewer.get(f"/api/dashboards/{draft['id']}").status_code == 200


# ------------------------------------------------------------------------- data
def test_worksheet_data_with_runtime_state(api):
    c = api.client("admin")
    did = sample_dashboard_id(c)
    r = c.post(f"/api/dashboards/{did}/worksheets/by_region/data", json={"state": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key_map"] == {"region": "d0", "sales": "m0", "ratio": "m1"}
    assert len(body["rows"]) == 4 and body["sql"] is not None  # admins can see SQL
    filtered = c.post(f"/api/dashboards/{did}/worksheets/trend/data", json={"state": {
        "selections": [{"action_id": "region_filter", "tuples": [{"region": "East"}]}],
        "filters": {"f_segment": {"values": ["Consumer"]}}}}).json()
    assert {r[1] for r in filtered["rows"]} == {"Consumer"}  # columns: dims (month, segment), then measures


def test_viewer_cannot_inject_fields_or_formulas(api):
    c = api.client("viewer")
    did = sample_dashboard_id(c)
    r = c.post(f"/api/dashboards/{did}/worksheets/by_region/data", json={"state": {"formula": "SUM([Profit])"}})
    assert r.status_code == 422
    r = c.post(f"/api/dashboards/{did}/worksheets/by_region/data",
               json={"state": {"selections": [{"action_id": "nope", "tuples": []}]}})
    assert r.status_code == 400
    r = c.post(f"/api/dashboards/{did}/worksheets/by_region/data", json={"state": {}})
    assert r.status_code == 200 and r.json()["sql"] is None  # viewers never see SQL


def test_row_level_security_through_the_api(api):
    did = sample_dashboard_id(api.client("admin"))
    west = api.client("west")
    rows = west.post(f"/api/dashboards/{did}/worksheets/by_region/data", json={"state": {}}).json()["rows"]
    assert [r[0] for r in rows] == ["West"]
    # Even selecting another region returns nothing beyond the user's entitlement.
    rows = west.post(f"/api/dashboards/{did}/worksheets/by_region/data", json={"state": {
        "filters": {"f_segment": {"values": []}},
        "selections": [{"action_id": "region_filter", "tuples": [{"region": "East"}]}]}}).json()["rows"]
    assert [r[0] for r in rows] == ["West"]  # by_region is the action source, so it isn't filtered
    trend = west.post(f"/api/dashboards/{did}/worksheets/trend/data", json={"state": {
        "selections": [{"action_id": "region_filter", "tuples": [{"region": "East"}]}]}}).json()
    assert trend["rows"] == []
    # A viewer without the attribute sees nothing at all.
    viewer_rows = api.client("viewer").post(f"/api/dashboards/{did}/worksheets/by_region/data", json={"state": {}}).json()
    assert viewer_rows["rows"] == []


def test_filter_values_are_scoped_to_dashboard_filters(api):
    c = api.client("viewer")
    did = sample_dashboard_id(c)
    assert c.get(f"/api/dashboards/{did}/filters/f_segment/values").status_code == 200
    assert c.get(f"/api/dashboards/{did}/filters/customer_name/values").status_code == 404


def test_export_permission_and_formula_injection_guard(api):
    did = sample_dashboard_id(api.client("admin"))
    assert api.client("viewer").post(f"/api/dashboards/{did}/worksheets/managers/export.csv", json={}).status_code == 403
    r = api.client("author").post(f"/api/dashboards/{did}/worksheets/managers/export.csv", json={})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("Region,Regional Manager,Sales")
    from intelliq.api.routes.dashboards import _csv_cell

    assert _csv_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and _csv_cell(5) == 5


def test_calc_validation_endpoint(api):
    c = api.client("author")
    ok = c.post("/api/calc/validate", json={"dataset_id": "superstore", "formula": "SUM([Profit]) / SUM([Sales])"}).json()
    assert ok["ok"] and ok["level"] == "agg" and ok["role"] == "measure" and set(ok["dependencies"]) == {"profit", "sales"}
    bad = c.post("/api/calc/validate", json={"dataset_id": "superstore", "formula": "[Sales] + SUM([Sales])"}).json()
    assert not bad["ok"] and bad["error"]["code"] == "mixed_aggregation"


def test_dashboard_versioning(api):
    c = api.client("author")
    did = sample_dashboard_id(c)
    spec = c.get(f"/api/dashboards/{did}").json()["spec"]
    spec["title"] = "Sales Performance (edited)"
    saved = c.put(f"/api/dashboards/{did}", json={"spec": spec, "note": "Renamed"}).json()
    assert saved["current_version"] == 2 and saved["published_version"] == 1
    assert api.client("viewer").get(f"/api/dashboards/{did}").json()["spec"]["title"] == "Sales Performance (sample)"
    assert [v["version"] for v in c.get(f"/api/dashboards/{did}/versions").json()] == [2, 1]
    bad = c.put(f"/api/dashboards/{did}", json={"spec": {**spec, "worksheets": [{**spec["worksheets"][0], "dataset_id": "nope"}]}})
    assert bad.status_code == 422


# --------------------------------------------------------------------- connections
def test_connection_secrets_are_write_only(api, tmp_path):
    c = api.client("admin")
    r = c.post("/api/connections", json={"name": "PG", "kind": "postgres",
                                         "config": {"host": "db", "database": "x", "user": "u"},
                                         "secrets": {"password": "s3cret-value"}})
    assert r.status_code == 200 and "s3cret-value" not in r.text and r.json()["has_secret"]
    listing = c.get("/api/connections").text
    assert "s3cret-value" not in listing and "secret_enc" not in listing
    assert c.post("/api/connections", json={"name": "x", "kind": "postgres", "config": {"password": "p"}}).status_code == 400
    raw = api.store.connection_secret(r.json()["id"])
    assert raw and "s3cret-value" not in raw  # encrypted at rest
    audit = c.get("/api/admin/audit").text
    assert "s3cret-value" not in audit


def test_duckdb_paths_outside_roots_are_refused(api):
    c = api.client("admin")
    conn = c.post("/api/connections", json={"name": "bad", "kind": "duckdb", "config": {"path": "/etc/passwd"}}).json()
    test = c.post(f"/api/connections/{conn['id']}/test").json()
    assert not test["ok"] and "outside the allowed" in test["message"]


# ------------------------------------------------------------------------- import
def test_import_flow(api):
    c = api.client("admin")
    up = c.post("/api/imports", files={"file": ("superstore_overview.twb", FIXTURE.read_bytes(), "application/xml")})
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["mapping"]["datasources"] == {"federated.0a1b2c3": "superstore"}
    assert body["report"]["summary"]["unsupported"] >= 3
    assert len(body["preview"]["dashboards"]) == 1
    # Remap one field and re-run
    mapping = body["mapping"]
    mapping["fields"]["federated.0a1b2c3"]["Postal Code"] = "state"
    remapped = c.put(f"/api/imports/{body['id']}/mapping", json=mapping)
    assert remapped.status_code == 200
    bad = c.put(f"/api/imports/{body['id']}/mapping", json={"datasources": {"federated.0a1b2c3": "nope"}, "fields": {}})
    assert bad.status_code == 400
    applied = c.post(f"/api/imports/{body['id']}/apply").json()
    assert applied["status"] == "applied" and len(applied["dashboard_ids"]) == 1
    dash = c.get(f"/api/dashboards/{applied['dashboard_ids'][0]}").json()
    assert dash["source"] == "tableau" and dash["status"] == "draft"
    assert dash["spec"]["provenance"]["workbook_sha256"] == body["sha256"]
    dataset = c.get("/api/datasets/superstore").json()
    assert "Sales per Customer" in [f["name"] for f in dataset["fields"]]
    # Imported dashboards run on the same runtime
    for ws in dash["spec"]["worksheets"]:
        r = c.post(f"/api/dashboards/{dash['id']}/worksheets/{ws['id']}/data", json={"draft": True, "state": {}})
        assert r.status_code == 200, (ws["id"], r.text)
    assert c.post(f"/api/imports/{body['id']}/apply").status_code == 409
    actions = [e["action"] for e in c.get("/api/admin/audit").json()]
    assert "import.applied" in actions and "import.analyzed" in actions


def test_import_rejects_hostile_files(api):
    c = api.client("author")
    xxe = b'<?xml version="1.0"?><!DOCTYPE w [<!ENTITY x SYSTEM "file:///etc/passwd">]><workbook>&x;</workbook>'
    r = c.post("/api/imports", files={"file": ("evil.twb", xxe, "application/xml")})
    assert r.status_code == 400 and "forbidden" in r.json()["message"]
    r = c.post("/api/imports", files={"file": ("notebook.ipynb", b"{}", "application/json")})
    assert r.status_code == 400


def test_author_import_needing_new_fields_requires_admin(api):
    c = api.client("author")
    up = c.post("/api/imports", files={"file": ("w.twb", FIXTURE.read_bytes(), "application/xml")}).json()
    r = c.post(f"/api/imports/{up['id']}/apply")
    assert r.status_code == 403 and "administrator" in r.json()["message"]


# ---------------------------------------------------------------------- admin
def test_user_management_guards_last_admin(api):
    c = api.client("admin")
    users = c.get("/api/admin/users").json()
    admin = next(u for u in users if u["role"] == "admin")
    r = c.patch(f"/api/admin/users/{admin['id']}", json={"role": "viewer"})
    assert r.status_code == 400 and "at least one" in r.json()["message"]
    new = c.post("/api/admin/users", json={"email": "new@example.com", "name": "New", "password": "long-enough-pw",
                                           "role": "author", "attributes": {"region": ["East"]}})
    assert new.status_code == 200 and "password" not in new.text
    short = c.post("/api/admin/users", json={"email": "s@example.com", "name": "S", "password": "short", "role": "viewer"})
    assert short.status_code == 422
