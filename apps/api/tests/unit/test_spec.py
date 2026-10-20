import copy

import pytest
from pydantic import ValidationError

from intelliq.platform.bootstrap import sample_dashboard_spec
from intelliq.sample.model import sample_dataset
from intelliq.spec.compile import RuntimeState, compile_worksheet
from intelliq.spec.dashboard import Dashboard, json_schema
from intelliq.spec.migrations import SpecMigrationError, load, upgrade


@pytest.fixture()
def spec():
    return Dashboard.model_validate(sample_dashboard_spec())


def test_sample_spec_is_valid_and_round_trips(spec):
    again = Dashboard.model_validate(spec.model_dump(mode="json"))
    assert again == spec


def test_json_schema_export():
    schema = json_schema()
    assert schema["title"].startswith("Intelliq Dashboard Specification")
    assert "Worksheet" in schema["$defs"]


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["layout"]["items"].append({"id": "x", "kind": "worksheet", "ref": "nope", "x": 0, "y": 0, "w": 1, "h": 1}),
     "unknown worksheet"),
    (lambda d: d["layout"]["items"][0].update({"x": 11, "w": 3}), "overflows"),
    (lambda d: d["actions"].append({"id": "a", "type": "filter", "source": "missing"}), "unknown source"),
    (lambda d: d["worksheets"].append(copy.deepcopy(d["worksheets"][0])), "unique"),
    (lambda d: d["worksheets"][0]["viz"]["fields"].append(
        {"key": "s", "role": "measure", "channel": "y", "use": {"field": "sales"}}), "unique"),
    (lambda d: d.update({"unexpected": 1}), "Extra inputs"),
    (lambda d: d["worksheets"][0].update({"id": "bad id!"}), "Invalid key"),
])
def test_invalid_specs_are_rejected(mutate, message):
    d = sample_dashboard_spec()
    mutate(d)
    with pytest.raises(ValidationError, match=message):
        Dashboard.model_validate(d)


def test_migration_framework():
    doc = {"spec_version": 0, "title": "Old", "panels": []}
    steps = {0: lambda d: {**{k: v for k, v in d.items() if k != "panels"}, "worksheets": d["panels"]}}
    upgraded = upgrade(doc, target=1, migrations=steps)
    assert upgraded["spec_version"] == 1 and upgraded["worksheets"] == [] and "panels" not in upgraded
    assert doc["spec_version"] == 0  # input untouched
    with pytest.raises(SpecMigrationError, match="newer"):
        upgrade({"spec_version": 99})
    with pytest.raises(SpecMigrationError, match="No migration"):
        upgrade({"spec_version": 0}, target=1, migrations={})
    assert load(sample_dashboard_spec()).title.startswith("Sales Performance")


def test_compile_applies_dashboard_filters_actions_and_drill(spec):
    ds = sample_dataset("c")
    state = RuntimeState.model_validate({
        "filters": {"f_segment": {"values": ["Consumer"]}, "f_year": {"values": ["2023-01-01", "2023-12-31"]}},
        "selections": [{"action_id": "region_filter", "tuples": [{"region": "West"}]}],
        "drill": ["Technology"],
    })
    req, key_map = compile_worksheet(spec, spec.worksheet("by_category"), state, ds)
    targets = {(f.target.field, f.op): f.values for f in req.filters if f.target}
    assert targets[("segment", "in")] == ["Consumer"]
    assert targets[("order_date", "between")] == ["2023-01-01", "2023-12-31"]
    assert targets[("region", "in")] == ["West"]  # cross-filter from by_region
    assert targets[("category", "in")] == ["Technology"]  # drill path
    assert req.dimensions[0].field == "sub_category"  # drilled one level down
    assert key_map == {"cat": "d0", "sales": "m0"}

    # The action's source worksheet is not filtered by its own selection.
    req_src, _ = compile_worksheet(spec, spec.worksheet("by_region"), state, ds)
    assert not any(f.target and f.target.field == "region" for f in req_src.filters)


def test_single_select_and_parameter_validation(spec):
    from intelliq.query.planner import PlanError

    d = spec.model_dump(mode="json")
    d["filters"][1]["control"] = "single_select"
    d["parameters"] = [{"id": "n", "name": "N", "data_type": "integer", "default": 5,
                        "allowed": {"type": "range", "min": 1, "max": 10}}]
    s = Dashboard.model_validate(d)
    ds = sample_dataset("c")
    with pytest.raises(PlanError, match="single value"):
        compile_worksheet(s, s.worksheets[0], RuntimeState.model_validate({"filters": {"f_segment": {"values": ["A", "B"]}}}), ds)
    with pytest.raises(PlanError, match="above its maximum"):
        compile_worksheet(s, s.worksheets[0], RuntimeState.model_validate({"parameters": {"n": 50}}), ds)
    req, _ = compile_worksheet(s, s.worksheets[0], RuntimeState(), ds)
    assert req.parameters[0].value == 5
