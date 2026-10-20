"""Application services shared by all routes."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..connectors.base import Connector, ConnectorError
from ..connectors.registry import get_connector
from ..platform.ratelimit import RateLimiter
from ..platform.secrets import SecretBox
from ..platform.store import Store
from ..query.executor import QueryExecutor
from ..semantic.model import Dataset


@dataclass
class AppContext:
    settings: Settings
    store: Store
    secrets: SecretBox
    executor: QueryExecutor
    login_limiter: RateLimiter
    query_limiter: RateLimiter

    def dataset(self, dataset_id: str, workspace_id: str) -> tuple[Dataset, int]:
        row = self.store.get_dataset(dataset_id)
        if row is None or row["workspace_id"] != workspace_id:
            raise LookupError("dataset")
        return Dataset.model_validate(row["model"]), row["version"]

    def connector_for(self, connection_id: str, workspace_id: str) -> Connector:
        conn = self.store.get_connection(connection_id)
        if conn is None or conn["workspace_id"] != workspace_id:
            raise ConnectorError("Connection not found")
        secrets = self.secrets.open(self.store.connection_secret(connection_id))
        return get_connector(conn["id"], conn["revision"], conn["kind"], conn["config"], secrets)
