"""
Semantic Plan & Cross-Stage Alignment Validator.
Verifies semantic continuity across agent stages to prevent the "telephone game":
1. Architecture satisfies Specification (functional requirements -> components)
2. Plan/DAG satisfies Specification & Architecture (tasks cover all components and requirements)
3. Implementation satisfies Architecture (workspace AST inspection for required files and symbols)
4. Tests cover Acceptance Criteria (test suite verification against ACs and edge cases)
"""

from __future__ import annotations
import ast
from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from ..contracts import (
    ArchitectureContract,
    ExecutionPlanContract,
    SpecificationContract,
)


@dataclass
class SemanticAlignmentReport:
    """Formal audit report for cross-stage semantic alignment."""
    is_aligned: bool
    alignment_type: str
    coverage_score: float  # 0.0 to 1.0
    satisfied_items: List[str] = field(default_factory=list)
    missing_items: List[str] = field(default_factory=list)
    orphaned_items: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        status = "ALIGNED" if self.is_aligned else "MISALIGNED"
        return (
            f"[{self.alignment_type}] {status} (Score: {self.coverage_score*100:.1f}%) - "
            f"Satisfied: {len(self.satisfied_items)}, Missing: {len(self.missing_items)}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_aligned": self.is_aligned,
            "alignment_type": self.alignment_type,
            "coverage_score": self.coverage_score,
            "satisfied_items": self.satisfied_items,
            "missing_items": self.missing_items,
            "orphaned_items": self.orphaned_items,
            "warnings": self.warnings,
            "details": self.details,
            "summary": self.summary(),
        }


