"""Typed expression tree for Intelliq calculations.

Formulas are parsed once into these immutable nodes. Every later stage
(type checking, dependency resolution, SQL compilation, result-stage
evaluation) walks the tree; nothing ever manipulates formula strings.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass(frozen=True)
class Span:
    """Character offsets into the source formula, for error reporting."""

    start: int
    end: int


class Node:
    span: Span | None

    def children(self) -> Iterator[Node]:
        return iter(())

    def walk(self) -> Iterator[Node]:
        yield self
        for child in self.children():
            yield from child.walk()


@dataclass(frozen=True)
class Literal(Node):
    value: Any  # int | float | str | bool | date | datetime | None
    kind: str  # "integer" | "real" | "string" | "boolean" | "date" | "datetime" | "null"
    span: Span | None = field(default=None, compare=False)


@dataclass(frozen=True)
class FieldRef(Node):
    """Reference to a semantic field: ``[Sales]``."""

    name: str
    span: Span | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ParamRef(Node):
    """Reference to a parameter: ``[Parameters].[Top N]``."""

    name: str
    span: Span | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ColumnRef(Node):
    """Physical column produced only by the planner when it binds a field to
    a table column. Never produced by the parser, so formulas cannot name
    arbitrary physical columns."""

    table_alias: str
    column: str
    data_type: str
    span: Span | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ResultRef(Node):
    """Reference to a column of an already-aggregated result. Produced by the
    planner when it splits table calculations from database aggregates."""

    column: str
    span: Span | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Unary(Node):
    op: str  # "-" | "NOT"
    operand: Node
    span: Span | None = field(default=None, compare=False)

    def children(self) -> Iterator[Node]:
        yield self.operand


@dataclass(frozen=True)
class Binary(Node):
    op: str  # + - * / % ^ = <> < <= > >= AND OR
    left: Node
    right: Node
    span: Span | None = field(default=None, compare=False)

    def children(self) -> Iterator[Node]:
        yield self.left
        yield self.right


@dataclass(frozen=True)
class InList(Node):
    operand: Node
    items: tuple[Node, ...]
    negated: bool = False
    span: Span | None = field(default=None, compare=False)

    def children(self) -> Iterator[Node]:
        yield self.operand
        yield from self.items


@dataclass(frozen=True)
class Func(Node):
    name: str  # upper-cased
    args: tuple[Node, ...]
    span: Span | None = field(default=None, compare=False)

    def children(self) -> Iterator[Node]:
        yield from self.args


@dataclass(frozen=True)
class If(Node):
    branches: tuple[tuple[Node, Node], ...]  # (condition, value)
    else_: Node | None
    span: Span | None = field(default=None, compare=False)

    def children(self) -> Iterator[Node]:
        for cond, value in self.branches:
            yield cond
            yield value
        if self.else_ is not None:
            yield self.else_


@dataclass(frozen=True)
class Case(Node):
    subject: Node
    whens: tuple[tuple[Node, Node], ...]  # (match value, result)
    else_: Node | None
    span: Span | None = field(default=None, compare=False)

    def children(self) -> Iterator[Node]:
        yield self.subject
        for value, result in self.whens:
            yield value
            yield result
        if self.else_ is not None:
            yield self.else_


class LodKind(StrEnum):
    FIXED = "FIXED"
    INCLUDE = "INCLUDE"
    EXCLUDE = "EXCLUDE"


@dataclass(frozen=True)
class Lod(Node):
    """Level-of-detail expression: ``{FIXED [Region] : SUM([Sales])}``."""

    kind: LodKind
    dimensions: tuple[Node, ...]
    expr: Node
    span: Span | None = field(default=None, compare=False)

    def children(self) -> Iterator[Node]:
        yield from self.dimensions
        yield self.expr


def transform(node: Node, fn) -> Node:
    """Bottom-up rewrite. ``fn`` receives a node whose children are already
    rewritten and returns a replacement (or the node itself)."""

    if isinstance(node, Unary):
        node = Unary(node.op, transform(node.operand, fn), node.span)
    elif isinstance(node, Binary):
        node = Binary(node.op, transform(node.left, fn), transform(node.right, fn), node.span)
    elif isinstance(node, InList):
        node = InList(
            transform(node.operand, fn), tuple(transform(i, fn) for i in node.items), node.negated, node.span
        )
    elif isinstance(node, Func):
        node = Func(node.name, tuple(transform(a, fn) for a in node.args), node.span)
    elif isinstance(node, If):
        node = If(
            tuple((transform(c, fn), transform(v, fn)) for c, v in node.branches),
            transform(node.else_, fn) if node.else_ is not None else None,
            node.span,
        )
    elif isinstance(node, Case):
        node = Case(
            transform(node.subject, fn),
            tuple((transform(v, fn), transform(r, fn)) for v, r in node.whens),
            transform(node.else_, fn) if node.else_ is not None else None,
            node.span,
        )
    elif isinstance(node, Lod):
        node = Lod(
            node.kind, tuple(transform(d, fn) for d in node.dimensions), transform(node.expr, fn), node.span
        )
    return fn(node)


def map_children(node: Node, fn) -> Node:
    """Return ``node`` with each direct child replaced by ``fn(child)``.
    Unlike ``transform`` this lets callers rewrite top-down and stop early."""

    if isinstance(node, Unary):
        return Unary(node.op, fn(node.operand), node.span)
    if isinstance(node, Binary):
        return Binary(node.op, fn(node.left), fn(node.right), node.span)
    if isinstance(node, InList):
        return InList(fn(node.operand), tuple(fn(i) for i in node.items), node.negated, node.span)
    if isinstance(node, Func):
        return Func(node.name, tuple(fn(a) for a in node.args), node.span)
    if isinstance(node, If):
        return If(
            tuple((fn(c), fn(v)) for c, v in node.branches),
            fn(node.else_) if node.else_ is not None else None,
            node.span,
        )
    if isinstance(node, Case):
        return Case(
            fn(node.subject),
            tuple((fn(v), fn(r)) for v, r in node.whens),
            fn(node.else_) if node.else_ is not None else None,
            node.span,
        )
    if isinstance(node, Lod):
        return Lod(node.kind, tuple(fn(d) for d in node.dimensions), fn(node.expr), node.span)
    return node


def field_refs(node: Node) -> list[str]:
    seen: list[str] = []
    for n in node.walk():
        if isinstance(n, FieldRef) and n.name not in seen:
            seen.append(n.name)
    return seen


def param_refs(node: Node) -> list[str]:
    seen: list[str] = []
    for n in node.walk():
        if isinstance(n, ParamRef) and n.name not in seen:
            seen.append(n.name)
    return seen


def to_formula(node: Node) -> str:
    """Render a tree back to canonical formula text (used for provenance,
    diffing and display; never for execution)."""

    if isinstance(node, Literal):
        if node.kind == "null":
            return "NULL"
        if node.kind == "boolean":
            return "TRUE" if node.value else "FALSE"
        if node.kind == "string":
            return "'" + str(node.value).replace("'", "''") + "'"
        if node.kind in ("date", "datetime"):
            return f"#{node.value.isoformat()}#"
        return repr(node.value)
    if isinstance(node, FieldRef):
        return "[" + node.name.replace("]", "]]") + "]"
    if isinstance(node, ParamRef):
        return "[Parameters].[" + node.name.replace("]", "]]") + "]"
    if isinstance(node, ResultRef):
        return f"<result:{node.column}>"
    if isinstance(node, ColumnRef):
        return f"<column:{node.table_alias}.{node.column}>"
    if isinstance(node, Unary):
        return f"{node.op} {to_formula(node.operand)}" if node.op == "NOT" else f"-{to_formula(node.operand)}"
    if isinstance(node, Binary):
        return f"({to_formula(node.left)} {node.op} {to_formula(node.right)})"
    if isinstance(node, InList):
        items = ", ".join(to_formula(i) for i in node.items)
        return f"{to_formula(node.operand)} {'NOT IN' if node.negated else 'IN'} ({items})"
    if isinstance(node, Func):
        return f"{node.name}({', '.join(to_formula(a) for a in node.args)})"
    if isinstance(node, If):
        parts = []
        for i, (c, v) in enumerate(node.branches):
            parts.append(f"{'IF' if i == 0 else 'ELSEIF'} {to_formula(c)} THEN {to_formula(v)}")
        if node.else_ is not None:
            parts.append(f"ELSE {to_formula(node.else_)}")
        return " ".join(parts) + " END"
    if isinstance(node, Case):
        whens = " ".join(f"WHEN {to_formula(v)} THEN {to_formula(r)}" for v, r in node.whens)
        tail = f" ELSE {to_formula(node.else_)}" if node.else_ is not None else ""
        return f"CASE {to_formula(node.subject)} {whens}{tail} END"
    if isinstance(node, Lod):
        dims = ", ".join(to_formula(d) for d in node.dimensions)
        sep = " " if dims else ""
        return f"{{{node.kind.value}{sep}{dims} : {to_formula(node.expr)}}}"
    raise TypeError(f"Unknown node {type(node).__name__}")
