"""Best-effort in-process network denial for the formal scorer CLI.

The release command also requires Docker's ``--network=none``. This guard
prevents an accidental future standard-library HTTP/socket use from silently
reintroducing egress when the CLI is exercised outside Docker.
"""
from __future__ import annotations

import socket


class NetworkDisabledError(RuntimeError):
    """Raised when scorer code attempts network I/O."""


def disable_network() -> None:
    if getattr(socket, "_sci3d_scorer_network_disabled", False):
        return
    original_socket = socket.socket

    class OfflineSocket(original_socket):
        def connect(self, address):  # type: ignore[no-untyped-def]
            raise NetworkDisabledError(f"network_disabled:{address!r}")

        def connect_ex(self, address):  # type: ignore[no-untyped-def]
            raise NetworkDisabledError(f"network_disabled:{address!r}")

    socket.socket = OfflineSocket
    socket._sci3d_scorer_network_disabled = True
