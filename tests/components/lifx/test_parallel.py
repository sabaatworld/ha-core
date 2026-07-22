"""Tests for LIFX Device Group packet dispatch."""

import struct
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from homeassistant.components.lifx import parallel
from homeassistant.components.lifx.parallel import (
    ACKNOWLEDGEMENT,
    HEADER,
    SET_COLOR,
    SET_POWER,
    LIFXParallelRuntime,
    ParallelCommand,
    ParallelTransport,
    _dispatch_prepared,
    _DispatchRequest,
    _header,
    _set_color,
    _set_power,
    _wait_for_ack,
    _worker,
)
from homeassistant.exceptions import HomeAssistantError


def test_set_power_does_not_request_an_ack() -> None:
    """The packet builder supports protocol packets without an ACK flag."""
    packet = _set_power(1, 2, bytes(8), (True, 0))
    _size, _frame, _source, _target, _reserved, flags, _sequence, _r2, packet_type, _r3 = (
        HEADER.unpack_from(packet)
    )

    assert packet_type == SET_POWER
    assert flags & 0b10 == 0
    assert len(packet) == HEADER.size + struct.calcsize("<HI")


def test_echo_request_uses_the_lifx_online_check_packet() -> None:
    """An idle Group health check uses the LIFX EchoRequest packet."""
    assert parallel.ECHO_REQUEST == 58
    assert parallel.ECHO_RESPONSE == 59


def test_echo_request_carries_an_opaque_64_byte_token() -> None:
    """An EchoResponse can only satisfy the matching worker request."""
    token = bytes(range(64))

    packet = parallel._echo_request(1, 2, bytes(8), token)
    _size, _frame, _source, _target, _reserved, flags, _sequence, _r2, packet_type, _r3 = (
        HEADER.unpack_from(packet)
    )

    assert packet_type == parallel.ECHO_REQUEST
    assert flags & 0b10 == 0
    assert packet[HEADER.size :] == token


def test_worker_starts_with_the_known_member_transport() -> None:
    """A group worker must not rediscover an already loaded physical light."""
    transport = ParallelTransport(
        "192.0.2.1", 56700, b"\xaa\xbb\xcc\xdd\xee\xff\x00\x00"
    )
    udp = MagicMock()
    pipe = MagicMock()
    pipe.recv.return_value = ("SHUTDOWN",)

    with patch("homeassistant.components.lifx.parallel.socket.socket", return_value=udp):
        _worker(
            transport,
            1,
            pipe,
            SimpleNamespace(value=0),
            MagicMock(),
        )

    udp.connect.assert_called_once_with((transport.host, transport.port))
    udp.send.assert_not_called()
    pipe.send.assert_called_once_with(("STARTED",))


def test_wait_for_echo_requires_the_matching_response_payload() -> None:
    """Stale or wrong EchoResponse payloads cannot satisfy a health check."""
    target = bytes((1,)) * 8
    token = bytes(range(64))
    udp = MagicMock()
    pipe = MagicMock()
    pipe.poll.return_value = False
    udp.recv.side_effect = (
        parallel._header(parallel.ECHO_RESPONSE, 1, 2, target, 64) + bytes(64),
        parallel._header(parallel.ECHO_RESPONSE, 1, 2, target, 64) + token,
    )

    assert parallel._wait_for_echo(
        udp,
        pipe,
        1,
        target,
        2,
        token,
        3,
        0,
        0,
        SimpleNamespace(value=3),
        time.monotonic() + 1,
    )


def test_parallel_runtime_exposes_a_non_exceptional_dispatch_outcome() -> None:
    """Operational Device Group outcomes are values, not service exceptions."""
    assert parallel.ParallelDispatchOutcome.COMPLETED.value


def test_single_command_stage_requires_an_ack() -> None:
    """A standalone Device Group write has the physical-light retry policy."""
    runtime = LIFXParallelRuntime(MagicMock(), ("host",))
    runtime._workers = [
        SimpleNamespace(host="host", process=MagicMock(), pipe=MagicMock())
    ]
    runtime._current_generation = SimpleNamespace(value=1)

    with (
        patch.object(runtime, "_replace_dead_workers"),
        patch.object(runtime, "_dispatch_stage", return_value=()) as dispatch_stage,
    ):
        runtime._dispatch(1, (ParallelCommand("power", (True, 0)),))

    assert dispatch_stage.call_args.args[4] is True
    assert dispatch_stage.call_args.args[5] is not None


def test_single_command_retries_unacknowledged_members_five_times() -> None:
    """A standalone write retries every missing ACK through the full budget."""
    runtime = LIFXParallelRuntime(MagicMock(), ("host",))
    worker = SimpleNamespace(host="host", process=MagicMock(), pipe=MagicMock())
    runtime._workers = [worker]
    runtime._current_generation = SimpleNamespace(value=1)
    command = ParallelCommand("power", (True, 0))

    with (
        patch.object(runtime, "_replace_dead_workers"),
        patch.object(
            runtime,
            "_dispatch_stage",
            return_value=((worker, command),),
        ) as dispatch_stage,
        pytest.raises(HomeAssistantError, match="acknowledgements"),
    ):
        runtime._dispatch(1, (command,))

    assert dispatch_stage.call_count == 5
    assert all(call.args[4] for call in dispatch_stage.call_args_list)
    assert len({call.args[5] for call in dispatch_stage.call_args_list}) == 1


