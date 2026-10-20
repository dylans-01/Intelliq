"""Semantic model: the canonical, versioned definition of business data.

A *dataset* binds a connection to a set of physical tables (a base table plus
explicit joins) and declares the business fields built on them. Fields are
either physical columns or calculations written in the Intelliq formula
language. Aggregate-level calculations (``SUM([Profit]) / SUM([Sales])``)
are the platform's *metrics*: each has exactly one definition here and is
referenced by id from any number of dashboards.

Physical identifiers (schemas, tables, columns) only ever come from this
model, which is authored by administrators and validated against the
connection's discovered schema. Dashboards and viewers can never name a
physical column directly.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DataType = Literal["integer", "real", "string", "boolean", "date", "datetime"]
Aggregation = Literal["sum", "avg", "min", "max", "count", "countd", "median", "attr", "stdev", "var"]

_ID = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$ \-]{0,127}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TableRef(Strict):
    """A physical table. ``schema_`` / ``database`` are optional qualifiers."""

    database: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    name: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("database", "schema_", "name")
    @classmethod
    def _identifier(cls, v: str | None) -> str | None:
        if v is not None and not _IDENT.match(v):
            raise ValueError(f"Invalid identifier {v!r}")
        return v


class JoinKey(Strict):
    left: str  # "alias.column"
    right: str  # "alias.column"

    @field_validator("left", "right")
    @classmethod
    def _qualified(cls, v: str) -> str:
        alias, _, column = v.partition(".")
        if not alias or not column or not _IDENT.match(column) or not _ID.match(alias):
            raise ValueError(f"Join key must look like 'alias.column', got {v!r}")
        return v


class Join(Strict):
    alias: str
    table: TableRef
    type: Literal["inner", "left"] = "left"
    on: list[JoinKey] = Field(min_length=1)

    @field_validator("alias")
    @classmethod
    def _alias(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("Alias must be lower-case letters, digits and underscores")
        return v


class Format(Strict):
    type: Literal["number", "currency", "percent", "date", "text"] = "number"
    decimals: int | None = Field(default=None, ge=0, le=10)
    currency: str = "USD"
    compact: bool = False  # 1.2K / 3.4M
    date_pattern: str | None = None  # e.g. "MMM yyyy"


class SemanticField(Strict):
    id: str
    name: str = Field(min_length=1, max_length=128)
    role: Literal["dimension", "measure"]
    data_type: DataType
    # Exactly one of column / formula
    column: str | None = None  # "alias.column"
    formula: str | None = Field(default=None, max_length=20_000)
    default_aggregation: Aggregation | None = None
    format: Format | None = None
    description: str = ""
    folder: str | None = None
    hidden: bool = False
    certified: bool = False
    owner: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("Field id must be lower-case letters, digits and underscores")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if v.strip() != v:
            raise ValueError("Field names cannot start or end with whitespace")
        return v

    @model_validator(mode="after")
    def _source(self) -> SemanticField:
        if (self.column is None) == (self.formula is None):
            raise ValueError(f"Field '{self.id}' must define exactly one of 'column' or 'formula'")
        if self.column is not None:
            JoinKey._qualified(self.column)
        if self.role == "measure" and self.column is not None and self.default_aggregation is None:
            self.default_aggregation = "sum"
        return self

    @property
    def is_calculated(self) -> bool:
        return self.formula is not None


class Hierarchy(Strict):
    id: str
    name: str
    levels: list[str] = Field(min_length=2)  # field ids, coarse → fine


class RowLevelSecurity(Strict):
    """Restrict rows to those where ``field`` equals one of the user's values
    for ``user_attribute``. A user lacking the attribute sees no rows."""

    field: str  # field id (must be a row-level dimension)
    user_attribute: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    exempt_roles: list[str] = Field(default_factory=lambda: ["admin"])


class DatasetSettings(Strict):
    week_start: Literal["sunday", "monday"] = "sunday"
    cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    max_rows: int = Field(default=50_000, ge=1, le=1_000_000)
    query_timeout_seconds: int = Field(default=60, ge=1, le=3_600)


class Dataset(Strict):
    id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    connection_id: str
    base: TableRef
    base_alias: str = "t0"
    joins: list[Join] = Field(default_factory=list)
    fields: list[SemanticField] = Field(default_factory=list)
    hierarchies: list[Hierarchy] = Field(default_factory=list)
    row_level_security: list[RowLevelSecurity] = Field(default_factory=list)
    settings: DatasetSettings = Field(default_factory=DatasetSettings)
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> Dataset:
        ids, names = set(), set()
        for f in self.fields:
            if f.id in ids:
                raise ValueError(f"Duplicate field id '{f.id}'")
            if f.name.lower() in names:
                raise ValueError(f"Duplicate field name '{f.name}'")
            ids.add(f.id)
            names.add(f.name.lower())
        aliases = {self.base_alias}
        for j in self.joins:
            if j.alias in aliases:
                raise ValueError(f"Duplicate table alias '{j.alias}'")
            aliases.add(j.alias)
        for j in self.joins:
            for key in j.on:
                for side in (key.left, key.right):
                    if side.split(".", 1)[0] not in aliases:
                        raise ValueError(f"Join key {side!r} references an unknown alias")
        for f in self.fields:
            if f.column and f.column.split(".", 1)[0] not in aliases:
                raise ValueError(f"Field '{f.id}' references unknown alias in {f.column!r}")
        for h in self.hierarchies:
            for level in h.levels:
                if level not in ids:
                    raise ValueError(f"Hierarchy '{h.id}' references unknown field '{level}'")
        for rule in self.row_level_security:
            if rule.field not in ids:
                raise ValueError(f"Row-level security references unknown field '{rule.field}'")
        return self

    def tables_by_alias(self) -> dict[str, TableRef]:
        out = {self.base_alias: self.base}
        out.update({j.alias: j.table for j in self.joins})
        return out
