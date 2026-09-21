"""
Dynamic Swarm Blackboard & Shared Collaboration Scratchpad.
Enables real-time intermediate finding publishing, topic subscriptions,
confidence-scored data synthesis, and concurrent resource reservations (locks)
to prevent multi-agent write collisions.
"""
from dataclasses import dataclass, field
from datetime import datetime
import fnmatch
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import uuid


@dataclass
class BlackboardEntry:
    """A single piece of knowledge, observation, or intermediate deliverable on the blackboard."""
    entry_id: str
    author_id: str
    topic: str
    key: str
    data: Any
    confidence: float = 1.0
    tags: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "author_id": self.author_id,
            "topic": self.topic,
            "key": self.key,
            "data": self.data,
            "confidence": self.confidence,
            "tags": list(self.tags),
            "timestamp": self.timestamp,
        }


@dataclass
class ResourceReservation:
    """Lease-based file or component reservation to prevent concurrent agent conflicts."""
    resource_path: str
    owner_id: str
    acquired_at: float = field(default_factory=time.time)
    lease_seconds: float = 60.0

    @property
    def expires_at(self) -> float:
        return self.acquired_at + self.lease_seconds

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_path": self.resource_path,
            "owner_id": self.owner_id,
            "acquired_at": self.acquired_at,
            "lease_seconds": self.lease_seconds,
            "expires_at": self.expires_at,
            "is_expired": self.is_expired(),
        }


class SwarmBlackboard:
    """
    High-throughput, thread-safe shared blackboard for multi-agent swarms.
    Provides structured pub/sub, confidence-scored knowledge lookup,
    and distributed resource reservations.
    """

    def __init__(
        self,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ):
        self._lock = threading.RLock()
        # topic -> key -> BlackboardEntry
        self._entries: Dict[str, Dict[str, BlackboardEntry]] = {}
        # topic -> list of (sub_id, callback)
        self._subscribers: Dict[str, List[tuple]] = {}
        # normalized_resource_path -> ResourceReservation
        self._resource_locks: Dict[str, ResourceReservation] = {}
        self.on_event = on_event_callback or (lambda stage, msg, payload=None: None)

    def post(
        self,
        topic: str,
        key: str,
        data: Any,
        author_id: str = "SYSTEM",
        confidence: float = 1.0,
        tags: Optional[List[str]] = None,
    ) -> BlackboardEntry:
        """
        Posts or updates an entry on the shared blackboard.
        Notifies all matching topic subscribers.
        """
        norm_topic = topic.strip().lower()
        norm_key = key.strip()
        entry = BlackboardEntry(
            entry_id=f"bb-{uuid.uuid4().hex[:8]}",
            author_id=author_id,
            topic=norm_topic,
            key=norm_key,
            data=data,
            confidence=max(0.0, min(1.0, float(confidence))),
            tags=tags or [],
        )

        subscribers_to_notify = []
        with self._lock:
            if norm_topic not in self._entries:
                self._entries[norm_topic] = {}
            self._entries[norm_topic][norm_key] = entry

            # Collect topic and wildcard subscribers
            for sub_topic, sub_list in self._subscribers.items():
                if sub_topic == "*" or sub_topic == norm_topic or fnmatch.fnmatch(norm_topic, sub_topic):
                    for _, callback in sub_list:
                        subscribers_to_notify.append(callback)

        # Invoke callbacks outside lock to prevent deadlocks
        for cb in subscribers_to_notify:
            try:
                cb(entry)
            except Exception as e:
                self.on_event("BLACKBOARD_SUB_ERROR", f"Subscriber error on topic [{norm_topic}]: {e}")

        self.on_event(
            "BLACKBOARD_POST",
            f"Agent [{author_id}] posted [{norm_key}] to topic [{norm_topic}] (conf={entry.confidence:.2f})",
            entry.to_dict(),
        )
        return entry

    def read(self, topic: str, key: str) -> Optional[BlackboardEntry]:
        """Reads a specific entry from a topic."""
        with self._lock:
            topic_entries = self._entries.get(topic.strip().lower())
            if not topic_entries:
                return None
            return topic_entries.get(key.strip())

    def query(
        self,
        topic: Optional[str] = None,
        tags: Optional[List[str]] = None,
        min_confidence: float = 0.0,
        author_id: Optional[str] = None,
    ) -> List[BlackboardEntry]:
        """
        Queries blackboard entries matching topic, tags, confidence, or author.
        """
        with self._lock:
            results = []
            topics_to_scan = [topic.strip().lower()] if topic else list(self._entries.keys())

            for t in topics_to_scan:
                for entry in self._entries.get(t, {}).values():
                    if entry.confidence < min_confidence:
                        continue
                    if author_id and entry.author_id != author_id:
                        continue
                    if tags:
                        if not any(tag in entry.tags for tag in tags):
                            continue
                    results.append(entry)
            return results

    def subscribe(self, topic: str, callback: Callable[[BlackboardEntry], None]) -> str:
        """
        Subscribes a callback to a topic or wildcard pattern (e.g. 'code.*', '*').
        Returns a unique subscription ID.
        """
        sub_id = f"sub-{uuid.uuid4().hex[:6]}"
        norm_topic = topic.strip().lower()
        with self._lock:
            if norm_topic not in self._subscribers:
                self._subscribers[norm_topic] = []
            self._subscribers[norm_topic].append((sub_id, callback))
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Removes a subscription by ID."""
        with self._lock:
            for topic, sub_list in list(self._subscribers.items()):
                new_list = [s for s in sub_list if s[0] != subscription_id]
                if len(new_list) != len(sub_list):
                    self._subscribers[topic] = new_list
                    return True
        return False

    def reserve_resource(
        self,
        resource_path: str,
        agent_id: str,
        lease_seconds: float = 60.0,
    ) -> bool:
        """
        Attempts to acquire an exclusive write lock on a resource (file or component).
        Returns True if acquired or renewed, False if already held by another active agent.
        """
        norm_path = resource_path.replace("\\", "/").strip("/").lower()
        with self._lock:
            existing = self._resource_locks.get(norm_path)
            if existing and not existing.is_expired():
                if existing.owner_id == agent_id:
                    # Renew lease
                    existing.acquired_at = time.time()
                    existing.lease_seconds = lease_seconds
                    return True
                return False

            self._resource_locks[norm_path] = ResourceReservation(
                resource_path=norm_path,
                owner_id=agent_id,
                lease_seconds=lease_seconds,
            )
            return True

    def release_resource(self, resource_path: str, agent_id: str) -> bool:
        """Releases a previously acquired resource lock."""
        norm_path = resource_path.replace("\\", "/").strip("/").lower()
        with self._lock:
            existing = self._resource_locks.get(norm_path)
            if not existing:
                return False
            if existing.owner_id != agent_id and not existing.is_expired():
                return False
            del self._resource_locks[norm_path]
            return True

    def is_resource_locked(self, resource_path: str, by_other_than: Optional[str] = None) -> bool:
        """Checks if a resource is locked."""
        norm_path = resource_path.replace("\\", "/").strip("/").lower()
        with self._lock:
            existing = self._resource_locks.get(norm_path)
            if not existing or existing.is_expired():
                return False
            if by_other_than and existing.owner_id == by_other_than:
                return False
            return True

    def get_active_locks(self) -> List[Dict[str, Any]]:
        """Returns all currently active resource locks."""
        with self._lock:
            return [lock.to_dict() for lock in self._resource_locks.values() if not lock.is_expired()]

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._subscribers.clear()
            self._resource_locks.clear()
