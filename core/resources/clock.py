"""Deterministic clock abstraction for Resource Manager and test harnesses.

Enables reproducible time testing without relying on time.sleep().
spec §9 (Resource Manager), Phase 3
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Protocol for time providers."""

    def now(self) -> datetime:
        """Return current timezone-aware UTC datetime."""
        ...

    def sleep(self, seconds: float) -> None:
        """Sleep or simulate sleeping for seconds."""
        ...


class SystemClock:
    """Production clock using real system UTC time."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock:
    """Deterministic, injectable clock for repeatable testing.

    Time only moves when advance() or set_time() is explicitly called.
    """

    def __init__(self, initial_time: datetime | None = None) -> None:
        if initial_time is None:
            self._current_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        else:
            if initial_time.tzinfo is None:
                self._current_time = initial_time.replace(tzinfo=timezone.utc)
            else:
                self._current_time = initial_time

    def now(self) -> datetime:
        return self._current_time

    def advance(self, seconds: float) -> datetime:
        """Advance fake clock forward by seconds."""
        if seconds < 0:
            raise ValueError("Clock cannot advance backward in time")
        from datetime import timedelta
        self._current_time += timedelta(seconds=seconds)
        return self._current_time

    def set_time(self, dt: datetime) -> None:
        """Explicitly set the fake clock to a given datetime."""
        if dt.tzinfo is None:
            self._current_time = dt.replace(tzinfo=timezone.utc)
        else:
            self._current_time = dt

    def sleep(self, seconds: float) -> None:
        """Fake sleep advances the clock immediately."""
        self.advance(seconds)

