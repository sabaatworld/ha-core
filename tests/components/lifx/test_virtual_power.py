"""Durable virtual-power recovery contracts."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
import math
from typing import Any, Literal
from unittest.mock import patch

from lifx import HSBK
import pytest

from homeassistant.components.lifx.util import parse_hsbk_changes
from homeassistant.components.lifx.virtual_power import (
    LIFXVirtualPowerStore,
    VirtualPowerImage,
    VirtualPowerRecord,
    VirtualPowerRequest,
    capture_image,
    darken_image,
    image_matches_layout,
    merge_image,
    resolve_recovery_image,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .helpers import (
    MockDevice,
    create_mock_light,
    create_mock_multizone_light,
    create_reference_ceiling_128_light,
    create_reference_ceiling_light,
    create_reference_extended_multizone_light,
    create_reference_legacy_multizone_light,
    create_reference_matrix_light,
)


def test_darken_preserves_multizone_resume_image() -> None:
    """Darkening must not mutate the saved image or device observations."""
    device = create_mock_multizone_light()
    original = tuple(device.state.zones)
    image = capture_image(device.state)
    dark = darken_image(image)
    assert image.zones == original
    assert tuple(color.brightness for color in dark.zones) == (0.0, 0.0)
    assert tuple(color.hue for color in dark.zones) == (300.0, 255.0)
    assert tuple(device.state.zones) == original


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(create_mock_light, id="single"),
        pytest.param(create_reference_legacy_multizone_light, id="legacy-strip"),
        pytest.param(create_reference_extended_multizone_light, id="extended-strip"),
        pytest.param(create_reference_matrix_light, id="five-tiles"),
        pytest.param(create_reference_ceiling_light, id="ceiling-64"),
        pytest.param(create_reference_ceiling_128_light, id="ceiling-128"),
    ],
)
async def test_image_round_trip(
    hass: HomeAssistant, factory: Callable[[], MockDevice]
) -> None:
    """Persistence retains complete supported addressing and component colors."""
    state = factory().state
    image = capture_image(state)
    assert image_matches_layout(image, state)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    tokens = await store.async_begin_many({state.serial: _off_request(image)})
    await store.async_finish(tokens[state.serial], "completed")
    restored = LIFXVirtualPowerStore(hass)
    await restored.async_load()
    assert restored.async_get_record(state.serial).image == image
    assert restored.async_get_record(state.serial).virtual_off
    assert restored.async_get_record(state.serial).resume_required


def _off_request(
    image: VirtualPowerImage, *, duration: float = 0
) -> VirtualPowerRequest:
    return VirtualPowerRequest(
        kind="virtual_off",
        before_image=image,
        target_image=darken_image(image),
        before_power_on=True,
        before_virtual_off=False,
        before_resume_required=False,
        duration=duration,
    )


def test_layout_and_partial_merge() -> None:
    """Changed coverage cannot reuse stale addresses; partial changes stay local."""
    state = create_mock_multizone_light().state
    image = capture_image(state)
    merged = merge_image(image, parse_hsbk_changes(brightness=0), zones=(1,))
    assert merged.zones[0].brightness == 1
    assert merged.zones[1].brightness == 0
    state.zone_count = 3
    assert not image_matches_layout(image, state)
    with pytest.raises(ValueError):
        capture_image(state)


@pytest.mark.parametrize(
    ("derived", "saved", "explicit", "expected"),
    [
        pytest.param(False, None, None, 0, id="unrelated-zero"),
        pytest.param(True, None, None, 1, id="missing-recovery"),
        pytest.param(True, 0.0, None, 0, id="prepared-zero"),
        pytest.param(True, 0.6, None, 0.6, id="legacy-brightness"),
        pytest.param(True, 0.6, 0, 0, id="explicit-zero"),
    ],
)
def test_recovery_brightness(
    derived: bool, saved: float | None, explicit: int | None, expected: float
) -> None:
    """Only missing virtual-derived brightness uses the approved fallback."""
    state = create_mock_multizone_light().state
    state.color = state.color.replace(brightness=0)
    state.zones = [color.replace(brightness=0) for color in state.zones]
    image = None if saved is None else VirtualPowerImage(HSBK(20, 0.5, saved, 3500))
    record = VirtualPowerRecord(resume_required=derived, image=image)
    changes = (
        parse_hsbk_changes()
        if explicit is None
        else parse_hsbk_changes(brightness=explicit)
    )
    resolved = resolve_recovery_image(record, state, changes)
    assert resolved.color.brightness == expected
    assert all(color.brightness == expected for color in resolved.zones)


async def test_save_barrier_and_stale_tokens(hass: HomeAssistant) -> None:
    """Dispatch waits for durable save and older preparations cannot win a race."""
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    image = capture_image(create_mock_light().state)
    entered = asyncio.Event()
    release = asyncio.Event()

    real_save = Store.async_save

    async def gated_save(
        storage: Store[dict[str, object]], data: dict[str, object]
    ) -> None:
        entered.set()
        await release.wait()
        await real_save(storage, data)

    with patch(
        "homeassistant.components.lifx.virtual_power.Store.async_save",
        side_effect=gated_save,
        autospec=True,
    ):
        first = asyncio.create_task(store.async_begin_many({"a": _off_request(image)}))
        await entered.wait()
        assert not first.done()
        second = asyncio.create_task(store.async_begin_many({"a": _off_request(image)}))
        await asyncio.sleep(0)
        release.set()
        assert await first == {}
        tokens = await second
    assert tokens["a"].revision == 2
    assert (
        await store.async_finish(replace(tokens["a"], revision=1), "completed") is False
    )
    assert await store.async_finish(tokens["a"], "completed") is True


async def test_old_reservation_cannot_overtake_newer_request(
    hass: HomeAssistant,
) -> None:
    """An await before preparation must not turn an old caller into the newest."""
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    image = capture_image(create_mock_light().state)
    reserved = store.async_reserve("a")
    newer = await store.async_begin_many({"a": _off_request(image)})
    old = await store.async_begin_many(
        {"a": _off_request(image)}, expected={"a": reserved}
    )
    assert old == {}
    assert store.async_is_current(newer["a"])


async def test_save_failure_prevents_dispatch(hass: HomeAssistant) -> None:
    """A failed preparation cannot yield a dispatchable token."""
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    with (
        patch(
            "homeassistant.components.lifx.virtual_power.Store.async_save",
            side_effect=OSError("disk full"),
        ),
        pytest.raises(HomeAssistantError),
    ):
        await store.async_begin_many(
            {"a": _off_request(capture_image(create_mock_light().state))}
        )


async def test_pending_restart_and_reconcile(hass: HomeAssistant) -> None:
    """A lost ACK and partial image retain the pre-off candidate across restart."""
    state = create_mock_multizone_light().state
    state.power = 65535
    image = capture_image(state)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    tokens = await store.async_begin_many({state.serial: _off_request(image)})
    await store.async_finish(tokens[state.serial], "uncertain")
    restored = LIFXVirtualPowerStore(hass)
    await restored.async_load()
    state.zones[0] = state.zones[0].replace(brightness=0)
    await restored.async_reconcile(
        state.serial, state, now=dt_util.utcnow() + timedelta(seconds=1)
    )
    record = restored.async_get_record(state.serial)
    assert record.pending is not None
    assert record.pending.delivery == "uncertain"
    assert record.image == image
    state.zones[1] = state.zones[1].replace(brightness=0)
    await restored.async_reconcile(
        state.serial, state, now=dt_util.utcnow() + timedelta(seconds=2)
    )
    record = restored.async_get_record(state.serial)
    assert record.pending is None
    assert record.virtual_off
    assert record.image == image


async def test_legacy_import_is_once_and_invalid_records_are_independent(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A corrupt neighbor or late restore cannot destroy authoritative recovery."""
    hass_storage["lifx.virtual_power"] = {
        "version": 1,
        "data": {"records": {"bad": {"image": {"color": {"brightness": -1}}}}},
    }
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    color = HSBK(20, 0.5, 0.7, 3500)
    assert await store.async_import_legacy(
        "a", {"virtual_off": True, "resume_hsbk": color.as_dict}
    )
    assert not await store.async_import_legacy("a", {"virtual_off": False})
    assert store.async_get_record("a").image.color == color
    assert store.async_get_record("bad").image is None


