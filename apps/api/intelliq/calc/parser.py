"""Lexer and recursive-descent parser for the Intelliq formula language.

The grammar intentionally follows Tableau's calculation syntax so imported
workbooks can be translated faithfully:

    IF [Profit] > 0 THEN 'Profitable' ELSEIF ISNULL([Profit]) THEN 'n/a' ELSE 'Loss' END
    {FIXED [Region] : SUM([Sales])}
    RUNNING_SUM(SUM([Sales]))
    [Order Date] >= #2024-01-01#

Precedence (low → high): OR, AND, NOT, comparison/IN, + -, * / %, ^, unary -.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .ast import (
    Binary,
    Case,
    FieldRef,
    Func,
    If,
    InList,
    Literal,
    Lod,
    LodKind,
    Node,
    ParamRef,
    Span,
    Unary,
)
from .errors import CalcError

KEYWORDS = {
    "IF", "THEN", "ELSEIF", "ELSE", "END", "CASE", "WHEN", "AND", "OR", "NOT",
    "TRUE", "FALSE", "NULL", "IN", "FIXED", "INCLUDE", "EXCLUDE",
}  # fmt: skip

MAX_FORMULA_LENGTH = 20_000
# Each nesting level costs ~10 Python frames in this recursive-descent parser.
MAX_DEPTH = 60


@dataclass(frozen=True)
class Token:
    kind: str  # NUMBER STRING FIELD DATE IDENT KW OP EOF
    value: str
    start: int
    end: int


_OPERATORS = ["<>", "!=", "<=", ">=", "==", "+", "-", "*", "/", "%", "^", "=", "<", ">", "(", ")", ",", "{", "}", ":"]  # fmt: skip
_NUMBER = re.compile(r"(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def tokenize(src: str) -> list[Token]:
    if len(src) > MAX_FORMULA_LENGTH:
        raise CalcError(f"Formula exceeds {MAX_FORMULA_LENGTH} characters", Span(0, 0))
    tokens: list[Token] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise CalcError("Unterminated block comment", Span(i, n))
            i = j + 2
            continue
        if ch in ("'", '"'):
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise CalcError("Unterminated string literal", Span(i, n))
                if src[j] == ch:
                    if j + 1 < n and src[j + 1] == ch:  # doubled quote escape
                        buf.append(ch)
                        j += 2
                        continue
                    break
                if src[j] == "\\" and j + 1 < n and src[j + 1] == ch:
                    buf.append(ch)
                    j += 2
                    continue
                buf.append(src[j])
                j += 1
            tokens.append(Token("STRING", "".join(buf), i, j + 1))
            i = j + 1
            continue
        if ch == "[":
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise CalcError("Unterminated field reference", Span(i, n))
                if src[j] == "]":
                    if j + 1 < n and src[j + 1] == "]":  # ]] escape
                        buf.append("]")
                        j += 2
                        continue
                    break
                buf.append(src[j])
                j += 1
            name = "".join(buf)
            if not name.strip():
                raise CalcError("Empty field reference", Span(i, j + 1))
            tokens.append(Token("FIELD", name, i, j + 1))
            i = j + 1
            continue
        if ch == "#":
            j = src.find("#", i + 1)
            if j < 0:
                raise CalcError("Unterminated date literal", Span(i, n))
            tokens.append(Token("DATE", src[i + 1 : j].strip(), i, j + 1))
            i = j + 1
            continue
        m = _NUMBER.match(src, i)
        if m and (ch.isdigit() or ch == "."):
            tokens.append(Token("NUMBER", m.group(0), i, m.end()))
            i = m.end()
            continue
        if ch == "." and src[i + 1 : i + 2] == "[":
            tokens.append(Token("OP", ".", i, i + 1))
            i += 1
            continue
        m = _IDENT.match(src, i)
        if m:
            word = m.group(0)
            kind = "KW" if word.upper() in KEYWORDS else "IDENT"
            tokens.append(Token(kind, word.upper() if kind == "KW" else word, i, m.end()))
            i = m.end()
            continue
        for op in _OPERATORS:
            if src.startswith(op, i):
                tokens.append(Token("OP", op, i, i + len(op)))
                i += len(op)
                break
        else:
            raise CalcError(f"Unexpected character {ch!r}", Span(i, i + 1))
    tokens.append(Token("EOF", "", n, n))
    return tokens


def _parse_date(text: str, span: Span) -> Literal:
    for fmt, kind in (
        ("%Y-%m-%d", "date"),
        ("%Y-%m-%d %H:%M:%S", "datetime"),
        ("%Y-%m-%dT%H:%M:%S", "datetime"),
        ("%Y-%m-%d %H:%M", "datetime"),
        ("%m/%d/%Y", "date"),
    ):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        value: date | datetime = parsed.date() if kind == "date" else parsed
        return Literal(value, kind, span)
    raise CalcError(f"Unrecognised date literal #{text}#", span)


class Parser:
    def __init__(self, src: str):
        self.src = src
        self.tokens = tokenize(src)
        self.pos = 0
        self.depth = 0

    # -- token helpers -------------------------------------------------
    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def at(self, kind: str, value: str | None = None) -> bool:
        t = self.tok
        return t.kind == kind and (value is None or t.value == value)

    def at_kw(self, *words: str) -> bool:
        return self.tok.kind == "KW" and self.tok.value in words

    def accept(self, kind: str, value: str | None = None) -> Token | None:
        if self.at(kind, value):
            return self.advance()
        return None

    def expect(self, kind: str, value: str | None = None, what: str | None = None) -> Token:
        if not self.at(kind, value):
            t = self.tok
            found = "end of formula" if t.kind == "EOF" else repr(t.value)
            raise CalcError(f"Expected {what or value or kind}, found {found}", Span(t.start, t.end))
        return self.advance()

    def _enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise CalcError("Formula is nested too deeply", Span(self.tok.start, self.tok.end))

    # -- grammar --------------------------------------------------------
    def parse(self) -> Node:
        if self.at("EOF"):
            raise CalcError("Formula is empty", Span(0, 0))
        node = self.expr()
        if not self.at("EOF"):
            t = self.tok
            raise CalcError(f"Unexpected {t.value!r}", Span(t.start, t.end))
        return node

    def expr(self) -> Node:
        self._enter()
        try:
            return self.or_expr()
        finally:
            self.depth -= 1

    def or_expr(self) -> Node:
        left = self.and_expr()
        while self.at_kw("OR"):
            self.advance()
            right = self.and_expr()
            left = Binary("OR", left, right, _join(left, right))
        return left

    def and_expr(self) -> Node:
        left = self.not_expr()
        while self.at_kw("AND"):
            self.advance()
            right = self.not_expr()
            left = Binary("AND", left, right, _join(left, right))
        return left

    def not_expr(self) -> Node:
        if self.at_kw("NOT"):
            t = self.advance()
            operand = self.not_expr()
            return Unary("NOT", operand, Span(t.start, _end(operand, t)))
        return self.comparison()

    def comparison(self) -> Node:
        left = self.additive()
        while True:
            if self.tok.kind == "OP" and self.tok.value in ("=", "==", "<>", "!=", "<", "<=", ">", ">="):
                op = self.advance().value
                op = {"==": "=", "!=": "<>"}.get(op, op)
                right = self.additive()
                left = Binary(op, left, right, _join(left, right))
                continue
            negated = False
            if self.at_kw("NOT") and self.tokens[self.pos + 1].kind == "KW" and self.tokens[self.pos + 1].value == "IN":
                self.advance()
                negated = True
            if self.at_kw("IN"):
                self.advance()
                self.expect("OP", "(")
                items = [self.expr()]
                while self.accept("OP", ","):
                    items.append(self.expr())
                close = self.expect("OP", ")")
                start = left.span.start if left.span else close.start
                left = InList(left, tuple(items), negated, Span(start, close.end))
                continue
            return left

    def additive(self) -> Node:
        left = self.multiplicative()
        while self.tok.kind == "OP" and self.tok.value in ("+", "-"):
            op = self.advance().value
            right = self.multiplicative()
            left = Binary(op, left, right, _join(left, right))
        return left

    def multiplicative(self) -> Node:
        left = self.power()
        while self.tok.kind == "OP" and self.tok.value in ("*", "/", "%"):
            op = self.advance().value
            right = self.power()
            left = Binary(op, left, right, _join(left, right))
        return left

    def power(self) -> Node:
        left = self.unary()
        if self.accept("OP", "^"):
            right = self.power()  # right-associative
            return Binary("^", left, right, _join(left, right))
        return left

    def unary(self) -> Node:
        if self.at("OP", "-"):
            t = self.advance()
            operand = self.unary()
            if isinstance(operand, Literal) and operand.kind in ("integer", "real"):
                return Literal(-operand.value, operand.kind, Span(t.start, _end(operand, t)))
            return Unary("-", operand, Span(t.start, _end(operand, t)))
        if self.at("OP", "+"):
            self.advance()
            return self.unary()
        return self.primary()

    def primary(self) -> Node:
        t = self.tok
        span = Span(t.start, t.end)
        if t.kind == "NUMBER":
            self.advance()
            text = t.value
            if any(c in text for c in ".eE"):
                return Literal(float(text), "real", span)
            return Literal(int(text), "integer", span)
        if t.kind == "STRING":
            self.advance()
            return Literal(t.value, "string", span)
        if t.kind == "DATE":
            self.advance()
            return _parse_date(t.value, span)
        if t.kind == "FIELD":
            self.advance()
            return self._maybe_qualified(t)
        if t.kind == "KW":
            if t.value in ("TRUE", "FALSE"):
                self.advance()
                return Literal(t.value == "TRUE", "boolean", span)
            if t.value == "NULL":
                self.advance()
                return Literal(None, "null", span)
            if t.value == "IF":
                return self.if_expr()
            if t.value == "CASE":
                return self.case_expr()
        if t.kind == "IDENT":
            self.advance()
            if self.at("OP", "("):
                return self.call(t)
            raise CalcError(
                f"Unknown identifier {t.value!r}. Field names must be wrapped in brackets, e.g. [{t.value}]",
                span,
            )
        if self.accept("OP", "("):
            inner = self.expr()
            self.expect("OP", ")")
            return inner
        if t.kind == "OP" and t.value == "{":
            return self.lod()
        found = "end of formula" if t.kind == "EOF" else repr(t.value)
        raise CalcError(f"Unexpected {found}", span)

    def _maybe_qualified(self, first: Token) -> Node:
        # Qualified reference: [Parameters].[Name] or [Datasource].[Field]
        if self.at("OP", "."):
            self.advance()
            second = self.expect("FIELD", what="field reference after '.'")
            span = Span(first.start, second.end)
            if first.value.strip().lower() == "parameters":
                return ParamRef(second.value, span)
            # Datasource-qualified field: the datasource is resolved by the
            # surrounding context, keep only the field name.
            return FieldRef(second.value, span)
        return FieldRef(first.value, Span(first.start, first.end))

    def call(self, name_tok: Token) -> Node:
        self.expect("OP", "(")
        args: list[Node] = []
        if not self.at("OP", ")"):
            args.append(self.expr())
            while self.accept("OP", ","):
                args.append(self.expr())
        close = self.expect("OP", ")", what="')' to close function call")
        return Func(name_tok.value.upper(), tuple(args), Span(name_tok.start, close.end))

    def if_expr(self) -> Node:
        start = self.advance()  # IF
        branches = []
        cond = self.expr()
        self.expect("KW", "THEN")
        branches.append((cond, self.expr()))
        else_ = None
        while True:
            if self.at_kw("ELSEIF"):
                self.advance()
                c = self.expr()
                self.expect("KW", "THEN")
                branches.append((c, self.expr()))
                continue
            if self.at_kw("ELSE"):
                self.advance()
                else_ = self.expr()
            break
        end = self.expect("KW", "END", what="END to close IF")
        return If(tuple(branches), else_, Span(start.start, end.end))

    def case_expr(self) -> Node:
        start = self.advance()  # CASE
        subject = self.expr()
        whens = []
        while self.at_kw("WHEN"):
            self.advance()
            value = self.expr()
            self.expect("KW", "THEN")
            whens.append((value, self.expr()))
        if not whens:
            raise CalcError("CASE requires at least one WHEN", Span(start.start, self.tok.end))
        else_ = None
        if self.at_kw("ELSE"):
            self.advance()
            else_ = self.expr()
        end = self.expect("KW", "END", what="END to close CASE")
        return Case(subject, tuple(whens), else_, Span(start.start, end.end))

    def lod(self) -> Node:
        open_ = self.advance()  # {
        if self.at_kw("FIXED", "INCLUDE", "EXCLUDE"):
            kind = LodKind(self.advance().value)
            dims: list[Node] = []
            if not self.at("OP", ":"):
                dims.append(self.expr())
                while self.accept("OP", ","):
                    dims.append(self.expr())
            self.expect("OP", ":", what="':' in level of detail expression")
        else:
            # {SUM([Sales])} is shorthand for a table-scoped FIXED expression.
            kind, dims = LodKind.FIXED, []
        inner = self.expr()
        close = self.expect("OP", "}", what="'}' to close level of detail expression")
        return Lod(kind, tuple(dims), inner, Span(open_.start, close.end))


def _end(node: Node, fallback: Token) -> int:
    return node.span.end if node.span else fallback.end


def _join(a: Node, b: Node) -> Span | None:
    if a.span and b.span:
        return Span(a.span.start, b.span.end)
    return a.span or b.span


def parse(formula: str) -> Node:
    return Parser(formula).parse()
