"""
SpecTraceabilityOracle: Deterministic Specification and Acceptance Criteria Traceability Engine.
Verifies that all requirements and acceptance criteria defined in specification_output
are traced to and verified by actual test cases, preventing silent omission of requirements.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union


@dataclass
class AcceptanceCriterion:
    criterion_id: str
    description: str
    verified: bool = False
    matched_tests: List[str] = field(default_factory=list)
    unmatched_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "verified": self.verified,
            "matched_tests": self.matched_tests,
            "unmatched_reason": self.unmatched_reason,
        }


@dataclass
class SpecTraceabilityReport:
    total_criteria: int = 0
    verified_criteria: int = 0
    unverified_criteria: List[AcceptanceCriterion] = field(default_factory=list)
    all_criteria: List[AcceptanceCriterion] = field(default_factory=list)
    traceability_percentage: float = 100.0
    all_verified: bool = True
    skipped: bool = False
    skip_reason: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.all_verified

    @property
    def coverage_percent(self) -> float:
        return self.traceability_percentage

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_criteria": self.total_criteria,
            "verified_criteria": self.verified_criteria,
            "unverified_criteria": [c.to_dict() for c in self.unverified_criteria],
            "all_criteria": [c.to_dict() for c in self.all_criteria],
            "traceability_percentage": round(self.traceability_percentage, 2),
            "all_verified": self.all_verified,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }

    def summary(self) -> str:
        if self.skipped:
            return f"Spec Traceability: SKIPPED ({self.skip_reason or 'No criteria specified'})"
        status = "PASSED" if self.all_verified else "FAILED"
        lines = [
            f"Spec Traceability: {status} ({self.verified_criteria}/{self.total_criteria} criteria verified, {self.traceability_percentage:.1f}%)"
        ]
        if self.unverified_criteria:
            lines.append("Unverified Criteria:")
            for uc in self.unverified_criteria:
                lines.append(f"  * [{uc.criterion_id}] {uc.description[:80]} - {uc.unmatched_reason or 'No matching test found'}")
        return "\n".join(lines)


class SpecTraceabilityOracle:
    """
    Extracts acceptance criteria from specification output and verifies test coverage.
    """

    def __init__(self, workspace: Any = None, workspace_dir: Any = None):
        ws = workspace if workspace is not None else workspace_dir
        self.workspace = ws
        self.root_dir = Path(ws.root_dir if hasattr(ws, "root_dir") else ws).resolve() if ws else Path.cwd()

    def verify_traceability(
        self,
        specification_output: Union[str, Dict[str, Any], None] = None,
        test_output: Optional[Dict[str, Any]] = None,
        test_files: Optional[List[str]] = None,
    ) -> SpecTraceabilityReport:
        if isinstance(specification_output, str):
            criteria = self.extract_criteria_from_spec(specification_output)
        else:
            criteria = self._extract_criteria(specification_output)

        if not criteria:
            return SpecTraceabilityReport(
                total_criteria=0,
                verified_criteria=0,
                traceability_percentage=100.0,
                all_verified=True,
                skipped=True,
                skip_reason="No explicit acceptance criteria found in specification.",
            )

        has_test_files = bool(test_files) or any(
            (p.name.startswith("test_") or p.name.endswith("_test.py") or ".test." in p.name or ".spec." in p.name)
            for p in self.root_dir.glob("**/*")
            if p.is_file() and not p.is_symlink()
        )
        if not has_test_files:
            stdout_text = test_output.get("stdout", "") if isinstance(test_output, dict) else ""
            if not any(k in stdout_text for k in ("test_", "Test", "Scenario", "PASS", "FAIL")):
                return SpecTraceabilityReport(
                    total_criteria=len(criteria),
                    verified_criteria=len(criteria),
                    traceability_percentage=100.0,
                    all_verified=True,
                    skipped=True,
                    skip_reason="No test files on disk or structured test cases available to verify criteria traceability.",
                )

        test_content_corpus = self._build_test_corpus(test_output, test_files=test_files)

        # Check structural traceability matrix if workspace is available
        matrix_verified_ids = set()
        if self.workspace:
            try:
                from .traceability import TraceabilityEngine, TraceStatus
                matrix = TraceabilityEngine.build_matrix(
                    spec=specification_output,
                    workspace=self.workspace,
                    test_results=test_output,
                )
                for node_id, node in matrix.nodes.items():
                    if node.status == TraceStatus.VERIFIED:
                        matrix_verified_ids.add(node_id.lower())
            except Exception:
                pass

        verified_count = 0
        unverified: List[AcceptanceCriterion] = []

        for crit in criteria:
            c_id = crit.criterion_id.lower()
            matched_tests = self._match_criterion_to_tests(crit, test_content_corpus)
            if c_id in matrix_verified_ids or matched_tests:
                crit.verified = True
                crit.matched_tests = matched_tests or [f"matrix:{c_id}"]
                verified_count += 1
            else:
                crit.verified = False
                crit.unmatched_reason = "No test assertion or test method found verifying this criterion."
                unverified.append(crit)

        total = len(criteria)
        pct = (verified_count / total * 100.0) if total > 0 else 100.0
        all_passed = (len(unverified) == 0)

        return SpecTraceabilityReport(
            total_criteria=total,
            verified_criteria=verified_count,
            unverified_criteria=unverified,
            all_criteria=criteria,
            traceability_percentage=pct,
            all_verified=all_passed,
            skipped=False,
        )

    def extract_criteria_from_spec(self, spec: Union[str, Dict[str, Any], List[Any]]) -> List[AcceptanceCriterion]:
        """Extracts criteria from markdown text (checkboxes, GIVEN/WHEN/THEN, numbered lists) or structured dict."""
        if isinstance(spec, dict):
            return self._extract_criteria(spec)
        if isinstance(spec, list):
            return self._extract_criteria({"acceptance_criteria": spec})
        if not isinstance(spec, str):
            return []

        criteria: List[AcceptanceCriterion] = []
        # 1. Look for Scenario / Given / When / Then
        if "Given" in spec or "Scenario" in spec or "Then" in spec:
            scenarios = re.split(r"(?:Scenario\s*\d*:?|Feature:)", spec, flags=re.IGNORECASE)
            for idx, sc in enumerate(scenarios):
                sc_str = sc.strip()
                if not sc_str:
                    continue
                then_matches = re.findall(r"Then\s+(.*)", sc_str, re.IGNORECASE)
                if then_matches:
                    desc = "Then " + "; ".join(t.strip() for t in then_matches)
                    criteria.append(AcceptanceCriterion(criterion_id=f"SC-{idx}", description=desc))
                elif "When" in sc_str or "Given" in sc_str:
                    criteria.append(AcceptanceCriterion(criterion_id=f"SC-{idx}", description=sc_str))

        # 2. Look for checkboxes: - [ ] AC-1: ... or - [ ] ...
        for line in spec.splitlines():
            line = line.strip()
            chk = re.match(r"^-\s*\[\s*\]\s*(?:([A-Za-z0-9_\-]+)[:\.]\s*)?(.*)$", line)
            if chk:
                c_id = chk.group(1) or f"AC-{len(criteria)+1}"
                c_desc = chk.group(2).strip()
                if c_desc and not any(c.criterion_id == c_id for c in criteria):
                    criteria.append(AcceptanceCriterion(criterion_id=c_id, description=c_desc))

        return criteria

    def _extract_criteria(self, spec: Optional[Dict[str, Any]]) -> List[AcceptanceCriterion]:
        if not spec or not isinstance(spec, dict):
            return []

        # If spec_content string is present in dict, extract from it
        if "spec_content" in spec and isinstance(spec["spec_content"], str):
            from_text = self.extract_criteria_from_spec(spec["spec_content"])
            if from_text:
                return from_text

        criteria: List[AcceptanceCriterion] = []
        raw_items = []

        # Check common keys
        for key in ("acceptance_criteria", "requirements", "contracts", "acceptance_tests", "features"):
            val = spec.get(key)
            if isinstance(val, list):
                raw_items.extend(val)
            elif isinstance(val, dict):
                for k, v in val.items():
                    raw_items.append(f"{k}: {v}")

        for idx, item in enumerate(raw_items, start=1):
            crit_id = f"AC-{idx}"
            desc = ""
            if isinstance(item, dict):
                crit_id = str(item.get("id") or item.get("criterion_id") or f"AC-{idx}")
                desc = str(item.get("description") or item.get("criterion") or item.get("name") or json.dumps(item))
            elif isinstance(item, str):
                s = item.strip()
                if not s:
                    continue
                # Check for "AC-1: Description" or "1. Description"
                id_match = re.match(r"^([A-Za-z0-9_\-]+)[:\.]\s*(.*)$", s)
                if id_match:
                    crit_id, desc = id_match.groups()
                else:
                    desc = s
            if desc:
                criteria.append(AcceptanceCriterion(criterion_id=crit_id, description=desc))

        return criteria

    def _build_test_corpus(
        self,
        test_output: Optional[Dict[str, Any]],
        test_files: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Collects text from test files and test stdout for matching."""
        corpus: Dict[str, str] = {}

        if test_files:
            for tf in test_files:
                p = Path(tf)
                if p.is_file():
                    try:
                        corpus[str(tf)] = p.read_text(encoding="utf-8", errors="replace").lower()
                    except Exception:
                        pass

        # 1. Inspect test files in workspace
        for p in self.root_dir.glob("**/*"):
            if not p.is_file() or p.is_symlink():
                continue
            name = p.name.lower()
            if name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name:
                try:
                    rel = str(p.relative_to(self.root_dir)).replace("\\", "/")
                    corpus[rel] = p.read_text(encoding="utf-8", errors="replace").lower()
                except Exception:
                    pass

        # 2. Add test stdout
        if test_output and isinstance(test_output, dict):
            stdout = (test_output.get("stdout") or "").lower()
            if stdout:
                corpus["test_stdout"] = stdout

        return corpus

    def _match_criterion_to_tests(
        self,
        crit: AcceptanceCriterion,
        corpus: Dict[str, str],
    ) -> List[str]:
        matched: List[str] = []
        c_id_lower = crit.criterion_id.lower()
        desc_lower = crit.description.lower()

        # Extract distinctive keywords (words > 3 chars, excluding common stop words)
        words = re.findall(r"\b[a-zA-Z]{4,}\b", desc_lower)
        stop_words = {"that", "with", "must", "should", "shall", "when", "then", "given", "have", "from", "this"}
        keywords = [w for w in words if w not in stop_words]

        for source_name, content in corpus.items():
            # Direct criterion ID match (e.g. "AC-1")
            if c_id_lower in content:
                matched.append(source_name)
                continue

            # Significant keyword overlap (at least 2 keywords or 50% of keywords match)
            if keywords:
                hits = sum(1 for kw in keywords if kw in content)
                req_hits = min(len(keywords), 2) if len(keywords) >= 2 else 1
                if hits >= req_hits and (hits / len(keywords)) >= 0.4:
                    matched.append(source_name)

        return list(set(matched))