async def test_repeated_off_and_actual_off_keep_original_recovery(
    hass: HomeAssistant,
) -> None:
    """Repeated destructive requests cannot replace recovery with observed black."""
    state = create_mock_multizone_light().state
    state.power = 65535
    image = capture_image(state)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    first = await store.async_begin_many({state.serial: _off_request(image)})
    await store.async_finish(first[state.serial], "completed")
    repeated = replace(
        _off_request(darken_image(image)),
        before_virtual_off=True,
        before_resume_required=True,
    )
    second = await store.async_begin_many({state.serial: repeated})
    await store.async_finish(second[state.serial], "completed")
    actual = await store.async_begin_many(
        {state.serial: replace(repeated, kind="actual_off")}
    )
    await store.async_finish(actual[state.serial], "completed")
    record = store.async_get_record(state.serial)
    assert not record.virtual_off
    assert record.resume_required
    assert record.image == image


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param("completed", id="actual-off-applied"),
        pytest.param("not_sent", id="replacement-not-sent"),
    ],
)
async def test_uncertain_off_replaced_by_actual_off_keeps_recovery(
    hass: HomeAssistant, outcome: Literal["completed", "not_sent"]
) -> None:
    """Actual off after an uncertain virtual off cannot strand recovery."""
    state = create_mock_light().state
    state.power = 65535
    image = capture_image(state)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    first = await store.async_begin_many({state.serial: _off_request(image)})
    await store.async_finish(first[state.serial], "uncertain")
    dark_state = create_mock_light().state
    dark_state.color = dark_state.color.replace(brightness=0)
    dark_image = capture_image(dark_state)
    actual = await store.async_begin_many(
        {state.serial: replace(_off_request(dark_image), kind="actual_off")}
    )
    await store.async_finish(actual[state.serial], outcome)
    record = store.async_get_record(state.serial)
    assert record.image == image
    assert record.resume_required
    assert not record.virtual_off
    assert (
        resolve_recovery_image(
            record, dark_state, parse_hsbk_changes()
        ).color.brightness
        == image.color.brightness
    )
    restored = LIFXVirtualPowerStore(hass)
    await restored.async_load()
    reloaded = restored.async_get_record(state.serial)
    assert reloaded.image == image
    assert reloaded.resume_required


