"""
Agent-to-Agent Message Bus & Structured Communication Protocol.
Provides strongly typed semantic messages (TaskResult, Artifact, Observation,
Finding, Request, Response), thread-safe inboxes, pub/sub topics, and synchronous peer querying.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional, Union


class MessageType(str, Enum):
    TASK_RESULT = "TASK_RESULT"
    ARTIFACT = "ARTIFACT"
    OBSERVATION = "OBSERVATION"
    FINDING = "FINDING"
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    BROADCAST = "BROADCAST"


@dataclass
class StructuredMessage:
    sender: str
    recipient: str
    message_type: Union[MessageType, str]
    content: str
    message_id: str = field(default_factory=lambda: f"msg-{uuid.uuid4().hex[:8]}")
    topic: str = "general"
    payload: Dict[str, Any] = field(default_factory=dict)
    correlation_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        if isinstance(self.message_type, str):
            try:
                self.message_type = MessageType(self.message_type.upper())
            except ValueError:
                pass

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "topic": self.topic,
            "message_type": self.message_type.value if hasattr(self.message_type, "value") else str(self.message_type),
            "content": self.content,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "in_reply_to": self.in_reply_to,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StructuredMessage":
        return cls(
            message_id=data.get("message_id") or f"msg-{uuid.uuid4().hex[:8]}",
            sender=data.get("sender", "UNKNOWN"),
            recipient=data.get("recipient", "*"),
            topic=data.get("topic", "general"),
            message_type=data.get("message_type", MessageType.BROADCAST),
            content=data.get("content", ""),
            payload=data.get("payload") or {},
            correlation_id=data.get("correlation_id"),
            in_reply_to=data.get("in_reply_to"),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )


class MessageBus:
    """
    Central Thread-Safe Agent Message Bus & Communication Hub.
    Manages direct agent/task inboxes, topic-based publish/subscribe,
    causal correlation tracking, and synchronous peer consultations.
    Supports SQLite persistence dual-writes and historical replay.
    """

    def __init__(
        self,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        state_store: Optional[Any] = None,
        session_id: Optional[str] = None,
    ):
        self._lock = threading.RLock()
        self._inboxes: Dict[str, List[StructuredMessage]] = {}
        self._subscribers: Dict[str, List[Callable[[StructuredMessage], None]]] = {}
        self._history: List[StructuredMessage] = []
        self.on_event = on_event_callback or (lambda stage, msg, payload=None: None)
        self.state_store = state_store
        self.session_id = session_id

    def set_session(self, session_id: str, state_store: Optional[Any] = None):
        """Sets the active session ID and optional state store for persistence."""
        self.session_id = session_id
        if state_store is not None:
            self.state_store = state_store

    def send_direct(self, message: StructuredMessage) -> StructuredMessage:
        """
        Sends a message directly to a target recipient inbox (agent name or task ID).
        """
        with self._lock:
            self._history.append(message)
            recipient = message.recipient.strip()
            if recipient not in self._inboxes:
                self._inboxes[recipient] = []
            self._inboxes[recipient].append(message)

        if self.state_store and self.session_id:
            try:
                self.state_store.save_message(self.session_id, message)
            except Exception as e:
                self.on_event("MESSAGE BUS PERSIST ERROR", f"Failed to persist message to SQLite: {e}")

        self.on_event(
            "MESSAGE BUS",
            f"Direct [{message.message_type}] from [{message.sender}] to [{message.recipient}]: {message.content[:80]}",
            message.to_dict(),
        )
        return message

    def publish(self, topic: str, message: StructuredMessage) -> StructuredMessage:
        """
        Publishes a message to a topic channel and delivers it to all subscribers and inboxes.
        """
        message.topic = topic
        callbacks_to_invoke = []

        with self._lock:
            self._history.append(message)
            # Deliver to wildcard and topic subscribers
            if topic in self._subscribers:
                callbacks_to_invoke.extend(self._subscribers[topic])
            if "*" in self._subscribers:
                callbacks_to_invoke.extend(self._subscribers["*"])

            # Also deliver to broadcast inboxes
            recipient = message.recipient.strip()
            if recipient in ["*", "all", "broadcast"]:
                for inb in self._inboxes.values():
                    inb.append(message)
            else:
                if recipient not in self._inboxes:
                    self._inboxes[recipient] = []
                self._inboxes[recipient].append(message)

        if self.state_store and self.session_id:
            try:
                self.state_store.save_message(self.session_id, message)
            except Exception as e:
                self.on_event("MESSAGE BUS PERSIST ERROR", f"Failed to persist message to SQLite: {e}")

        # Fire callbacks outside lock to prevent deadlocks
        for cb in callbacks_to_invoke:
            try:
                cb(message)
            except Exception as e:
                self.on_event("MESSAGE BUS ERROR", f"Error in topic [{topic}] subscriber callback: {e}")

        self.on_event(
            "MESSAGE BUS",
            f"Published [{message.message_type}] on topic [{topic}] from [{message.sender}]: {message.content[:80]}",
            message.to_dict(),
        )
        return message

    def load_history_from_store(self, session_id: str):
        """Loads and re-hydrates message history and inboxes from the persistent state store."""
        if not self.state_store:
            return
        self.session_id = session_id
        messages = self.state_store.get_messages(session_id)
        with self._lock:
            self._history = list(messages)
            self._inboxes = {}
            for msg in messages:
                rec = msg.recipient.strip()
                if rec not in ["*", "all", "broadcast"]:
                    if rec not in self._inboxes:
                        self._inboxes[rec] = []
                    self._inboxes[rec].append(msg)

    def subscribe(self, topic: str, callback: Callable[[StructuredMessage], None]):
        """
        Subscribes a callback to a specific topic channel (or '*' for all topics).
        """
        with self._lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            self._subscribers[topic].append(callback)

    def get_inbox(self, recipient: str, clear: bool = False) -> List[StructuredMessage]:
        """
        Retrieves all pending messages delivered to this recipient (agent or task ID).
        """
        with self._lock:
            msgs = list(self._inboxes.get(recipient, []))
            if clear and recipient in self._inboxes:
                self._inboxes[recipient] = []
            return msgs

    def clear_inbox(self, recipient: str):
        with self._lock:
            if recipient in self._inboxes:
                self._inboxes[recipient] = []

    def query_agent_sync(
        self,
        sender: str,
        target_agent: str,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        agent_registry: Any = None,
        llm: Any = None,
        workspace: Any = None,
    ) -> StructuredMessage:
        """
        Executes a synchronous peer consultation:
        Dispatches a targeted query from sender to target_agent, generates an expert response,
        and records both REQUEST and RESPONSE in the message bus.
        """
        req_msg = StructuredMessage(
            sender=sender,
            recipient=target_agent,
            message_type=MessageType.REQUEST,
            content=query,
            topic=f"query_{target_agent.lower()}",
            payload=context or {},
        )
        self.send_direct(req_msg)

        reply_content = ""
        structured_reply = {}

        if agent_registry and hasattr(agent_registry, "create_agent_instance") and llm:
            try:
                agent_instance = agent_registry.create_agent_instance(
                    name_or_manifest=target_agent,
                    llm=llm,
                    workspace=workspace,
                )
                prompt = f"""
