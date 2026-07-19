"""Low-skew LIFX LAN dispatch for virtual parallel groups."""

from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
import gc
import multiprocessing as mp
from multiprocessing.connection import Connection, wait
import secrets
import signal
import socket
import struct
import threading
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

DEFAULT_PORT = 56700
HEADER = struct.Struct("<HHI8s6sBBQHH")
COLOR_PAYLOAD = struct.Struct("<BHHHHI")
POWER_PAYLOAD = struct.Struct("<HI")
WAVEFORM_OPTIONAL_PAYLOAD = struct.Struct("<BBHHHHIfhBBBBB")
MULTIZONE_EFFECT_PAYLOAD = struct.Struct("<IB2xIQII32s")
TILE_EFFECT_PAYLOAD = struct.Struct("<2xIBIQII32sB128s")
HEADER_SIZE = HEADER.size

GET_SERVICE = 2
STATE_SERVICE = 3
GET_COLOR = 101
SET_COLOR = 102
LIGHT_STATE = 107
ACKNOWLEDGEMENT = 45
SET_POWER = 117
SET_WAVEFORM_OPTIONAL = 119
SET_REBOOT = 38
SET_MULTIZONE_EFFECT = 508
SET_TILE_EFFECT = 719


class _ParallelPreparationError(HomeAssistantError):
    """A worker failed before the shared send deadline."""


@dataclass(frozen=True, slots=True)
class ParallelCommand:
    """One already-resolved instruction for a single worker."""

    kind: str
    payload: tuple[Any, ...]
    second: ParallelCommand | None = None


@dataclass(frozen=True, slots=True)
class ParallelLightState:
    """A device-confirmed state returned by a parallel worker."""

    color: tuple[int, int, int, int]
    power_level: int
    label: str


@dataclass(slots=True)
class _Worker:
    host: str
    process: Any
    pipe: Connection


def _header(
    packet_type: int,
    source: int,
    sequence: int,
    target: bytes,
    payload_size: int,
    *,
    tagged: bool = False,
    ack_required: bool = False,
) -> bytes:
    """Build an addressable LIFX header with an optional acknowledgement."""
    frame = 1024 | (1 << 12) | ((1 << 13) if tagged else 0)
    return HEADER.pack(
        HEADER_SIZE + payload_size,
        frame,
        source,
        target,
        bytes(6),
        2 if ack_required else 0,
        sequence,
        0,
        packet_type,
        0,
    )


def _get_service(source: int, sequence: int) -> bytes:
    return _header(GET_SERVICE, source, sequence, bytes(8), 0, tagged=True)


def _get_color(source: int, sequence: int, target: bytes) -> bytes:
    return _header(GET_COLOR, source, sequence, target, 0)


def _set_color(
    source: int, sequence: int, target: bytes, payload: tuple[Any, ...]
) -> bytes:
    hue, saturation, brightness, kelvin, duration = payload
    body = COLOR_PAYLOAD.pack(0, hue, saturation, brightness, kelvin, duration)
    return (
        _header(SET_COLOR, source, sequence, target, len(body), ack_required=True)
        + body
    )


def _set_power(
    source: int, sequence: int, target: bytes, payload: tuple[Any, ...]
) -> bytes:
    power, duration = payload
    body = POWER_PAYLOAD.pack(65535 if power else 0, duration)
    return (
        _header(SET_POWER, source, sequence, target, len(body), ack_required=True)
        + body
    )


def _set_waveform_optional(
    source: int, sequence: int, target: bytes, payload: tuple[Any, ...]
) -> bytes:
    (
        transient,
        hue,
        saturation,
        brightness,
        kelvin,
        period,
        cycles,
        skew_ratio,
        waveform,
        set_hue,
        set_saturation,
        set_brightness,
        set_kelvin,
    ) = payload
    body = WAVEFORM_OPTIONAL_PAYLOAD.pack(
        0,
        transient,
        hue,
        saturation,
        brightness,
        kelvin,
        period,
        cycles,
        skew_ratio,
        waveform,
        set_hue,
        set_saturation,
        set_brightness,
        set_kelvin,
    )
    return (
        _header(
            SET_WAVEFORM_OPTIONAL,
            source,
            sequence,
            target,
            len(body),
            ack_required=True,
        )
        + body
    )


