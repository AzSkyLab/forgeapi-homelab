"""A selected size estimate has a defined shape before fallback pricing is considered."""

import pytest

from app.budgets import estimated_cost
from app.tenants import TenancyError


@pytest.mark.parametrize("entry", [None, 7, True, [], "bad"])
def test_malformed_selected_size_is_configuration_error(entry):
    with pytest.raises(TenancyError) as error:
        estimated_cost({"estimated_costs": {"small": entry, "dev": 60}}, "dev", "small")
    assert error.value.status == 503
    assert error.value.message == "invalid estimated cost configuration"


@pytest.mark.parametrize(
    ("costs", "size", "expected"),
    [
        ({"small": {"dev": 40}, "dev": 60}, "small", 40.0),
        ({"small": {"prd": 40}, "dev": 60}, "small", 60.0),
        ({"small": {"dev": None}, "dev": 60}, "small", 60.0),
        ({"dev": 60}, "small", 60.0),
        ({"small": None, "dev": 60}, None, 60.0),
        ({"large": None, "dev": 60}, "small", 60.0),
        (60, "small", 60.0),
    ],
)
def test_valid_selection_and_fallback_stay_unchanged(costs, size, expected):
    assert estimated_cost({"estimated_costs": costs}, "dev", size) == expected
