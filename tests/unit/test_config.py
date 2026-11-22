import pytest

from ingestion.synthetic import GeneratorConfig

pytestmark = pytest.mark.unit


def test_defaults_are_valid():
    GeneratorConfig().validate()


def test_school_year_label():
    assert GeneratorConfig(school_year_start=2025).school_year == "2025-2026"


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenants": 0},
        {"tenants": 51},
        {"schools_per_tenant": 0},
        {"students_per_school": 5001},
        {"attendance_days": 0},
        {"lms_events_per_student": -1},
        {"defect_rate": -0.01},
        {"defect_rate": 0.5},
        {"tenants": True},
        {"seed": "abc"},
    ],
)
def test_out_of_range_values_are_rejected(overrides):
    with pytest.raises(ValueError, match="must be"):
        GeneratorConfig(**overrides).validate()


def test_from_mapping_rejects_unknown_keys():
    with pytest.raises(ValueError, match="unknown config keys: real_data"):
        GeneratorConfig.from_mapping({"real_data": True})


def test_from_mapping_round_trip():
    config = GeneratorConfig(seed=3, tenants=1)
    assert GeneratorConfig.from_mapping(config.as_dict()) == config
