"""
Context Sufficiency Oracle & Evidence Evaluator.
Evaluates whether retrieved context (focal files, interface signatures, CBM sub-graphs)
provides sufficient evidence to implement a requested task without hallucinating APIs or guessing contracts.
"""
from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .relevance_ranker import RankedContextItem, ContextTier


@dataclass
class SufficiencyEvaluation:
    """Detailed evaluation of context sufficiency for an active task."""
    is_sufficient: bool
    confidence_score: float  # 0.0 to 1.0
    entity_coverage: float   # 0.0 to 1.0
    interface_completeness: float # 0.0 to 1.0
    focal_presence: float    # 0.0 to 1.0
    referenced_entities: List[str] = field(default_factory=list)
    found_entities: List[str] = field(default_factory=list)
    missing_entities: List[str] = field(default_factory=list)
    missing_signatures: List[str] = field(default_factory=list)
    recommended_actions: List[str] = field(default_factory=list)
    evidence_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_sufficient": self.is_sufficient,
            "confidence_score": round(self.confidence_score, 3),
            "entity_coverage": round(self.entity_coverage, 3),
            "interface_completeness": round(self.interface_completeness, 3),
            "focal_presence": round(self.focal_presence, 3),
            "referenced_entities": self.referenced_entities,
            "found_entities": self.found_entities,
            "missing_entities": self.missing_entities,
            "missing_signatures": self.missing_signatures,
            "recommended_actions": self.recommended_actions,
            "evidence_summary": self.evidence_summary,
        }


