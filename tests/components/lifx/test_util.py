"""Shared ordinary-color timing policy."""

import pytest

from homeassistant.components.lifx.util import resolve_color_ramp_duration


@pytest.mark.parametrize(
    ("duration", "explicit", "ordinary", "expected"),
    [
        pytest.param(0.0, False, True, 0.25, id="implicit-zero"),
        pytest.param(0.1, False, True, 0.25, id="implicit-short"),
        pytest.param(0.8, False, True, 0.8, id="implicit-long"),
        pytest.param(0.0, True, True, 0.0, id="explicit-zero"),
        pytest.param(0.1, True, True, 0.1, id="explicit-short"),
        pytest.param(0.8, True, True, 0.8, id="explicit-long"),
        pytest.param(0.0, False, False, 0.0, id="hidden-preparation"),
        pytest.param(0.1, False, False, 0.1, id="on-off"),
    ],
)
def test_color_ramp_duration(
    duration: float, explicit: bool, ordinary: bool, expected: float
) -> None:
    """Only implicit ordinary writes acquire the shared minimum."""
    assert (
        resolve_color_ramp_duration(duration, explicit=explicit, ordinary=ordinary)
        == expected
    )
