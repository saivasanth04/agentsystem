"""
Working Memory & Inter-Task Epistemic State.
Maintains an active scratchpad of verified facts, discovered pitfalls, and modified symbols,
passing compact situational awareness across task waves and replan cycles.
"""
from dataclasses import dataclass, field
import threading
from typing import Any, Dict, List, Optional


@dataclass
class WorkingMemory:
    """
    Thread-safe working memory buffer for multi-agent execution.
    Maintains an active scratchpad of verified facts, discovered pitfalls,
    active hypotheses, and key-value state across task waves and replan cycles.
    """
    active_goal: str = ""
    verified_facts: List[str] = field(default_factory=list)
    discovered_pitfalls: List[str] = field(default_factory=list)
    modified_symbols: List[str] = field(default_factory=list)
    scratchpad_notes: List[str] = field(default_factory=list)
    current_hypothesis: str = ""
    tested_hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    _kv_store: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self._lock = threading.RLock()
        if not hasattr(self, "_kv_store") or self._kv_store is None:
            self._kv_store = {}
        if not hasattr(self, "tested_hypotheses") or self.tested_hypotheses is None:
            self.tested_hypotheses = []

    def set_active_goal(self, goal: str):
        with self._lock:
            self.active_goal = goal.strip()

    def set(self, key: str, value: Any) -> None:
        """Stores a key-value item in working memory."""
        with self._lock:
            self._kv_store[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieves a key-value item from working memory."""
        with self._lock:
            return self._kv_store.get(key, default)

    def record_hypothesis(self, hypothesis: str, status: str = "PENDING", evidence: str = "") -> None:
        """Records an active or evaluated hypothesis."""
        with self._lock:
            h_clean = hypothesis.strip()
            self.current_hypothesis = h_clean
            entry = {
                "hypothesis": h_clean,
                "status": status.upper().strip(),
                "evidence": evidence.strip(),
            }
            self.tested_hypotheses.append(entry)

    def record_fact(self, fact: str):
        """Records a verified truth or discovery (e.g. 'auth module expects Bearer token')."""
        with self._lock:
            fact_clean = fact.strip()
            if fact_clean and fact_clean not in self.verified_facts:
                self.verified_facts.append(fact_clean)

    def record_pitfall(self, pitfall: str):
        """Records a defect, bug pattern, or constraint discovered during execution."""
        with self._lock:
            p_clean = pitfall.strip()
            if p_clean and p_clean not in self.discovered_pitfalls:
                self.discovered_pitfalls.append(p_clean)

    def record_modified_symbol(self, symbol: str):
        """Records a class, function, or interface modified by an agent."""
        with self._lock:
            s_clean = symbol.strip()
            if s_clean and s_clean not in self.modified_symbols:
                self.modified_symbols.append(s_clean)

    def update_scratchpad(self, note: str):
        """Appends a working note or hypothesis."""
        with self._lock:
            n_clean = note.strip()
            if n_clean:
                self.scratchpad_notes.append(n_clean)

    def get_summary(self, max_tokens: int = 1500) -> str:
        """
        Returns a token-compact markdown summary of working memory suitable for prompt injection.
        """
        with self._lock:
            lines = []
            if self.active_goal:
                lines.append(f"**Active Milestone Goal**: {self.active_goal}")

            if self.current_hypothesis:
                lines.append(f"**Current Working Hypothesis**: {self.current_hypothesis}")

            if self.verified_facts:
                lines.append("\n**Verified Findings & Facts**:")
                for f in self.verified_facts[-10:]:
                    lines.append(f"• {f}")

            if self.discovered_pitfalls:
                lines.append("\n**Discovered Pitfalls & Constraints**:")
                for p in self.discovered_pitfalls[-8:]:
                    lines.append(f"⚠️ {p}")

            if self.tested_hypotheses:
                lines.append("\n**Tested Hypotheses**:")
                for th in self.tested_hypotheses[-5:]:
                    stat = th.get("status", "PENDING")
                    ev = f" (Evidence: {th['evidence']})" if th.get("evidence") else ""
                    lines.append(f"• [{stat}] {th.get('hypothesis')}{ev}")

            if self.modified_symbols:
                lines.append(f"\n**Modified Symbols Across Waves**: {', '.join(self.modified_symbols[-15:])}")

            if self._kv_store:
                summary_keys = [k for k in self._kv_store.keys() if not k.startswith("_")][:5]
                if summary_keys:
                    lines.append(f"\n**Working State Keys**: {', '.join(summary_keys)}")

            if self.scratchpad_notes:
                lines.append("\n**Agent Scratchpad Notes**:")
                for n in self.scratchpad_notes[-5:]:
                    lines.append(f"- {n}")

            if not lines:
                return "Working memory is currently empty."

            summary = "\n".join(lines)
            max_chars = max_tokens * 4
            if len(summary) > max_chars:
                return summary[:max_chars] + "\n... [Working memory summary truncated] ..."
            return summary

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "active_goal": self.active_goal,
                "verified_facts": list(self.verified_facts),
                "discovered_pitfalls": list(self.discovered_pitfalls),
                "modified_symbols": list(self.modified_symbols),
                "scratchpad_notes": list(self.scratchpad_notes),
                "current_hypothesis": self.current_hypothesis,
                "tested_hypotheses": list(self.tested_hypotheses),
                "kv_store": dict(self._kv_store),
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkingMemory":
        inst = cls(
            active_goal=data.get("active_goal", ""),
            verified_facts=list(data.get("verified_facts", [])),
            discovered_pitfalls=list(data.get("discovered_pitfalls", [])),
            modified_symbols=list(data.get("modified_symbols", [])),
            scratchpad_notes=list(data.get("scratchpad_notes", [])),
            current_hypothesis=data.get("current_hypothesis", ""),
            tested_hypotheses=list(data.get("tested_hypotheses", [])),
            _kv_store=dict(data.get("kv_store", {})),
        )
        return inst

    def clear(self):
        with self._lock:
            self.active_goal = ""
            self.verified_facts.clear()
            self.discovered_pitfalls.clear()
            self.modified_symbols.clear()
            self.scratchpad_notes.clear()
            self.current_hypothesis = ""
            self.tested_hypotheses.clear()
            self._kv_store.clear()