def test_final_stage_of_a_combined_command_requires_its_own_ack_budget() -> None:
    """The second command has a separate physical-light retry policy."""
    runtime = LIFXParallelRuntime(MagicMock(), ("host",))
    runtime._workers = [
        SimpleNamespace(host="host", process=MagicMock(), pipe=MagicMock())
    ]
    runtime._current_generation = SimpleNamespace(value=1)
    command = ParallelCommand(
        "color",
        (1, 2, 3, 3500, 0),
        ParallelCommand("power", (True, 0)),
    )

    with (
        patch.object(runtime, "_replace_dead_workers"),
        patch.object(runtime, "_dispatch_stage", return_value=()) as dispatch_stage,
    ):
        runtime._dispatch(1, (command,))

    assert dispatch_stage.call_count == 2
    assert all(call.args[4] for call in dispatch_stage.call_args_list)
    assert dispatch_stage.call_args_list[0].args[5] != dispatch_stage.call_args_list[1].args[5]


def test_new_request_cancels_active_ack_wait_without_waiting_for_cleanup() -> None:
    """Supersession signals the worker and completes the old caller immediately."""
    runtime = LIFXParallelRuntime(MagicMock(), ("host",))
    worker = SimpleNamespace(host="host", process=MagicMock(), pipe=MagicMock())
    runtime._workers = [worker]
    old_request = _DispatchRequest(1, (), MagicMock())
    runtime._request_id = 1
    runtime._active_dispatch = old_request
    runtime._active_stage = (1, 0, (worker,))
    runtime._current_generation = SimpleNamespace(value=1)

    with patch("homeassistant.components.lifx.parallel.threading.Event") as event:
        event.return_value.wait.return_value = None
        runtime._queue_dispatch((ParallelCommand("power", (False, 0)),))

    assert runtime._current_generation.value == 2
    old_request.done.set.assert_called_once()
    assert old_request.result == parallel.ParallelDispatchResult(
        parallel.ParallelDispatchOutcome.SUPERSEDED, 1
    )
    worker.pipe.send.assert_called_once_with(("CANCEL", 1, 0))


def test_late_ack_result_is_not_matched_to_the_next_attempt() -> None:
    """A retry only consumes its own attempt-scoped worker events."""
    runtime = LIFXParallelRuntime(MagicMock(), ("host",))
    pipe = MagicMock()
    runtime._current_generation = SimpleNamespace(value=1)
    event = ("ACK_TIMEOUT", 1, 0, 0)
    runtime._store_worker_event(pipe, event)

    assert runtime._pop_worker_event(pipe, 1, 0, 1) is None
    assert runtime._pop_worker_event(pipe, 1, 0, 0) == event


def test_set_color_can_request_an_ack_for_a_later_stage() -> None:
    """A non-final Device Group command must require a LIFX acknowledgement."""
    packet = _set_color(1, 2, bytes(8), (1, 2, 3, 3500, 0), ack_required=True)
    _size, _frame, _source, _target, _reserved, flags, _sequence, _r2, packet_type, _r3 = (
        HEADER.unpack_from(packet)
    )

    assert packet_type == SET_COLOR
    assert flags & 0b10


def test_command_stages_preserve_member_dependency_order() -> None:
    """A worker must retain the member's ordered dependent commands."""
    command = ParallelCommand(
        "power",
        (True, 0),
        ParallelCommand("color", (1, 2, 3, 3500, 0)),
    )

    assert tuple(stage.kind for stage in command.stages) == ("power", "color")


def test_wait_for_ack_ignores_malformed_response_and_matches_sequence() -> None:
    """Only the matching acknowledgement completes a required stage."""
    target = bytes((1,)) * 8
    udp = MagicMock()
    pipe = MagicMock()
    pipe.poll.return_value = False
    udp.recv.side_effect = (
        b"invalid",
        _header(ACKNOWLEDGEMENT, 1, 2, target, 0),
    )

    assert _wait_for_ack(
        udp,
        pipe,
        1,
        target,
        2,
        3,
        0,
        0,
        SimpleNamespace(value=3),
        time.monotonic() + 1,
    )


def test_wait_for_ack_treats_socket_error_as_retryable_stage_failure() -> None:
    """A receive fault must fail only the stage, not the warmed worker."""
    udp = MagicMock()
    pipe = MagicMock()
    pipe.poll.return_value = False
    udp.recv.side_effect = OSError("network changed")

    assert (
        _wait_for_ack(
            udp,
            pipe,
            1,
            bytes(8),
            2,
            3,
            0,
            0,
            SimpleNamespace(value=3),
            time.monotonic() + 1,
        )
        is False
    )


def test_worker_keeps_running_after_a_dispatch_socket_error() -> None:
    """A packet send failure must return the worker to its warmed command loop."""
    udp = MagicMock()
    udp.send.return_value = 0
    pipe = MagicMock()
    pipe.poll.side_effect = (False, True, False)
    pipe.recv.return_value = (
        "DISPATCH",
        3,
        0,
        0,
        time.monotonic_ns(),
        time.monotonic(),
    )
    generation = SimpleNamespace(value=3)
    stop_event = MagicMock()
    stop_event.is_set.return_value = False

    assert _dispatch_prepared(
        udp,
        1,
        bytes(8),
        pipe,
        3,
        0,
        0,
        ParallelCommand("power", (True, 0)),
        False,
        0,
        generation,
        stop_event,
        lambda: 2,
        False,
    )
    pipe.send.assert_any_call(("ERROR", 3, 0, 0, "short UDP send"))


def test_worker_cancels_an_ack_wait_when_a_newer_generation_arrives() -> None:
    """A superseded ACK wait must not authorize a later stage."""
    udp = MagicMock()
    pipe = MagicMock()

    assert (
        _wait_for_ack(
            udp,
            pipe,
            1,
            bytes(8),
            2,
            3,
            0,
            0,
            SimpleNamespace(value=4),
            time.monotonic() + 1,
        )
        is None
    )
    udp.recv.assert_not_called()
