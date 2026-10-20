"""Versioned migrations for stored dashboard specifications.

Specs are stored with their ``spec_version``. When the schema changes, add a
function ``MIGRATIONS[n]`` that upgrades a version-``n`` document to ``n+1``
and bump ``SPEC_VERSION``. Stored documents are upgraded on read and
re-validated, so old dashboards keep working without a bulk rewrite.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from .dashboard import SPEC_VERSION, Dashboard

Migration = Callable[[dict[str, Any]], dict[str, Any]]

MIGRATIONS: dict[int, Migration] = {}


class SpecMigrationError(ValueError):
    pass


def upgrade(doc: dict[str, Any], *, target: int = SPEC_VERSION, migrations: dict[int, Migration] | None = None) -> dict[str, Any]:
    registry = MIGRATIONS if migrations is None else migrations
    out = copy.deepcopy(doc)
    # A newly authored document may omit the version; it is written against the current spec.
    version = out.setdefault("spec_version", target)
    if not isinstance(version, int) or isinstance(version, bool):
        raise SpecMigrationError("Document has no integer spec_version")
    if version > target:
        raise SpecMigrationError(f"Spec version {version} is newer than this server supports ({target})")
    while version < target:
        step = registry.get(version)
        if step is None:
            raise SpecMigrationError(f"No migration from spec version {version}")
        out = step(out)
        version += 1
        out["spec_version"] = version
    return out


def load(doc: dict[str, Any]) -> Dashboard:
    return Dashboard.model_validate(upgrade(doc))