def _set_reboot(source: int, sequence: int, target: bytes) -> bytes:
    return _header(SET_REBOOT, source, sequence, target, 0, ack_required=True)


def _set_multizone_effect(
    source: int, sequence: int, target: bytes, payload: tuple[Any, ...]
) -> bytes:
    """Build a SetMultiZoneEffect packet."""
    effect, speed, direction = payload
    parameters = struct.pack("<II6I", 0, direction, 0, 0, 0, 0, 0, 0)
    body = MULTIZONE_EFFECT_PAYLOAD.pack(
        secrets.randbits(32), effect, speed, 0, 0, 0, parameters
    )
    return (
        _header(
            SET_MULTIZONE_EFFECT,
            source,
            sequence,
            target,
            len(body),
            ack_required=True,
        )
        + body
    )


def _set_tile_effect(
    source: int, sequence: int, target: bytes, payload: tuple[Any, ...]
) -> bytes:
    """Build a SetTileEffect packet."""
    effect, speed, sky_type, cloud_saturation_min, cloud_saturation_max, palette = (
        payload
    )
    parameters = bytes(
        (
            sky_type,
            0,
            0,
            0,
            cloud_saturation_min,
            0,
            0,
            0,
            cloud_saturation_max,
        )
    ) + bytes(23)
    colors = tuple(palette)[:16]
    packed_palette = b"".join(struct.pack("<HHHH", *color) for color in colors)
    packed_palette += bytes(128 - len(packed_palette))
    body = TILE_EFFECT_PAYLOAD.pack(
        secrets.randbits(32),
        effect,
        speed,
        0,
        0,
        0,
        parameters,
        len(colors),
        packed_palette,
    )
    return (
        _header(
            SET_TILE_EFFECT,
            source,
            sequence,
            target,
            len(body),
            ack_required=True,
        )
        + body
    )


def _send_packet(udp: socket.socket, packet: bytes, message: str) -> None:
    """Send a complete datagram or raise a useful socket error."""
    if udp.send(packet) != len(packet):
        raise OSError(message)


def _parse_header(data: bytes) -> tuple[int, int, bytes, int]:
    if len(data) < HEADER_SIZE:
        raise ValueError("LIFX response is shorter than its header")
    size, _frame, source, target, _reserved, _flags, sequence, _r2, packet_type, _r3 = (
        HEADER.unpack_from(data)
    )
    if size != len(data):
        raise ValueError("LIFX response has an invalid size")
    return source, sequence, target, packet_type


def _parse_light_state(data: bytes) -> ParallelLightState:
    """Parse a LightState response into coordinator cache fields."""
    if len(data) != HEADER_SIZE + 52:
        raise ValueError("LIFX LightState has an invalid size")
    hue, saturation, brightness, kelvin, _reserved, power, label, _reserved2 = (
        struct.unpack_from("<HHHHhH32sQ", data, HEADER_SIZE)
    )
    return ParallelLightState(
        (hue, saturation, brightness, kelvin),
        power,
        label.split(b"\0", 1)[0].decode(errors="replace"),
    )


def _wait_for_packet(
    udp: socket.socket,
    source: int,
    sequence: int,
    target: bytes,
    packet_type: int,
    timeout: float,
) -> bytes:
    """Read until the matching response arrives or the deadline expires."""
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        udp.settimeout(remaining)
        data = udp.recv(2048)
        response_source, response_sequence, response_target, response_type = (
            _parse_header(data)
        )
        if (
            response_source == source
            and response_sequence == sequence
            and response_target == target
            and response_type == packet_type
        ):
            return data
    raise TimeoutError("Timed out waiting for LIFX response")


def _preflight(udp: socket.socket, source: int, next_sequence: Any) -> bytes:
    """Resolve the target and prove the member can answer before dispatching."""
    sequence = next_sequence()
    udp.send(_get_service(source, sequence))
    udp.settimeout(1.0)
    while True:
        data = udp.recv(2048)
        response_source, response_sequence, target, packet_type = _parse_header(data)
        if (
            response_source == source
            and response_sequence == sequence
            and packet_type == STATE_SERVICE
            and len(data) == HEADER_SIZE + 5
        ):
            _service, port = struct.unpack_from("<BI", data, HEADER_SIZE)
            if not 1 <= port <= 65535:
                raise ValueError("LIFX member reported an invalid UDP port")
            return target, port


