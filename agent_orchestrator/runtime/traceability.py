"""
TraceabilityEngine: End-to-End Acceptance Criteria & Requirement Traceability Matrix.
Establishes and verifies the deterministic chain:
FR-1 -> Implementation (File/Symbol) -> Test Case (Function) -> Execution Evidence (PASSED/FAILED).
"""
import ast
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Union

from .test_evidence_parser import TestCaseEvidence, TestExecutionParser


class TraceStatus(str, Enum):
    UNIMPLEMENTED = "UNIMPLEMENTED"
    IMPLEMENTED_UNTESTED = "IMPLEMENTED_UNTESTED"
    TEST_FAILED = "TEST_FAILED"
    VERIFIED = "VERIFIED"


@dataclass
class RequirementTraceNode:
    requirement_id: str
    title: str = ""
    description: str = ""
    acceptance_criteria: List[str] = field(default_factory=list)
    implementation_files: List[str] = field(default_factory=list)
    implementation_symbols: List[str] = field(default_factory=list)
    test_cases: List[str] = field(default_factory=list)
    evidence: List[TestCaseEvidence] = field(default_factory=list)
    status: TraceStatus = TraceStatus.UNIMPLEMENTED
    unverified_reason: Optional[str] = None

    @property
    def is_verified(self) -> bool:
        return self.status == TraceStatus.VERIFIED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "implementation_files": self.implementation_files,
            "implementation_symbols": self.implementation_symbols,
            "test_cases": self.test_cases,
            "evidence": [e.to_dict() for e in self.evidence],
            "status": self.status.value,
            "unverified_reason": self.unverified_reason,
        }


@dataclass
class TraceabilityMatrix:
    nodes: Dict[str, RequirementTraceNode] = field(default_factory=dict)
    total_requirements: int = 0
    verified_requirements: int = 0
    traceability_score: float = 100.0

    @property
    def passed(self) -> bool:
        return self.verified_requirements == self.total_requirements if self.total_requirements > 0 else True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requirements": self.total_requirements,
            "verified_requirements": self.verified_requirements,
            "traceability_score": round(self.traceability_score, 2),
            "passed": self.passed,
            "requirements": {k: v.to_dict() for k, v in self.nodes.items()},
        }

    def to_markdown_table(self) -> str:
        if not self.nodes:
            return "No requirements or acceptance criteria to trace."

        lines = [
            f"### Requirement Traceability Matrix ({self.verified_requirements}/{self.total_requirements} Verified - {self.traceability_score:.1f}%)\n",
            "| Requirement ID | Description | Implementation | Test Cases | Status | Evidence |",
            "| :--- | :--- | :--- | :--- | :---: | :--- |",
        ]

        for req_id, node in self.nodes.items():
            status_badge = {
                TraceStatus.VERIFIED: "🟢 VERIFIED",
                TraceStatus.IMPLEMENTED_UNTESTED: "🟡 UNTESTED",
                TraceStatus.TEST_FAILED: "🔴 FAILED",
                TraceStatus.UNIMPLEMENTED: "⚪ UNIMPLEMENTED",
            }.get(node.status, node.status.value)

            impl_str = "<br>".join(node.implementation_symbols or node.implementation_files) or "*None*"
            test_str = "<br>".join(node.test_cases) or "*None*"
            
            ev_parts = []
            if node.evidence:
                for ev in node.evidence[:3]:
                    ev_parts.append(f"{ev.test_name or ev.test_id}: {ev.status}")
                if len(node.evidence) > 3:
                    ev_parts.append(f"... +{len(node.evidence) - 3} more")
            ev_str = "<br>".join(ev_parts) or (node.unverified_reason or "N/A")

            desc = node.description[:60] + "..." if len(node.description) > 60 else node.description
            lines.append(f"| `{req_id}` | {desc} | {impl_str} | {test_str} | {status_badge} | {ev_str} |")

        return "\n".join(lines)


