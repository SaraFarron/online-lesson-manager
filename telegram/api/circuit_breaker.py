import asyncio
import time
from collections.abc import Callable

from .errors import BackendUnavailable


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 3,
        recovery: float = 15,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.recovery = recovery
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self.probing = False
        self.lock = asyncio.Lock()

    async def enter(self) -> bool:
        async with self.lock:
            if self.opened_at is None:
                return False
            if self.probing or self.clock() - self.opened_at < self.recovery:
                raise BackendUnavailable()
            self.probing = True
            return True

    async def finish(self, failed: bool, probe: bool) -> None:
        async with self.lock:
            if probe:
                self.probing = False
            if failed:
                self.failures += 1
                if probe or self.failures >= self.threshold:
                    self.opened_at = self.clock()
            elif probe or self.opened_at is None:
                self.failures = 0
                self.opened_at = None