def _handle_stage(
    udp: socket.socket,
    source: int,
    target: bytes,
    pipe: Connection,
    request_id: int,
    payload: tuple[Any, ...],
    next_sequence: Callable[[], int],
) -> None:
    """Send one non-gated color staging command and receive its ACK."""
    packet_sequence = next_sequence()
    packet = _set_color(source, packet_sequence, target, payload)
    try:
        _send_packet(udp, packet, "short UDP staging send")
        _wait_for_packet(udp, source, packet_sequence, target, ACKNOWLEDGEMENT, 1.0)
    except (OSError, TimeoutError, ValueError) as err:
        pipe.send(("ERROR", request_id, str(err)))
    else:
        pipe.send(("STAGED", request_id))


def _handle_query_state(
    udp: socket.socket,
    source: int,
    target: bytes,
    pipe: Connection,
    request_id: int,
    next_sequence: Callable[[], int],
) -> None:
    """Fetch device state through the worker-owned socket."""
    query_sequence = next_sequence()
    try:
        _send_packet(
            udp,
            _get_color(source, query_sequence, target),
            "LIFX state query send",
        )
        response = _wait_for_packet(
            udp, source, query_sequence, target, LIGHT_STATE, 1.0
        )
        state = _parse_light_state(response)
    except (OSError, TimeoutError, ValueError) as err:
        pipe.send(("ERROR", request_id, str(err)))
    else:
        pipe.send(("STATE", request_id, state))


def _dispatch_prepared(
    udp: socket.socket,
    source: int,
    target: bytes,
    pipe: Connection,
    request_id: int,
    command_specs: tuple[tuple[str, tuple[Any, ...]], ...],
    first_dispatch_gate: Any,
    second_dispatch_gate: Any,
    stop_event: Any,
    next_sequence: Callable[[], int],
    gc_was_enabled: bool,
) -> bool:
    """Send one or two prebuilt command phases and return whether to continue."""

    def build_packet(command_kind: str, payload: tuple[Any, ...]) -> tuple[bytes, int]:
        """Build one pre-resolved command packet and its ACK sequence."""
        packet_sequence = next_sequence()
        if command_kind == "color":
            packet = _set_color(source, packet_sequence, target, payload)
        elif command_kind == "power":
            packet = _set_power(source, packet_sequence, target, payload)
        elif command_kind == "waveform_optional":
            packet = _set_waveform_optional(source, packet_sequence, target, payload)
        elif command_kind == "reboot":
            packet = _set_reboot(source, packet_sequence, target)
        elif command_kind == "multizone_effect":
            packet = _set_multizone_effect(source, packet_sequence, target, payload)
        elif command_kind == "tile_effect":
            packet = _set_tile_effect(source, packet_sequence, target, payload)
        else:
            raise ValueError(f"unsupported command: {command_kind}")
        return packet, packet_sequence

    try:
        packets = tuple(build_packet(*spec) for spec in command_specs)
    except (TypeError, ValueError) as err:
        pipe.send(("ERROR", request_id, str(err)))
        return True
    if gc_was_enabled:
        gc.disable()
    pipe.send(("READY", request_id))
    first_dispatch_gate.acquire()
    if stop_event.is_set():
        pipe.send(("ABORTED", request_id))
        if gc_was_enabled and not gc.isenabled():
            gc.enable()
        return True
    try:
        packet, packet_sequence = packets[0]
        _send_packet(udp, packet, "short UDP send")
        _wait_for_packet(udp, source, packet_sequence, target, ACKNOWLEDGEMENT, 1.0)
        pipe.send(("ACK", request_id, 0))
    except (OSError, TimeoutError, ValueError) as err:
        pipe.send(("ERROR", request_id, str(err)))
    if len(packets) == 1:
        if gc_was_enabled and not gc.isenabled():
            gc.enable()
        return True

    second_command = pipe.recv()
    if second_command[0] == "SHUTDOWN":
        return False
    if second_command[0] == "ABORT_SECOND":
        pipe.send(("ABORTED", request_id, 1))
    elif second_command != ("DISPATCH_SECOND", request_id):
        pipe.send(("ERROR", request_id, "invalid second phase command"))
    else:
        pipe.send(("READY_SECOND", request_id))
        second_dispatch_gate.acquire()
        if stop_event.is_set():
            pipe.send(("ABORTED", request_id, 1))
        else:
            try:
                packet, packet_sequence = packets[1]
                _send_packet(udp, packet, "short UDP send")
                _wait_for_packet(
                    udp, source, packet_sequence, target, ACKNOWLEDGEMENT, 1.0
                )
                pipe.send(("ACK", request_id, 1))
            except (OSError, TimeoutError, ValueError) as err:
                pipe.send(("ERROR", request_id, str(err)))
    if gc_was_enabled and not gc.isenabled():
        gc.enable()
    return True