async def test_prepared_restart_preserves_fade_bound(hass: HomeAssistant) -> None:
    """A restarted process must not mistake intermediate fade visibility for failure."""
    state = create_mock_light().state
    state.power = 65535
    image = capture_image(state)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    await store.async_begin_many({state.serial: _off_request(image, duration=10)})
    restored = LIFXVirtualPowerStore(hass)
    await restored.async_load()
    state.color = state.color.replace(brightness=0.4)
    await restored.async_reconcile(state.serial, state, now=dt_util.utcnow())
    assert restored.async_get_record(state.serial).pending.delivery == "prepared"
    assert restored.async_get_record(state.serial).image == image
    state.power = 0
    await restored.async_reconcile(
        state.serial, state, now=dt_util.utcnow() + timedelta(seconds=11)
    )
    assert restored.async_get_record(state.serial).pending is None
    assert restored.async_get_record(state.serial).resume_required


async def test_unchanged_pending_image_resolves_not_applied(
    hass: HomeAssistant,
) -> None:
    """An unchanged complete observation resolves a missing ACK without false off."""
    state = create_mock_multizone_light().state
    state.power = 65535
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    tokens = await store.async_begin_many(
        {state.serial: _off_request(capture_image(state))}
    )
    await store.async_finish(tokens[state.serial], "uncertain")
    await store.async_reconcile(
        state.serial, state, now=dt_util.utcnow() + timedelta(seconds=1)
    )
    assert store.async_get_record(state.serial).pending is None
    assert not store.async_get_record(state.serial).virtual_off
    assert not store.async_get_record(state.serial).resume_required


async def test_incomplete_observation_retains_pending_image(
    hass: HomeAssistant,
) -> None:
    """Missing pixels cannot resolve uncertainty or replace a complete candidate."""
    state = create_reference_matrix_light().state
    image = capture_image(state)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    await store.async_begin_many({state.serial: _off_request(image)})
    state.tile_colors.pop()
    await store.async_reconcile(
        state.serial, state, now=dt_util.utcnow() + timedelta(seconds=1)
    )
    assert store.async_get_record(state.serial).pending is not None
    assert store.async_get_record(state.serial).image == image


