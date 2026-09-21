"""
Context Isolation & Task Projection Engine.
Enforces the principle of least privilege / need-to-know context scoping for agents.
Replaces monolithic state broadcast with isolated task context views and on-demand retrieval tools.
"""
from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional, Set


@dataclass
class IsolatedTaskContext:
    """
    A strictly scoped, role- and task-specific view of state.
    Contains only what the agent needs for its active assignment,
    while leaving broader context accessible via on-demand query tools.
    """
    task_id: str
    objective: str
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    task_contract: Optional[Dict[str, Any]] = None
    parent_artifacts: Dict[str, Any] = field(default_factory=dict)
    replan_context: Optional[str] = None
    scoped_specification: Optional[Dict[str, Any]] = None
    scoped_architecture: Optional[Dict[str, Any]] = None
    has_full_spec_available: bool = False
    has_full_arch_available: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "acceptance_criteria": self.acceptance_criteria,
            "task_contract": self.task_contract,
            "parent_artifacts": self.parent_artifacts,
            "replan_context": self.replan_context,
            "scoped_specification": self.scoped_specification,
            "scoped_architecture": self.scoped_architecture,
            "has_full_spec_available": self.has_full_spec_available,
            "has_full_arch_available": self.has_full_arch_available,
        }


class ContextIsolationEngine:
    """
    Extracts isolated, scoped task views from OrchestratorState and provides
    on-demand retrieval methods for agents to query detailed specifications,
    architectures, and upstream deliverables.
    """

    STOP_WORDS = {
        "the", "and", "for", "with", "this", "that", "from", "into", "src", "tests",
        "test", "py", "implement", "implementation", "create", "build", "add", "adding",
        "support", "structure", "structures", "data", "feature", "system", "service",
        "function", "functions", "method", "methods", "class", "classes", "code", "file",
        "files", "module", "modules", "component", "components", "unit", "integration",
        "new", "update", "fix", "verify", "run", "all", "get", "set"
    }

    @classmethod
    def _tokenize(cls, text: str) -> Set[str]:
        cleaned = re.sub(r"[^a-zA-Z0-9_]", " ", str(text)).lower()
        return {w for w in cleaned.split() if len(w) >= 3 and w not in cls.STOP_WORDS}

    @classmethod
    def _extract_keywords(cls, task_id: str, objective: str, inputs: List[str], outputs: List[str]) -> Set[str]:
        words = set()
        for text in [task_id, objective] + (inputs or []) + (outputs or []):
            words.update(cls._tokenize(text))
        return words


    @classmethod
    def isolate_for_task(
        cls,
        state: Any,
        task_info: Optional[Dict[str, Any]] = None,
        task: Optional[Any] = None,
    ) -> IsolatedTaskContext:
        """
        Projects OrchestratorState into an IsolatedTaskContext for a specific subtask.
        """
        task_info = task_info or {}
        task_id = str(task_info.get("task_id") or getattr(task, "task_id", "") or "default-task")
        objective = str(task_info.get("objective") or getattr(task, "objective", "") or getattr(state, "user_request", "Execute task"))
        inputs = list(task_info.get("inputs") or getattr(task, "inputs", []) or [])
        outputs = list(task_info.get("outputs") or getattr(task, "outputs", []) or [])

        # Extract acceptance criteria
        acceptance_criteria = []
        if "acceptance_tests" in task_info and isinstance(task_info["acceptance_tests"], list):
            acceptance_criteria.extend(str(x) for x in task_info["acceptance_tests"])
        if "acceptance_criteria" in task_info and isinstance(task_info["acceptance_criteria"], list):
            acceptance_criteria.extend(str(x) for x in task_info["acceptance_criteria"])
        if task and hasattr(task, "acceptance_tests") and task.acceptance_tests:
            for at in task.acceptance_tests:
                if str(at) not in acceptance_criteria:
                    acceptance_criteria.append(str(at))

        keywords = cls._extract_keywords(task_id, objective, inputs, outputs)

        # 1. Scoped Specification
        full_spec = getattr(state, "specification_output", None)
        if not full_spec and isinstance(state, dict):
            full_spec = state.get("specification_output")

        scoped_spec = None
        has_full_spec = bool(full_spec and isinstance(full_spec, dict))

        if has_full_spec:
            scoped_spec = cls._filter_specification(full_spec, keywords, objective)

        # 2. Scoped Architecture
        full_arch = getattr(state, "architecture_output", None)
        if not full_arch and isinstance(state, dict):
            full_arch = state.get("architecture_output")

        scoped_arch = None
        has_full_arch = bool(full_arch and isinstance(full_arch, dict))

        if has_full_arch:
            scoped_arch = cls._filter_architecture(full_arch, keywords, inputs, outputs)

        # 3. Strict Parent Dependency Isolation
        dependencies = list(task_info.get("dependencies") or getattr(task, "dependencies", []) or [])
        all_parent_artifacts = task_info.get("parent_artifacts") or {}
        prev_step_results = task_info.get("previous_step_results") or {}

        isolated_parent_artifacts: Dict[str, Any] = {}
        if dependencies:
            dep_set = {str(d).lower().strip() for d in dependencies}
            for k, v in all_parent_artifacts.items():
                if str(k).lower().strip() in dep_set:
                    isolated_parent_artifacts[k] = v
            for k, v in prev_step_results.items():
                if str(k).lower().strip() in dep_set and k not in isolated_parent_artifacts:
                    isolated_parent_artifacts[k] = v
        else:
            # If no dependencies declared, do not leak previous steps from parallel tasks!
            isolated_parent_artifacts = {}

        # 4. Scoped Re-planning Context
        scoped_replan = None
        replan_history = getattr(state, "replan_history", None) or []
        if replan_history and isinstance(replan_history, list):
            latest = replan_history[-1]
            latest_dict = latest if isinstance(latest, dict) else (latest.__dict__ if hasattr(latest, "__dict__") else {})
            feed = str(latest_dict.get("feedback_summary", ""))
            plan_steps = latest_dict.get("remediation_plan", [])
            plan_str = json.dumps(plan_steps)

            # Check if this task is affected by the replan
            is_relevant = False
            if task_id.lower() in feed.lower() or task_id.lower() in plan_str.lower():
                is_relevant = True
            elif any(kw in feed.lower() or kw in plan_str.lower() for kw in keywords if len(kw) > 3):
                is_relevant = True

            if is_relevant:
                scoped_replan = f"[RE-PLANNING FIX CONTEXT - Iteration {latest_dict.get('iteration', 1)}]\nIssues: {feed}\nAction Plan: {plan_str}"

        # 5. Local Task Contract
        task_contract = task_info.get("agent_contract") or getattr(task, "agent_contract", None)
        if task_contract and hasattr(task_contract, "to_dict"):
            task_contract = task_contract.to_dict()

        return IsolatedTaskContext(
            task_id=task_id,
            objective=objective,
            inputs=inputs,
            outputs=outputs,
            acceptance_criteria=acceptance_criteria,
            task_contract=task_contract,
            parent_artifacts=isolated_parent_artifacts,
            replan_context=scoped_replan,
            scoped_specification=scoped_spec,
            scoped_architecture=scoped_arch,
            has_full_spec_available=has_full_spec,
            has_full_arch_available=has_full_arch,
        )

    @classmethod
    def _filter_specification(
        cls,
        spec: Dict[str, Any],
        keywords: Set[str],
        objective: str,
    ) -> Dict[str, Any]:
        """
        Filters the full specification to only retain functional requirements,
        edge cases, and acceptance criteria relevant to the task's scope.
        """
        result: Dict[str, Any] = {
            "feature_name": spec.get("feature_name", ""),
            "isolated_view": True,
        }

        # Filter Functional Requirements
        frs = spec.get("functional_requirements", [])
        matched_frs = []
        if isinstance(frs, list):
            for fr in frs:
                if not isinstance(fr, dict):
                    continue
                fr_text = f"{fr.get('id', '')} {fr.get('description', '')} {fr.get('input_contract', '')} {fr.get('output_contract', '')}"
                fr_tokens = cls._tokenize(fr_text)
                if bool(keywords & fr_tokens):
                    matched_frs.append(fr)

            # If no direct keyword match, include top 1 requirement if objective is generic
            if not matched_frs and frs:
                matched_frs = [frs[0]]
        result["functional_requirements"] = matched_frs

        # Filter Edge Cases
        edge_cases = spec.get("edge_cases", [])
        matched_edges = []
        if isinstance(edge_cases, list):
            for ec in edge_cases:
                if not isinstance(ec, dict):
                    continue
                ec_text = f"{ec.get('scenario', '')} {ec.get('expected_behavior', '')}"
                ec_tokens = cls._tokenize(ec_text)
                if bool(keywords & ec_tokens):
                    matched_edges.append(ec)
        if matched_edges:
            result["edge_cases"] = matched_edges

        # Filter Acceptance Criteria
        ac_list = spec.get("acceptance_criteria", [])
        matched_acs = []
        if isinstance(ac_list, list):
            for ac in ac_list:
                ac_tokens = cls._tokenize(str(ac))
                if bool(keywords & ac_tokens):
                    matched_acs.append(ac)
        if matched_acs:
            result["acceptance_criteria"] = matched_acs

        return result

    @classmethod
    def _filter_architecture(
        cls,
        arch: Dict[str, Any],
        keywords: Set[str],
        inputs: List[str],
        outputs: List[str],
    ) -> Dict[str, Any]:
        """
        Filters architecture blueprint to only retain component structures
        and file layouts relevant to the target task.
        """
        result: Dict[str, Any] = {
            "system_title": arch.get("system_title", ""),
            "isolated_view": True,
        }

        target_files = {f.replace("\\", "/").lower().strip() for f in (inputs + outputs) if f}

        # Filter component structure
        comps = arch.get("component_structure", [])
        matched_comps = []
        if isinstance(comps, list):
            for comp in comps:
                if not isinstance(comp, dict):
                    continue
                m_name = str(comp.get("module_name", "")).lower()
                purpose = str(comp.get("purpose", "")).lower()
                classes = json.dumps(comp.get("classes_or_functions", [])).lower()
                comp_tokens = cls._tokenize(f"{m_name} {purpose} {classes}")

                if bool(keywords & comp_tokens) or any(tf in m_name for tf in target_files):
                    matched_comps.append(comp)

            if not matched_comps and comps:
                matched_comps = [comps[0]]
        result["component_structure"] = matched_comps

        # Filter file layout
        layout = arch.get("file_layout", [])
        matched_layout = []
        if isinstance(layout, list):
            for entry in layout:
                if not isinstance(entry, dict):
                    continue
                fp = str(entry.get("filepath", "")).replace("\\", "/").lower().strip()
                fp_tokens = cls._tokenize(fp)
                if any(tf in fp or fp in tf for tf in target_files) or bool(keywords & fp_tokens):
                    matched_layout.append(entry)
        if matched_layout:
            result["file_layout"] = matched_layout

        return result


    @staticmethod
    def query_specification(
        spec: Optional[Dict[str, Any]],
        query: str,
        section: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Searches active specification on demand for agents requesting more context.
        """
        if not spec or not isinstance(spec, dict):
            return {"found": False, "message": "No specification output is recorded in orchestrator state."}

        clean_query = query.lower().strip()
        matched: Dict[str, Any] = {}

        # 1. Search functional requirements
        if not section or "func" in section.lower() or "req" in section.lower():
            frs = spec.get("functional_requirements", [])
            matched_frs = []
            if isinstance(frs, list):
                for fr in frs:
                    if isinstance(fr, dict) and clean_query in json.dumps(fr).lower():
                        matched_frs.append(fr)
            if matched_frs:
                matched["functional_requirements"] = matched_frs

        # 2. Search edge cases
        if not section or "edge" in section.lower():
            edges = spec.get("edge_cases", [])
            matched_edges = []
            if isinstance(edges, list):
                for ec in edges:
                    if isinstance(ec, dict) and clean_query in json.dumps(ec).lower():
                        matched_edges.append(ec)
            if matched_edges:
                matched["edge_cases"] = matched_edges

        # 3. Search acceptance criteria
        if not section or "accept" in section.lower() or "crit" in section.lower():
            acs = spec.get("acceptance_criteria", [])
            matched_acs = []
            if isinstance(acs, list):
                for ac in acs:
                    if clean_query in str(ac).lower():
                        matched_acs.append(ac)
            if matched_acs:
                matched["acceptance_criteria"] = matched_acs

        # 4. Search non-functional requirements
        if not section or "non" in section.lower() or "nfr" in section.lower():
            nfrs = spec.get("non_functional_requirements", [])
            matched_nfrs = []
            if isinstance(nfrs, list):
                for nfr in nfrs:
                    if isinstance(nfr, dict) and clean_query in json.dumps(nfr).lower():
                        matched_nfrs.append(nfr)
            if matched_nfrs:
                matched["non_functional_requirements"] = matched_nfrs

        total_matched = sum(len(v) if isinstance(v, list) else 1 for v in matched.values())
        if total_matched > 0:
            return {
                "found": True,
                "query": query,
                "total_matches": total_matched,
                "results": matched,
            }
        return {
            "found": False,
            "query": query,
            "message": f"No specification entries matched query '{query}'. Available sections: functional_requirements, edge_cases, acceptance_criteria, non_functional_requirements.",
        }

    @staticmethod
    def query_architecture(
        arch: Optional[Dict[str, Any]],
        module_name: Optional[str] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Searches active architecture blueprint on demand for agents requesting more context.
        """
        if not arch or not isinstance(arch, dict):
            return {"found": False, "message": "No architecture output is recorded in orchestrator state."}

        comps = arch.get("component_structure", [])
        layout = arch.get("file_layout", [])

        matched_comps = []
        if isinstance(comps, list):
            for c in comps:
                if not isinstance(c, dict):
                    continue
                c_mod = str(c.get("module_name", "")).lower()
                c_str = json.dumps(c).lower()
                if module_name and module_name.lower() in c_mod:
                    matched_comps.append(c)
                elif query and query.lower() in c_str:
                    matched_comps.append(c)

        matched_layout = []
        if isinstance(layout, list):
            for l in layout:
                if not isinstance(l, dict):
                    continue
                l_fp = str(l.get("filepath", "")).lower()
                l_str = json.dumps(l).lower()
                if module_name and module_name.lower() in l_fp:
                    matched_layout.append(l)
                elif query and query.lower() in l_str:
                    matched_layout.append(l)

        if matched_comps or matched_layout:
            return {
                "found": True,
                "module_name": module_name,
                "query": query,
                "components": matched_comps,
                "file_layout": matched_layout,
            }

        return {
            "found": False,
            "module_name": module_name,
            "query": query,
            "message": f"No architecture components matched module '{module_name}' or query '{query}'.",
        }

    @staticmethod
    def get_task_artifact(
        state_or_results: Any,
        task_id: str,
    ) -> Dict[str, Any]:
        """
        Retrieves the deliverable of a specific predecessor task on demand.
        """
        clean_id = str(task_id).strip()
        clean_id_lower = clean_id.lower()

        # Check in task_dag if state has it
        dag = getattr(state_or_results, "task_dag", None)
        if dag and hasattr(dag, "get_task"):
            t = dag.get_task(clean_id)
            if t:
                # Find artifact in outputs, checkpoints or observations
                return {
                    "found": True,
                    "task_id": clean_id,
                    "objective": t.objective,
                    "state": t.state.value if hasattr(t.state, "value") else str(t.state),
                    "outputs": t.outputs,
                    "tools_used": t.tools_used,
                    "summary": t.observations[-1].summary if t.observations else "No observations recorded",
                }

        # Check in dictionary or step_results
        results_dict = getattr(state_or_results, "step_results", None)
        if not results_dict and isinstance(state_or_results, dict):
            results_dict = state_or_results.get("step_results") or state_or_results.get("completed_subtasks") or state_or_results

        if isinstance(results_dict, dict):
            for k, v in results_dict.items():
                if str(k).lower() == clean_id_lower or clean_id_lower in str(k).lower():
                    return {"found": True, "task_id": k, "deliverable": v}

        return {
            "found": False,
            "task_id": clean_id,
            "message": f"Task artifact for '{clean_id}' was not found in active step results.",
        }
