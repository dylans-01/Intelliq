from datetime import date

import pytest

from intelliq.calc import ast as A
from intelliq.calc.errors import CalcError
from intelliq.calc.parser import parse


def test_precedence_and_associativity():
    tree = parse("1 + 2 * 3 - 4 / 2")
    assert A.to_formula(tree) == "((1 + (2 * 3)) - (4 / 2))"
    assert A.to_formula(parse("2 ^ 3 ^ 2")) == "(2 ^ (3 ^ 2))"
    assert A.to_formula(parse("NOT [A] AND [B] OR [C]")) == "((NOT [A] AND [B]) OR [C])"


def test_field_references_and_escapes():
    tree = parse("[Sub-Category] + [Weird]]Name]")
    assert A.field_refs(tree) == ["Sub-Category", "Weird]Name"]


def test_qualified_references():
    tree = parse("[Parameters].[Top N] + [federated.abc].[Sales]")
    assert A.param_refs(tree) == ["Top N"]
    assert A.field_refs(tree) == ["Sales"]


def test_string_literals_both_quotes_and_escapes():
    assert parse("'it''s'").value == "it's"
    assert parse('"say ""hi"""').value == 'say "hi"'


def test_date_literals():
    lit = parse("#2024-02-29#")
    assert lit.kind == "date" and lit.value == date(2024, 2, 29)
    assert parse("#2024-02-29 13:45:00#").kind == "datetime"
    with pytest.raises(CalcError):
        parse("#not a date#")


def test_if_elseif_else_case():
    tree = parse("IF [A] > 1 THEN 'x' ELSEIF [A] > 0 THEN 'y' ELSE 'z' END")
    assert isinstance(tree, A.If) and len(tree.branches) == 2 and tree.else_ is not None
    tree = parse("CASE [Region] WHEN 'East' THEN 1 WHEN 'West' THEN 2 ELSE 0 END")
    assert isinstance(tree, A.Case) and len(tree.whens) == 2


def test_lod_forms():
    fixed = parse("{FIXED [Region], [Segment] : SUM([Sales])}")
    assert isinstance(fixed, A.Lod) and fixed.kind == A.LodKind.FIXED and len(fixed.dimensions) == 2
    table_scoped = parse("{SUM([Sales])}")
    assert table_scoped.kind == A.LodKind.FIXED and table_scoped.dimensions == ()
    assert parse("{EXCLUDE [Region] : AVG([Sales])}").kind == A.LodKind.EXCLUDE
    assert parse("{FIXED : SUM([Sales])}").dimensions == ()


def test_in_and_not_in():
    tree = parse("[Region] NOT IN ('East', 'West')")
    assert isinstance(tree, A.InList) and tree.negated and len(tree.items) == 2


def test_comments_are_ignored():
    tree = parse("// leading comment\nSUM([Sales]) /* inline */ + 1")
    assert A.to_formula(tree) == "(SUM([Sales]) + 1)"


def test_negative_literals_fold():
    lit = parse("-5")
    assert isinstance(lit, A.Literal) and lit.value == -5


@pytest.mark.parametrize(
    "formula, fragment, start",
    [
        ("SUM([Sales]", "Expected ')' to close function call", 11),
        ("IF [A] THEN 1", "Expected END to close IF", 13),
        ("[Sales] +", "Unexpected end of formula", 9),
        ("Sales + 1", "Field names must be wrapped in brackets", 0),
        ("'unterminated", "Unterminated string literal", 0),
        ("{FIXED [A] SUM([B])}", "Expected ':'", 11),
        ("1 $ 2", "Unexpected character '$'", 2),
        ("", "Formula is empty", 0),
    ],
)
def test_errors_have_positions(formula, fragment, start):
    with pytest.raises(CalcError) as e:
        parse(formula)
    assert fragment in e.value.message
    assert e.value.span is not None and e.value.span.start == start


def test_depth_limit():
    with pytest.raises(CalcError, match="nested too deeply"):
        parse("(" * 400 + "1" + ")" * 400)


def test_roundtrip_is_stable():
    src = "IF ISNULL([Profit]) THEN 0 ELSE {FIXED [Region] : SUM([Profit])} / SUM([Sales]) END"
    once = A.to_formula(parse(src))
    assert A.to_formula(parse(once)) == once
