"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("INTELLIQ_DATA_DIR", ".intelliq")).resolve())
    secret_key: str | None = field(default_factory=lambda: os.environ.get("INTELLIQ_SECRET_KEY"))
    cookie_secure: bool = field(default_factory=lambda: _bool("INTELLIQ_COOKIE_SECURE", False))
    session_hours: int = field(default_factory=lambda: int(os.environ.get("INTELLIQ_SESSION_HOURS", "12")))
    dev_mode: bool = field(default_factory=lambda: _bool("INTELLIQ_DEV", False))
    web_dist: Path | None = field(
        default_factory=lambda: Path(os.environ["INTELLIQ_WEB_DIST"]).resolve() if os.environ.get("INTELLIQ_WEB_DIST") else None
    )
    max_upload_mb: int = field(default_factory=lambda: int(os.environ.get("INTELLIQ_MAX_UPLOAD_MB", "100")))
    query_rate_per_minute: int = field(default_factory=lambda: int(os.environ.get("INTELLIQ_QUERY_RATE", "240")))
    login_rate_per_minute: int = field(default_factory=lambda: int(os.environ.get("INTELLIQ_LOGIN_RATE", "10")))

    @property
    def metadata_path(self) -> Path:
        return self.data_dir / "metadata.sqlite3"

    @property
    def warehouse_dir(self) -> Path:
        return self.data_dir / "warehouses"