[PEER CONSULTATION REQUEST]
Sender: {sender}
Query: {query}
Context Provided:
{json.dumps(context or {}, indent=2)}

Provide your expert answer and recommendations:
"""
                system_prompt = getattr(agent_instance, "build_system_prompt", lambda: f"You are the {target_agent} agent.")()
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ]
                reply_content = llm.chat(messages, model=getattr(agent_instance, "model", None), temperature=0.2)
                structured_reply = {"reply": reply_content}
            except Exception as e:
                reply_content = f"Error during peer query to [{target_agent}]: {str(e)}"
                structured_reply = {"error": str(e)}
        elif llm:
            messages = [
                {"role": "system", "content": f"You are the {target_agent} specialized agent answering a peer query."},
                {"role": "user", "content": f"Query from {sender}:\n{query}\nContext: {json.dumps(context or {})}"},
            ]
            reply_content = llm.chat(messages, temperature=0.2)
            structured_reply = {"reply": reply_content}
        else:
            reply_content = f"Peer response from [{target_agent}] acknowledged for query: '{query}'"
            structured_reply = {"status": "ACK"}

        resp_msg = StructuredMessage(
            sender=target_agent,
            recipient=sender,
            message_type=MessageType.RESPONSE,
            content=reply_content,
            topic=req_msg.topic,
            payload=structured_reply,
            correlation_id=req_msg.message_id,
            in_reply_to=req_msg.message_id,
        )
        self.send_direct(resp_msg)
        return resp_msg

    def get_history(
        self,
        topic: Optional[str] = None,
        sender: Optional[str] = None,
        recipient: Optional[str] = None,
        message_type: Optional[Union[MessageType, str]] = None,
        correlation_id: Optional[str] = None,
    ) -> List[StructuredMessage]:
        """
        Queries the message audit trail with optional filters.
        """
        with self._lock:
            matched = list(self._history)

        if topic:
            matched = [m for m in matched if m.topic == topic]
        if sender:
            matched = [m for m in matched if m.sender.upper() == sender.upper()]
        if recipient:
            matched = [m for m in matched if m.recipient.upper() in [recipient.upper(), "*"]]
        if message_type:
            type_val = message_type.value if hasattr(message_type, "value") else str(message_type).upper()
            matched = [m for m in matched if (m.message_type.value if hasattr(m.message_type, "value") else str(m.message_type)).upper() == type_val]
        if correlation_id:
            matched = [m for m in matched if m.correlation_id == correlation_id]

        return matched

    def export_trace(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [m.to_dict() for m in self._history]

