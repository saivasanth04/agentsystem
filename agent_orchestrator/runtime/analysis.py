"""
ParallelDomainAnalyzer: Concurrent Multi-Domain Analysis Fan-Out Engine.
Dispatches specialized domain personas (Backend, Frontend, Database, Security) in parallel
to synthesize a comprehensive technical analysis matrix before task decomposition.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional
if TYPE_CHECKING:
    from ..llm import LLMClient
from ..tools.workspace import WorkspaceManager


@dataclass
class DomainAnalysisMatrix:
    core_goal: str
    domain: str = "full-stack"
    backend_findings: Dict[str, Any] = field(default_factory=dict)
    frontend_findings: Dict[str, Any] = field(default_factory=dict)
    database_findings: Dict[str, Any] = field(default_factory=dict)
    security_findings: Dict[str, Any] = field(default_factory=dict)
    technical_constraints: List[str] = field(default_factory=list)
    complexity_rating: str = "MEDIUM"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "core_goal": self.core_goal,
            "domain": self.domain,
            "backend_findings": self.backend_findings,
            "frontend_findings": self.frontend_findings,
            "database_findings": self.database_findings,
            "security_findings": self.security_findings,
            "technical_constraints": self.technical_constraints,
            "complexity_rating": self.complexity_rating,
        }


class ParallelDomainAnalyzer:
    """
    Executes parallel multi-domain analysis fan-out across specialized engineering domains.
    """

    def __init__(
        self,
        llm: LLMClient,
        workspace: WorkspaceManager,
        on_event_callback: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
    ):
        self.llm = llm
        self.workspace = workspace
        self.on_event = on_event_callback or (lambda stage, msg, payload=None: None)

    def analyze_in_parallel(self, user_request: str, model: Optional[str] = None) -> DomainAnalysisMatrix:
        """
        Fans out 4 specialized domain analysts concurrently and merges findings into a DomainAnalysisMatrix.
        """
        existing_files = self.workspace.list_files()[:30]
        context_files_summary = json.dumps(existing_files, indent=2)

        domains = [
            {
                "key": "backend",
                "role": "Backend & API Architect",
                "prompt": f"""Analyze the backend, API architecture, endpoints, and services needed for:
"{user_request}"
Workspace existing files: {context_files_summary}

Respond in JSON format:
{{
  "proposed_endpoints": ["string"],
  "services": ["string"],
  "data_models": ["string"],
  "dependencies": ["string"]
}}""",
            },
            {
                "key": "frontend",
                "role": "Frontend & UI/UX Engineer",
                "prompt": f"""Analyze the user interface components, client state, and user interactions needed for:
"{user_request}"
Workspace existing files: {context_files_summary}

Respond in JSON format:
{{
  "components": ["string"],
  "state_management": ["string"],
  "views_or_routes": ["string"],
  "user_interactions": ["string"]
}}""",
            },
            {
                "key": "database",
                "role": "Database & Data Persistence Architect",
                "prompt": f"""Analyze the data schemas, storage requirements, migrations, and caching needed for:
"{user_request}"
Workspace existing files: {context_files_summary}

Respond in JSON format:
{{
  "schemas": ["string"],
  "tables_or_collections": ["string"],
  "indexing_or_caching": ["string"],
  "migration_notes": ["string"]
}}""",
            },
            {
                "key": "security",
                "role": "Security & Quality Auditor",
                "prompt": f"""Analyze security boundaries, input validation, authentication, and vulnerability vectors for:
"{user_request}"
Workspace existing files: {context_files_summary}

Respond in JSON format:
{{
  "auth_boundaries": ["string"],
  "input_sanitization": ["string"],
  "threat_mitigations": ["string"],
  "quality_requirements": ["string"]
}}""",
            },
        ]

        self.on_event("PARALLEL ANALYSIS", f"Fanning out {len(domains)} concurrent domain analyses: {[d['role'] for d in domains]}")

        results: Dict[str, Any] = {}

        def _run_domain(domain_spec: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
            messages = [
                {"role": "system", "content": f"You are the {domain_spec['role']}."},
                {"role": "user", "content": domain_spec["prompt"]},
            ]
            try:
                res = self.llm.chat_json(messages, model=model, temperature=0.2)
                return domain_spec["key"], res if isinstance(res, dict) else {"raw": res}
            except Exception as e:
                return domain_spec["key"], {"error": str(e)}

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_run_domain, spec) for spec in domains]
            for future in as_completed(futures):
                k, res_data = future.result()
                results[k] = res_data

        # Aggregate constraints
        constraints = []
        for d_key, data in results.items():
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list):
                        constraints.extend([str(item) for item in v[:2]])

        matrix = DomainAnalysisMatrix(
            core_goal=user_request,
            domain="full-stack",
            backend_findings=results.get("backend", {}),
            frontend_findings=results.get("frontend", {}),
            database_findings=results.get("database", {}),
            security_findings=results.get("security", {}),
            technical_constraints=constraints[:10],
            complexity_rating="MEDIUM" if len(existing_files) < 10 else "HIGH",
        )

        self.on_event("PARALLEL ANALYSIS COMPLETE", "Synthesized comprehensive DomainAnalysisMatrix across Backend, Frontend, Database, and Security.")
        return matrix
