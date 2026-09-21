"""
Production-Grade Execution Lifecycle Event Bus for Autonomous Coding Agents.
Provides strongly-typed event streams (TASK_*, AGENT_*, TOOL_*, FILE_*, TEST_*, REPLAN_*),
thread-safe pub/sub subscribers, SQLite state store persistence, and a backward-compatible
bridge to legacy on_event_callback handlers.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Union


class EventType(str, Enum):
    # Task Lifecycle
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"

    # Agent Selection & Routing
    AGENT_SELECTED = "AGENT_SELECTED"

    # Tool Execution
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_COMPLETED = "TOOL_COMPLETED"

    # Workspace Mutations
    FILE_CHANGED = "FILE_CHANGED"

    # Testing & Verification
    TEST_STARTED = "TEST_STARTED"
    TEST_FAILED = "TEST_FAILED"
    TEST_PASSED = "TEST_PASSED"

    # Epistemic Replanning
    REPLAN_STARTED = "REPLAN_STARTED"
    REPLAN_COMPLETED = "REPLAN_COMPLETED"

    # Top-Level Workflow
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"


@dataclass
class ExecutionEvent:
    """
    Strongly-typed, immutable lifecycle event emitted during agent orchestration.
    """
    event_type: Union[EventType, str]
    event_id: str = field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:8]}")
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_name: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        if isinstance(self.event_type, str):
            try:
                self.event_type = EventType(self.event_type.upper())
            except ValueError:
                pass

    @property
    def event_type_value(self) -> str:
        return self.event_type.value if hasattr(self.event_type, "value") else str(self.event_type)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type_value,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionEvent":
        return cls(
            event_id=data.get("event_id") or f"evt-{uuid.uuid4().hex[:8]}",
            event_type=data.get("event_type", "UNKNOWN"),
            session_id=data.get("session_id"),
            task_id=data.get("task_id"),
            agent_name=data.get("agent_name"),
            payload=data.get("payload") or {},
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )


class EventBus:
    """
    Thread-Safe Lifecycle Event Bus for Central Orchestration Telemetry.
    Manages typed subscriptions, wildcard listeners, in-memory event stream history,
    SQLite persistence dual-writes, and backward-compatible translation to on_event_callback.
    """

    def __init__(
        self,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        state_store: Optional[Any] = None,
        session_id: Optional[str] = None,
    ):
        self._lock = threading.RLock()
        self._subscribers: Dict[str, List[Callable[[ExecutionEvent], None]]] = {}
        self._history: List[ExecutionEvent] = []
        self.on_event = on_event_callback
        self.state_store = state_store
        self.session_id = session_id

    def set_session(self, session_id: str, state_store: Optional[Any] = None):
        """Sets the active session ID and optional state store for persistence."""
        with self._lock:
            self.session_id = session_id
            if state_store is not None:
                self.state_store = state_store

    def subscribe(
        self,
        event_type: Union[EventType, str],
        callback: Callable[[ExecutionEvent], None],
    ) -> None:
        """
        Subscribes a listener to a specific EventType, string name, or '*' wildcard.
        """
        key = event_type.value if hasattr(event_type, "value") else str(event_type).upper()
        with self._lock:
            if key not in self._subscribers:
                self._subscribers[key] = []
            if callback not in self._subscribers[key]:
                self._subscribers[key].append(callback)

    def unsubscribe(
        self,
        event_type: Union[EventType, str],
        callback: Callable[[ExecutionEvent], None],
    ) -> None:
        """
        Unregisters a previously subscribed listener.
        """
        key = event_type.value if hasattr(event_type, "value") else str(event_type).upper()
        with self._lock:
            if key in self._subscribers and callback in self._subscribers[key]:
                self._subscribers[key].remove(callback)

    def emit(self, event: ExecutionEvent) -> ExecutionEvent:
        """
        Emits an ExecutionEvent to all matching subscribers, writes to SQLite,
        and translates to legacy on_event_callback if configured.
        """
        if not event.session_id and self.session_id:
            event.session_id = self.session_id

        callbacks_to_invoke = []
        key = event.event_type_value.upper()

        with self._lock:
            self._history.append(event)
            # Match exact event type subscribers
            if key in self._subscribers:
                callbacks_to_invoke.extend(self._subscribers[key])
            # Match wildcard subscribers
            if "*" in self._subscribers:
                callbacks_to_invoke.extend(self._subscribers["*"])

        # Persist to SQLite state store if available
        if self.state_store and event.session_id and hasattr(self.state_store, "save_event"):
            try:
                self.state_store.save_event(event.session_id, event)
            except Exception:
                pass

        # Invoke subscribers outside lock to prevent deadlocks
        for cb in callbacks_to_invoke:
            try:
                cb(event)
            except Exception:
                pass

        # Legacy backward-compatible on_event_callback forwarder
        if self.on_event:
            try:
                stage = key
                msg = self._format_legacy_message(event)
                self.on_event(stage, msg, event.payload or event.to_dict())
            except Exception:
                pass

        return event

    def publish(
        self,
        event_type: Union[EventType, str],
        payload: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ExecutionEvent:
        """
        Convenience builder to create and emit an ExecutionEvent in one step.
        """
        event = ExecutionEvent(
            event_type=event_type,
            session_id=session_id or self.session_id,
            task_id=task_id,
            agent_name=agent_name,
            payload=payload or {},
        )
        return self.emit(event)

    def get_events(
        self,
        event_type: Optional[Union[EventType, str]] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[ExecutionEvent]:
        """
        Queries the in-memory event stream with optional filters.
        """
        target_type = None
        if event_type:
            target_type = (event_type.value if hasattr(event_type, "value") else str(event_type)).upper()

        with self._lock:
            matched = list(self._history)

        if target_type:
            matched = [e for e in matched if e.event_type_value.upper() == target_type]
        if task_id:
            matched = [e for e in matched if e.task_id == task_id]
        if session_id:
            matched = [e for e in matched if e.session_id == session_id]

        if limit and limit > 0:
            return matched[-limit:]
        return matched

    def clear(self) -> None:
        """Clears in-memory history (useful in test teardowns)."""
        with self._lock:
            self._history.clear()

    def _format_legacy_message(self, event: ExecutionEvent) -> str:
        """Formats an ExecutionEvent into a concise human-readable message for on_event_callback."""
        p = event.payload or {}
        t_val = event.event_type_value.upper()

        if t_val == EventType.TASK_CREATED.value:
            return f"Task [{event.task_id}] created: '{p.get('objective', '')}'"
        elif t_val == EventType.TASK_STARTED.value:
            return f"Task [{event.task_id}] started execution with agent [{event.agent_name or 'CODER'}]"
        elif t_val == EventType.TASK_COMPLETED.value:
            return f"Task [{event.task_id}] completed successfully"
        elif t_val == EventType.TASK_FAILED.value:
            return f"Task [{event.task_id}] failed: {p.get('error', 'Execution failure')}"
        elif t_val == EventType.AGENT_SELECTED.value:
            return f"Agent [{event.agent_name}] selected for task [{event.task_id}] (Score: {p.get('score', 'N/A')})"
        elif t_val == EventType.TOOL_CALLED.value:
            return f"Tool [{p.get('tool_name')}] called by [{event.agent_name or 'AGENT'}]"
        elif t_val == EventType.TOOL_COMPLETED.value:
            return f"Tool [{p.get('tool_name')}] completed (status={p.get('status', 'SUCCESS')}, duration={p.get('duration_seconds', 0)}s)"
        elif t_val == EventType.FILE_CHANGED.value:
            return f"File [{p.get('filepath')}] mutated ({p.get('change_type', 'MODIFIED')}, version={p.get('version', 'unknown')})"
        elif t_val == EventType.TEST_STARTED.value:
            return f"Test stage [{p.get('stage', 'UNIT_TEST')}] started: `{p.get('command', '')}`"
        elif t_val == EventType.TEST_FAILED.value:
            return f"Test stage [{p.get('stage', 'UNIT_TEST')}] FAILED (exit code {p.get('exit_code', 1)})"
        elif t_val == EventType.TEST_PASSED.value:
            return f"Test stage [{p.get('stage', 'UNIT_TEST')}] PASSED ({p.get('duration_seconds', 0)}s)"
        elif t_val == EventType.REPLAN_STARTED.value:
            return f"Epistemic replanning started (iter {p.get('iteration', 1)}): {p.get('root_cause', '')}"
        elif t_val == EventType.REPLAN_COMPLETED.value:
            return f"Replanning completed: {len(p.get('injected_tasks', []))} tasks injected, {len(p.get('pruned_tasks', []))} pruned"
        elif t_val == EventType.WORKFLOW_STARTED.value:
            return f"Workflow started for: '{p.get('user_request', '')}'"
        elif t_val == EventType.WORKFLOW_COMPLETED.value:
            return f"Workflow finished with verdict: {p.get('verdict', 'PASS')}"
        return f"Event {t_val} on task [{event.task_id}]: {p}"

    async def stream_async(
        self,
        session_id: Optional[str] = None,
        event_types: Optional[Set[str]] = None,
        timeout: Optional[float] = None,
    ):
        """
        Asynchronously yields ExecutionEvents as they are published in real-time.
        """
        import asyncio
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _listener(evt: ExecutionEvent):
            if session_id and evt.session_id != session_id:
                return
            if event_types and evt.event_type_value not in event_types:
                return
            loop.call_soon_threadsafe(queue.put_nowait, evt)

        self.subscribe("*", _listener)
        try:
            while True:
                if timeout is not None:
                    try:
                        evt = await asyncio.wait_for(queue.get(), timeout=timeout)
                    except asyncio.TimeoutError:
                        break
                else:
                    evt = await queue.get()
                yield evt
                # Break on terminal workflow events
                if evt.event_type_value in ("WORKFLOW_COMPLETED", "WORKFLOW_FAILED"):
                    break
        finally:
            self.unsubscribe("*", _listener)

    async def publish_async(
        self,
        event_type: Union[EventType, str],
        payload: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ExecutionEvent:
        """Asynchronously publishes an event."""
        return self.publish(
            event_type=event_type,
            payload=payload,
            task_id=task_id,
            agent_name=agent_name,
            session_id=session_id,
        )
