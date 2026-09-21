"""
Model Context Protocol (MCP) Circuit Breaker.
Protects agents from blocking timeouts and cascading failures when external or embedded MCP servers become unhealthy.
Implements the standard 3-state Circuit Breaker pattern:
- CLOSED: Server is healthy; requests pass through directly.
- OPEN: Server is unhealthy; fail-fast immediately and route to fallback providers.
- HALF_OPEN: Canary state probing whether the server has recovered.
"""
from enum import Enum
import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("mcp.circuit_breaker")


class CircuitState(str, Enum):
    """States of the MCP Circuit Breaker."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class MCPCircuitBreaker:
    """
    Per-server Circuit Breaker with sliding failure count and automatic recovery timeout.
    """

    def __init__(
        self,
        server_name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 5.0,
        half_open_max_trials: int = 1,
    ):
        self.server_name = server_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_trials = half_open_max_trials

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change: float = time.time()
        self._successful_trials: int = 0
        self._last_error: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            # Check if recovery timeout has elapsed while in OPEN state
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_state_change >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._last_state_change = time.time()
                    self._successful_trials = 0
                    logger.info(f"Circuit breaker for '{self.server_name}' transitioned from OPEN to HALF_OPEN (probing recovery).")
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def can_attempt(self) -> bool:
        """Determines if a request or health probe is allowed to proceed."""
        current_state = self.state
        with self._lock:
            if current_state == CircuitState.CLOSED:
                return True
            if current_state == CircuitState.HALF_OPEN:
                return self._successful_trials < self.half_open_max_trials
            return False  # OPEN

    def record_success(self) -> None:
        """Record a successful tool execution or health ping."""
        with self._lock:
            self._failure_count = 0
            self._last_error = None
            if self._state != CircuitState.CLOSED:
                self._state = CircuitState.CLOSED
                self._last_state_change = time.time()
                self._successful_trials = 0
                logger.info(f"Circuit breaker for '{self.server_name}' reset to CLOSED (healthy).")

    def record_failure(self, error: Optional[Exception] = None) -> None:
        """Record a failure (timeout, crash, connection drop, or failed ping)."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            self._last_error = str(error) if error else "Unspecified error"

            if self._state == CircuitState.HALF_OPEN:
                # Canary failed: immediately trip back to OPEN
                self._state = CircuitState.OPEN
                self._last_state_change = time.time()
                logger.warning(
                    f"Canary probe failed for '{self.server_name}': {self._last_error}. Tripping back to OPEN."
                )
            elif self._failure_count >= self.failure_threshold:
                if self._state != CircuitState.OPEN:
                    self._state = CircuitState.OPEN
                    self._last_state_change = time.time()
                    logger.warning(
                        f"Circuit breaker for '{self.server_name}' tripped to OPEN after {self._failure_count} consecutive failures. Last error: {self._last_error}"
                    )

    def trip_open(self, reason: str = "Explicitly marked unhealthy") -> None:
        """Manually trip the circuit breaker to OPEN state."""
        with self._lock:
            self._state = CircuitState.OPEN
            self._last_state_change = time.time()
            self._last_error = reason
            logger.warning(f"Circuit breaker for '{self.server_name}' explicitly tripped to OPEN: {reason}")

    def reset(self) -> None:
        """Reset the circuit breaker to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_error = None
            self._last_state_change = time.time()
            self._successful_trials = 0

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "server_name": self.server_name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "last_failure_time": self._last_failure_time,
                "last_state_change": self._last_state_change,
                "last_error": self._last_error,
            }
