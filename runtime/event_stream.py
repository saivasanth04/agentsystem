"""
Event Stream.
Provides typed event streaming and pub/sub subscription for the Claude-style execution loop.
Emits fine-grained events for every transition in the 7-phase state machine:
Reason -> Select Tool -> Execute -> Observation -> State Update -> Context Rebuild -> Reason Again
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Union

logger = logging.getLogger("runtime.event_stream")


class LoopEventType(str, Enum):
    REASONING_STARTED = "reasoning_started"
    REASONING_COMPLETED = "reasoning_completed"
    TOOL_SELECTED = "tool_selected"
    TOOL_EXECUTION_STARTED = "tool_execution_started"
    TOOL_EXECUTION_COMPLETED = "tool_execution_completed"
    OBSERVATION_PRODUCED = "observation_produced"
    STATE_UPDATED = "state_updated"
    CONTEXT_REBUILT = "context_rebuilt"
    LOOP_COMPLETED = "loop_completed"
    LOOP_FAILED = "loop_failed"
    PERMISSION_DENIED = "permission_denied"


@dataclass
class LoopEvent:
    """Represents a single typed lifecycle event during loop execution."""
    event_type: LoopEventType
    iteration: int
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value if isinstance(self.event_type, LoopEventType) else str(self.event_type),
            "iteration": self.iteration,
            "timestamp": self.timestamp,
            "message": self.message,
            "data": self.data,
        }


class EventStream:
    """
    Publish-subscribe event stream for real-time telemetry, UI streaming,
    and agent orchestration observation.
    """

    def __init__(self, external_event_bus: Optional[Any] = None):
        self.external_event_bus = external_event_bus
        self._listeners: List[Tuple[Callable[[LoopEvent], None], Optional[Set[LoopEventType]]]] = []
        self._history: List[LoopEvent] = []

    def subscribe(
        self,
        listener: Callable[[LoopEvent], None],
        event_types: Optional[Union[List[Union[str, LoopEventType]], Set[Union[str, LoopEventType]]]] = None,
    ) -> Callable[[], None]:
        """
        Subscribes a callback to events. Optionally filter by event types.
        Returns an unsubscribe callable.
        """
        filter_set: Optional[Set[LoopEventType]] = None
        if event_types:
            filter_set = {
                t if isinstance(t, LoopEventType) else LoopEventType(str(t))
                for t in event_types
            }

        subscription = (listener, filter_set)
        self._listeners.append(subscription)

        def unsubscribe():
            if subscription in self._listeners:
                self._listeners.remove(subscription)

        return unsubscribe

    def emit(
        self,
        event_type: Union[str, LoopEventType],
        data: Optional[Dict[str, Any]] = None,
        message: str = "",
        iteration: int = 0,
    ) -> LoopEvent:
        """Publishes an event to all registered listeners and internal history."""
        typed_event = event_type if isinstance(event_type, LoopEventType) else LoopEventType(str(event_type))
        payload = data or {}
        event = LoopEvent(
            event_type=typed_event,
            iteration=iteration,
            timestamp=time.time(),
            data=payload,
            message=message,
        )

        self._history.append(event)

        # Notify local subscribers
        for listener, filter_types in list(self._listeners):
            if filter_types is None or typed_event in filter_types:
                try:
                    listener(event)
                except Exception as e:
                    logger.warning(f"Error in EventStream listener: {e}")

        # Bridge to external EventBus if provided
        if self.external_event_bus and hasattr(self.external_event_bus, "publish"):
            try:
                self.external_event_bus.publish(
                    event_type=typed_event.value,
                    payload={"iteration": iteration, "message": message, **payload},
                )
            except Exception as e:
                logger.debug(f"Bridging to external event bus failed: {e}")

        return event

    def get_history(
        self,
        event_type: Optional[Union[str, LoopEventType]] = None,
    ) -> List[LoopEvent]:
        """Returns recorded events, optionally filtered by event type."""
        if not event_type:
            return list(self._history)
        typed_filter = event_type if isinstance(event_type, LoopEventType) else LoopEventType(str(event_type))
        return [e for e in self._history if e.event_type == typed_filter]

    def clear(self) -> None:
        """Clears the event ledger."""
        self._history.clear()
