"""
Skills System for Autonomous Coding Agents.
Provides Skill Registry, Semantic Discovery (TF-IDF & Metadata matching), JIT Loader,
Execution Engine, Domain Categorization, and Skills.sh ecosystem integration.
"""
from dataclasses import dataclass, field
import json
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger("registry.skill")


class SkillError(Exception):
    """Base exception for skill errors."""
    pass


class SkillDependencyError(SkillError):
    """Base exception for skill dependency resolution errors."""
    pass


class CircularDependencyError(SkillDependencyError):
    """Raised when a circular dependency loop is detected."""
    pass


class MissingDependencyError(SkillDependencyError):
    """Raised when a required skill dependency is not found."""
    pass


class SemVer:
    """
    Semantic Version (SemVer 2.0.0 compliant) implementation supporting
    parsing, comparison, and constraint evaluation (^, ~, >=, <=, >, <, ==, !=, *).
    """
    SEMVER_PATTERN = re.compile(
        r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
        r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
    )
    LOOSE_PATTERN = re.compile(
        r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?"
    )

    def __init__(self, major: int = 0, minor: int = 0, patch: int = 0, prerelease: str = "", build: str = ""):
        self.major = int(major)
        self.minor = int(minor)
        self.patch = int(patch)
        self.prerelease = prerelease or ""
        self.build = build or ""

    @classmethod
    def parse(cls, version_str: str) -> "SemVer":
        if not version_str or not isinstance(version_str, str):
            raise ValueError(f"Invalid version string: '{version_str}'")

        clean = version_str.strip()
        match = cls.SEMVER_PATTERN.match(clean)
        if match:
            gd = match.groupdict()
            return cls(
                major=int(gd["major"]),
                minor=int(gd["minor"]),
                patch=int(gd["patch"]),
                prerelease=gd.get("prerelease") or "",
                build=gd.get("build") or "",
            )
        loose = cls.LOOSE_PATTERN.match(clean)
        if loose:
            gd = loose.groupdict()
            return cls(
                major=int(gd["major"]),
                minor=int(gd["minor"] or 0),
                patch=int(gd["patch"] or 0),
            )
        raise ValueError(f"Cannot parse SemVer from: '{version_str}'")

    @classmethod
    def is_valid(cls, version_str: str) -> bool:
        try:
            cls.parse(version_str)
            return True
        except (ValueError, TypeError):
            return False

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base += f"-{self.prerelease}"
        if self.build:
            base += f"+{self.build}"
        return base

    def __repr__(self) -> str:
        return f"SemVer('{str(self)}')"

    def _cmp_key(self) -> Tuple[int, int, int, int, str]:
        has_pre = 0 if self.prerelease else 1
        return (self.major, self.minor, self.patch, has_pre, self.prerelease)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            try:
                other = SemVer.parse(other)
            except Exception:
                return False
        if not isinstance(other, SemVer):
            return False
        return (self.major, self.minor, self.patch, self.prerelease) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )

    def __lt__(self, other: Any) -> bool:
        if isinstance(other, str):
            other = SemVer.parse(other)
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._cmp_key() < other._cmp_key()

    def __le__(self, other: Any) -> bool:
        return self == other or self < other

    def __gt__(self, other: Any) -> bool:
        return not (self <= other)

    def __ge__(self, other: Any) -> bool:
        return not (self < other)

    def satisfies(self, constraint: str) -> bool:
        """
        Evaluates whether this SemVer satisfies a given constraint expression.
        Supports:
          - '*' or '': matches any
          - '==1.2.3' or '1.2.3': exact match
          - '>=1.0.0', '<2.0.0', '<=1.5.0', '>0.9.0', '!=1.0.0'
          - '^1.2.3': compatible with 1.x (>=1.2.3, <2.0.0; if major=0, >=0.2.3, <0.3.0)
          - '~1.2.3': approximately equivalent to 1.2.x (>=1.2.3, <1.3.0)
          - Compound constraints separated by ',' or ' ' (e.g., '>=1.0.0, <2.0.0')
        """
        if not constraint or constraint.strip() in ("*", ""):
            return True

        clauses = [c.strip() for c in re.split(r"[, ]+", constraint.strip()) if c.strip()]
        for clause in clauses:
            if not self._satisfies_single_clause(clause):
                return False
        return True

    def _satisfies_single_clause(self, clause: str) -> bool:
        clause = clause.strip().lstrip("@").strip()
        if clause in ("*", ""):
            return True

        if clause.startswith("^"):
            target = SemVer.parse(clause[1:])
            if target.major > 0:
                upper = SemVer(target.major + 1, 0, 0)
            elif target.minor > 0:
                upper = SemVer(0, target.minor + 1, 0)
            else:
                upper = SemVer(0, 0, target.patch + 1)
            return self >= target and self < upper

        if clause.startswith("~"):
            target = SemVer.parse(clause[1:])
            upper = SemVer(target.major, target.minor + 1, 0)
            return self >= target and self < upper

        if clause.startswith(">="):
            target = SemVer.parse(clause[2:])
            return self >= target
        elif clause.startswith("<="):
            target = SemVer.parse(clause[2:])
            return self <= target
        elif clause.startswith(">"):
            target = SemVer.parse(clause[1:])
            return self > target
        elif clause.startswith("<"):
            target = SemVer.parse(clause[1:])
            return self < target
        elif clause.startswith("!="):
            target = SemVer.parse(clause[2:])
            return self != target
        elif clause.startswith("=="):
            target = SemVer.parse(clause[2:])
            return self == target
        else:
            target = SemVer.parse(clause)
            return self == target


def parse_dependency_spec(dep_spec: str) -> Tuple[str, Optional[str]]:
    """
    Parses a dependency string like 'git-workflow>=1.0.0', 'git-workflow@^1.0.0',
    or 'base-skill' into (skill_name, version_constraint).
    """
    clean = dep_spec.strip()
    match = re.match(r"^([a-zA-Z0-9_\-]+)(?:@|\s*([<>=!~^].*)|@(.*))?$", clean)
    if match:
        name = match.group(1).strip()
        constraint = (match.group(2) or match.group(3) or "").strip() or None
        return name, constraint
    return clean, None


