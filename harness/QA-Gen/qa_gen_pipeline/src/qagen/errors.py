"""Typed failures map deterministic registry failures to CLI exit codes."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    UNEXPECTED = 1
    INVALID_INPUT = 2
    GATE_PENDING = 3
    STALE = 4
    QUARANTINED = 5
    INVALID_STATE = 6


class QAGenError(RuntimeError):
    exit_code = ExitCode.UNEXPECTED


class InputError(QAGenError):
    exit_code = ExitCode.INVALID_INPUT


class GatePendingError(QAGenError):
    exit_code = ExitCode.GATE_PENDING


class StaleInputError(QAGenError):
    exit_code = ExitCode.STALE


class QuarantinedError(QAGenError):
    exit_code = ExitCode.QUARANTINED


class InvalidStateError(QAGenError):
    exit_code = ExitCode.INVALID_STATE
