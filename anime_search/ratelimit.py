from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ProviderState:
    name: str
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    last_request_time: float = 0.0
    total_requests: int = 0
    total_failures: int = 0
    total_rate_limited: int = 0

    def is_in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until

    def cooldown_remaining(self) -> float:
        remaining = self.cooldown_until - time.time()
        return max(0.0, remaining)

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_request_time = time.time()
        self.total_requests += 1

    def record_failure(self, is_rate_limit: bool = False) -> None:
        self.consecutive_failures += 1
        self.total_requests += 1
        self.total_failures += 1
        if is_rate_limit:
            self.total_rate_limited += 1
        self.last_request_time = time.time()
        self._apply_cooldown()

    def record_blocked(self) -> None:
        self.consecutive_failures += 1
        self.total_requests += 1
        self.total_failures += 1
        self.last_request_time = time.time()
        self.cooldown_until = time.time() + 120

    def _apply_cooldown(self) -> None:
        if self.consecutive_failures >= 5:
            delay = 120
        elif self.consecutive_failures >= 3:
            delay = 60
        elif self.consecutive_failures >= 2:
            delay = 30
        elif self.consecutive_failures >= 1:
            delay = 10
        else:
            delay = 0
        if delay > 0:
            self.cooldown_until = time.time() + delay
            log.warning(
                "%s: cooldown %.0fs after %d consecutive failures",
                self.name, delay, self.consecutive_failures,
            )


class RateLimiter:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderState] = {}
        self._lock = threading.Lock()

    def _get_state(self, provider: str) -> ProviderState:
        with self._lock:
            if provider not in self._providers:
                self._providers[provider] = ProviderState(name=provider)
            return self._providers[provider]

    def can_request(self, provider: str) -> bool:
        state = self._get_state(provider)
        if state.is_in_cooldown():
            remaining = state.cooldown_remaining()
            log.debug("%s: in cooldown for %.0fs more", provider, remaining)
            return False
        return True

    def wait_time(self, provider: str) -> float:
        state = self._get_state(provider)
        return state.cooldown_remaining()

    def record_success(self, provider: str) -> None:
        self._get_state(provider).record_success()

    def record_failure(self, provider: str, is_rate_limit: bool = False) -> None:
        self._get_state(provider).record_failure(is_rate_limit)

    def record_blocked(self, provider: str) -> None:
        self._get_state(provider).record_blocked()

    def get_status(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        with self._lock:
            for name, state in self._providers.items():
                in_cooldown = state.is_in_cooldown()
                result[name] = {
                    "status": "cooldown" if in_cooldown else "ok",
                    "cooldown_remaining": round(state.cooldown_remaining(), 1) if in_cooldown else 0,
                    "consecutive_failures": state.consecutive_failures,
                    "total_requests": state.total_requests,
                    "total_failures": state.total_failures,
                    "total_rate_limited": state.total_rate_limited,
                }
        return result

    def reset(self, provider: str | None = None) -> None:
        with self._lock:
            if provider:
                self._providers.pop(provider, None)
            else:
                self._providers.clear()