@dataclass
class SkillValidationReport:
    """
    Comprehensive validation report for a skill package.
    """
    skill_name: str
    is_valid: bool
    version: str = ""
    semver_valid: bool = True
    manifest_valid: bool = True
    files_exist: bool = True
    dependencies_resolved: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    missing_dependencies: List[str] = field(default_factory=list)
    missing_tools: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "is_valid": self.is_valid,
            "version": self.version,
            "semver_valid": self.semver_valid,
            "manifest_valid": self.manifest_valid,
            "files_exist": self.files_exist,
            "dependencies_resolved": self.dependencies_resolved,
            "errors": self.errors,
            "warnings": self.warnings,
            "missing_files": self.missing_files,
            "missing_dependencies": self.missing_dependencies,
            "missing_tools": self.missing_tools,
        }


class LoadedSkillBundle(str):
    """
    Loaded skill bundle containing consolidated instructions, references, scripts,
    rules, and resolved dependency manifests. Subclasses str so that any code expecting
    a markdown instruction string continues to work without modification (isinstance(bundle, str) == True).
    """
    manifest: "SkillManifest"
    instructions: str
    dependencies: List["SkillManifest"]
    scripts: Dict[str, str]
    references: Dict[str, str]
    rules: Dict[str, str]

    def __new__(
        cls,
        content: str,
        manifest: "SkillManifest",
        dependencies: Optional[List["SkillManifest"]] = None,
        scripts: Optional[Dict[str, str]] = None,
        references: Optional[Dict[str, str]] = None,
        rules: Optional[Dict[str, str]] = None,
    ):
        obj = super().__new__(cls, content)
        obj.manifest = manifest
        obj.instructions = manifest.system_instructions
        obj.dependencies = dependencies or []
        obj.scripts = scripts or dict(manifest.scripts)
        obj.references = references or dict(manifest.references)
        obj.rules = rules or dict(manifest.rules)
        return obj

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.manifest.name,
            "version": self.manifest.version,
            "category": self.manifest.category,
            "instructions": self.instructions,
            "dependencies": [d.name for d in self.dependencies],
            "scripts": list(self.scripts.keys()),
            "references": list(self.references.keys()),
            "rules": list(self.rules.keys()),
        }


@dataclass
class SkillManifest:
    name: str
    description: str
    category: str = "general"  # python | react | database | git | security | code-review | architecture | deployment | general
    version: str = "1.0.0"
    source: str = "skills.sh"  # skills.sh | builtin | local
    tags: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    system_instructions: str = ""
    scripts: Dict[str, str] = field(default_factory=dict)
    references: Dict[str, str] = field(default_factory=dict)
    rules: Dict[str, str] = field(default_factory=dict)
    base_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "version": self.version,
            "source": self.source,
            "tags": self.tags,
            "required_tools": self.required_tools,
            "dependencies": self.dependencies,
            "permissions": self.permissions,
            "scripts": list(self.scripts.keys()),
            "references": list(self.references.keys()),
            "rules": list(self.rules.keys()),
            "base_dir": self.base_dir,
        }

    def get_summary(self) -> str:
        tools_str = f", tools: {','.join(self.required_tools)}" if self.required_tools else ""
        scripts_str = f", scripts: {','.join(self.scripts.keys())}" if self.scripts else ""
        return f"[{self.category}] {self.name} (v{self.version}): {self.description[:120]}...{tools_str}{scripts_str}"


