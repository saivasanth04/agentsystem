"""
Swarm Consensus & Conflict Resolution Engine.
Enables multi-agent voting (majority, confidence-weighted, unanimous) and
arbiter-mediated conflict resolution for architectural, code, and security trade-offs.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import threading
from typing import Any, Callable, Dict, List, Optional, Union
import uuid


class VotingMechanism(str, Enum):
    MAJORITY = "MAJORITY"
    CONFIDENCE_WEIGHTED = "CONFIDENCE_WEIGHTED"
    UNANIMOUS = "UNANIMOUS"
    ARBITER_DECIDED = "ARBITER_DECIDED"


@dataclass
class Vote:
    voter_id: str
    option: str
    confidence: float = 1.0
    rationale: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "voter_id": self.voter_id,
            "option": self.option,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "timestamp": self.timestamp,
        }


@dataclass
class Proposal:
    proposal_id: str
    proposer_id: str
    issue: str
    options: List[str]
    voting_mechanism: VotingMechanism = VotingMechanism.MAJORITY
    quorum: int = 2
    votes: Dict[str, Vote] = field(default_factory=dict)
    resolved: bool = False
    winning_option: Optional[str] = None
    resolution_summary: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposer_id": self.proposer_id,
            "issue": self.issue,
            "options": list(self.options),
            "voting_mechanism": self.voting_mechanism.value if hasattr(self.voting_mechanism, "value") else str(self.voting_mechanism),
            "quorum": self.quorum,
            "votes": {voter: v.to_dict() for voter, v in self.votes.items()},
            "resolved": self.resolved,
            "winning_option": self.winning_option,
            "resolution_summary": self.resolution_summary,
            "created_at": self.created_at,
        }


@dataclass
class ConflictRecord:
    conflict_id: str
    agent_a_id: str
    agent_b_id: str
    issue: str
    agent_a_position: str
    agent_b_position: str
    arbiter_agent_id: str
    resolution: Optional[str] = None
    resolved: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "agent_a_id": self.agent_a_id,
            "agent_b_id": self.agent_b_id,
            "issue": self.issue,
            "agent_a_position": self.agent_a_position,
            "agent_b_position": self.agent_b_position,
            "arbiter_agent_id": self.arbiter_agent_id,
            "resolution": self.resolution,
            "resolved": self.resolved,
            "timestamp": self.timestamp,
        }


class SwarmConsensusEngine:
    """
    Manages voting proposals and dialectic conflict mediation across swarm agents.
    """

    def __init__(
        self,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ):
        self._lock = threading.RLock()
        self._proposals: Dict[str, Proposal] = {}
        self._conflicts: Dict[str, ConflictRecord] = {}
        self.on_event = on_event_callback or (lambda stage, msg, payload=None: None)

    def create_proposal(
        self,
        proposer_id: str,
        issue: str,
        options: List[str],
        mechanism: Union[VotingMechanism, str] = VotingMechanism.MAJORITY,
        quorum: int = 2,
    ) -> Proposal:
        """Creates a new voting proposal."""
        if isinstance(mechanism, str):
            try:
                mechanism = VotingMechanism(mechanism.upper())
            except ValueError:
                mechanism = VotingMechanism.MAJORITY

        proposal_id = f"prop-{uuid.uuid4().hex[:8]}"
        prop = Proposal(
            proposal_id=proposal_id,
            proposer_id=proposer_id,
            issue=issue,
            options=options,
            voting_mechanism=mechanism,
            quorum=max(1, quorum),
        )

        with self._lock:
            self._proposals[proposal_id] = prop

        self.on_event(
            "SWARM_PROPOSAL",
            f"Agent [{proposer_id}] proposed [{issue[:80]}] with options {options} (mech={prop.voting_mechanism.value})",
            prop.to_dict(),
        )
        return prop

    def cast_vote(
        self,
        proposal_id: str,
        voter_id: str,
        option: str,
        confidence: float = 1.0,
        rationale: str = "",
    ) -> bool:
        """Casts a vote on an active proposal."""
        with self._lock:
            prop = self._proposals.get(proposal_id)
            if not prop or prop.resolved:
                return False

            if option not in prop.options:
                return False

            vote = Vote(
                voter_id=voter_id,
                option=option,
                confidence=max(0.0, min(1.0, float(confidence))),
                rationale=rationale,
            )
            prop.votes[voter_id] = vote

        self.on_event(
            "SWARM_VOTE",
            f"Agent [{voter_id}] voted [{option}] on proposal [{proposal_id}] (conf={vote.confidence:.2f})",
            {"proposal_id": proposal_id, "vote": vote.to_dict()},
        )
        return True

    def tally_votes(self, proposal_id: str) -> Dict[str, Any]:
        """
        Tallies votes according to the proposal's voting mechanism.
        If quorum is reached, marks proposal as resolved and sets the winning option.
        """
        with self._lock:
            prop = self._proposals.get(proposal_id)
            if not prop:
                return {"status": "NOT_FOUND"}

            total_votes = len(prop.votes)
            if total_votes < prop.quorum:
                return {
                    "status": "QUORUM_NOT_REACHED",
                    "votes_count": total_votes,
                    "quorum": prop.quorum,
                }

            scores: Dict[str, float] = {opt: 0.0 for opt in prop.options}
            counts: Dict[str, int] = {opt: 0 for opt in prop.options}

            for vote in prop.votes.values():
                counts[vote.option] = counts.get(vote.option, 0) + 1
                if prop.voting_mechanism == VotingMechanism.CONFIDENCE_WEIGHTED:
                    scores[vote.option] = scores.get(vote.option, 0.0) + vote.confidence
                else:
                    scores[vote.option] = scores.get(vote.option, 0.0) + 1.0

            winner = None
            if prop.voting_mechanism == VotingMechanism.UNANIMOUS:
                for opt, count in counts.items():
                    if count == total_votes:
                        winner = opt
                        break
                if not winner:
                    prop.resolved = True
                    prop.winning_option = None
                    prop.resolution_summary = "Unanimous consent could not be reached."
                    return {
                        "status": "NO_UNANIMITY",
                        "counts": counts,
                        "scores": scores,
                    }
            else:
                # Majority or Confidence-Weighted
                winner = max(scores, key=lambda k: scores[k])

            prop.resolved = True
            prop.winning_option = winner
            prop.resolution_summary = f"Decided via {prop.voting_mechanism.value} with score {scores.get(winner, 0.0):.2f}"

            self.on_event(
                "SWARM_CONSENSUS_REACHED",
                f"Proposal [{proposal_id}] resolved: [{winner}] ({prop.resolution_summary})",
                prop.to_dict(),
            )

            return {
                "status": "RESOLVED",
                "winning_option": winner,
                "summary": prop.resolution_summary,
                "counts": counts,
                "scores": scores,
            }

    def resolve_conflict(
        self,
        agent_a_id: str,
        agent_b_id: str,
        issue: str,
        agent_a_position: str,
        agent_b_position: str,
        arbiter_agent_id: str = "ARCHITECTURE",
        arbiter_decision: Optional[str] = None,
    ) -> ConflictRecord:
        """
        Registers an explicit conflict and mediates a resolution via an arbiter agent.
        """
        conflict_id = f"conf-{uuid.uuid4().hex[:8]}"
        record = ConflictRecord(
            conflict_id=conflict_id,
            agent_a_id=agent_a_id,
            agent_b_id=agent_b_id,
            issue=issue,
            agent_a_position=agent_a_position,
            agent_b_position=agent_b_position,
            arbiter_agent_id=arbiter_agent_id,
            resolution=arbiter_decision,
            resolved=bool(arbiter_decision),
        )

        with self._lock:
            self._conflicts[conflict_id] = record

        self.on_event(
            "SWARM_CONFLICT_RESOLVED" if record.resolved else "SWARM_CONFLICT_OPENED",
            f"Conflict between [{agent_a_id}] & [{agent_b_id}] on [{issue[:60]}]: "
            f"{'Resolved by ' + arbiter_agent_id if record.resolved else 'Awaiting arbiter ' + arbiter_agent_id}",
            record.to_dict(),
        )
        return record

    def get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        with self._lock:
            return self._proposals.get(proposal_id)

    def get_conflict(self, conflict_id: str) -> Optional[ConflictRecord]:
        with self._lock:
            return self._conflicts.get(conflict_id)
