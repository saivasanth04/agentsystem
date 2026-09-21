"""
Architecture Comprehension Engine.
Detects frameworks, architectural layers, entrypoints, and produces structured architecture summaries.
"""
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set

from .graph import CodebaseGraph


@dataclass
class ArchitectureSummary:
    """Structured architectural overview of a repository."""
    project_name: str
    detected_frameworks: List[str] = field(default_factory=list)
    entrypoints: List[str] = field(default_factory=list)
    layers: Dict[str, List[str]] = field(default_factory=dict)
    key_classes: List[str] = field(default_factory=list)
    database_models: List[str] = field(default_factory=list)
    api_routes: List[str] = field(default_factory=list)
    test_frameworks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "detected_frameworks": self.detected_frameworks,
            "entrypoints": self.entrypoints,
            "layers": self.layers,
            "key_classes": self.key_classes,
            "database_models": self.database_models,
            "api_routes": self.api_routes,
            "test_frameworks": self.test_frameworks,
        }

    def to_markdown(self) -> str:
        lines = [f"# Architectural Summary: {self.project_name}"]

        if self.detected_frameworks:
            lines.append(f"- **Frameworks**: {', '.join(self.detected_frameworks)}")
        if self.entrypoints:
            lines.append(f"- **Entrypoints**: {', '.join(self.entrypoints)}")
        if self.test_frameworks:
            lines.append(f"- **Test Frameworks**: {', '.join(self.test_frameworks)}")

        if self.layers:
            lines.append("\n### Architectural Layers:")
            for layer_name, files in sorted(self.layers.items()):
                lines.append(f"- **{layer_name.title()}** ({len(files)} files): {', '.join(files[:6])}" + ("..." if len(files) > 6 else ""))

        if self.key_classes:
            lines.append(f"\n- **Core Classes**: {', '.join(self.key_classes[:10])}" + ("..." if len(self.key_classes) > 10 else ""))
        if self.database_models:
            lines.append(f"- **Data Models**: {', '.join(self.database_models[:8])}")
        if self.api_routes:
            lines.append(f"- **API Routes**: {', '.join(self.api_routes[:8])}")

        return "\n".join(lines)


class ArchitectureAnalyzer:
    """Analyzes a codebase graph and workspace files to deduce architectural patterns."""

    FRAMEWORK_SIGNATURES = {
        "FastAPI": ["fastapi", "APIRouter"],
        "Flask": ["flask", "Flask("],
        "Django": ["django", "django.db", "django.urls"],
        "Pytest": ["pytest"],
        "Unittest": ["unittest"],
        "React": ["react", "useState", "useEffect"],
        "Next.js": ["next", "next/router", "next/server"],
        "Express": ["express", "express()"],
        "Pydantic": ["pydantic", "BaseModel"],
        "SQLAlchemy": ["sqlalchemy", "declarative_base", "sessionmaker"],
    }

    ENTRYPOINT_NAMES = {
        "main.py", "app.py", "cli.py", "__main__.py", "server.py",
        "index.ts", "index.js", "main.ts", "server.ts", "server.js"
    }

    def __init__(self, workspace_dir: Path, code_graph: Optional[CodebaseGraph] = None):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.code_graph = code_graph or CodebaseGraph(self.workspace_dir)

    def analyze(self) -> ArchitectureSummary:
        """Inspects symbols, imports, and file paths to build an ArchitectureSummary."""
        detected_frameworks: Set[str] = set()
        test_frameworks: Set[str] = set()
        entrypoints: List[str] = []
        layers: Dict[str, List[str]] = {
            "api": [],
            "services": [],
            "models": [],
            "persistence": [],
            "config": [],
            "utils": [],
            "tests": [],
        }
        key_classes: List[str] = []
        database_models: List[str] = []
        api_routes: List[str] = []

        # 1. Inspect all files in the code graph
        for rel_path, syms in self.code_graph.file_to_symbols.items():
            path_lower = rel_path.lower()
            p = Path(rel_path)

            # Check Entrypoints
            if p.name.lower() in self.ENTRYPOINT_NAMES:
                entrypoints.append(rel_path)

            # Classify Layers
            if any(k in path_lower for k in ["test", "spec"]):
                layers["tests"].append(rel_path)
            elif any(k in path_lower for k in ["api", "route", "controller", "endpoint"]):
                layers["api"].append(rel_path)
            elif any(k in path_lower for k in ["service", "logic", "domain", "core"]):
                layers["services"].append(rel_path)
            elif any(k in path_lower for k in ["model", "schema", "entity"]):
                layers["models"].append(rel_path)
            elif any(k in path_lower for k in ["db", "repository", "storage", "dao"]):
                layers["persistence"].append(rel_path)
            elif any(k in path_lower for k in ["config", "setting", "env"]):
                layers["config"].append(rel_path)
            elif any(k in path_lower for k in ["util", "helper", "tool"]):
                layers["utils"].append(rel_path)

            # Check Frameworks & Routes in symbols
            for s in syms:
                if s.kind == "class":
                    # Key classes: high method count or defined in core/services
                    if len(s.calls) > 2 or "service" in path_lower or "engine" in path_lower:
                        key_classes.append(f"{s.name} ({rel_path})")
                    if any("base" in b.lower() or "model" in b.lower() for b in s.bases):
                        database_models.append(s.name)

                # Route decorators
                for dec in s.decorators:
                    if any(verb in dec.lower() for verb in ["get(", "post(", "put(", "delete(", "patch(", "route("]):
                        api_routes.append(f"{s.name} [{dec}]")

        # 2. Inspect Imports for Frameworks
        all_imports: Set[str] = set()
        for imps in self.code_graph.file_imports.values():
            all_imports.update(imps)

        for fw, sigs in self.FRAMEWORK_SIGNATURES.items():
            for sig in sigs:
                if any(sig in imp for imp in all_imports):
                    if fw in ["Pytest", "Unittest"]:
                        test_frameworks.add(fw)
                    else:
                        detected_frameworks.add(fw)
                    break

        # Remove empty layers
        layers = {k: v for k, v in layers.items() if v}

        return ArchitectureSummary(
            project_name=self.workspace_dir.name,
            detected_frameworks=sorted(list(detected_frameworks)),
            entrypoints=sorted(entrypoints),
            layers=layers,
            key_classes=key_classes[:15],
            database_models=sorted(list(set(database_models))),
            api_routes=api_routes[:15],
            test_frameworks=sorted(list(test_frameworks)),
        )
