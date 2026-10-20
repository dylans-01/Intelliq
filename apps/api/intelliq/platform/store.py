"""Metadata store (SQLite, WAL mode).

Holds users, workspaces, sessions, connections, versioned semantic models,
versioned dashboards, imports and the audit log. The interface is narrow so
it can be re-implemented on PostgreSQL for multi-node deployments.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = [
    # v1
    """
    CREATE TABLE users (
      id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
      password_hash TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
      attributes TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, last_login_at TEXT);
    CREATE TABLE workspaces (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
    CREATE TABLE memberships (
      user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      role TEXT NOT NULL CHECK (role IN ('admin', 'author', 'viewer')),
      PRIMARY KEY (user_id, workspace_id));
    CREATE TABLE sessions (
      token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      csrf_token TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, ip TEXT, user_agent TEXT);
    CREATE TABLE connections (
      id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      name TEXT NOT NULL, kind TEXT NOT NULL, config TEXT NOT NULL, secret_enc TEXT,
      revision INTEGER NOT NULL DEFAULT 1, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE datasets (
      id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      name TEXT NOT NULL, connection_id TEXT NOT NULL REFERENCES connections(id),
      current_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE dataset_versions (
      dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE, version INTEGER NOT NULL,
      model TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_by TEXT, created_at TEXT NOT NULL,
      PRIMARY KEY (dataset_id, version));
    CREATE TABLE dashboards (
      id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
      current_version INTEGER NOT NULL, published_version INTEGER, source TEXT NOT NULL DEFAULT 'native',
      created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE dashboard_versions (
      dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE, version INTEGER NOT NULL,
      spec TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_by TEXT, created_at TEXT NOT NULL,
      PRIMARY KEY (dashboard_id, version));
    CREATE TABLE imports (
      id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
      filename TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, status TEXT NOT NULL,
      workbook TEXT NOT NULL, report TEXT NOT NULL, mapping TEXT NOT NULL DEFAULT '{}',
      dashboard_ids TEXT NOT NULL DEFAULT '[]', created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, workspace_id TEXT, actor_id TEXT,
      action TEXT NOT NULL, target_type TEXT, target_id TEXT, detail TEXT NOT NULL DEFAULT '{}', ip TEXT);
    CREATE INDEX audit_ts ON audit_log (ts);
    CREATE INDEX sessions_user ON sessions (user_id);
    """,
]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:20]


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Read-only access. Uses a deferred transaction so concurrent readers
        never wait on SQLite's write lock (WAL gives each a consistent snapshot)."""

        conn = self._conn()
        conn.execute("BEGIN")
        try:
            yield conn
        finally:
            conn.execute("COMMIT")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Read-write transaction; takes the write lock up front to avoid upgrade deadlocks."""

        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def _migrate(self) -> None:
        with self.tx() as c:
            c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = c.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            version = int(row["value"]) if row else 0
            for i in range(version, len(SCHEMA)):
                # Statement by statement: executescript() would COMMIT our transaction.
                for stmt in [s.strip() for s in SCHEMA[i].split(";") if s.strip()]:
                    c.execute(stmt)
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(len(SCHEMA)),))

    # ---------------------------------------------------------------- users
    def create_user(self, email: str, name: str, password_hash: str, attributes: dict | None = None) -> dict:
        uid = new_id()
        with self.tx() as c:
            c.execute(
                "INSERT INTO users (id, email, name, password_hash, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, email.strip().lower(), name, password_hash, json.dumps(attributes or {}), now()),
            )
        return self.get_user(uid)  # type: ignore[return-value]

    def get_user(self, uid: str) -> dict | None:
        with self.read() as c:
            row = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return _user(row)

    def user_by_email(self, email: str) -> dict | None:
        with self.read() as c:
            row = c.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return _user(row)

    def list_users(self, workspace_id: str) -> list[dict]:
        with self.read() as c:
            rows = c.execute(
                "SELECT u.*, m.role FROM users u JOIN memberships m ON m.user_id = u.id "
                "WHERE m.workspace_id = ? ORDER BY u.name", (workspace_id,)
            ).fetchall()
        return [{**_user(r), "role": r["role"]} for r in rows]  # type: ignore[dict-item]

    def update_user(self, uid: str, **fields: Any) -> None:
        allowed = {"name", "password_hash", "is_active", "attributes", "last_login_at"}
        sets, values = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(k)
            sets.append(f"{k} = ?")
            values.append(json.dumps(v) if k == "attributes" else v)
        if sets:
            with self.tx() as c:
                c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*values, uid))

    # ----------------------------------------------------------- workspaces
    def create_workspace(self, name: str, wid: str | None = None) -> dict:
        wid = wid or new_id()
        with self.tx() as c:
            c.execute("INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)", (wid, name, now()))
        return {"id": wid, "name": name}

    def workspaces_for(self, uid: str) -> list[dict]:
        with self.read() as c:
            rows = c.execute(
                "SELECT w.id, w.name, w.settings, m.role FROM workspaces w JOIN memberships m ON m.workspace_id = w.id "
                "WHERE m.user_id = ? ORDER BY w.name", (uid,)
            ).fetchall()
        return [{"id": r["id"], "name": r["name"], "role": r["role"], "settings": json.loads(r["settings"])} for r in rows]

    def set_membership(self, uid: str, wid: str, role: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO memberships (user_id, workspace_id, role) VALUES (?, ?, ?) "
                "ON CONFLICT (user_id, workspace_id) DO UPDATE SET role = excluded.role", (uid, wid, role)
            )

    def role_in(self, uid: str, wid: str) -> str | None:
        with self.read() as c:
            row = c.execute("SELECT role FROM memberships WHERE user_id = ? AND workspace_id = ?", (uid, wid)).fetchone()
        return row["role"] if row else None

    def count_admins(self, wid: str) -> int:
        with self.read() as c:
            return c.execute(
                "SELECT COUNT(*) FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.workspace_id = ? AND m.role = 'admin' AND u.is_active = 1", (wid,)
            ).fetchone()[0]

    # ------------------------------------------------------------- sessions
    def create_session(self, token_hash: str, uid: str, csrf: str, expires_at: str, ip: str | None, ua: str | None) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
            c.execute(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at, ip, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (token_hash, uid, csrf, now(), expires_at, ip, (ua or "")[:300])
            )

    def get_session(self, token_hash: str) -> dict | None:
        with self.read() as c:
            row = c.execute("SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?", (token_hash, now())).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, uid: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))

    # ----------------------------------------------------------- connections
    def create_connection(self, wid: str, name: str, kind: str, config: dict, secret_enc: str | None, actor: str | None) -> dict:
        cid = new_id()
        with self.tx() as c:
            c.execute(
                "INSERT INTO connections (id, workspace_id, name, kind, config, secret_enc, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (cid, wid, name, kind, json.dumps(config), secret_enc, actor, now(), now())
            )
        return self.get_connection(cid)  # type: ignore[return-value]

    def update_connection(self, cid: str, *, name: str, config: dict, secret_enc: str | None) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE connections SET name = ?, config = ?, secret_enc = COALESCE(?, secret_enc), "
                "revision = revision + 1, updated_at = ? WHERE id = ?", (name, json.dumps(config), secret_enc, now(), cid)
            )

    def get_connection(self, cid: str) -> dict | None:
        with self.read() as c:
            row = c.execute("SELECT * FROM connections WHERE id = ?", (cid,)).fetchone()
        return _conn(row)

    def connection_secret(self, cid: str) -> str | None:
        """Encrypted credential blob; only the platform's SecretBox can open it."""

        with self.read() as c:
            row = c.execute("SELECT secret_enc FROM connections WHERE id = ?", (cid,)).fetchone()
        return row["secret_enc"] if row else None

    def list_connections(self, wid: str) -> list[dict]:
        with self.read() as c:
            rows = c.execute("SELECT * FROM connections WHERE workspace_id = ? ORDER BY name", (wid,)).fetchall()
        return [_conn(r) for r in rows]  # type: ignore[misc]

    # -------------------------------------------------------------- datasets
    def create_dataset(self, wid: str, did: str, name: str, connection_id: str, model: dict, actor: str | None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO datasets (id, workspace_id, name, connection_id, current_version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)", (did, wid, name, connection_id, now(), now())
            )
            c.execute(
                "INSERT INTO dataset_versions (dataset_id, version, model, note, created_by, created_at) VALUES (?, 1, ?, ?, ?, ?)",
                (did, json.dumps(model), "Created", actor, now()),
            )

    def save_dataset_version(self, did: str, name: str, model: dict, note: str, actor: str | None) -> int:
        with self.tx() as c:
            version = c.execute("SELECT current_version FROM datasets WHERE id = ?", (did,)).fetchone()[0] + 1
            c.execute(
                "INSERT INTO dataset_versions (dataset_id, version, model, note, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (did, version, json.dumps(model), note, actor, now()),
            )
            c.execute("UPDATE datasets SET current_version = ?, name = ?, updated_at = ? WHERE id = ?", (version, name, now(), did))
        return version

    def get_dataset(self, did: str, version: int | None = None) -> dict | None:
        with self.read() as c:
            row = c.execute("SELECT * FROM datasets WHERE id = ?", (did,)).fetchone()
            if row is None:
                return None
            v = version or row["current_version"]
            mv = c.execute("SELECT * FROM dataset_versions WHERE dataset_id = ? AND version = ?", (did, v)).fetchone()
        if mv is None:
            return None
        return {**dict(row), "version": v, "model": json.loads(mv["model"])}

    def list_datasets(self, wid: str) -> list[dict]:
        with self.read() as c:
            rows = c.execute(
                "SELECT d.*, v.model FROM datasets d JOIN dataset_versions v ON v.dataset_id = d.id AND v.version = d.current_version "
                "WHERE d.workspace_id = ? ORDER BY d.name", (wid,)
            ).fetchall()
        return [{**{k: r[k] for k in r.keys() if k != "model"}, "model": json.loads(r["model"])} for r in rows]

    def dataset_versions(self, did: str) -> list[dict]:
        with self.read() as c:
            rows = c.execute(
                "SELECT version, note, created_by, created_at FROM dataset_versions WHERE dataset_id = ? ORDER BY version DESC",
                (did,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ dashboards
    def create_dashboard(self, wid: str, title: str, spec: dict, actor: str | None, source: str = "native") -> str:
        did = new_id()
        with self.tx() as c:
            c.execute(
                "INSERT INTO dashboards (id, workspace_id, title, current_version, source, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?)", (did, wid, title, source, actor, now(), now())
            )
            c.execute(
                "INSERT INTO dashboard_versions (dashboard_id, version, spec, note, created_by, created_at) VALUES (?, 1, ?, ?, ?, ?)",
                (did, json.dumps(spec), "Created", actor, now()),
            )
        return did

    def save_dashboard_version(self, did: str, title: str, spec: dict, note: str, actor: str | None) -> int:
        with self.tx() as c:
            version = c.execute("SELECT current_version FROM dashboards WHERE id = ?", (did,)).fetchone()[0] + 1
            c.execute(
                "INSERT INTO dashboard_versions (dashboard_id, version, spec, note, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (did, version, json.dumps(spec), note, actor, now()),
            )
            c.execute("UPDATE dashboards SET current_version = ?, title = ?, updated_at = ? WHERE id = ?", (version, title, now(), did))
        return version

    def publish_dashboard(self, did: str, version: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE dashboards SET status = 'published', published_version = ?, updated_at = ? WHERE id = ?", (version, now(), did)
            )

    def unpublish_dashboard(self, did: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE dashboards SET status = 'draft', published_version = NULL, updated_at = ? WHERE id = ?", (now(), did))

    def get_dashboard(self, did: str, version: int | None = None) -> dict | None:
        with self.read() as c:
            row = c.execute("SELECT * FROM dashboards WHERE id = ?", (did,)).fetchone()
            if row is None:
                return None
            v = version or row["current_version"]
            sv = c.execute("SELECT * FROM dashboard_versions WHERE dashboard_id = ? AND version = ?", (did, v)).fetchone()
        if sv is None:
            return None
        return {**dict(row), "version": v, "spec": json.loads(sv["spec"]), "version_note": sv["note"]}

    def list_dashboards(self, wid: str, published_only: bool = False) -> list[dict]:
        q = "SELECT * FROM dashboards WHERE workspace_id = ?" + (" AND status = 'published'" if published_only else "")
        with self.read() as c:
            rows = c.execute(q + " ORDER BY updated_at DESC", (wid,)).fetchall()
        return [dict(r) for r in rows]

    def dashboard_versions(self, did: str) -> list[dict]:
        with self.read() as c:
            rows = c.execute(
                "SELECT version, note, created_by, created_at FROM dashboard_versions WHERE dashboard_id = ? ORDER BY version DESC",
                (did,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_dashboard(self, did: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM dashboards WHERE id = ?", (did,))

    # --------------------------------------------------------------- imports
    def create_import(self, wid: str, filename: str, sha256: str, size: int, workbook: dict, report: dict, actor: str | None) -> str:
        iid = new_id()
        with self.tx() as c:
            c.execute(
                "INSERT INTO imports (id, workspace_id, filename, sha256, size, status, workbook, report, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'analyzed', ?, ?, ?, ?, ?)",
                (iid, wid, filename, sha256, size, json.dumps(workbook), json.dumps(report), actor, now(), now()),
            )
        return iid

    def update_import(self, iid: str, **fields: Any) -> None:
        allowed = {"status", "report", "mapping", "dashboard_ids"}
        sets, values = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(k)
            sets.append(f"{k} = ?")
            values.append(v if k == "status" else json.dumps(v))
        with self.tx() as c:
            c.execute(f"UPDATE imports SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*values, now(), iid))

    def get_import(self, iid: str) -> dict | None:
        with self.read() as c:
            row = c.execute("SELECT * FROM imports WHERE id = ?", (iid,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for k in ("workbook", "report", "mapping", "dashboard_ids"):
            d[k] = json.loads(d[k])
        return d

    def list_imports(self, wid: str) -> list[dict]:
        with self.read() as c:
            rows = c.execute(
                "SELECT id, filename, sha256, size, status, report, dashboard_ids, created_by, created_at FROM imports "
                "WHERE workspace_id = ? ORDER BY created_at DESC", (wid,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["summary"] = json.loads(d.pop("report")).get("summary", {})
            d["dashboard_ids"] = json.loads(d["dashboard_ids"])
            out.append(d)
        return out

    # ----------------------------------------------------------------- audit
    def audit(self, action: str, *, actor: str | None, workspace: str | None = None, target_type: str | None = None,
              target_id: str | None = None, detail: dict | None = None, ip: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO audit_log (ts, workspace_id, actor_id, action, target_type, target_id, detail, ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), workspace, actor, action, target_type, target_id, json.dumps(detail or {}, default=str), ip),
            )

    def audit_entries(self, wid: str, limit: int = 200, before_id: int | None = None, action: str | None = None) -> list[dict]:
        q = "SELECT a.*, u.email AS actor_email FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id WHERE (a.workspace_id = ? OR a.workspace_id IS NULL)"
        args: list[Any] = [wid]
        if before_id:
            q += " AND a.id < ?"
            args.append(before_id)
        if action:
            q += " AND a.action LIKE ?"
            args.append(action.replace("%", "") + "%")
        q += " ORDER BY a.id DESC LIMIT ?"
        args.append(min(limit, 1000))
        with self.read() as c:
            rows = c.execute(q, args).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]


def _user(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["attributes"] = json.loads(d["attributes"])
    d["is_active"] = bool(d["is_active"])
    return d


def _conn(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["config"] = json.loads(d["config"])
    d["has_secret"] = bool(d.pop("secret_enc", None)) if "secret_enc" in d else False
    return d