async def test_save_failure_invalidates_undispatched_intent(
    hass: HomeAssistant,
) -> None:
    """A failed durable barrier must not become an issued off during later polls."""
    state = create_mock_light().state
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    with (
        patch(
            "homeassistant.components.lifx.virtual_power.Store.async_save",
            side_effect=OSError("disk full"),
        ),
        pytest.raises(HomeAssistantError),
    ):
        await store.async_begin_many({state.serial: _off_request(capture_image(state))})
    assert store.async_get_record(state.serial).pending is None


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(create_reference_matrix_light, id="tile"),
        pytest.param(create_reference_ceiling_128_light, id="ceiling"),
    ],
)
def test_layout_change_and_pixel_merge(factory: Callable[[], MockDevice]) -> None:
    """Tile dimensions and addresses are part of compatibility, including Ceiling."""
    state = factory().state
    image = capture_image(state)
    merged = merge_image(image, parse_hsbk_changes(brightness=0), zones=(0,))
    assert merged.tiles[0].colors[0].brightness == 0
    assert merged.tiles[0].colors[1].brightness == 0.5
    assert darken_image(image).tiles[-1].colors[-1].brightness == 0
    state.chain[0].tile_index = 9
    assert not image_matches_layout(image, state)
    assert (
        resolve_recovery_image(
            VirtualPowerRecord(resume_required=True, image=image),
            state,
            parse_hsbk_changes(),
        )
        .tiles[0]
        .index
        == 9
    )


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(-1, id="negative"),
        pytest.param(math.nan, id="nan"),
        pytest.param("0.5", id="string"),
        pytest.param(True, id="boolean"),
    ],
)
async def test_invalid_saved_color_retains_recovery_flags(
    hass: HomeAssistant, bad: object
) -> None:
    """Invalid numeric data cannot block setup or discard virtual-derived history."""
    payload = {
        "records": {
            "a": {
                "virtual_off": True,
                "resume_required": True,
                "revision": 3,
                "pending": None,
                "image": {
                    "color": {
                        "hue": 0,
                        "saturation": 0,
                        "brightness": bad,
                        "kelvin": 3500,
                    }
                },
            },
            "b": {
                "virtual_off": False,
                "resume_required": False,
                "revision": 2,
                "pending": None,
                "image": None,
            },
        }
    }
    store = LIFXVirtualPowerStore(hass)
    with patch(
        "homeassistant.components.lifx.virtual_power.Store.async_load",
        return_value=payload,
    ):
        await store.async_load()
    assert store.async_get_record("a").resume_required
    assert store.async_get_record("a").image is None
    assert store.async_get_record("b").revision == 2


async def test_silently_failed_save_cannot_release_dispatch(
    hass: HomeAssistant,
) -> None:
    """HA Store can log write failure and return normally; that is not durability."""
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    with (
        patch("homeassistant.components.lifx.virtual_power.Store.async_save"),
        pytest.raises(HomeAssistantError),
    ):
        await store.async_begin_many(
            {"a": _off_request(capture_image(create_mock_light().state))}
        )


async def test_partial_visibility_does_not_replace_protected_pattern(
    hass: HomeAssistant,
) -> None:
    """Visible partial output reports on while retaining the original off recovery."""
    state = create_mock_multizone_light().state
    state.power = 65535
    image = capture_image(state)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    first = await store.async_begin_many({state.serial: _off_request(image)})
    await store.async_finish(first[state.serial], "completed")
    second = await store.async_begin_many(
        {
            state.serial: replace(
                _off_request(image),
                before_virtual_off=True,
                before_resume_required=True,
            )
        }
    )
    await store.async_finish(second[state.serial], "uncertain")
    state.zones[0] = state.zones[0].replace(brightness=0)
    await store.async_reconcile(
        state.serial, state, now=dt_util.utcnow() + timedelta(seconds=1)
    )
    record = store.async_get_record(state.serial)
    assert not record.virtual_off
    assert record.pending is not None
    assert record.image == image


