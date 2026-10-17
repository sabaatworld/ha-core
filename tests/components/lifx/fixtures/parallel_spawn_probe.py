"""Exercise real LIFX workers in an interpreter without pytest's import cache."""

import asyncio
import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import socket
import struct
import sys
import threading
from types import SimpleNamespace

HEADER = struct.Struct("<HHI8s6sBBQHH")


def respond_to_echo(receiver: socket.socket, packets: list[bytes]) -> None:
    """Return one independently encoded LIFX EchoResponse on loopback."""
    data, address = receiver.recvfrom(2048)
    fields = list(HEADER.unpack_from(data))
    assert fields[8] == 58
    assert len(data) == 100
    fields[8] = 59
    fields[5] = 0
    receiver.sendto(HEADER.pack(*fields) + data[HEADER.size :], address)
    packets.append(data)


def acknowledge(receiver: socket.socket, data: bytes, address: tuple[str, int]) -> None:
    """Encode the matching protocol acknowledgement independently of the worker."""
    fields = list(HEADER.unpack_from(data))
    fields[0] = HEADER.size
    fields[5] = 0
    fields[8] = 45
    receiver.sendto(HEADER.pack(*fields), address)


def respond_to_batch(receiver: socket.socket, received: list[bytes]) -> None:
    """Acknowledge a burst in reverse order."""
    messages = [receiver.recvfrom(2048), receiver.recvfrom(2048)]
    for data, address in reversed(messages):
        acknowledge(receiver, data, address)
    received.extend(data for data, _address in messages)


def respond_after_cancel(
    receiver: socket.socket, received: list[bytes], entered: threading.Event
) -> None:
    """Withhold the old ACK, then acknowledge only the replacement command."""
    data, _address = receiver.recvfrom(2048)
    received.append(data)
    entered.set()
    data, address = receiver.recvfrom(2048)
    received.append(data)
    acknowledge(receiver, data, address)


def main() -> None:
    """Load the actual integration and start a loopback-only worker."""
    mode, config_directory, operation = sys.argv[1:]
    sys.path.insert(0, str(Path(__file__).parents[4]))
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415

    if mode == "builtin":
        module_name = "homeassistant.components.lifx.parallel"
    else:
        sys.path.insert(0, config_directory)
        importlib.import_module("custom_components")
        sys.path.remove(config_directory)
        sys.path_importer_cache.pop(config_directory, None)
        module_name = "custom_components.lifx.parallel"
    parallel = importlib.import_module(module_name)
    original_path = sys.path.copy()
    original_environment = dict(os.environ)
    evidence: dict[str, object] = {}
    responder: threading.Thread | None = None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.1)
        runtime = parallel.LIFXParallelRuntime(
            None,
            (
                parallel.ParallelTransport(
                    "127.0.0.1", receiver.getsockname()[1], bytes(8)
                ),
            ),
        )
        try:
            if operation == "failure":
                try:
                    runtime._start()
                except HomeAssistantError as err:
                    evidence["startup_error"] = str(err)
                else:
                    raise AssertionError("The broken bootstrap unexpectedly started")
            else:
                runtime._start()
                evidence["available"] = runtime.available
                try:
                    receiver.recv(2048)
                except TimeoutError:
                    evidence["startup_datagrams"] = 0
                else:
                    evidence["startup_datagrams"] = 1
                if operation == "replacement":
                    original_worker = runtime._workers[0].process
                    original_worker.terminate()
                    original_worker.join(timeout=5)
                    assert not original_worker.is_alive()
                    runtime._replace_dead_worker(0)
                    evidence["worker_replaced"] = (
                        runtime._workers[0].process.pid != original_worker.pid
                        and runtime.available
                    )
                if operation in {"echo", "replacement"}:
                    receiver.settimeout(5)
                    packets: list[bytes] = []
                    responder = threading.Thread(
                        target=respond_to_echo, args=(receiver, packets), daemon=True
                    )
                    responder.start()
                    result = runtime._queue_dispatch(
                        (parallel.ParallelCommand("echo", (bytes(range(64)),)),), True
                    )
                    responder.join(timeout=5)
                    evidence["echo_completed"] = (
                        result.outcome == parallel.ParallelDispatchOutcome.COMPLETED
                        and not result.failed_member_indexes
                    )
                    evidence["echo_packets"] = len(packets)
                if operation == "batch":
                    receiver.settimeout(5)
                    received: list[bytes] = []
                    responder = threading.Thread(
                        target=respond_to_batch, args=(receiver, received), daemon=True
                    )
                    responder.start()
                    payload = struct.pack("<BHHHHI", 0, 100, 200, 300, 3500, 0)
                    command = parallel.ParallelCommand(
                        "packet_batch",
                        (
                            (
                                parallel.ParallelPacket(102, payload),
                                parallel.ParallelPacket(102, payload),
                            ),
                        ),
                    )
                    result = runtime._queue_dispatch((command,))
                    responder.join(timeout=5)
                    evidence["batch_completed"] = (
                        result.outcome == parallel.ParallelDispatchOutcome.COMPLETED
                    )
                    evidence["packet_count"] = len(received)
                if operation == "cancel":
                    receiver.settimeout(5)
                    received = []
                    entered = threading.Event()
                    responder = threading.Thread(
                        target=respond_after_cancel,
                        args=(receiver, received, entered),
                        daemon=True,
                    )
                    responder.start()

                    async def cancel_and_replace() -> None:
                        runtime.hass = SimpleNamespace(
                            async_add_executor_job=asyncio.to_thread
                        )
                        command = parallel.ParallelCommand(
                            "color",
                            (100, 200, 300, 3500, 0),
                            parallel.ParallelCommand("power", (True, 0)),
                        )
                        old = asyncio.create_task(runtime.async_dispatch((command,)))
                        assert await asyncio.to_thread(entered.wait, 5)
                        await runtime.async_cancel_members(frozenset({0}))
                        evidence["cancelled"] = (
                            await old
                        ).outcome == parallel.ParallelDispatchOutcome.SUPERSEDED
                        result = await runtime.async_dispatch(
                            (parallel.ParallelCommand("power", (False, 0)),)
                        )
                        evidence["replacement_completed"] = (
                            result.outcome == parallel.ParallelDispatchOutcome.COMPLETED
                        )

                    asyncio.run(cancel_and_replace())
                    responder.join(timeout=5)
                    evidence["packet_types"] = [
                        HEADER.unpack_from(data)[8] for data in received
                    ]
        finally:
            dispatcher = runtime._dispatcher
            runtime._stop()
            if responder is not None:
                responder.join(timeout=5)
                assert not responder.is_alive()
    evidence["parent_path_unchanged"] = sys.path == original_path
    evidence["parent_environment_unchanged"] = dict(os.environ) == original_environment
    evidence["children_stopped"] = not mp.active_children()
    evidence["dispatcher_stopped"] = dispatcher is None or not dispatcher.is_alive()
    sys.stdout.write(json.dumps(evidence) + "\n")


if __name__ == "__main__":
    main()