class SkillExecutionEngine:
    """
    Executes and interacts with skill assets (executable scripts, reference guides, rule definitions).
    """

    def __init__(self, registry: "SkillRegistry"):
        self.registry = registry

    def load_skill_details(self, name: str) -> Dict[str, Any]:
        """
        Loads full skill instructions, metadata, scripts, and available references.
        """
        skill = self.registry.get_skill(name)
        if not skill:
            return {"error": f"Skill '{name}' not found in registry."}

        return {
            "name": skill.name,
            "category": skill.category,
            "description": skill.description,
            "version": skill.version,
            "tags": skill.tags,
            "required_tools": skill.required_tools,
            "dependencies": skill.dependencies,
            "permissions": skill.permissions,
            "available_scripts": list(skill.scripts.keys()),
            "available_references": list(skill.references.keys()),
            "available_rules": list(skill.rules.keys()),
            "instructions": skill.system_instructions,
        }

    def read_reference(
        self,
        skill_name: str,
        reference_name: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Reads reference material or rules associated with a skill.
        """
        skill = self.registry.get_skill(skill_name)
        if not skill:
            return {"error": f"Skill '{skill_name}' not found in registry."}

        # Check references first, then rules, scripts, resources
        ref_path_str = skill.references.get(reference_name) or skill.rules.get(reference_name)
        if not ref_path_str:
            # Fallback search by partial name or direct relative path inside base_dir
            for k, v in {**skill.references, **skill.rules}.items():
                if reference_name.lower() in k.lower():
                    ref_path_str = v
                    break

            if not ref_path_str and skill.base_dir:
                candidate = (Path(skill.base_dir) / reference_name).resolve()
                if candidate.exists() and candidate.is_file():
                    ref_path_str = str(candidate)

        if not ref_path_str:
            avail = list(skill.references.keys()) + list(skill.rules.keys())
            return {
                "error": f"Reference '{reference_name}' not found for skill '{skill_name}'. Available: {avail}"
            }

        ref_path = Path(ref_path_str).resolve()
        if skill.base_dir:
            base_p = Path(skill.base_dir).resolve()
            try:
                if not ref_path.is_relative_to(base_p):
                    return {"error": f"Access denied: reference '{reference_name}' is outside skill directory."}
            except AttributeError:
                # Python < 3.9 compatibility
                if not str(ref_path).startswith(str(base_p)):
                    return {"error": f"Access denied: reference '{reference_name}' is outside skill directory."}

        if not ref_path.exists() or not ref_path.is_file():
            return {"error": f"Reference file at '{ref_path_str}' does not exist on disk."}

        try:
            content = ref_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            total_lines = len(lines)

            if start_line is not None or end_line is not None:
                s = max(1, start_line or 1) - 1
                e = min(total_lines, end_line or total_lines)
                sliced_lines = lines[s:e]
                return {
                    "skill": skill_name,
                    "reference": reference_name,
                    "path": str(ref_path),
                    "total_lines": total_lines,
                    "line_range": f"{s+1}-{e}",
                    "content": "\n".join(sliced_lines),
                }

            return {
                "skill": skill_name,
                "reference": reference_name,
                "path": str(ref_path),
                "total_lines": total_lines,
                "content": content,
            }
        except Exception as ex:
            return {"error": f"Failed reading reference '{reference_name}': {str(ex)}"}

    def execute_script(
        self,
        skill_name: str,
        script_name: str,
        args: Optional[List[str]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Safely executes an executable script from a skill package (Python, Node/JS, Shell/PowerShell).
        """
        skill = self.registry.get_skill(skill_name)
        if not skill:
            return {"error": f"Skill '{skill_name}' not found in registry."}

        script_path_str = skill.scripts.get(script_name)
        if not script_path_str:
            # Try fuzzy match
            for k, v in skill.scripts.items():
                if script_name.lower() in k.lower():
                    script_path_str = v
                    break

            if not script_path_str and skill.base_dir:
                candidate = (Path(skill.base_dir) / script_name).resolve()
                if candidate.exists() and candidate.is_file():
                    script_path_str = str(candidate)

        if not script_path_str:
            return {
                "error": f"Script '{script_name}' not found in skill '{skill_name}'. Available: {list(skill.scripts.keys())}"
            }

        script_path = Path(script_path_str).resolve()
        if skill.base_dir:
            base_p = Path(skill.base_dir).resolve()
            try:
                if not script_path.is_relative_to(base_p):
                    return {"error": f"Access denied: script '{script_name}' is outside skill directory."}
            except AttributeError:
                if not str(script_path).startswith(str(base_p)):
                    return {"error": f"Access denied: script '{script_name}' is outside skill directory."}

        if not script_path.exists():
            return {"error": f"Script file at '{script_path_str}' does not exist."}

        # Determine runtime executable based on file extension
        suffix = script_path.suffix.lower()
        args = args or []

        if suffix == ".py":
            cmd = [sys.executable, str(script_path)] + args
        elif suffix in [".js", ".mjs"]:
            cmd = ["node", str(script_path)] + args
        elif suffix in [".sh", ".bash"]:
            cmd = ["bash", str(script_path)] + args
        elif suffix in [".bat", ".cmd"]:
            cmd = [str(script_path)] + args
        elif suffix == ".ps1":
            cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)] + args
        else:
            cmd = [str(script_path)] + args

        try:
            try:
                from ..security.sandbox import create_sandbox
            except (ImportError, ValueError):
                from agent_orchestrator.security.sandbox import create_sandbox

            sandbox = create_sandbox(skill.base_dir)
            res = sandbox.run_command(cmd, timeout=timeout, cwd=str(skill.base_dir))
            if res.timed_out:
                return {
                    "skill": skill_name,
                    "script": script_name,
                    "error": f"Execution timed out after {timeout} seconds.",
                    "success": False,
                }
            return {
                "skill": skill_name,
                "script": script_name,
                "exit_code": res.exit_code,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "success": res.success,
            }
        except Exception as ex:
            return {
                "skill": skill_name,
                "script": script_name,
                "error": f"Execution failed: {str(ex)}",
                "success": False,
            }


