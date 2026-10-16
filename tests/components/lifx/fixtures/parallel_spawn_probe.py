"""Exercise real LIFX workers in an interpreter without pytest's import cache."""

import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import socket
import struct
import sys
import threading

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
