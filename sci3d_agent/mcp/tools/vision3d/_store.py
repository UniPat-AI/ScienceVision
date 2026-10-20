"""F-vision session store + helpers (Phase 3.1).

Singleton session store, GPU concurrency semaphore, and immutable
``dataclasses.replace`` helpers used by Phase 3.2/3.3 to update fields of
a frozen ``Session``. All public mutation paths swap the entry under the
store lock so readers never observe a torn write.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from typing import Iterator, Optional

from sci3d_agent.mcp.tools.vision3d._types import (
    CameraState,
    ClipPlaneState,
    MaskState,
    Mode,
    Opacity,
    Session,
    SliceState,
)


class CapacityError(RuntimeError):
    """Raised when ``SessionStore.add`` exceeds ``max_sessions``."""


class SessionStore:
    """Thread-safe map of ``session_id -> Session`` with capacity + TTL."""

    def __init__(self, max_sessions: int = 4) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self._max = max_sessions

    def capacity(self) -> int:
        return self._max

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def add(self, session: Session) -> None:
        with self._lock:
            if len(self._sessions) >= self._max:
                raise CapacityError(
                    f"session capacity reached: {len(self._sessions)} active"
                )
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> Session:
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(session_id)
            return self._sessions[session_id]

    def update(self, session: Session) -> None:
        with self._lock:
            if session.session_id not in self._sessions:
                raise KeyError(session.session_id)
            self._sessions[session.session_id] = session

    def remove(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def evict_expired(self, now: float) -> int:
        with self._lock:
            stale = [
                sid
                for sid, s in self._sessions.items()
                if (now - s.created_at) > s.ttl_seconds
            ]
            for sid in stale:
                del self._sessions[sid]
            return len(stale)


_STORE: Optional[SessionStore] = None
_STORE_LOCK = threading.Lock()
_GPU_SEMAPHORE: Optional[threading.Semaphore] = None


def get_store() -> SessionStore:
    """Return the process-wide singleton ``SessionStore``."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            n = int(os.environ.get("SV_MAX_SESSIONS", "4"))
            _STORE = SessionStore(max_sessions=n)
    return _STORE


def reset_store_for_tests() -> None:
    """Test-only: clear the singleton so each test gets a fresh store."""
    global _STORE, _GPU_SEMAPHORE
    with _STORE_LOCK:
        _STORE = None
        _GPU_SEMAPHORE = None


def _gpu_sem() -> threading.Semaphore:
    global _GPU_SEMAPHORE
    if _GPU_SEMAPHORE is None:
        n = int(os.environ.get("SV_GPU_CONCURRENCY", "1"))
        _GPU_SEMAPHORE = threading.Semaphore(n)
    return _GPU_SEMAPHORE


@contextmanager
def gpu_slot() -> Iterator[None]:
    """Block until a GPU slot is free; release on exit."""
    sem = _gpu_sem()
    sem.acquire()
    try:
        yield
    finally:
        sem.release()


# --- Phase 3.2/3.3 atomic update helpers ----------------------------------
# Each helper reads the current Session under the store lock, builds a new
# frozen copy with the changed field, and swaps it back atomically.


def replace_camera(sid: str, camera: CameraState) -> Session:
    store = get_store()
    s = store.get(sid)
    new = replace(s, camera=camera)
    store.update(new)
    return new


def replace_mode(sid: str, mode: Mode) -> Session:
    store = get_store()
    s = store.get(sid)
    new = replace(s, mode=mode)
    store.update(new)
    return new


def replace_slice(sid: str, slice_state: SliceState) -> Session:
    store = get_store()
    s = store.get(sid)
    new = replace(s, slice=slice_state)
    store.update(new)
    return new


def replace_mask_state(sid: str, ms: MaskState) -> Session:
    store = get_store()
    s = store.get(sid)
    new = replace(s, mask_state=ms)
    store.update(new)
    return new


def replace_clip_plane(sid: str, cp: ClipPlaneState) -> Session:
    store = get_store()
    s = store.get(sid)
    new = replace(s, clip_plane=cp)
    store.update(new)
    return new


def replace_opacity(sid: str, op: Opacity) -> Session:
    store = get_store()
    s = store.get(sid)
    new = replace(s, opacity=op)
    store.update(new)
    return new


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]


def monotonic_now() -> float:
    return time.monotonic()
