import pytest

from intelliq.calc.errors import CalcError
from intelliq.calc.parser import parse
from intelliq.calc.typecheck import Typed, infer

FIELDS = {
    "Sales": Typed("real", "row"),
    "Quantity": Typed("integer", "row"),
    "Region": Typed("string", "row"),
    "Order Date": Typed("date", "row"),
    "Profit Ratio": Typed("real", "agg"),
}


def check(formula: str, params=None) -> Typed:
    def fields(name):
        if name not in FIELDS:
            raise CalcError(f"Unknown field [{name}]", code="unknown_field")
        return FIELDS[name]

    return infer(parse(formula), fields, (lambda n: params[n]) if params else None)


@pytest.mark.parametrize(
    "formula, data_type, level",
    [
        ("[Sales] * 2", "real", "row"),
        ("[Quantity] + 1", "integer", "row"),
        ("[Quantity] / 2", "real", "row"),
        ("SUM([Sales])", "real", "agg"),
        ("SUM([Quantity])", "integer", "agg"),
        ("COUNTD([Region])", "integer", "agg"),
        ("SUM([Sales]) / SUM([Quantity])", "real", "agg"),
        ("[Profit Ratio] * 100", "real", "agg"),
        ("RUNNING_SUM(SUM([Sales]))", "real", "table"),
        ("SUM([Sales]) / TOTAL(SUM([Sales]))", "real", "table"),
        ("RANK(SUM([Sales]), 'asc')", "integer", "table"),
        ("{FIXED [Region] : SUM([Sales])}", "real", "row"),
        ("AVG({INCLUDE [Region] : SUM([Sales])})", "real", "agg"),
        ("DATETRUNC('month', [Order Date])", "date", "row"),
        ("DATEPART('weekday', [Order Date])", "integer", "row"),
        ("[Order Date] + 7", "date", "row"),
        ("IF [Sales] > 0 THEN 'a' ELSE 'b' END", "string", "row"),
        ("IIF([Sales] > 0, 1, 2.5)", "real", "row"),
        ("[Region] + '-' + STR([Quantity])", "string", "row"),
        ("[Region] IN ('East', 'West')", "boolean", "row"),
        ("ZN([Sales])", "real", "row"),
        ("1 + 2", "integer", "const"),
    ],
)
def test_inference(formula, data_type, level):
    t = check(formula)
    assert (t.data_type, t.level) == (data_type, level)


@pytest.mark.parametrize(
    "formula, code",
    [
        ("[Sales] + SUM([Sales])", "mixed_aggregation"),
        ("IF SUM([Sales]) > 0 THEN [Region] END", "mixed_aggregation"),
        ("SUM(SUM([Sales]))", "nested_aggregation"),
        ("SUM([Profit Ratio])", "nested_aggregation"),
        ("RUNNING_SUM([Sales])", "table_calc_requires_aggregate"),
        ("{FIXED [Region] : [Sales]}", "invalid_lod"),
        ("[Region] * 2", "type_mismatch"),
        ("SUM([Region])", "type_mismatch"),
        ("[Region] + 1", "type_mismatch"),
        ("IF [Sales] THEN 1 END", "type_mismatch"),
        ("IF [Sales] > 0 THEN 1 ELSE 'x' END", "type_mismatch"),
        ("DATETRUNC('fortnight', [Order Date])", "invalid_argument"),
        ("DATETRUNC([Region], [Order Date])", "invalid_argument"),
        ("LEFT([Region])", "arity"),
        ("NOPE([Sales])", "unsupported_function"),
        ("[Missing]", "unknown_field"),
        ("[Parameters].[Nope]", "unknown_parameter"),
        ("RANK(SUM([Sales]), 'sideways')", "invalid_argument"),
    ],
)
def test_errors(formula, code):
    with pytest.raises(CalcError) as e:
        check(formula)
    assert e.value.code == code


def test_parameters_are_constants():
    t = check("[Sales] > [Parameters].[Threshold]", {"Threshold": "real"})
    assert t == Typed("boolean", "row")


def test_error_span_points_at_offending_argument():
    with pytest.raises(CalcError) as e:
        check("SUM([Sales]) + SUM(SUM([Sales]))")
    assert e.value.span.start == 19