class SemanticPlanValidator:
    """
    Deterministic validator enforcing semantic alignment between
    specifications, architectures, execution plans, implementations, and tests.
    """

    @classmethod
    def _extract_spec(cls, spec_input: Any) -> Dict[str, Any]:
        if hasattr(spec_input, "model_dump"):
            return spec_input.model_dump()
        elif hasattr(spec_input, "dict"):
            return spec_input.dict()
        elif isinstance(spec_input, dict):
            return spec_input
        return {}

    @classmethod
    def _extract_arch(cls, arch_input: Any) -> Dict[str, Any]:
        if hasattr(arch_input, "model_dump"):
            return arch_input.model_dump()
        elif hasattr(arch_input, "dict"):
            return arch_input.dict()
        elif isinstance(arch_input, dict):
            return arch_input
        return {}

    @classmethod
    def _tokenize(cls, text: str) -> Set[str]:
        """Extract alphanumeric tokens in lowercase, filtering short stopwords."""
        words = re.findall(r"[a-zA-Z0-9_]+", text.lower())
        stopwords = {"the", "and", "for", "with", "that", "this", "from", "each", "must", "will", "have"}
        return {w for w in words if len(w) > 2 and w not in stopwords}

    # =========================================================================
    # GATE 1: Does Architecture Satisfy Specification?
    # =========================================================================
    @classmethod
    def verify_architecture_against_spec(
        cls,
        spec: Union[SpecificationContract, Dict[str, Any]],
        arch: Union[ArchitectureContract, Dict[str, Any]],
        min_threshold: float = 0.75,
    ) -> SemanticAlignmentReport:
        """
        Verify that the architecture provides components, modules, or patterns
        to fulfill every functional requirement in the specification.
        """
        spec_data = cls._extract_spec(spec)
        arch_data = cls._extract_arch(arch)

        reqs = spec_data.get("functional_requirements", [])
        if not reqs and "requirements" in spec_data:
            reqs = spec_data["requirements"]

        components = arch_data.get("component_structure", [])
        file_layout = arch_data.get("file_layout", [])
        data_flow = arch_data.get("data_flow_description", "")
        design_patterns = arch_data.get("design_patterns", [])

        # Build searchable architecture context
        arch_corpus = [data_flow]
        arch_corpus.extend(design_patterns)
        for comp in components:
            if isinstance(comp, dict):
                arch_corpus.append(comp.get("module_name", ""))
                arch_corpus.append(comp.get("purpose", ""))
                classes_or_fns = comp.get("classes_or_functions", [])
                if isinstance(classes_or_fns, list):
                    for item in classes_or_fns:
                        if isinstance(item, dict):
                            arch_corpus.append(item.get("name", ""))
                            arch_corpus.append(item.get("purpose", ""))
                        elif isinstance(item, str):
                            arch_corpus.append(item)
        for f in file_layout:
            if isinstance(f, dict):
                arch_corpus.extend(list(f.values()))
            elif isinstance(f, str):
                arch_corpus.append(f)

        full_arch_text = " ".join(arch_corpus).lower()
        arch_tokens = cls._tokenize(full_arch_text)

        satisfied: List[str] = []
        missing: List[str] = []

        if not reqs:
            # If no explicit functional requirements, check feature_name
            feat = spec_data.get("feature_name", "")
            if feat and any(t in arch_tokens for t in cls._tokenize(feat)):
                satisfied.append(f"Feature: {feat}")
            elif feat:
                missing.append(f"Feature: {feat}")

        for req in reqs:
            if isinstance(req, dict):
                req_id = req.get("id", "REQ")
                req_desc = req.get("description", "")
            elif isinstance(req, str):
                req_id = req[:10]
                req_desc = req
            else:
                continue

            # Check if requirement ID or key terms appear in architecture
            req_tokens = cls._tokenize(req_desc)
            overlap = req_tokens.intersection(arch_tokens)
            is_covered = (
                req_id.lower() in full_arch_text
                or len(overlap) >= min(2, len(req_tokens))
                or (len(req_tokens) <= 1 and len(overlap) >= 1)
            )

            item_label = f"Requirement [{req_id}]: {req_desc[:60]}"
            if is_covered:
                satisfied.append(item_label)
            else:
                missing.append(item_label)

        total = len(satisfied) + len(missing)
        score = (len(satisfied) / total) if total > 0 else 1.0
        is_aligned = score >= min_threshold

        return SemanticAlignmentReport(
            is_aligned=is_aligned,
            alignment_type="SPEC_TO_ARCHITECTURE",
            coverage_score=score,
            satisfied_items=satisfied,
            missing_items=missing,
            warnings=[f"Architecture does not address {len(missing)} requirement(s)"] if missing else [],
            details={"total_requirements": total, "satisfied_count": len(satisfied), "missing_count": len(missing)},
        )

    # =========================================================================
    # GATE 2: Does Plan/DAG Satisfy Specification & Architecture?
    # =========================================================================
    @classmethod
    def verify_plan_against_spec_and_arch(
        cls,
        spec: Optional[Union[SpecificationContract, Dict[str, Any]]],
        arch: Optional[Union[ArchitectureContract, Dict[str, Any]]],
        tasks: List[Any],
        min_threshold: float = 0.8,
    ) -> SemanticAlignmentReport:
        """
        Verify that the executable task DAG covers all components in the architecture
        and all functional requirements in the specification.
        """
        arch_data = cls._extract_arch(arch) if arch else {}
        spec_data = cls._extract_spec(spec) if spec else {}

        # 1. Gather all tasks' searchable text
        task_corpus_list = []
        for t in tasks:
            if hasattr(t, "to_dict"):
                t_dict = t.to_dict()
            elif isinstance(t, dict):
                t_dict = t
            else:
                continue

            task_text = " ".join([
                t_dict.get("task_id", ""),
                t_dict.get("objective", ""),
                t_dict.get("description", ""),
                " ".join(t_dict.get("inputs", [])),
                " ".join(t_dict.get("outputs", [])),
                " ".join(t_dict.get("acceptance_tests", [])),
            ])
            task_corpus_list.append(task_text.lower())

        combined_tasks_text = " ".join(task_corpus_list)
        task_tokens = cls._tokenize(combined_tasks_text)

        satisfied: List[str] = []
        missing: List[str] = []

        # Check architecture components
        components = arch_data.get("component_structure", [])
        for comp in components:
            if isinstance(comp, dict):
                mod_name = comp.get("module_name", "")
                purpose = comp.get("purpose", "")
            elif isinstance(comp, str):
                mod_name = comp
                purpose = ""
            else:
                continue

            mod_clean = mod_name.replace(".py", "").replace("src/", "").replace("tests/", "").lower()
            mod_tokens = cls._tokenize(mod_name + " " + purpose)

            # Is this module targeted by any task?
            is_covered = (
                mod_name.lower() in combined_tasks_text
                or mod_clean in combined_tasks_text
                or len(mod_tokens.intersection(task_tokens)) >= 2
            )

            item_label = f"Component '{mod_name}'"
            if is_covered:
                satisfied.append(item_label)
            else:
                missing.append(item_label)

        # Check specification requirements if provided
        reqs = spec_data.get("functional_requirements", [])
        for req in reqs:
            if isinstance(req, dict):
                req_id = req.get("id", "REQ")
                req_desc = req.get("description", "")
            else:
                continue

            req_tokens = cls._tokenize(req_desc)
            is_covered = (
                req_id.lower() in combined_tasks_text
                or len(req_tokens.intersection(task_tokens)) >= min(2, len(req_tokens))
            )

            # Transitive coverage: check if a satisfied architecture component covers this requirement
            if not is_covered:
                for comp in components:
                    if isinstance(comp, dict):
                        comp_name = comp.get("module_name", "")
                        comp_purpose = comp.get("purpose", "")
                        if f"Component '{comp_name}'" in satisfied:
                            comp_tokens = cls._tokenize(comp_name + " " + comp_purpose)
                            if (
                                req_id.lower() in (comp_name + " " + comp_purpose).lower()
                                or len(req_tokens.intersection(comp_tokens)) >= min(2, len(req_tokens))
                            ):
                                is_covered = True
                                break

            item_label = f"Requirement [{req_id}]: {req_desc[:50]}"
            if is_covered:
                satisfied.append(item_label)
            else:
                missing.append(item_label)

        total = len(satisfied) + len(missing)
        score = (len(satisfied) / total) if total > 0 else 1.0
        is_aligned = score >= min_threshold

        return SemanticAlignmentReport(
            is_aligned=is_aligned,
            alignment_type="PLAN_TO_SPEC_ARCH",
            coverage_score=score,
            satisfied_items=satisfied,
            missing_items=missing,
            warnings=[f"Task DAG drops {len(missing)} planned component(s)/requirement(s)"] if missing else [],
            details={"total_items": total, "satisfied_count": len(satisfied), "missing_count": len(missing)},
        )

    @classmethod
    def generate_compensatory_tasks(
        cls,
        missing_items: List[str],
        base_task_id: str = "T-COMP",
    ) -> List[Dict[str, Any]]:
        """
        Generate concrete ExecutableTask payloads to compensate for items
        dropped during task DAG decomposition.
        """
        compensatory_tasks: List[Dict[str, Any]] = []
        for i, item in enumerate(missing_items, 1):
            task_id = f"{base_task_id}-{i:02d}"
            # Extract name/identifier from label
            clean_name = item.replace("Component '", "").replace("Requirement [", "").replace("']", "").replace("'", "")
            compensatory_tasks.append({
                "task_id": task_id,
                "objective": f"Implement & integrate missing element: {clean_name[:80]}",
                "description": f"Automated compensatory task: The planning phase omitted {item}. Implement and verify this component.",
                "dependencies": [],
                "required_capabilities": ["code-generation", "refactoring"],
                "required_tools": ["filesystem", "terminal"],
                "preferred_skills": ["incremental-implementation"],
                "inputs": [],
                "outputs": [],
                "acceptance_tests": [],
                "permissions": {
                    "allowed_read_paths": ["*"],
                    "allowed_write_paths": ["*"],
                    "allowed_commands": [],
                },
                "timeout_seconds": 180,
                "max_turns": 15,
            })
        return compensatory_tasks

    # =========================================================================
    # GATE 3: Does Implementation Satisfy Architecture?
    # =========================================================================
    @classmethod
    def verify_implementation_against_arch(
        cls,
        arch: Union[ArchitectureContract, Dict[str, Any]],
        workspace: Any,
        min_threshold: float = 0.8,
    ) -> SemanticAlignmentReport:
        """
        Inspect the workspace filesystem and Python AST to verify that the files,
        classes, and functions prescribed by the architecture actually exist.
        """
        arch_data = cls._extract_arch(arch)
        components = arch_data.get("component_structure", [])
        file_layout = arch_data.get("file_layout", [])

        # Collect expected files
        expected_files: Dict[str, List[str]] = {}  # filepath -> list of expected symbols
        for comp in components:
            if isinstance(comp, dict):
                mod_name = comp.get("module_name", "")
                symbols = []
                for item in comp.get("classes_or_functions", []):
                    if isinstance(item, dict) and "name" in item:
                        symbols.append(item["name"])
                    elif isinstance(item, str):
                        symbols.append(item)
                if mod_name:
                    expected_files[mod_name] = symbols

        for fl in file_layout:
            if isinstance(fl, dict):
                for p in fl.values():
                    if isinstance(p, str) and p.endswith(".py") and p not in expected_files:
                        expected_files[p] = []
            elif isinstance(fl, str) and fl.endswith(".py") and fl not in expected_files:
                expected_files[fl] = []

        satisfied: List[str] = []
        missing: List[str] = []

        workspace_files = []
        if hasattr(workspace, "list_files"):
            try:
                workspace_files = [f.replace("\\", "/") for f in workspace.list_files()]
            except Exception:
                workspace_files = []

        for expected_path, expected_symbols in expected_files.items():
            norm_expected = expected_path.replace("\\", "/")

            # Find matching file in workspace
            matched_file = None
            for wf in workspace_files:
                if wf == norm_expected or wf.endswith(norm_expected) or norm_expected.endswith(wf):
                    matched_file = wf
                    break

            if not matched_file:
                missing.append(f"Missing File: '{expected_path}'")
                continue

            satisfied.append(f"File Exists: '{expected_path}'")

            # If Python file and symbols were declared, inspect AST
            if expected_symbols and (matched_file.endswith(".py") or expected_path.endswith(".py")):
                file_content = ""
                if hasattr(workspace, "read_file"):
                    try:
                        read_res = workspace.read_file(matched_file)
                        if isinstance(read_res, dict) and "content" in read_res:
                            file_content = read_res["content"]
                        elif isinstance(read_res, str):
                            file_content = read_res
                    except Exception:
                        file_content = ""

                if file_content:
                    try:
                        tree = ast.parse(file_content)
                        defined_symbols = set()
                        for node in ast.walk(tree):
                            if isinstance(node, ast.ClassDef):
                                defined_symbols.add(node.name)
                            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                defined_symbols.add(node.name)

                        for sym in expected_symbols:
                            if sym in defined_symbols:
                                satisfied.append(f"Symbol Exists: '{expected_path}:{sym}'")
                            else:
                                missing.append(f"Missing Symbol: '{expected_path}:{sym}'")
                    except Exception:
                        # AST parse failure or syntax issue in workspace file
                        for sym in expected_symbols:
                            if sym in file_content:
                                satisfied.append(f"Symbol (raw): '{expected_path}:{sym}'")
                            else:
                                missing.append(f"Missing Symbol: '{expected_path}:{sym}'")

        total = len(satisfied) + len(missing)
        score = (len(satisfied) / total) if total > 0 else 1.0
        is_aligned = score >= min_threshold

        return SemanticAlignmentReport(
            is_aligned=is_aligned,
            alignment_type="ARCH_TO_IMPLEMENTATION",
            coverage_score=score,
            satisfied_items=satisfied,
            missing_items=missing,
            warnings=[f"Implementation is missing {len(missing)} file(s)/symbol(s)"] if missing else [],
            details={"total_checks": total, "satisfied_count": len(satisfied), "missing_count": len(missing)},
        )

    # =========================================================================
    # GATE 4: Does Test Suite Cover Acceptance Criteria?
    # =========================================================================
    @classmethod
    def verify_tests_against_acceptance_criteria(
        cls,
        spec: Union[SpecificationContract, Dict[str, Any]],
        workspace: Any,
        test_results: Optional[Dict[str, Any]] = None,
        min_threshold: float = 0.75,
    ) -> SemanticAlignmentReport:
        """
        Verify that tests in the workspace explicitly exercise all acceptance criteria
        and edge cases defined in the specification.
        """
        spec_data = cls._extract_spec(spec)
        criteria = spec_data.get("acceptance_criteria", [])
        edge_cases = spec_data.get("edge_cases", [])

        # Collect test file contents
        test_content_corpus = []
        if hasattr(workspace, "list_files"):
            try:
                all_files = workspace.list_files()
                for f in all_files:
                    norm = f.replace("\\", "/").lower()
                    if "test" in norm:
                        res = workspace.read_file(f)
                        content = res.get("content", "") if isinstance(res, dict) else str(res or "")
                        test_content_corpus.append(content)
            except Exception:
                pass

        if test_results and isinstance(test_results, dict):
            test_content_corpus.append(test_results.get("stdout", ""))
            test_content_corpus.append(test_results.get("stderr", ""))

        all_tests_text = " ".join(test_content_corpus).lower()
        test_tokens = cls._tokenize(all_tests_text)

        satisfied: List[str] = []
        missing: List[str] = []

        for i, crit in enumerate(criteria, 1):
            crit_text = str(crit)
            crit_tokens = cls._tokenize(crit_text)
            crit_id_match = re.search(r"\b(AC[-_]?\d+|CRIT[-_]?\d+)\b", crit_text, re.IGNORECASE)
            has_id = crit_id_match and crit_id_match.group(1).lower() in all_tests_text

            is_covered = (
                has_id
                or len(crit_tokens.intersection(test_tokens)) >= min(2, len(crit_tokens))
                or (len(crit_tokens) <= 1 and len(crit_tokens.intersection(test_tokens)) >= 1)
            )

            item_label = f"Acceptance Criterion #{i}: '{crit_text[:60]}'"
            if is_covered:
                satisfied.append(item_label)
            else:
                missing.append(item_label)

        for i, ec in enumerate(edge_cases, 1):
            if isinstance(ec, dict):
                scenario = ec.get("scenario", "")
                expected = ec.get("expected_behavior", "")
            elif isinstance(ec, str):
                scenario = ec
                expected = ""
            else:
                continue

            ec_tokens = cls._tokenize(f"{scenario} {expected}")
            is_covered = len(ec_tokens.intersection(test_tokens)) >= min(2, len(ec_tokens))

            item_label = f"Edge Case #{i}: '{scenario[:60]}'"
            if is_covered:
                satisfied.append(item_label)
            else:
                missing.append(item_label)

        total = len(satisfied) + len(missing)
        score = (len(satisfied) / total) if total > 0 else 1.0
        is_aligned = score >= min_threshold

        return SemanticAlignmentReport(
            is_aligned=is_aligned,
            alignment_type="TEST_TO_ACCEPTANCE_CRITERIA",
            coverage_score=score,
            satisfied_items=satisfied,
            missing_items=missing,
            warnings=[f"Test suite omits {len(missing)} acceptance criterion/criteria"] if missing else [],
            details={"total_criteria": total, "satisfied_count": len(satisfied), "missing_count": len(missing)},
        )
