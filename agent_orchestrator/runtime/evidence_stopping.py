"""
Evidence-Based Stopping Criteria & Verification Ledger.
Defines explicit evidence types, proof citations, epistemic confidence scoring,
and evidence aggregation for autonomous agent stopping decisions.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class EvidenceType(str, Enum):
    """Categories of empirical verification evidence used to justify task completion."""
    TEST_PASS = "TEST_PASS"                         # Direct test suite execution passed with exit code 0
    INSPECTION_CONFIRMED = "INSPECTION_CONFIRMED"   # Target files or workspace state read and confirmed
    SYNTAX_VALID = "SYNTAX_VALID"                   # Code AST parsed and validated without syntax errors
    INFORMATION_SATURATED = "INFORMATION_SATURATED" # Exploratory context gathered with 0 missing entities
    GOAL_SATISFIED_EARLY = "GOAL_SATISFIED_EARLY"   # Overall objective achieved early; downstream tasks redundant


@dataclass
class EvidenceRecord:
    """An empirical proof citation justifying task completion."""
    evidence_type: EvidenceType
    proof_citation: str = ""                         # Specific command output, test result, or file observation
    confidence_score: float = 1.0                    # Epistemic confidence between 0.0 and 1.0
    redundant_tasks: List[str] = field(default_factory=list) # Downstream task IDs rendered unnecessary
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_type": self.evidence_type.value if isinstance(self.evidence_type, EvidenceType) else str(self.evidence_type),
            "proof_citation": self.proof_citation,
            "confidence_score": round(self.confidence_score, 4),
            "redundant_tasks": list(self.redundant_tasks),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceRecord":
        raw_type = data.get("evidence_type", EvidenceType.INSPECTION_CONFIRMED.value)
        try:
            ev_type = EvidenceType(raw_type)
        except ValueError:
            ev_type = EvidenceType.INSPECTION_CONFIRMED
        return cls(
            evidence_type=ev_type,
            proof_citation=data.get("proof_citation", ""),
            confidence_score=float(data.get("confidence_score", 1.0)),
            redundant_tasks=list(data.get("redundant_tasks", [])),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )


@dataclass
class EvidenceLedger:
    """Cumulative ledger tracking empirical evidence records across turns and tasks."""
    records: List[EvidenceRecord] = field(default_factory=list)

    def add_evidence(self, record: EvidenceRecord) -> None:
        self.records.append(record)

    def cumulative_confidence(self) -> float:
        if not self.records:
            return 0.0
        # Weighted confidence emphasizing highest quality evidence
        type_weights = {
            EvidenceType.TEST_PASS: 1.0,
            EvidenceType.GOAL_SATISFIED_EARLY: 0.95,
            EvidenceType.SYNTAX_VALID: 0.85,
            EvidenceType.INSPECTION_CONFIRMED: 0.80,
            EvidenceType.INFORMATION_SATURATED: 0.75,
        }
        total_weight = sum(type_weights.get(r.evidence_type, 0.7) for r in self.records)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(
            r.confidence_score * type_weights.get(r.evidence_type, 0.7)
            for r in self.records
        )
        return round(min(1.0, weighted_sum / total_weight), 4)

    def is_sufficient(self, min_threshold: float = 0.70) -> bool:
        return self.cumulative_confidence() >= min_threshold

    def get_redundant_tasks(self) -> List[str]:
        redundant = []
        for r in self.records:
            if r.evidence_type == EvidenceType.GOAL_SATISFIED_EARLY:
                redundant.extend(r.redundant_tasks)
        return sorted(list(set(redundant)))

    def summary(self) -> str:
        lines = [f"Evidence Ledger ({len(self.records)} records, Cumulative Confidence: {self.cumulative_confidence():.2f}):"]
        for idx, r in enumerate(self.records, start=1):
            proof = f" - '{r.proof_citation}'" if r.proof_citation else ""
            lines.append(f"  {idx}. [{r.evidence_type.value}] Confidence: {r.confidence_score:.2f}{proof}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_records": len(self.records),
            "cumulative_confidence": self.cumulative_confidence(),
            "is_sufficient": self.is_sufficient(),
            "redundant_tasks": self.get_redundant_tasks(),
            "records": [r.to_dict() for r in self.records],
        }


__all__ = ["EvidenceType", "EvidenceRecord", "EvidenceLedger"]
