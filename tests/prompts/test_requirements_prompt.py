from agent.prompts_library import SYSTEM_PROMPTS
from agent.tools.formula_library import ASSUMPTION_DEFAULTS, FORMULA_LIBRARY

PROMPT = SYSTEM_PROMPTS["requirements"]


def test_prompt_has_no_json():
    assert "{" not in PROMPT and "}" not in PROMPT


def test_prompt_has_no_system_type_values():
    assert not [t for t in FORMULA_LIBRARY if t in PROMPT.lower()]


def test_prompt_has_no_assumption_keys():
    assert not [k for k in ASSUMPTION_DEFAULTS if k in PROMPT]


def test_prompt_has_no_output_field_names():
    for field in (
        "system_type",
        "explicit_requirements",
        "daily_active_users",
        "monthly_budget_usd",
    ):
        assert field not in PROMPT