class SkillManager:
    """
    Unified Skill Manager / Registry managing discovery, domain categorization,
    semantic TF-IDF matching, lazy progressive disclosure, and JIT prompt injection.
    """

    # Category domain classification
    CATEGORY_MAPPING = {
        "python": [
            "test-driven-development",
            "debugging-and-error-recovery",
            "code-simplification",
            "source-driven-development",
            "python-debugging",
            "python-testing",
            "python-refactoring",
            "tdd",
            "debug",
            "diagnosing-bugs",
        ],
        "react": [
            "frontend-ui-engineering",
            "frontend-design",
            "vercel-react-best-practices",
            "vercel-composition-patterns",
            "vercel-react-view-transitions",
            "vercel-react-native-skills",
            "web-design-guidelines",
        ],
        "database": [
            "deprecation-and-migration",
            "performance-optimization",
            "database-migration",
            "database-query-optimization",
        ],
        "git": [
            "git-workflow-and-versioning",
            "shipping-and-launch",
            "ci-cd-and-automation",
            "setup-pre-commit",
            "git-workflow",
        ],
        "security": [
            "security-and-hardening",
            "constraint-driven-development",
            "security-owasp-hardening",
            "permissioned-github",
        ],
        "code-review": [
            "code-review-and-quality",
            "code-review",
            "doubt-driven-development",
            "writing-guidelines",
            "code-review-audit",
        ],
        "architecture": [
            "api-and-interface-design",
            "spec-driven-development",
            "documentation-and-adrs",
            "planning-and-task-breakdown",
            "create-implementation-plan",
            "codebase-design",
            "incremental-implementation",
            "context-engineering",
            "idea-refine",
            "interview-me",
            "domain-modeling",
            "brainstorming",
            "persona-project-manager",
            "using-agent-skills",
            "find-skills",
            "graphify",
            "generative_ui",
            "agy-customizations",
            "antigravity-guide",
            "migrate-workflows",
        ],
        "deployment": [
            "deploy-to-vercel",
            "vercel-cli-with-tokens",
            "vercel-optimize",
            "observability-and-instrumentation",
            "zero",
            "workflow-automation",
        ],
    }

    def __init__(self, custom_skills_dir: Optional[Path] = None):
        self.custom_skills_dir = custom_skills_dir
        self._lock = threading.RLock()
        self._skills: Dict[str, SkillManifest] = {}
        self._versions: Dict[str, Dict[str, SkillManifest]] = {}
        self._loaded_cache: Dict[str, LoadedSkillBundle] = {}
        self._corpus_tokens: Dict[str, Dict[str, float]] = {}
        self._doc_lengths: Dict[str, float] = {}
        self._idf: Dict[str, float] = {}
        self._catalog_cache: Optional[str] = None
        self.engine = SkillExecutionEngine(self)
        self._discover_all_skills()

    def _get_search_paths(self) -> List[Path]:
        """Returns discovery search paths in order of priority (workspace -> user -> builtin)."""
        paths = []
        if self.custom_skills_dir and self.custom_skills_dir.exists():
            paths.append(self.custom_skills_dir)

        # Workspace paths
        paths.append(Path.cwd() / ".agents" / "skills")
        paths.append(Path.cwd() / ".agent" / "skills")
        paths.append(Path(__file__).resolve().parents[2] / ".agents" / "skills")

        # Global user paths
        paths.append(Path.home() / ".agents" / "skills")
        paths.append(Path.home() / ".agent" / "skills")

        # Builtin system paths
        paths.append(Path.home() / ".gemini" / "antigravity" / "builtin" / "skills")

        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for p in paths:
            try:
                resolved = p.resolve()
                if resolved not in seen and resolved.exists():
                    seen.add(resolved)
                    deduped.append(resolved)
            except Exception:
                continue
        return deduped

    def _discover_all_skills(self):
        """Scans directories, parses real SKILL.md packages, and builds the semantic index."""
        with self._lock:
            search_paths = self._get_search_paths()
            for base_path in search_paths:
                for skill_file in base_path.rglob("SKILL.md"):
                    try:
                        if skill_file.stat().st_size == 0:
                            continue
                        self._parse_and_register_skill(skill_file)
                    except Exception as e:
                        logger.debug(f"Failed parsing skill file {skill_file}: {e}")
            self._catalog_cache = None
            self._rebuild_semantic_index()

    def _rebuild_semantic_index(self):
        """Builds TF-IDF / BM25 token vectors for all registered skills."""
        with self._lock:
            doc_freq: Dict[str, int] = {}
            total_docs = len(self._skills)
            if total_docs == 0:
                return

            self._corpus_tokens.clear()
            self._doc_lengths.clear()

            # Step 1: Tokenize each skill document
            for name, manifest in list(self._skills.items()):
                doc_text = f"{manifest.name} {manifest.name.replace('-', ' ')} {manifest.category} {' '.join(manifest.tags)} {manifest.description} {manifest.system_instructions[:600]}"
                tokens = self._tokenize(doc_text)
                term_counts: Dict[str, int] = {}
                for t in tokens:
                    term_counts[t] = term_counts.get(t, 0) + 1

                for t in term_counts:
                    doc_freq[t] = doc_freq.get(t, 0) + 1

                self._corpus_tokens[name] = {t: float(count) for t, count in term_counts.items()}

            # Step 2: Compute IDF and Document Vectors
            self._idf = {
                t: math.log((total_docs + 1.0) / (df + 1.0)) + 1.0
                for t, df in doc_freq.items()
            }

            for name, tf_map in self._corpus_tokens.items():
                vec_length_sq = 0.0
                for t, count in tf_map.items():
                    tfidf_val = (1.0 + math.log(count)) * self._idf.get(t, 1.0)
                    tf_map[t] = tfidf_val
                    vec_length_sq += tfidf_val * tfidf_val
                self._doc_lengths[name] = math.sqrt(vec_length_sq) if vec_length_sq > 0 else 1.0

    def _tokenize(self, text: str) -> List[str]:
        return [w for w in re.findall(r"[a-zA-Z0-9_\-]+", text.lower()) if len(w) >= 2]

    def _infer_category(self, name: str, tags: List[str], parent_category: Optional[str] = None) -> str:
        if parent_category and parent_category != "general":
            return parent_category

        name_lower = name.lower()
        for cat, skill_names in self.CATEGORY_MAPPING.items():
            if name_lower in skill_names or any(name_lower.startswith(s) for s in skill_names):
                return cat

        tag_set = {t.lower() for t in tags}
        if {"python", "pytest", "fastapi", "django"}.intersection(tag_set) or "python" in name_lower or "test" in name_lower:
            return "python"
        if {"react", "nextjs", "frontend", "ui", "tailwind"}.intersection(tag_set) or "react" in name_lower or "ui" in name_lower:
            return "react"
        if {"database", "sql", "migration", "postgres"}.intersection(tag_set) or "db" in name_lower or "database" in name_lower:
            return "database"
        if {"git", "github", "ci", "cd", "branch"}.intersection(tag_set) or "git" in name_lower or "ci-cd" in name_lower:
            return "git"
        if {"security", "owasp", "auth", "vulnerability"}.intersection(tag_set) or "security" in name_lower:
            return "security"
        if {"review", "quality", "audit", "lint"}.intersection(tag_set) or "review" in name_lower:
            return "code-review"
        if {"architecture", "spec", "plan", "design"}.intersection(tag_set) or "design" in name_lower or "spec" in name_lower:
            return "architecture"
        if {"deploy", "vercel", "cloud", "aws", "docker"}.intersection(tag_set) or "deploy" in name_lower or "vercel" in name_lower:
            return "deployment"

        return "general"

    def _parse_and_register_skill(self, skill_file: Path):
        content = skill_file.read_text(encoding="utf-8", errors="replace")
        skill_dir = skill_file.parent
        name = skill_dir.name
        description = "Agent skill package from skills.sh"
        version = "1.0.0"
        category = "general"
        tags: List[str] = []
        required_tools: List[str] = []
        dependencies: List[str] = []
        permissions: List[str] = []
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter_str = parts[1]
                body = parts[2].strip()

                parsed_fm = None
                if yaml is not None:
                    try:
                        parsed_fm = yaml.safe_load(frontmatter_str)
                    except Exception as yex:
                        logger.debug(f"PyYAML parsing failed for {skill_file}: {yex}")

                if isinstance(parsed_fm, dict):
                    name = str(parsed_fm.get("name") or name).strip()
                    desc = parsed_fm.get("description")
                    if desc:
                        if isinstance(desc, str):
                            description = desc.strip()
                        elif isinstance(desc, list):
                            description = " ".join(str(x) for x in desc)
                        else:
                            description = str(desc).strip()
                    version = str(parsed_fm.get("version") or version).strip()
                    category = str(parsed_fm.get("category") or category).strip()

                    raw_tags = parsed_fm.get("tags") or []
                    if isinstance(raw_tags, list):
                        tags = [str(t).strip() for t in raw_tags if str(t).strip()]
                    elif isinstance(raw_tags, str):
                        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

                    raw_req = parsed_fm.get("required_tools") or []
                    if isinstance(raw_req, list):
                        required_tools = [str(t).strip() for t in raw_req if str(t).strip()]
                    elif isinstance(raw_req, str):
                        required_tools = [t.strip() for t in raw_req.split(",") if t.strip()]

                    raw_deps = parsed_fm.get("dependencies") or []
                    if isinstance(raw_deps, list):
                        dependencies = [str(d).strip() for d in raw_deps if str(d).strip()]
                    elif isinstance(raw_deps, str):
                        dependencies = [d.strip() for d in raw_deps.split(",") if d.strip()]

                    raw_perms = parsed_fm.get("permissions") or []
                    if isinstance(raw_perms, list):
                        permissions = [str(p).strip() for p in raw_perms if str(p).strip()]
                    elif isinstance(raw_perms, str):
                        permissions = [p.strip() for p in raw_perms.split(",") if p.strip()]
                else:
                    # Fallback line parser
                    current_key = None
                    for line in frontmatter_str.splitlines():
                        trimmed = line.strip()
                        if not trimmed or trimmed.startswith("#"):
                            continue

                        if ":" in trimmed and not trimmed.startswith("-"):
                            k, v = trimmed.split(":", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            current_key = k

                            if k == "name" and v:
                                name = v
                            elif k == "description" and v and v not in (">", "|"):
                                description = v
                            elif k == "version" and v:
                                version = v
                            elif k == "category" and v:
                                category = v
                            elif k in ["tags", "required_tools", "dependencies", "permissions"] and v:
                                clean_val = v.strip("[]")
                                items = [x.strip().strip('"').strip("'") for x in clean_val.split(",") if x.strip()]
                                if k == "tags":
                                    tags = items
                                elif k == "required_tools":
                                    required_tools = items
                                elif k == "dependencies":
                                    dependencies = items
                                elif k == "permissions":
                                    permissions = items
                        elif trimmed.startswith("-") and current_key:
                            item_val = trimmed.lstrip("-").strip().strip('"').strip("'")
                            if current_key == "tags":
                                tags.append(item_val)
                            elif current_key == "required_tools":
                                required_tools.append(item_val)
                            elif current_key == "dependencies":
                                dependencies.append(item_val)
                            elif current_key == "permissions":
                                permissions.append(item_val)
                        elif current_key == "description" and trimmed:
                            if description in (">", "|", "Agent skill package from skills.sh"):
                                description = trimmed
                            else:
                                description += " " + trimmed

        scripts: Dict[str, str] = {}
        references: Dict[str, str] = {}
        rules: Dict[str, str] = {}

        def _index_dir(dir_path: Path, target_dict: Dict[str, str]):
            if dir_path.exists() and dir_path.is_dir():
                for f in dir_path.rglob("*"):
                    if f.is_file():
                        target_dict[f.name] = str(f)
                        try:
                            rel_posix = str(f.relative_to(skill_dir)).replace("\\", "/")
                            target_dict[rel_posix] = str(f)
                            sub_rel = str(f.relative_to(dir_path)).replace("\\", "/")
                            target_dict[sub_rel] = str(f)
                        except Exception:
                            pass

        _index_dir(skill_dir / "scripts", scripts)
        _index_dir(skill_dir / "references", references)
        _index_dir(skill_dir / "resources", references)
        _index_dir(skill_dir / "rules", rules)

        if not required_tools:
            required_tools = ["read_file", "replace_file_content", "terminal_execute"]
        if not permissions:
            permissions = ["filesystem.read", "filesystem.write"]
            if scripts:
                permissions.append("terminal.execute")

        inferred_cat = self._infer_category(name, tags, category if category != "general" else None)

        norm = name.replace("_", "-").lower()
        if norm not in self._versions:
            self._versions[norm] = {}

        manifest = SkillManifest(
            name=name,
            description=description,
            category=inferred_cat,
            version=version,
            source="skills.sh",
            tags=tags if tags else [name.replace("-", "_")],
            required_tools=required_tools,
            dependencies=dependencies,
            permissions=permissions,
            system_instructions=body,
            scripts=scripts,
            references=references,
            rules=rules,
            base_dir=str(skill_dir),
        )

        self._versions[norm][version] = manifest

        if name in self._skills:
            try:
                if SemVer.parse(self._skills[name].version) >= SemVer.parse(version):
                    return
            except Exception:
                if self._skills[name].system_instructions:
                    return

        self._skills[name] = manifest

    def register_skill(self, skill: SkillManifest):
        """Manually registers or overrides a skill, indexing its version."""
        with self._lock:
            norm = skill.name.replace("_", "-").lower()
            if norm not in self._versions:
                self._versions[norm] = {}
            self._versions[norm][skill.version] = skill

            # Update default/active manifest in self._skills if higher SemVer
            current = self._skills.get(skill.name)
            if current:
                try:
                    if SemVer.parse(skill.version) >= SemVer.parse(current.version):
                        self._skills[skill.name] = skill
                except Exception:
                    self._skills[skill.name] = skill
            else:
                self._skills[skill.name] = skill

            self._catalog_cache = None
            self._rebuild_semantic_index()

    def get_skill(self, name: str, version: Optional[str] = None) -> Optional[SkillManifest]:
        """Gets a skill by exact or normalized name, optionally constrained by version."""
        with self._lock:
            norm = name.replace("_", "-").lower()

            # Check versioned map first if present
            if norm in self._versions and self._versions[norm]:
                version_map = self._versions[norm]
                if version:
                    clean_v = version.lstrip("@").strip()
                    if clean_v in version_map:
                        return version_map[clean_v]
                    matching: List[Tuple[SemVer, SkillManifest]] = []
                    for v_str, manifest in version_map.items():
                        try:
                            sv = SemVer.parse(v_str)
                            if sv.satisfies(clean_v):
                                matching.append((sv, manifest))
                        except Exception:
                            if v_str == clean_v:
                                matching.append((SemVer(0, 0, 0), manifest))
                    if matching:
                        matching.sort(key=lambda x: x[0], reverse=True)
                        return matching[0][1]
                    return None
                else:
                    try:
                        sorted_versions = sorted(
                            version_map.items(),
                            key=lambda x: SemVer.parse(x[0]),
                            reverse=True,
                        )
                        return sorted_versions[0][1]
                    except Exception:
                        pass

            # Fallback to unversioned _skills map
            manifest = self._skills.get(name)
            if not manifest:
                for k, v in self._skills.items():
                    if k.replace("_", "-").lower() == norm:
                        manifest = v
                        break

            if manifest and version:
                clean_v = version.lstrip("@").strip()
                try:
                    if SemVer.parse(manifest.version).satisfies(clean_v):
                        return manifest
                    return None
                except Exception:
                    return manifest if manifest.version == clean_v else None

            return manifest

    def version(self, name: str) -> Optional[str]:
        """Returns the active/latest version string of the requested skill."""
        manifest = self.get_skill(name)
        return manifest.version if manifest else None

    def list_versions(self, name: str) -> List[str]:
        """Returns all registered versions for a skill, sorted by SemVer ascending."""
        with self._lock:
            norm = name.replace("_", "-").lower()
            if norm in self._versions:
                versions = list(self._versions[norm].keys())
                try:
                    return sorted(versions, key=lambda v: SemVer.parse(v))
                except Exception:
                    return sorted(versions)
            manifest = self.get_skill(name)
            return [manifest.version] if manifest else []

    def resolve_dependency_manifests(self, skill_name: str, version: Optional[str] = None) -> List[SkillManifest]:
        """
        Resolves transitive dependencies and returns ordered list of SkillManifest objects
        preserving the exact constraint-satisfying version for each node.
        """
        with self._lock:
            root_manifest = self.get_skill(skill_name, version=version)
            if not root_manifest:
                raise MissingDependencyError(f"Root skill '{skill_name}' not found in registry.")

            resolved: List[SkillManifest] = []
            visited: Set[str] = set()
            visiting: List[str] = []

            def dfs(current_name: str, current_version_constraint: Optional[str] = None):
                norm = current_name.replace("_", "-").lower()
                if norm in visiting:
                    cycle_path = " -> ".join(visiting + [norm])
                    raise CircularDependencyError(f"Circular dependency detected: {cycle_path}")
                if norm in visited:
                    return

                manifest = self.get_skill(current_name, version=current_version_constraint)
                if not manifest:
                    raise MissingDependencyError(
                        f"Dependency '{current_name}' (constraint: {current_version_constraint or '*'}) not found in registry."
                    )

                visiting.append(norm)

                for dep_spec in manifest.dependencies:
                    dep_name, dep_constraint = parse_dependency_spec(dep_spec)
                    dfs(dep_name, dep_constraint)

                visiting.pop()
                visited.add(norm)
                resolved.append(manifest)

            dfs(root_manifest.name, version)
            return resolved

    def resolve_dependencies(self, skill_name: str, version: Optional[str] = None) -> List[str]:
        """
        Resolves the transitive dependency tree for the given skill,
        returning an ordered list of skill names in topological order
        (dependencies first, root skill last).
        Detects circular dependencies and raises CircularDependencyError.
        """
        return [m.name for m in self.resolve_dependency_manifests(skill_name, version=version)]

    def validate(
        self,
        skill_or_name: Union[str, SkillManifest],
        tool_registry: Optional[Any] = None,
    ) -> SkillValidationReport:
        """
        Validates the integrity of a skill:
          - Manifest completeness (name, description)
          - SemVer format compliance
          - File integrity (scripts, references, rules exist on disk if base_dir is set)
          - Dependency availability and absence of circular dependencies
          - Required tools availability (against tool_registry if provided)
        """
        if isinstance(skill_or_name, str):
            manifest = self.get_skill(skill_or_name)
            if not manifest:
                return SkillValidationReport(
                    skill_name=skill_or_name,
                    is_valid=False,
                    errors=[f"Skill '{skill_or_name}' not found in registry."],
                )
        else:
            manifest = skill_or_name

        errors: List[str] = []
        warnings: List[str] = []
        missing_files: List[str] = []
        missing_deps: List[str] = []
        missing_tools: List[str] = []

        manifest_valid = bool(manifest.name and manifest.name.strip())
        if not manifest_valid:
            errors.append("Skill name cannot be empty.")
        if not manifest.description:
            warnings.append("Skill description is empty.")

        semver_valid = SemVer.is_valid(manifest.version)
        if not semver_valid:
            errors.append(f"Invalid SemVer version '{manifest.version}'.")

        files_exist = True
        all_file_dicts = [
            ("script", manifest.scripts),
            ("reference", manifest.references),
            ("rule", manifest.rules),
        ]
        for asset_type, asset_dict in all_file_dicts:
            for item_name, file_path_str in asset_dict.items():
                p = Path(file_path_str)
                if not p.exists():
                    missing_files.append(f"{asset_type}:{item_name} -> {file_path_str}")
                    files_exist = False

        if missing_files:
            errors.append(f"Missing asset files on disk: {', '.join(missing_files)}")

        deps_resolved = True
        for dep_spec in manifest.dependencies:
            dep_name, dep_constraint = parse_dependency_spec(dep_spec)
            dep_manifest = self.get_skill(dep_name, version=dep_constraint)
            if not dep_manifest:
                missing_deps.append(dep_spec)
                deps_resolved = False

        if missing_deps:
            errors.append(f"Missing required dependencies: {', '.join(missing_deps)}")

        # Check for circular dependencies
        try:
            self.resolve_dependencies(manifest.name)
        except CircularDependencyError as cde:
            deps_resolved = False
            errors.append(str(cde))
        except MissingDependencyError:
            pass

        if tool_registry and manifest.required_tools:
            registered_tools: Optional[Set[str]] = None
            if hasattr(tool_registry, "list_tools"):
                tools_list = tool_registry.list_tools()
                if tools_list and hasattr(tools_list[0], "name"):
                    registered_tools = {t.name for t in tools_list}
                else:
                    registered_tools = set(tools_list)
            elif hasattr(tool_registry, "get_all_tool_names"):
                registered_tools = set(tool_registry.get_all_tool_names())

            for tool_name in manifest.required_tools:
                if registered_tools is not None:
                    if tool_name not in registered_tools:
                        missing_tools.append(tool_name)
                elif hasattr(tool_registry, "has_tool") and not tool_registry.has_tool(tool_name):
                    missing_tools.append(tool_name)

            if missing_tools:
                warnings.append(f"Required tools not found in ToolRegistry: {', '.join(missing_tools)}")

        is_valid = len(errors) == 0

        return SkillValidationReport(
            skill_name=manifest.name,
            is_valid=is_valid,
            version=manifest.version,
            semver_valid=semver_valid,
            manifest_valid=manifest_valid,
            files_exist=files_exist,
            dependencies_resolved=deps_resolved,
            errors=errors,
            warnings=warnings,
            missing_files=missing_files,
            missing_dependencies=missing_deps,
            missing_tools=missing_tools,
        )

    def validate_all(self, tool_registry: Optional[Any] = None) -> Dict[str, SkillValidationReport]:
        """Validates all registered skills and returns a dictionary of reports."""
        with self._lock:
            return {name: self.validate(manifest, tool_registry=tool_registry) for name, manifest in list(self._skills.items())}

    def list(self, category: Optional[str] = None) -> List[SkillManifest]:
        """Lists registered skills, optionally filtered by category domain."""
        with self._lock:
            all_skills = list(self._skills.values())
            if category:
                cat_clean = category.lower().strip()
                return [s for s in all_skills if s.category.lower() == cat_clean]
            return all_skills

    def list_skills(self, category: Optional[str] = None) -> List[SkillManifest]:
        """Alias for list()."""
        return self.list(category=category)

    def get_categories(self) -> List[str]:
        """Returns all unique skill categories."""
        with self._lock:
            return sorted(list({s.category for s in self._skills.values()}))

    # ==========================================
    # DYNAMIC JIT SKILL DISCOVERY & SEMANTIC MATCHING
    # ==========================================
    def discover(
        self,
        query: str = "",
        top_k: int = 5,
        threshold: float = 0.0,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        required_tools: Optional[List[str]] = None,
        version_constraint: Optional[str] = None,
    ) -> List[Tuple[SkillManifest, float]]:
        """
        Dynamically discovers and ranks skills using hybrid semantic TF-IDF similarity,
        metadata keyword matching, and multi-criteria filters (category, tags, required_tools, version_constraint).
        Returns list of (SkillManifest, relevance_score).
        """
        with self._lock:
            if not self._skills:
                return []

            query_clean = (query or "").lower().strip()
            query_tokens = self._tokenize(query_clean) if query_clean else []

            # Pre-filter candidate skills
            candidate_manifests: List[SkillManifest] = []
            for name, manifest in self._skills.items():
                # Category filter
                if category and manifest.category.lower() != category.lower().strip():
                    continue

                # Version constraint filter
                if version_constraint:
                    clean_v = version_constraint.lstrip("@").strip()
                    try:
                        if not SemVer.parse(manifest.version).satisfies(clean_v):
                            continue
                    except Exception:
                        if manifest.version != clean_v:
                            continue

                # Required tools filter
                if required_tools:
                    skill_tools = set(manifest.required_tools)
                    req_tools_set = set(required_tools)
                    if not skill_tools.intersection(req_tools_set):
                        continue

                # Tags filter
                if tags:
                    skill_tags = {t.lower() for t in manifest.tags}
                    req_tags = {t.lower() for t in tags}
                    if not skill_tags.intersection(req_tags):
                        continue

                candidate_manifests.append(manifest)

            if not candidate_manifests:
                return []

            # If no query string, rank candidates by default score
            if not query_tokens:
                results = [(m, 1.0) for m in candidate_manifests]
                return results[:top_k]

            # Build query vector
            query_counts: Dict[str, int] = {}
            for t in query_tokens:
                query_counts[t] = query_counts.get(t, 0) + 1

            query_vec: Dict[str, float] = {}
            q_len_sq = 0.0
            for t, count in query_counts.items():
                val = (1.0 + math.log(count)) * self._idf.get(t, 1.0)
                query_vec[t] = val
                q_len_sq += val * val
            q_len = math.sqrt(q_len_sq) if q_len_sq > 0 else 1.0

            scores: List[Tuple[SkillManifest, float]] = []

            for manifest in candidate_manifests:
                name = manifest.name
                # 1. Cosine similarity from TF-IDF vector space
                doc_vec = self._corpus_tokens.get(name, {})
                doc_len = self._doc_lengths.get(name, 1.0)
                dot_product = sum(query_vec[t] * doc_vec.get(t, 0.0) for t in query_vec if t in doc_vec)
                cosine_sim = dot_product / (q_len * doc_len) if (q_len * doc_len) > 0 else 0.0

                # 2. Metadata matching bonus
                meta_bonus = 0.0
                name_norm = manifest.name.replace("-", " ").replace("_", " ").lower()
                tag_str = " ".join(manifest.tags).lower()

                if query_clean == manifest.name.lower() or query_clean in name_norm:
                    meta_bonus += 1.0
                if any(t in name_norm for t in query_tokens):
                    meta_bonus += 0.4
                if any(t in tag_str for t in query_tokens):
                    meta_bonus += 0.3
                if any(t == manifest.category.lower() for t in query_tokens):
                    meta_bonus += 0.2

                final_score = (cosine_sim * 0.7) + (min(meta_bonus, 1.0) * 0.3)

                if final_score >= threshold:
                    scores.append((manifest, round(final_score, 4)))

            scores.sort(key=lambda x: x[1], reverse=True)
            return scores[:top_k]

    def search_skills(
        self,
        query: str = "",
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        required_tools: Optional[List[str]] = None,
        version_constraint: Optional[str] = None,
    ) -> List[SkillManifest]:
        """
        Search interface returning matching SkillManifests.
        """
        results = self.discover(
            query=query,
            top_k=10,
            category=category,
            tags=tags,
            required_tools=required_tools,
            version_constraint=version_constraint,
        )
        return [manifest for manifest, _ in results]

    def find_best_skills_for_task(self, task_description: str, top_k: int = 4) -> List[SkillManifest]:
        """
        Dynamically matches task description to skills in the catalog.
        """
        return [m for m, _ in self.discover(task_description, top_k=top_k)]

    # ==========================================
    # LAZY LOADING & JIT INSTRUCTION INJECTION
    # ==========================================
    def load(
        self,
        skill_name: str,
        version: Optional[str] = None,
        resolve_deps: bool = True,
    ) -> Optional[LoadedSkillBundle]:
        """
        Loads the markdown system instructions and guidelines for a specific skill as a LoadedSkillBundle
        (subclassing str for full backwards compatibility).
        Caches the parsed result in memory.
        """
        with self._lock:
            norm_name = skill_name.replace("_", "-").lower()
            cache_key = f"{norm_name}@{version}" if version else norm_name
            if cache_key in self._loaded_cache:
                return self._loaded_cache[cache_key]

            manifest = self.get_skill(skill_name, version=version)
            if not manifest:
                return None

            # Resolve transitive dependencies if requested
            dep_manifests: List[SkillManifest] = []
            if resolve_deps and manifest.dependencies:
                try:
                    ordered_manifests = self.resolve_dependency_manifests(manifest.name, version=version)
                    for dm in ordered_manifests:
                        if dm.name != manifest.name:
                            dep_manifests.append(dm)
                except Exception as ex:
                    logger.debug(f"Dependency resolution notice for '{skill_name}': {ex}")

            # Consolidate scripts, references, and rules across dependencies
            consolidated_scripts = dict(manifest.scripts)
            consolidated_refs = dict(manifest.references)
            consolidated_rules = dict(manifest.rules)
            for dm in dep_manifests:
                for k, v in dm.scripts.items():
                    consolidated_scripts.setdefault(k, v)
                for k, v in dm.references.items():
                    consolidated_refs.setdefault(k, v)
                for k, v in dm.rules.items():
                    consolidated_rules.setdefault(k, v)

            # Format skill guidelines
            lines = [
                f"### [Skill: {manifest.name} ({manifest.category.title()})]",
                f"**Description**: {manifest.description}",
            ]
            if manifest.system_instructions:
                lines.append(f"\n{manifest.system_instructions}")
            if consolidated_rules:
                lines.append(f"\n**Enforced Rules**: {', '.join(consolidated_rules.keys())}")
            if consolidated_refs:
                lines.append(f"**References Available**: {', '.join(consolidated_refs.keys())}")
            if dep_manifests:
                lines.append(f"**Dependencies**: {', '.join(d.name for d in dep_manifests)}")

            full_text = "\n".join(lines)
            bundle = LoadedSkillBundle(
                content=full_text,
                manifest=manifest,
                dependencies=dep_manifests,
                scripts=consolidated_scripts,
                references=consolidated_refs,
                rules=consolidated_rules,
            )
            self._loaded_cache[cache_key] = bundle
            if not version:
                self._loaded_cache[norm_name] = bundle
            return bundle

    def load_many(self, skill_names: List[str]) -> str:
        """
        Consolidates instructions from multiple skills into a cohesive markdown block
        ready for direct JIT prompt injection.
        """
        sections = []
        for name in skill_names:
            inst = self.load(name)
            if inst:
                sections.append(str(inst))

        if not sections:
            return ""

        header = "## Active Dynamic Skills & Engineering Guidelines\n"
        return header + "\n\n---\n\n".join(sections)

    def unload(self, skill_name: str) -> None:
        """
        Unloads and removes a skill from the memory cache.
        """
        with self._lock:
            norm_name = skill_name.replace("_", "-").lower()
            keys_to_remove = [k for k in self._loaded_cache if k == norm_name or k.startswith(f"{norm_name}@")]
            for k in keys_to_remove:
                self._loaded_cache.pop(k, None)

    def get_catalog_summary(self, agent_name: Optional[str] = None) -> str:
        """
        Generates a concise markdown catalog summary of all available domains and skills.
        """
        with self._lock:
            if self._catalog_cache is not None:
                return self._catalog_cache

            categories = self.get_categories()
            total_count = len(self._skills)
            lines = [
                f"### Available Skills Catalog ({total_count} skills across {len(categories)} domains)",
                "| Domain | Key Skills | Capabilities |",
                "|---|---|---|",
            ]

            for cat in categories:
                cat_skills = self.list(category=cat)
                names_preview = ", ".join([s.name for s in cat_skills[:4]])
                if len(cat_skills) > 4:
                    names_preview += f" (+{len(cat_skills)-4} more)"
                sample_desc = cat_skills[0].description[:60] + "..." if cat_skills else ""
                lines.append(f"| **{cat.title()}** | `{names_preview}` | {sample_desc} |")

            summary = "\n".join(lines)
            self._catalog_cache = summary
            return summary

    def install_skill_from_skills_sh(self, skill_slug: str) -> bool:
        """
        Installs a skill from skills.sh via CLI ('npx skills add <skill_slug>') or local directory.
        """
        target_dir = (Path.cwd() / ".agents" / "skills" / skill_slug.split("/")[-1])
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            res = subprocess.run(
                ["npx", "skills", "add", skill_slug, "--dest", str(target_dir.parent)],
                capture_output=True,
                text=True,
                timeout=30,
                shell=(os.name == "nt"),
            )
            if res.returncode == 0:
                self._discover_all_skills()
                return True
        except Exception as e:
            logger.debug(f"skills.sh CLI install failed: {e}")

        # Fallback registration
        skill_manifest = self.get_skill(skill_slug)
        if skill_manifest:
            skill_md_path = target_dir / "SKILL.md"
            content = f"---\nname: {skill_manifest.name}\ndescription: {skill_manifest.description}\ncategory: {skill_manifest.category}\nversion: {skill_manifest.version}\n---\n\n{skill_manifest.system_instructions}\n"
            skill_md_path.write_text(content, encoding="utf-8")
            self._discover_all_skills()
            return True
        return False

    def install(self, source: str) -> bool:
        """Alias for install_skill_from_skills_sh."""
        return self.install_skill_from_skills_sh(source)


# Alias for backward compatibility
SkillRegistry = SkillManager