def _worker(
    host: str,
    source: int,
    pipe: Connection,
    first_dispatch_gate: Any,
    second_dispatch_gate: Any,
    stop_event: Any,
) -> None:
    """Own a socket for one light and wait beside the dispatch gate."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sequence = 0
    gc_was_enabled = gc.isenabled()

    def next_sequence() -> int:
        nonlocal sequence
        value = sequence
        sequence = (sequence + 1) & 0xFF
        return value

    try:
        udp.connect((host, DEFAULT_PORT))
        target, port = _preflight(udp, source, next_sequence)
        udp.connect((host, port))
        query_sequence = next_sequence()
        udp.send(_get_color(source, query_sequence, target))
        udp.settimeout(1.0)
        while True:
            data = udp.recv(2048)
            response_source, response_sequence, _target, packet_type = _parse_header(
                data
            )
            if (
                response_source == source
                and response_sequence == query_sequence
                and packet_type == LIGHT_STATE
            ):
                break
        pipe.send(("PREFLIGHT_OK",))

        while command := pipe.recv():
            if command[0] == "SHUTDOWN":
                break
            if command[0] == "STAGE":
                _kind, request_id, payload = command
                _handle_stage(
                    udp, source, target, pipe, request_id, payload, next_sequence
                )
                continue
            if command[0] == "QUERY_STATE":
                _kind, request_id = command
                _handle_query_state(
                    udp, source, target, pipe, request_id, next_sequence
                )
                continue
            _kind, request_id, command_specs = command
            if not _dispatch_prepared(
                udp,
                source,
                target,
                pipe,
                request_id,
                command_specs,
                first_dispatch_gate,
                second_dispatch_gate,
                stop_event,
                next_sequence,
                gc_was_enabled,
            ):
                break
    except (EOFError, OSError, ValueError) as err:
        with suppress(BrokenPipeError, EOFError, OSError):
            pipe.send(("PREFLIGHT_ERROR", str(err)))
    finally:
        if gc_was_enabled and not gc.isenabled():
            gc.enable()
        udp.close()
        pipe.close()


class LIFXParallelRuntime:
    """A warmed, process-isolated LIFX dispatcher for one virtual group."""

    def __init__(self, hass: HomeAssistant, hosts: Iterable[str]) -> None:
        """Initialize the process supervisor."""
        self.hass = hass
        self.hosts = tuple(hosts)
        self._workers: list[_Worker] = []
        self._dispatch_gates: tuple[Any, Any] | None = None
        self._stop_event: Any = None
        self._request_id = 0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """Return whether every persistent worker remains alive."""
        return bool(self._workers) and all(
            worker.process.is_alive() for worker in self._workers
        )

    @property
    def worker_health(self) -> tuple[bool, ...]:
        """Return non-sensitive liveness for the persistent worker pool."""
        return tuple(worker.process.is_alive() for worker in self._workers)

    async def async_start(self) -> None:
        """Start and preflight all member workers outside the event loop."""
        await self.hass.async_add_executor_job(self._start)

    def _start(self) -> None:
        if not self.hosts:
            raise HomeAssistantError("A parallel LIFX group needs at least one member")
        context = mp.get_context("spawn")
        self._dispatch_gates = (context.Semaphore(0), context.Semaphore(0))
        self._stop_event = context.Event()
        source = secrets.randbelow(0xFFFFFFFE) + 2
        try:
            for host in self.hosts:
                parent, child = context.Pipe(duplex=True)
                process = context.Process(
                    target=_worker,
                    args=(
                        host,
                        source,
                        child,
                        *self._dispatch_gates,
                        self._stop_event,
                    ),
                    name=f"lifx-parallel-{host}",
                )
                process.start()
                child.close()
                self._workers.append(_Worker(host, process, parent))
            self._collect("PREFLIGHT_OK", 5.0)
        except Exception:
            self._stop()
            raise

    async def async_stop(self) -> None:
        """Stop every worker outside the event loop."""
        await self.hass.async_add_executor_job(self._stop)

    def _stop(self) -> None:
        with self._lock:
            if self._stop_event is not None:
                self._stop_event.set()
            for worker in self._workers:
                if worker.process.is_alive():
                    with suppress(BrokenPipeError, EOFError, OSError):
                        worker.pipe.send(("SHUTDOWN",))
            if self._dispatch_gates is not None:
                for dispatch_gate in self._dispatch_gates:
                    for _worker in self._workers:
                        dispatch_gate.release()
            for worker in self._workers:
                worker.process.join(timeout=1.0)
                if worker.process.is_alive():
                    worker.process.terminate()
                    worker.process.join(timeout=1.0)
                worker.pipe.close()
            self._workers.clear()

    async def async_dispatch(
        self,
        commands: tuple[ParallelCommand, ...],
        on_ack: Callable[[int, int], None] | None = None,
    ) -> None:
        """Stage a command per worker, then receive its acknowledgement."""
        await self.hass.async_add_executor_job(self._dispatch, commands, on_ack)

    async def async_stage_colors(
        self,
        colors: dict[int, tuple[Any, ...]],
        on_ack: Callable[[int], None] | None = None,
    ) -> None:
        """Set colors on powered-off members before their shared power deadline."""
        if colors:
            await self.hass.async_add_executor_job(self._stage_colors, colors, on_ack)

    async def async_query_states(self) -> tuple[ParallelLightState, ...]:
        """Fetch device-confirmed visual state through the worker sockets."""
        return await self.hass.async_add_executor_job(self._query_states)

    def _stage_colors(
        self, colors: dict[int, tuple[Any, ...]], on_ack: Callable[[int], None] | None
    ) -> None:
        if not self.available:
            raise HomeAssistantError("The LIFX Device Group transport is unavailable")
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            selected: dict[Connection, _Worker] = {}
            try:
                for index, payload in colors.items():
                    worker = self._workers[index]
                    worker.pipe.send(("STAGE", request_id, payload))
                    selected[worker.pipe] = worker
            except (BrokenPipeError, EOFError, OSError) as err:
                raise HomeAssistantError(
                    "A LIFX parallel worker is unavailable"
                ) from err
            worker_indices = {
                worker.pipe: index for index, worker in enumerate(self._workers)
            }
            self._collect_selected(
                selected,
                "STAGED",
                5.0,
                request_id,
                (
                    None
                    if on_ack is None
                    else lambda worker, _message: on_ack(worker_indices[worker.pipe])
                ),
            )

    def _dispatch(
        self,
        commands: tuple[ParallelCommand, ...],
        on_ack: Callable[[int, int], None] | None,
    ) -> None:
        if len(commands) != len(self._workers) or not self.available:
            raise HomeAssistantError("The LIFX Device Group transport is unavailable")
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            second_workers: dict[Connection, _Worker] = {}
            try:
                for worker, command in zip(self._workers, commands, strict=True):
                    command_specs = [(command.kind, command.payload)]
                    if command.second is not None:
                        command_specs.append(
                            (command.second.kind, command.second.payload)
                        )
                        second_workers[worker.pipe] = worker
                    worker.pipe.send(("PREPARE", request_id, tuple(command_specs)))
                self._collect("READY", 5.0, request_id)
            except (BrokenPipeError, EOFError, HomeAssistantError, OSError) as err:
                for dispatch_gate in self._dispatch_gates:
                    for _worker in self._workers:
                        dispatch_gate.release()
                raise _ParallelPreparationError(str(err)) from err

            worker_indices = {
                worker.pipe: index for index, worker in enumerate(self._workers)
            }
            self._dispatch_at_deadline(self._dispatch_gates[0], self._workers)
            try:
                self._collect(
                    "ACK",
                    5.0,
                    request_id,
                    (
                        None
                        if on_ack is None
                        else lambda worker, _message: on_ack(
                            worker_indices[worker.pipe], 0
                        )
                    ),
                )
            except HomeAssistantError:
                self._abort_second_phase(second_workers, request_id)
                raise

            if not second_workers:
                return
            try:
                for _worker in second_workers.values():
                    worker.pipe.send(("DISPATCH_SECOND", request_id))
                self._collect_selected(
                    second_workers,
                    "READY_SECOND",
                    5.0,
                    request_id,
                )
            except (BrokenPipeError, EOFError, HomeAssistantError, OSError) as err:
                for _worker in second_workers.values():
                    self._dispatch_gates[1].release()
                raise _ParallelPreparationError(str(err)) from err
            self._dispatch_at_deadline(
                self._dispatch_gates[1], tuple(second_workers.values())
            )
            self._collect_selected(
                second_workers,
                "ACK",
                5.0,
                request_id,
                (
                    None
                    if on_ack is None
                    else lambda worker, _message: on_ack(worker_indices[worker.pipe], 1)
                ),
            )

    @staticmethod
    def _dispatch_at_deadline(dispatch_gate: Any, workers: Iterable[_Worker]) -> None:
        """Release one pre-created worker gate at one absolute deadline."""
        deadline = time.monotonic_ns() + 5_000_000
        while (remaining := deadline - time.monotonic_ns()) > 1_000_000:
            time.sleep(remaining / 1_000_000_000)
        while time.monotonic_ns() < deadline:
            pass
        for _worker in workers:
            dispatch_gate.release()

    def _abort_second_phase(
        self, workers: dict[Connection, _Worker], request_id: int
    ) -> None:
        """Return workers waiting after a failed first phase to the command loop."""
        if not workers:
            return
        try:
            for worker in workers.values():
                worker.pipe.send(("ABORT_SECOND", request_id))
            self._collect_selected(workers, "ABORTED", 5.0, request_id)
        except BrokenPipeError, EOFError, HomeAssistantError, OSError:
            pass

    def _query_states(self) -> tuple[ParallelLightState, ...]:
        if not self.available:
            raise HomeAssistantError("The LIFX Device Group transport is unavailable")
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            try:
                for worker in self._workers:
                    worker.pipe.send(("QUERY_STATE", request_id))
            except (BrokenPipeError, EOFError, OSError) as err:
                raise HomeAssistantError(
                    "A LIFX parallel worker is unavailable"
                ) from err
            responses = self._collect("STATE", 5.0, request_id)
            return tuple(responses[worker.pipe][2] for worker in self._workers)

    def _collect(
        self,
        expected: str,
        timeout: float,
        request_id: int | None = None,
        on_message: Callable[[_Worker, tuple[Any, ...]], None] | None = None,
    ) -> dict[Connection, tuple[Any, ...]]:
        return self._collect_selected(
            {worker.pipe: worker for worker in self._workers},
            expected,
            timeout,
            request_id,
            on_message,
        )

    def _collect_selected(
        self,
        pending: dict[Connection, _Worker],
        expected: str,
        timeout: float,
        request_id: int | None = None,
        on_message: Callable[[_Worker, tuple[Any, ...]], None] | None = None,
    ) -> dict[Connection, tuple[Any, ...]]:
        pending = pending.copy()
        responses: dict[Connection, tuple[Any, ...]] = {}
        errors: list[str] = []
        deadline = time.monotonic() + timeout
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HomeAssistantError(
                    f"Timed out waiting for LIFX workers to {expected.lower()}"
                )
            ready = wait(tuple(pending), timeout=min(0.05, remaining))
            if not ready:
                if any(not worker.process.is_alive() for worker in pending.values()):
                    raise HomeAssistantError("A LIFX parallel worker exited")
                continue
            for pipe in ready:
                worker = pending[pipe]
                try:
                    message = pipe.recv()
                except EOFError as err:
                    raise HomeAssistantError(
                        f"LIFX worker for {worker.host} closed unexpectedly"
                    ) from err
                if request_id is not None and (
                    len(message) < 2 or message[1] != request_id
                ):
                    continue
                pending.pop(pipe)
                if message[0] != expected:
                    detail = message[-1] if len(message) > 1 else message[0]
                    errors.append(f"LIFX worker for {worker.host}: {detail}")
                    continue
                responses[pipe] = message
                if on_message is not None:
                    on_message(worker, message)
        if errors:
            raise HomeAssistantError("; ".join(errors))
        return responses
