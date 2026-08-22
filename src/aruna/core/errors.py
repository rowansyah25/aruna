"""ARUNA exception hierarchy.

Every failure mode that ARUNA is allowed to *report* has a type here.  Code
must never paper over a failure by returning plausible-looking data - it raises
or returns an explicit unavailable/degraded status instead (SPEC 4, 5).
"""

from __future__ import annotations


class ArunaError(Exception):
    """Base class for all ARUNA errors."""


# ---------------------------------------------------------------------------
# Configuration / startup
# ---------------------------------------------------------------------------


class ConfigError(ArunaError):
    """Configuration is missing, malformed, or forbidden by the spec."""


class StartupError(ArunaError):
    """A component failed during application startup."""


class ShutdownError(ArunaError):
    """A component failed during shutdown."""


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class DatabaseError(ArunaError):
    """MySQL is unreachable or a query failed."""


class MigrationError(DatabaseError):
    """A schema migration could not be applied or verified."""


class CacheError(ArunaError):
    """Redis is unreachable or a cache operation failed."""


# ---------------------------------------------------------------------------
# Data integrity (SPEC 4, 5, 24)
# ---------------------------------------------------------------------------


class DataSourceUnavailableError(ArunaError):
    """A provider is not configured or not reachable.

    ARUNA reports ``STATUS: DATA SOURCE UNAVAILABLE`` rather than inventing a
    value (SPEC 4).
    """


class DataIntegrityError(ArunaError):
    """Received data failed a validity check (stale, duplicate, anomalous)."""


class DataLeakageError(DataIntegrityError):
    """Data newer than the signal timestamp was touched (SPEC 24).

    Any signal implicated by this must be invalidated, never silently repaired.
    """


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


class TelegramError(ArunaError):
    """Telegram transport failure.

    ``permanent`` membedakan dua kegagalan yang terlihat sama di log dan
    menuntut jawaban yang berlawanan: token yang tidak sah tidak akan pernah
    pulih sendiri, sementara jaringan yang tersumbat hampir selalu pulih.

    Pembedaan itu yang menentukan apakah ARUNA boleh mencoba lagi. Tanpa ia,
    hanya ada dua pilihan yang sama buruknya: menyerah pada kegagalan jaringan
    yang sementara - dan diam sampai seseorang me-restart - atau mencoba ulang
    token yang salah selamanya sambil membanjiri log dengan kegagalan yang
    sama.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class NotAuthorizedError(ArunaError):
    """A command arrived from a chat that is not on the allowlist."""


# ---------------------------------------------------------------------------
# Build-stage guards
# ---------------------------------------------------------------------------


class NotImplementedInPhaseError(ArunaError):
    """A feature scheduled for a later phase was invoked.

    Raised instead of returning a placeholder result, so that an unfinished
    subsystem can never be mistaken for a working one (SPEC 49).
    """

    def __init__(self, feature: str, phase: int) -> None:
        self.feature = feature
        self.phase = phase
        super().__init__(f"{feature} is not implemented yet - scheduled for PHASE {phase}")


class RealTradingForbiddenError(ArunaError):
    """Something attempted real-money execution.

    SPEC 46: the MVP is paper trading only.  There is no code path to a live
    order, and this exists so that any future attempt fails loudly.
    """