def test_snapshot_alone_does_not_activate_recovery() -> None:
    """An old clean baseline is not a restoration obligation."""
    state = create_mock_light().state
    state.color = state.color.replace(brightness=0.4)
    record = VirtualPowerRecord(image=VirtualPowerImage(HSBK(120, 1, 0.8, 4000)))
    assert (
        resolve_recovery_image(record, state, parse_hsbk_changes()).color.brightness
        == 0.4
    )


async def test_missing_legacy_image_accepts_new_valid_candidate(
    hass: HomeAssistant,
) -> None:
    """Missing old data must not suppress a newly usable pre-write snapshot."""
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    await store.async_import_legacy("a", {"virtual_off": True})
    image = capture_image(create_mock_light().state)
    await store.async_begin_many({"a": _off_request(image)})
    assert store.async_get_record("a").image == image


@pytest.mark.parametrize(
    ("outcome", "observed_power"),
    [
        pytest.param("completed", 0, id="completed-actual-off"),
        pytest.param("not_sent", 65535, id="not-sent-replacement"),
    ],
)
async def test_uncertain_off_replacement_preserves_recovery(
    hass: HomeAssistant,
    outcome: Literal["completed", "not_sent"],
    observed_power: int,
) -> None:
    """Replacing unresolved virtual off must retain its durable recovery obligation."""
    state = create_mock_light().state
    state.power = 65535
    state.color = state.color.replace(brightness=0.7)
    image = capture_image(state)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    tokens = await store.async_begin_many({state.serial: _off_request(image)})
    assert await store.async_finish(tokens[state.serial], "uncertain")
    assert not store.async_get_record(state.serial).virtual_off
    assert not store.async_get_record(state.serial).resume_required

    state.color = state.color.replace(brightness=0)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    before = store.async_get_record(state.serial)
    assert before.pending is not None
    assert before.pending.delivery == "uncertain"
    tokens = await store.async_begin_many(
        {
            state.serial: VirtualPowerRequest(
                kind="actual_off",
                before_image=capture_image(state),
                target_image=None,
                before_power_on=True,
                before_virtual_off=before.virtual_off,
                before_resume_required=before.resume_required,
                duration=0,
            )
        }
    )
    assert store.async_get_record(state.serial).image == image
    state.power = observed_power
    assert await store.async_finish(tokens[state.serial], outcome)

    restored = LIFXVirtualPowerStore(hass)
    await restored.async_load()
    for authority in (store, restored):
        record = authority.async_get_record(state.serial)
        assert record.image == image
        assert record.pending is None
        assert not record.virtual_off
        assert (
            resolve_recovery_image(record, state, parse_hsbk_changes()).color.brightness
            == 0.7
        )
        assert record.resume_required


@pytest.mark.parametrize(
    ("outcome", "observed_power"),
    [
        pytest.param("completed", 0, id="clean-completed-actual-off"),
        pytest.param("not_sent", 65535, id="clean-not-sent-replacement"),
    ],
)
async def test_actual_off_does_not_activate_clean_snapshot(
    hass: HomeAssistant,
    outcome: Literal["completed", "not_sent"],
    observed_power: int,
) -> None:
    """A retained clean image cannot create virtual-derived eligibility on its own."""
    state = create_mock_light().state
    state.power = 65535
    state.color = state.color.replace(brightness=0.7)
    image = capture_image(state)
    store = LIFXVirtualPowerStore(hass)
    await store.async_load()
    assert await store.async_import_legacy(
        state.serial, {"virtual_off": False, "resume_hsbk": state.color.as_dict}
    )
    tokens = await store.async_begin_many(
        {
            state.serial: replace(
                _off_request(image), kind="actual_off", target_image=None
            )
        }
    )
    assert await store.async_finish(tokens[state.serial], "uncertain")
    state.color = state.color.replace(brightness=0)
    tokens = await store.async_begin_many(
        {
            state.serial: replace(
                _off_request(capture_image(state)), kind="actual_off", target_image=None
            )
        }
    )
    assert await store.async_finish(tokens[state.serial], outcome)
    state.power = observed_power

    restored = LIFXVirtualPowerStore(hass)
    await restored.async_load()
    for authority in (store, restored):
        record = authority.async_get_record(state.serial)
        assert record.image == image
        assert record.pending is None
        assert not record.virtual_off
        assert not record.resume_required
        assert (
            resolve_recovery_image(record, state, parse_hsbk_changes()).color.brightness
            == 0
        )