class TraceabilityEngine:
    """
    Analyzes specifications, workspace ASTs, test suites, and test execution evidence
    to construct a deterministic end-to-end TraceabilityMatrix.
    """

    @classmethod
    def build_matrix(
        cls,
        spec: Union[Dict[str, Any], Any],
        workspace: Any,
        test_results: Optional[Dict[str, Any]] = None,
        architecture: Optional[Union[Dict[str, Any], Any]] = None,
    ) -> TraceabilityMatrix:
        nodes: Dict[str, RequirementTraceNode] = {}

        # 1. Extract requirements & criteria from spec
        raw_nodes = cls._extract_requirements(spec)
        for node in raw_nodes:
            nodes[node.requirement_id] = node

        if not nodes:
            return TraceabilityMatrix(
                nodes={},
                total_requirements=0,
                verified_requirements=0,
                traceability_score=100.0,
            )

        # 2. Extract implementation symbols from workspace
        ws_symbols = cls._extract_workspace_symbols(workspace)

        # 3. Extract test functions & citations from workspace
        test_functions = cls._extract_test_functions(workspace)

        # 4. Parse test execution evidence
        evidence_list: List[TestCaseEvidence] = []
        if test_results and isinstance(test_results, dict):
            stdout = test_results.get("stdout", "")
            stderr = test_results.get("stderr", "")
            evidence_list = TestExecutionParser.parse(stdout=stdout, stderr=stderr)

        # 5. Map Architecture blueprints if available
        arch_map = cls._extract_architecture_map(architecture)

        # 6. Build the chain for each requirement
        for req_id, node in nodes.items():
            # A. Trace Implementation
            impl_files, impl_symbols = cls._trace_implementation(node, ws_symbols, arch_map)
            node.implementation_files = impl_files
            node.implementation_symbols = impl_symbols

            # B. Trace Tests
            matched_tests = cls._trace_tests(node, test_functions, impl_symbols)
            node.test_cases = matched_tests

            # C. Trace Execution Evidence
            matched_evidence = cls._correlate_evidence(matched_tests, evidence_list, test_results)
            node.evidence = matched_evidence

            # D. Determine Deterministic Status
            if not impl_files and not impl_symbols:
                node.status = TraceStatus.UNIMPLEMENTED
                node.unverified_reason = "No implementation files or symbols found."
            elif not matched_tests:
                node.status = TraceStatus.IMPLEMENTED_UNTESTED
                node.unverified_reason = "Implementation exists, but no test cases exercise this requirement."
            else:
                has_failure = any(ev.status in ("FAILED", "ERROR") for ev in matched_evidence)
                if has_failure:
                    node.status = TraceStatus.TEST_FAILED
                    node.unverified_reason = "One or more associated test cases failed execution."
                elif matched_evidence and all(ev.status == "PASSED" for ev in matched_evidence):
                    node.status = TraceStatus.VERIFIED
                    node.unverified_reason = None
                elif not matched_evidence:
                    # If test exists on disk, check aggregate test_results outcome
                    if test_results and test_results.get("execution_success") and test_results.get("exit_code", 0) == 0:
                        node.status = TraceStatus.VERIFIED
                        node.unverified_reason = None
                    else:
                        node.status = TraceStatus.TEST_FAILED
                        node.unverified_reason = "Test suite failed or execution evidence missing."

        total = len(nodes)
        verified = sum(1 for n in nodes.values() if n.status == TraceStatus.VERIFIED)
        score = (verified / total * 100.0) if total > 0 else 100.0

        return TraceabilityMatrix(
            nodes=nodes,
            total_requirements=total,
            verified_requirements=verified,
            traceability_score=score,
        )

    @classmethod
    def _extract_requirements(cls, spec: Any) -> List[RequirementTraceNode]:
        """Extracts requirements and acceptance criteria from specification contracts or dicts."""
        nodes: List[RequirementTraceNode] = []
        spec_data = spec.model_dump() if hasattr(spec, "model_dump") else (spec.dict() if hasattr(spec, "dict") else (spec if isinstance(spec, dict) else {}))

        frs = spec_data.get("functional_requirements", [])
        acs = spec_data.get("acceptance_criteria", [])

        # 1. Structured Functional Requirements
        if isinstance(frs, list) and frs:
            for item in frs:
                if isinstance(item, dict):
                    req_id = item.get("id") or f"FR-{len(nodes)+1}"
                    desc = item.get("description") or ""
                    title = item.get("title") or desc[:40]
                    item_acs = item.get("acceptance_criteria") or []
                    nodes.append(
                        RequirementTraceNode(
                            requirement_id=req_id,
                            title=title,
                            description=desc,
                            acceptance_criteria=item_acs if isinstance(item_acs, list) else [str(item_acs)],
                        )
                    )

        # 2. Acceptance Criteria (if not tied to FRs or legacy string format)
        if isinstance(acs, list):
            for idx, item in enumerate(acs, start=1):
                if isinstance(item, dict):
                    cid = item.get("id") or item.get("criterion_id") or f"AC-{idx}"
                    cdesc = item.get("description") or item.get("criterion") or json.dumps(item)
                elif isinstance(item, str):
                    s = item.strip()
                    if not s:
                        continue
                    m = re.match(r"^([A-Za-z0-9_\-]+)[:\.]\s*(.*)$", s)
                    if m:
                        cid, cdesc = m.groups()
                    else:
                        cid = f"AC-{idx}"
                        cdesc = s
                else:
                    continue

                # Check if this criterion already maps to an existing node
                matched_node = None
                for n in nodes:
                    if n.requirement_id == cid or cid in n.acceptance_criteria:
                        matched_node = n
                        break
                if matched_node:
                    if cdesc not in matched_node.acceptance_criteria:
                        matched_node.acceptance_criteria.append(cdesc)
                else:
                    # Create dedicated AC node
                    nodes.append(
                        RequirementTraceNode(
                            requirement_id=cid,
                            title=cid,
                            description=cdesc,
                            acceptance_criteria=[cdesc],
                        )
                    )

        return nodes

    @classmethod
    def _extract_workspace_symbols(cls, workspace: Any) -> Dict[str, Dict[str, Any]]:
        """
        Parses all Python non-test files in workspace to extract classes, methods, and functions.
        Returns: { symbol_name: { "file": path, "type": "class|function|method", "docstring": doc, "lines": (start, end) } }
        """
        symbols: Dict[str, Dict[str, Any]] = {}
        if not workspace:
            return symbols

        all_files = []
        if hasattr(workspace, "list_files"):
            try:
                res = workspace.list_files()
                all_files = res.get("files", []) if isinstance(res, dict) else (res or [])
            except Exception:
                pass
        elif hasattr(workspace, "root_dir"):
            root = Path(workspace.root_dir)
            all_files = [str(p.relative_to(root)).replace("\\", "/") for p in root.glob("**/*.py")]

        for rel_path in all_files:
            norm = rel_path.replace("\\", "/")
            # Skip test files and hidden files
            if "test" in norm.lower() or norm.startswith("."):
                continue
            if not norm.endswith(".py"):
                continue

            content = ""
            if hasattr(workspace, "read_file"):
                try:
                    res = workspace.read_file(norm)
                    content = res.get("content", "") if isinstance(res, dict) else str(res or "")
                except Exception:
                    continue
            elif hasattr(workspace, "root_dir"):
                try:
                    content = (Path(workspace.root_dir) / norm).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

            if not content:
                continue

            try:
                tree = ast.parse(content, filename=norm)
            except Exception:
                continue

            for node in ast.iter_child_nodes(tree):
                if isinstance(node, ast.ClassDef):
                    cls_sym = f"{norm}::{node.name}"
                    symbols[cls_sym] = {
                        "file": norm,
                        "name": node.name,
                        "type": "class",
                        "docstring": ast.get_docstring(node) or "",
                    }
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            m_sym = f"{norm}::{node.name}::{sub.name}"
                            symbols[m_sym] = {
                                "file": norm,
                                "name": sub.name,
                                "class_name": node.name,
                                "type": "method",
                                "docstring": ast.get_docstring(sub) or "",
                            }
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    f_sym = f"{norm}::{node.name}"
                    symbols[f_sym] = {
                        "file": norm,
                        "name": node.name,
                        "type": "function",
                        "docstring": ast.get_docstring(node) or "",
                    }

        return symbols

    @classmethod
    def _extract_test_functions(cls, workspace: Any) -> Dict[str, Dict[str, Any]]:
        """
        Parses all test files in workspace to extract test functions, methods, and AST calls.
        Returns: { test_id: { "file": path, "name": name, "docstring": doc, "calls": set_of_called_names } }
        """
        test_funcs: Dict[str, Dict[str, Any]] = {}
        if not workspace:
            return test_funcs

        all_files = []
        if hasattr(workspace, "list_files"):
            try:
                res = workspace.list_files()
                all_files = res.get("files", []) if isinstance(res, dict) else (res or [])
            except Exception:
                pass
        elif hasattr(workspace, "root_dir"):
            root = Path(workspace.root_dir)
            all_files = [str(p.relative_to(root)).replace("\\", "/") for p in root.glob("**/*.py")]

        for rel_path in all_files:
            norm = rel_path.replace("\\", "/")
            if not ("test" in norm.lower() or "spec" in norm.lower()) or not norm.endswith(".py"):
                continue

            content = ""
            if hasattr(workspace, "read_file"):
                try:
                    res = workspace.read_file(norm)
                    content = res.get("content", "") if isinstance(res, dict) else str(res or "")
                except Exception:
                    continue
            elif hasattr(workspace, "root_dir"):
                try:
                    content = (Path(workspace.root_dir) / norm).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

            if not content:
                continue

            try:
                tree = ast.parse(content, filename=norm)
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                    if name.startswith("test_") or name.endswith("_test"):
                        doc = ast.get_docstring(node) or ""
                        # Find all identifiers called in this test function
                        calls = set()
                        for subnode in ast.walk(node):
                            if isinstance(subnode, ast.Call):
                                if isinstance(subnode.func, ast.Name):
                                    calls.add(subnode.func.id)
                                elif isinstance(subnode.func, ast.Attribute):
                                    calls.add(subnode.func.attr)

                        test_id = f"{norm}::{name}"
                        test_funcs[test_id] = {
                            "file": norm,
                            "name": name,
                            "docstring": doc,
                            "calls": calls,
                        }

        return test_funcs

    @classmethod
    def _extract_architecture_map(cls, arch: Any) -> Dict[str, List[str]]:
        """Maps requirement keywords or module blueprints from ArchitectureContract."""
        arch_map: Dict[str, List[str]] = {}
        if not arch:
            return arch_map

        arch_data = arch.model_dump() if hasattr(arch, "model_dump") else (arch.dict() if hasattr(arch, "dict") else (arch if isinstance(arch, dict) else {}))
        components = arch_data.get("component_structure", [])
        for comp in components:
            if isinstance(comp, dict):
                m_name = comp.get("module_name", "")
                funcs = comp.get("classes_or_functions", [])
                syms = []
                for f in funcs:
                    if isinstance(f, dict):
                        syms.append(f.get("name", ""))
                    elif isinstance(f, str):
                        syms.append(f)
                arch_map[m_name] = syms
        return arch_map

    @classmethod
    def _trace_implementation(
        cls,
        node: RequirementTraceNode,
        ws_symbols: Dict[str, Dict[str, Any]],
        arch_map: Dict[str, List[str]],
    ) -> (List[str], List[str]):
        """Finds implementation files and symbols satisfying the requirement."""
        matched_files = set()
        matched_symbols = set()

        req_id_lower = node.requirement_id.lower()
        desc_tokens = set(re.findall(r"\b[a-zA-Z]{4,}\b", (node.title + " " + node.description).lower()))

        for sym_id, meta in ws_symbols.items():
            sym_name = meta["name"].lower()
            doc = meta["docstring"].lower()
            file_path = meta["file"]

            # 1. Explicit citation in docstring (e.g. "Implements FR-1")
            if req_id_lower in doc:
                matched_files.add(file_path)
                matched_symbols.add(sym_id)
                continue

            # 2. Symbol name matches requirement tokens
            if any(tok in sym_name for tok in desc_tokens):
                matched_files.add(file_path)
                matched_symbols.add(sym_id)
                continue

            # 3. Check architecture mapping
            for mod_name, arch_syms in arch_map.items():
                if mod_name in file_path:
                    if any(s.lower() == sym_name for s in arch_syms):
                        matched_files.add(file_path)
                        matched_symbols.add(sym_id)

        # If no specific symbol matched, but a file matches requirement tokens
        if not matched_files:
            for sym_id, meta in ws_symbols.items():
                f_name = Path(meta["file"]).stem.lower()
                if any(tok in f_name for tok in desc_tokens):
                    matched_files.add(meta["file"])
                    matched_symbols.add(sym_id)

        return sorted(list(matched_files)), sorted(list(matched_symbols))

    @classmethod
    def _trace_tests(
        cls,
        node: RequirementTraceNode,
        test_funcs: Dict[str, Dict[str, Any]],
        impl_symbols: List[str],
    ) -> List[str]:
        """Finds test functions that test this requirement or its implementation symbols."""
        matched = set()
        req_id_lower = node.requirement_id.lower()
        desc_tokens = set(re.findall(r"\b[a-zA-Z]{4,}\b", (node.title + " " + node.description).lower()))

        # Extract bare function/class names from impl_symbols (e.g. "authenticate" from "auth.py::UserManager::authenticate")
        bare_impl_names = set()
        for sym in impl_symbols:
            parts = sym.split("::")
            bare_impl_names.update(parts[1:])

        for test_id, meta in test_funcs.items():
            test_name = meta["name"].lower()
            doc = meta["docstring"].lower()
            calls = meta["calls"]

            # 1. Explicit requirement ID citation in test name or docstring
            if req_id_lower in test_name or req_id_lower in doc:
                matched.add(test_id)
                continue

            # 2. Test calls one of the implementation symbols
            if calls and bare_impl_names.intersection(calls):
                matched.add(test_id)
                continue

            # 3. Test name shares significant keywords with requirement
            if desc_tokens and any(tok in test_name for tok in desc_tokens):
                matched.add(test_id)

        return sorted(list(matched))

    @classmethod
    def _correlate_evidence(
        cls,
        matched_tests: List[str],
        evidence_list: List[TestCaseEvidence],
        test_results: Optional[Dict[str, Any]],
    ) -> List[TestCaseEvidence]:
        """Correlates test cases with execution evidence."""
        correlated: List[TestCaseEvidence] = []
        for test_id in matched_tests:
            # Look for exact or substring match in parsed evidence
            found = False
            for ev in evidence_list:
                if ev.test_id == test_id or ev.test_name in test_id or test_id in ev.test_id:
                    correlated.append(ev)
                    found = True
                    break

            if not found:
                # If test ran without individual line log, inherit aggregate test result
                if test_results and isinstance(test_results, dict):
                    success = test_results.get("execution_success", True)
                    exit_code = test_results.get("exit_code", 0)
                    status = "PASSED" if (success and exit_code == 0) else "FAILED"
                    correlated.append(
                        TestCaseEvidence(
                            test_id=test_id,
                            test_name=test_id.split("::")[-1],
                            status=status,
                            message=test_results.get("stderr"),
                        )
                    )

        return correlated
