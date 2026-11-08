import copy

import pytest
from pydantic import ValidationError

from agent.tools.formula_library import ASSUMPTION_DEFAULTS, FORMULA_LIBRARY
from schemas import ASSUMPTION_KEYS, SYSTEM_TYPES, RequirementSpec

WHATSAPP = {
    "system_type": "messaging / chat apps",
    "explicit_requirements": {
        "daily_active_users": 1_000_000,
        "regions": ["ap-south-1", "ap-southeast-1"],
        "monthly_budget_usd": 12_000,
    },
    "clarifications": {"voice_video": "Yes, voice and video", "compliance": "None"},
    "assumptions": {"messages_per_user_per_day": 40, "avg_message_size_kb": 1},
}


def whatsapp(**changes):
    spec = copy.deepcopy(WHATSAPP)
    spec.update(changes)
    return spec


def with_explicit(**changes):
    spec = copy.deepcopy(WHATSAPP)
    spec["explicit_requirements"].update(changes)
    return spec


def test_valid_whatsapp_spec_parses():
    spec = RequirementSpec.model_validate(WHATSAPP)
    assert spec.system_type == "messaging / chat apps"
    assert spec.explicit_requirements.daily_active_users == 1_000_000
    assert spec.assumptions["messages_per_user_per_day"] == 40
    assert spec.model_dump(mode="json")["explicit_requirements"]["regions"] == [
        "ap-south-1",
        "ap-southeast-1",
    ]


def test_optional_fields_default():
    spec = RequirementSpec.model_validate(
        {
            "system_type": "e-commerce platforms",
            "explicit_requirements": {"daily_active_users": 200_000},
        }
    )
    assert spec.explicit_requirements.regions == []
    assert spec.explicit_requirements.monthly_budget_usd is None
    assert spec.clarifications == {}
    assert spec.assumptions == {}


@pytest.mark.parametrize(
    "system_type", ["Messaging App", "messaging", "Messaging / Chat Apps", "other"]
)
def test_unknown_system_type_rejected_with_allowed_values(system_type):
    with pytest.raises(ValidationError) as exc:
        RequirementSpec.model_validate(whatsapp(system_type=system_type))
    assert "messaging / chat apps" in str(exc.value)


def test_unknown_assumption_key_rejected():
    with pytest.raises(ValidationError) as exc:
        RequirementSpec.model_validate(whatsapp(assumptions={"msgs_per_day": 60}))
    assert "messages_per_user_per_day" in str(exc.value)


@pytest.mark.parametrize("value", [0, -5])
def test_non_positive_assumption_rejected(value):
    with pytest.raises(ValidationError):
        RequirementSpec.model_validate(
            whatsapp(assumptions={"messages_per_user_per_day": value})
        )


@pytest.mark.parametrize("dau", [0, -1])
def test_non_positive_dau_rejected(dau):
    with pytest.raises(ValidationError):
        RequirementSpec.model_validate(with_explicit(daily_active_users=dau))


def test_missing_dau_rejected():
    spec = copy.deepcopy(WHATSAPP)
    del spec["explicit_requirements"]["daily_active_users"]
    with pytest.raises(ValidationError):
        RequirementSpec.model_validate(spec)


@pytest.mark.parametrize("region", ["India", "ap-south", "AP-SOUTH-1", "mumbai-1", ""])
def test_bad_region_rejected_with_examples(region):
    with pytest.raises(ValidationError) as exc:
        RequirementSpec.model_validate(with_explicit(regions=[region]))
    assert "ap-south-1" in str(exc.value)


def test_gov_region_accepted():
    spec = RequirementSpec.model_validate(with_explicit(regions=["us-gov-west-1"]))
    assert spec.explicit_requirements.regions == ["us-gov-west-1"]


def test_zero_budget_rejected():
    with pytest.raises(ValidationError):
        RequirementSpec.model_validate(with_explicit(monthly_budget_usd=0))


def test_extra_top_level_key_rejected():
    with pytest.raises(ValidationError):
        RequirementSpec.model_validate(whatsapp(features=["media"]))


def test_extra_nested_key_rejected():
    with pytest.raises(ValidationError):
        RequirementSpec.model_validate(with_explicit(latency_ms=200))


def test_every_system_type_is_a_formula_library_key():
    assert set(SYSTEM_TYPES) == set(FORMULA_LIBRARY)


def test_every_assumption_key_is_in_assumption_defaults():
    assert set(ASSUMPTION_KEYS) == set(ASSUMPTION_DEFAULTS)


def test_assumptions_description_lists_every_default():
    description = RequirementSpec.model_json_schema()["properties"]["assumptions"][
        "description"
    ]
    for key, default in ASSUMPTION_DEFAULTS.items():
        assert f"{key}={default:g}" in description
