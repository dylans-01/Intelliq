"""Resolution of semantic fields into expression trees, with type inference,
dependency tracking and lineage."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..calc import ast as A
from ..calc.errors import CalcError
from ..calc.parser import parse
from ..calc.typecheck import TypeChecker, Typed
from .model import Dataset, SemanticField


@dataclass
class Lineage:
    field_id: str
    depends_on: list[str] = field(default_factory=list)  # direct field ids
    columns: list[str] = field(default_factory=list)  # physical "alias.column"
    used_by: list[str] = field(default_factory=list)  # direct dependants


class SemanticModel:
    """Read-only view over a validated dataset definition."""

    def __init__(self, dataset: Dataset, parameter_types: dict[str, str] | None = None):
        self.dataset = dataset
        self.parameter_types = parameter_types or {}
        self._by_id = {f.id: f for f in dataset.fields}
        self._by_name = {f.name.lower(): f for f in dataset.fields}
        self._trees: dict[str, A.Node] = {}
        self._typed: dict[str, Typed] = {}
        self._errors: dict[str, CalcError] = {}
        self._resolving: list[str] = []

    # -- lookup -----------------------------------------------------------------
    def field(self, ref: str) -> SemanticField:
        """Find a field by id or (case-insensitive) display name."""

        f = self._by_id.get(ref) or self._by_name.get(ref.lower())
        if f is None:
            raise CalcError(f"Unknown field [{ref}]", code="unknown_field")
        return f

    def has_field(self, ref: str) -> bool:
        return ref in self._by_id or ref.lower() in self._by_name

    @property
    def fields(self) -> list[SemanticField]:
        return list(self.dataset.fields)

    # -- parsing & typing ---------------------------------------------------------
    def tree(self, f: SemanticField) -> A.Node:
        if f.formula is None:
            return A.FieldRef(f.name)
        if f.id not in self._trees:
            self._trees[f.id] = parse(f.formula)
        return self._trees[f.id]

    def param_type(self, name: str) -> str:
        if name not in self.parameter_types:
            raise CalcError(f"Unknown parameter '{name}'", code="unknown_parameter")
        return self.parameter_types[name]

    def checker(self) -> TypeChecker:
        return TypeChecker(self.type_of_ref, self.param_type)

    def type_of_ref(self, ref: str) -> Typed:
        return self.typed(self.field(ref))

    def typed(self, f: SemanticField) -> Typed:
        if f.id in self._typed:
            return self._typed[f.id]
        if f.id in self._errors:
            raise self._errors[f.id]
        if f.formula is None:
            t = Typed(f.data_type, "row")
            self._typed[f.id] = t
            return t
        if f.id in self._resolving:
            cycle = " → ".join(self._resolving[self._resolving.index(f.id):] + [f.id])
            raise CalcError(f"Circular reference between calculated fields: {cycle}", code="circular_reference")
        self._resolving.append(f.id)
        try:
            t = self.checker().check(self.tree(f))
        except CalcError as e:
            err = CalcError(f"In [{f.name}]: {e.message}" if not e.message.startswith("In [") else e.message,
                            e.span, e.code)
            self._errors[f.id] = err
            raise err from None
        finally:
            self._resolving.pop()
        self._typed[f.id] = t
        return t

    def check_formula(self, formula: str) -> tuple[A.Node, Typed]:
        tree = parse(formula)
        return tree, self.checker().check(tree)

    # -- expansion ----------------------------------------------------------------
    def expand(self, node: A.Node, _stack: tuple[str, ...] = ()) -> A.Node:
        """Inline calculated fields so the result references only column fields."""

        def rewrite(n: A.Node) -> A.Node:
            if isinstance(n, A.FieldRef):
                f = self.field(n.name)
                if f.formula is None:
                    return A.FieldRef(f.name, n.span)
                if f.id in _stack:
                    raise CalcError("Circular reference between calculated fields", n.span, "circular_reference")
                return self.expand(self.tree(f), _stack + (f.id,))
            return n

        return A.transform(node, rewrite)

    # -- dependencies & lineage -----------------------------------------------------
    def direct_dependencies(self, f: SemanticField) -> list[str]:
        if f.formula is None:
            return []
        try:
            refs = A.field_refs(self.tree(f))
        except CalcError:
            return []
        out = []
        for r in refs:
            if self.has_field(r):
                out.append(self.field(r).id)
        return out

    def lineage(self) -> dict[str, Lineage]:
        result = {f.id: Lineage(f.id, self.direct_dependencies(f)) for f in self.dataset.fields}
        for lin in result.values():
            for dep in lin.depends_on:
                result[dep].used_by.append(lin.field_id)

        def columns(fid: str, seen: frozenset[str]) -> list[str]:
            f = self._by_id[fid]
            if f.column:
                return [f.column]
            cols: list[str] = []
            for dep in result[fid].depends_on:
                if dep in seen:
                    continue
                for c in columns(dep, seen | {dep}):
                    if c not in cols:
                        cols.append(c)
            return cols

        for fid, lin in result.items():
            lin.columns = columns(fid, frozenset({fid}))
        return result

    def validate(self) -> list[dict]:
        """Return a list of problems (empty when the model is valid)."""

        problems = []
        for f in self.dataset.fields:
            try:
                t = self.typed(f)
            except CalcError as e:
                problems.append({"field": f.id, **e.to_dict()})
                continue
            if f.formula is not None:
                if t.data_type != "null" and _family(t.data_type) != _family(f.data_type):
                    problems.append({
                        "field": f.id, "code": "type_mismatch", "start": None, "end": None,
                        "message": f"[{f.name}] is declared {f.data_type} but its formula returns {t.data_type}",
                    })
                if f.role == "dimension" and t.level in ("agg", "table"):
                    problems.append({
                        "field": f.id, "code": "aggregate_dimension", "start": None, "end": None,
                        "message": f"[{f.name}] is an aggregate calculation and must be a measure",
                    })
        for rule in self.dataset.row_level_security:
            f = self._by_id[rule.field]
            if f.role != "dimension":
                problems.append({"field": f.id, "code": "invalid_rls", "start": None, "end": None,
                                 "message": "Row-level security must reference a dimension"})
        return problems


def _family(t: str) -> str:
    return {"integer": "number", "real": "number", "date": "temporal", "datetime": "temporal"}.get(t, t)