class ContextSufficiencyOracle:
    """
    Analyzes task requirements and measures coverage of referenced symbols, types,
    and files across the retrieved context tiers.
    """

    STOPWORDS = {
        "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "with",
        "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "can", "could", "should", "would", "will", "shall",
        "this", "that", "these", "those", "it", "its", "from", "as", "by", "of",
        "create", "build", "implement", "update", "modify", "add", "delete",
        "test", "write", "fix", "run", "make", "ensure", "check", "file", "files",
        "code", "class", "method", "function", "using", "into", "through", "when",
        "return", "true", "false", "none", "null", "self", "def", "import", "from",
    }

    def __init__(self, min_confidence_threshold: float = 0.70):
        self.min_confidence_threshold = min_confidence_threshold

    @classmethod
    def extract_task_entities(cls, task_info: Dict[str, Any]) -> List[str]:
        """
        Extracts candidate class names, function names, module names, and file paths
        from the task specification.
        """
        objective = task_info.get("objective", "")
        description = task_info.get("description", "")
        inputs = task_info.get("inputs", []) or []
        outputs = task_info.get("outputs", []) or []

        combined_text = f"{objective} {description} {' '.join(inputs)} {' '.join(outputs)}"

        # 1. File paths (e.g. auth/service.py, models.py)
        file_matches = re.findall(r"[\w\/\.\-]+\.(?:py|ts|js|json|go|rs|java)", combined_text)
        entities: Set[str] = set(file_matches)

        # 2. PascalCase / CamelCase identifiers (e.g., UserAccount, PaymentGateway, verifyUser)
        camel_matches = re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:[A-Z][a-z0-9]+)+\b", combined_text)
        entities.update(camel_matches)

        # 3. snake_case / identifier terms with underscores or length > 3
        snake_matches = re.findall(r"\b[a-z][a-z0-9]+_[a-z0-9_]+\b", combined_text)
        entities.update(snake_matches)

        # Filter out stopwords and numeric strings
        cleaned = []
        for e in sorted(entities):
            clean = e.strip(".,;:()[]{}\"'")
            if (
                clean
                and len(clean) > 2
                and clean.lower() not in cls.STOPWORDS
                and not clean.isdigit()
            ):
                cleaned.append(clean)

        return cleaned

    def evaluate(
        self,
        task_info: Dict[str, Any],
        ranked_items: List[RankedContextItem],
        code_graph: Optional[Any] = None,
        semantic_index: Optional[Any] = None,
        cbm: Optional[Any] = None,
    ) -> SufficiencyEvaluation:
        """
        Evaluates whether the ranked context items provide sufficient evidence
        for the given task.
        """
        referenced_entities = self.extract_task_entities(task_info)

        # Concatenate all retrieved text and collect filepaths
        retrieved_text = " ".join(item.formatted_content for item in ranked_items)
        retrieved_filepaths = {item.filepath.replace("\\", "/") for item in ranked_items}

        # Collect focal items and interface items
        focal_items = [it for it in ranked_items if it.tier == ContextTier.FOCAL]
        interface_items = [it for it in ranked_items if it.tier == ContextTier.INTERFACE]

        # 1. Entity Coverage Calculation
        found_entities: List[str] = []
        missing_entities: List[str] = []

        if not referenced_entities:
            # If no specific entities extracted, default to neutral coverage
            entity_coverage = 0.85
        else:
            for entity in referenced_entities:
                # Check basename if file path
                base_name = entity.split("/")[-1].split("\\")[-1]
                stem_name = base_name.rsplit(".", 1)[0] if "." in base_name else base_name

                if (
                    entity in retrieved_text
                    or base_name in retrieved_text
                    or stem_name in retrieved_text
                    or any(entity in fp or base_name in fp or stem_name in fp for fp in retrieved_filepaths)
                ):
                    found_entities.append(entity)
                else:
                    missing_entities.append(entity)

            entity_coverage = len(found_entities) / max(1, len(referenced_entities))

        # 2. Focal Presence Calculation
        # Check if focal items exist and contain substantive content (not just empty placeholders)
        focal_presence = 0.0
        if focal_items:
            has_substantive = any(
                len(it.formatted_content.strip()) > 30 and "[File to be created or currently empty]" not in it.formatted_content
                for it in focal_items
            )
            inputs_or_outputs = (task_info.get("inputs", []) or []) + (task_info.get("outputs", []) or [])
            if has_substantive:
                focal_presence = 1.0
            elif inputs_or_outputs:
                # Target files explicitly specified, even if new/empty
                focal_presence = 0.80
            else:
                focal_presence = 0.40
        else:
            focal_presence = 0.20

        # 3. Interface Completeness Calculation
        # Check if dependencies have signatures available
        missing_signatures: List[str] = []
        if interface_items:
            total_interfaces = len(interface_items)
            with_signatures = 0
            for it in interface_items:
                if "• " in it.formatted_content or "def " in it.formatted_content or "class " in it.formatted_content:
                    with_signatures += 1
                elif it.symbols:
                    with_signatures += 1
                elif "codebase_memory://subgraph" in it.filepath:
                    with_signatures += 1
                else:
                    missing_signatures.append(it.filepath)

            interface_completeness = with_signatures / max(1, total_interfaces)
        else:
            # If no interface files found, check if code_graph has known dependencies
            interface_completeness = 0.60 if focal_items else 0.30

        # 4. Composite Confidence Score
        confidence_score = (
            0.45 * entity_coverage +
            0.30 * interface_completeness +
            0.25 * focal_presence
        )

        is_sufficient = confidence_score >= self.min_confidence_threshold

        # Recommended actions if insufficient
        recommended_actions = []
        if missing_entities:
            recommended_actions.append(f"Resolve missing symbols via code_graph: {missing_entities[:3]}")
        if focal_presence < 0.50:
            recommended_actions.append("Locate or assign explicit focal target files from codebase")
        if interface_completeness < 0.60:
            recommended_actions.append("Traverse 2-hop dependencies or call graph to extract interface contracts")

        summary = (
            f"Evidence confidence: {confidence_score:.0%} "
            f"(Coverage: {len(found_entities)}/{len(referenced_entities)} entities, "
            f"Focal: {len(focal_items)} files, "
            f"Interfaces: {len(interface_items)} modules). "
            f"{'SUFFICIENT' if is_sufficient else 'INSUFFICIENT'}"
        )

        return SufficiencyEvaluation(
            is_sufficient=is_sufficient,
            confidence_score=confidence_score,
            entity_coverage=entity_coverage,
            interface_completeness=interface_completeness,
            focal_presence=focal_presence,
            referenced_entities=referenced_entities,
            found_entities=found_entities,
            missing_entities=missing_entities,
            missing_signatures=missing_signatures,
            recommended_actions=recommended_actions,
            evidence_summary=summary,
        )


__all__ = ["ContextSufficiencyOracle", "SufficiencyEvaluation"]
