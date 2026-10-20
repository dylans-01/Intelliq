from intelliq.validation.compare import Tolerance, compare, normalise_key, to_number


def test_numbers_and_formats():
    assert to_number("$1,234.50") == 1234.5
    assert to_number("12.5%") == 0.125
    assert to_number("%null%") is None
    assert normalise_key("1/31/2024") == "2024-01-31"
    assert normalise_key("2024-01-31 00:00:00") == "2024-01-31"
    assert normalise_key(3.0) == "3"


def test_compare_detects_every_discrepancy_kind():
    actual = [{"Region": "East", "Sales": 100.004}, {"Region": "West", "Sales": 250}, {"Region": "North", "Sales": 1}]
    reference = [{"Region": "East", "Sales": "100"}, {"Region": "West", "Sales": "$260"}, {"Region": "South", "Sales": "5"}]
    result = compare(actual, reference, keys=["Region"], values=["Sales"], tolerance=Tolerance(absolute=0.005))
    kinds = sorted(d.kind for d in result.discrepancies)
    assert kinds == ["missing_row", "unexpected_row", "value"]
    assert result.matched_rows == 2 and not result.passed


def test_nulls_only_match_nulls():
    r = compare([{"k": "a", "v": None}], [{"k": "a", "v": ""}], keys=["k"], values=["v"])
    assert r.passed
    r = compare([{"k": "a", "v": 0}], [{"k": "a", "v": ""}], keys=["k"], values=["v"])
    assert not r.passed


def test_relative_tolerance():
    r = compare([{"k": 1, "v": 1_000_001}], [{"k": "1", "v": "1000000"}], keys=["k"], values=["v"],
                tolerance=Tolerance(absolute=0, relative=1e-5))
    assert r.passed
