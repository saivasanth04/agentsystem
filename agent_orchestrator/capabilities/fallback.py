"""
Multi-Tier Capability Fallback Engine.
Provides graceful, resilient capability degradation across the fidelity hierarchy:
Tier 1: Graft       (Repo wiring graph, caller hierarchy, blast radius, repo map)
Tier 2: CBM         (In-process CodebaseMemory AST knowledge graph & symbol index)
Tier 3: ripgrep     (High-speed regex/literal text pattern matching)
Tier 4: tree-sitter (Concrete syntax tree structural symbol parsing)
Tier 5: filesystem  (Direct recursive directory walk & line scanning - 100% baseline)
"""
import abc
import ast
from enum import IntEnum
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("capabilities.fallback")


class CapabilityTier(IntEnum):
    """Fidelity tiers for codebase intelligence capabilities."""
    TIER_1_GRAFT = 1
    TIER_2_CBM = 2
    TIER_3_RIPGREP = 3
    TIER_4_TREE_SITTER = 4
    TIER_5_FILESYSTEM = 5

    @property
    def label(self) -> str:
        labels = {
            CapabilityTier.TIER_1_GRAFT: "Graft (Wiring Graph)",
            CapabilityTier.TIER_2_CBM: "CBM (AST Knowledge Graph)",
            CapabilityTier.TIER_3_RIPGREP: "Ripgrep (Lexical Search)",
            CapabilityTier.TIER_4_TREE_SITTER: "Tree-sitter (Structural Syntax)",
            CapabilityTier.TIER_5_FILESYSTEM: "Filesystem (Raw Traversal)",
        }
        return labels.get(self, "Unknown Tier")


class ProviderUnavailableError(Exception):
    """Raised when a capability provider is uninstalled, disabled, or fails liveness probes."""
    pass


class CapabilityProvider(abc.ABC):
    """Abstract interface for a capability provider in the fallback ladder."""

    def __init__(self, name: str, tier: CapabilityTier, enabled: bool = True):
        self.name = name
        self.tier = tier
        self.enabled = enabled

    @abc.abstractmethod
    def is_available(self, workspace_dir: Path) -> bool:
        """Check if this provider is installed and ready to service queries in workspace."""
        pass

    @abc.abstractmethod
    def find_symbols(self, query: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        """Locate function/class symbols matching query."""
        pass

    @abc.abstractmethod
    def find_references(self, symbol_name: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        """Find callers and references for a given symbol."""
        pass

    @abc.abstractmethod
    def search_code(self, pattern: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        """Search code contents matching pattern."""
        pass

    @abc.abstractmethod
    def get_repo_map(self, workspace_dir: Path, **kwargs) -> Dict[str, Any]:
        """Generate high-level architectural map / orientation of the repository."""
        pass


class GraftProvider(CapabilityProvider):
    """
    Tier 1: Graft Repository Context Graph Provider.
    Queries deep wiring graph, caller hierarchies, blast radius, and repo map cards.
    """

    def __init__(self, graft_cmd: Optional[str] = None, enabled: bool = True):
        super().__init__(name="graft", tier=CapabilityTier.TIER_1_GRAFT, enabled=enabled)
        self.graft_cmd = graft_cmd or self._detect_graft_cmd()

    def _detect_graft_cmd(self) -> Optional[str]:
        cmd = shutil.which("graft")
        if cmd:
            return cmd
        # Windows npm fallback
        npm_path = Path(os.environ.get("APPDATA", "")) / "npm" / "graft.ps1"
        if npm_path.exists():
            return str(npm_path)
        npm_cmd = Path(os.environ.get("APPDATA", "")) / "npm" / "graft.cmd"
        if npm_cmd.exists():
            return str(npm_cmd)
        return None

    def is_available(self, workspace_dir: Path) -> bool:
        if not self.enabled or not self.graft_cmd:
            return False
        # Check if graft executable responds
        try:
            res = subprocess.run(
                [self.graft_cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=2.0,
                shell=True,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _run_graft(self, args: List[str], workspace_dir: Path) -> str:
        if not self.graft_cmd:
            raise ProviderUnavailableError("Graft command is not installed or configured.")
        try:
            cmd = [self.graft_cmd] + args + [str(workspace_dir)]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10.0,
                cwd=str(workspace_dir),
                shell=True,
            )
            if res.returncode != 0:
                raise RuntimeError(f"Graft exited with code {res.returncode}: {res.stderr}")
            return res.stdout
        except Exception as e:
            raise ProviderUnavailableError(f"Graft execution failed: {e}")

    def find_symbols(self, query: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        out = self._run_graft(["ask", query], workspace_dir)
        results = []
        for line in out.splitlines():
            line = line.strip()
            if line:
                results.append({"symbol": query, "output": line, "provider": self.name})
        return results

    def find_references(self, symbol_name: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        out = self._run_graft(["callers", symbol_name], workspace_dir)
        results = []
        for line in out.splitlines():
            line = line.strip()
            if line:
                results.append({"symbol": symbol_name, "caller": line, "provider": self.name})
        return results

    def search_code(self, pattern: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        out = self._run_graft(["grep", pattern], workspace_dir)
        results = []
        for line in out.splitlines():
            line = line.strip()
            if line:
                results.append({"match": line, "provider": self.name})
        return results

    def get_repo_map(self, workspace_dir: Path, **kwargs) -> Dict[str, Any]:
        out = self._run_graft(["map"], workspace_dir)
        return {"repo_map": out, "tier": self.tier.value, "provider": self.name}


class CBMProvider(CapabilityProvider):
    """
    Tier 2: Codebase Memory (CBM) AST Knowledge Graph Provider.
    Queries in-process AST symbol index, layer communities, and symbol call graphs.
    """

    def __init__(self, cbm_instance: Optional[Any] = None, enabled: bool = True):
        super().__init__(name="cbm", tier=CapabilityTier.TIER_2_CBM, enabled=enabled)
        self.cbm = cbm_instance

    def is_available(self, workspace_dir: Path) -> bool:
        if not self.enabled:
            return False
        if self.cbm is not None:
            return True
        # Check if workspace has code graph capability
        return workspace_dir.exists()

    def _get_cbm(self, workspace_dir: Path) -> Any:
        if self.cbm is not None:
            return self.cbm
        try:
            from ..codebase.cbm import CodebaseMemory
            self.cbm = CodebaseMemory(workspace_dir=workspace_dir)
            return self.cbm
        except Exception as e:
            raise ProviderUnavailableError(f"CBM instance unavailable: {e}")

    def find_symbols(self, query: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        cbm = self._get_cbm(workspace_dir)
        q_norm = query.lower().strip()
        results = []

        # Query CBM nodes and code_graph symbols
        if hasattr(cbm, "nodes") and cbm.nodes:
            for node_id, node in cbm.nodes.items():
                node_label = getattr(node, "label", getattr(node, "name", ""))
                if q_norm in node_label.lower():
                    results.append({
                        "name": node_label,
                        "label": node_label,
                        "kind": getattr(node, "kind", "symbol"),
                        "type": getattr(node, "kind", "symbol"),
                        "filepath": getattr(node, "filepath", ""),
                        "line": getattr(node, "line", 1),
                        "provider": self.name,
                    })

        if not results and hasattr(cbm, "code_graph") and hasattr(cbm.code_graph, "symbols"):
            for sym_name, sym_info in cbm.code_graph.symbols.items():
                if q_norm in sym_name.lower():
                    results.append({
                        "name": sym_name,
                        "label": sym_name,
                        "kind": "symbol",
                        "details": str(sym_info),
                        "provider": self.name,
                    })

        return results

    def find_references(self, symbol_name: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        cbm = self._get_cbm(workspace_dir)
        results = []
        sym_norm = symbol_name.lower().strip()

        if hasattr(cbm, "reverse_adj_list"):
            for target, incoming in cbm.reverse_adj_list.items():
                if sym_norm in target.lower():
                    for src, rel, weight in incoming:
                        results.append({
                            "symbol": symbol_name,
                            "caller": src,
                            "relation": rel,
                            "weight": weight,
                            "provider": self.name,
                        })

        if not results and hasattr(cbm, "find_related_symbols"):
            try:
                related = cbm.find_related_symbols(symbol_name, max_hops=2)
                for r in related:
                    results.append({"symbol": symbol_name, "related": str(r), "provider": self.name})
            except Exception:
                pass

        return results

    def search_code(self, pattern: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        cbm = self._get_cbm(workspace_dir)
        pat_norm = pattern.lower().strip()
        results = []

        if hasattr(cbm, "nodes"):
            for node_id, node in cbm.nodes.items():
                node_label = getattr(node, "label", getattr(node, "name", ""))
                doc = getattr(node, "docstring", "")
                sig = getattr(node, "signature", "")
                fp = getattr(node, "filepath", "")
                text_to_search = f"{node_label} {doc} {fp} {sig}".lower()
                if pat_norm in text_to_search:
                    results.append({
                        "name": node_label,
                        "filepath": fp,
                        "line": getattr(node, "line", 1),
                        "provider": self.name,
                    })
        return results

    def get_repo_map(self, workspace_dir: Path, **kwargs) -> Dict[str, Any]:
        cbm = self._get_cbm(workspace_dir)
        layers = {}
        if hasattr(cbm, "arch_analyzer") and cbm.arch_analyzer:
            try:
                summary = cbm.arch_analyzer.analyze()
                layers = summary.layers if hasattr(summary, "layers") else {}
            except Exception:
                pass

        total_nodes = len(cbm.nodes) if hasattr(cbm, "nodes") else 0
        total_edges = len(cbm.edges) if hasattr(cbm, "edges") else 0
        modules = []
        if hasattr(cbm, "code_graph") and hasattr(cbm.code_graph, "file_to_symbols"):
            modules = list(cbm.code_graph.file_to_symbols.keys())

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "layers": layers,
            "modules": modules,
            "tier": self.tier.value,
            "provider": self.name,
        }


class RipgrepProvider(CapabilityProvider):
    """
    Tier 3: Ripgrep / Optimized Lexical Search Provider.
    High-speed regex and literal text pattern search across files.
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name="ripgrep", tier=CapabilityTier.TIER_3_RIPGREP, enabled=enabled)
        self.rg_cmd = shutil.which("rg")

    def is_available(self, workspace_dir: Path) -> bool:
        if not self.enabled:
            return False
        return workspace_dir.exists()

    def search_code(self, pattern: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        # Fast path 1: rg binary
        if self.rg_cmd:
            try:
                cmd = [self.rg_cmd, "-n", "-e", pattern, "."]
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    cwd=str(workspace_dir),
                )
                if res.returncode in (0, 1):
                    results = []
                    for line in res.stdout.splitlines():
                        parts = line.split(":", 2)
                        if len(parts) >= 3:
                            results.append({
                                "filepath": parts[0],
                                "line": int(parts[1]) if parts[1].isdigit() else 1,
                                "content": parts[2],
                                "provider": self.name,
                            })
                    return results
            except Exception:
                pass

        # Fast path 2: In-process regex walk
        results = []
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            rx = re.compile(re.escape(pattern), re.IGNORECASE)

        for root, _, files in os.walk(workspace_dir):
            for file in files:
                if file.endswith((".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".md", ".yaml", ".yml", ".toml")):
                    full_p = Path(root) / file
                    try:
                        rel_p = str(full_p.relative_to(workspace_dir)).replace("\\", "/")
                        lines = full_p.read_text(encoding="utf-8", errors="replace").splitlines()
                        for i, l in enumerate(lines, 1):
                            if rx.search(l):
                                results.append({
                                    "filepath": rel_p,
                                    "line": i,
                                    "content": l.strip(),
                                    "provider": self.name,
                                })
                                if len(results) >= 100:
                                    return results
                    except Exception:
                        continue
        return results

    def find_symbols(self, query: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        pat = rf"^\s*(class|def|function|fn|const|let|var)\s+({re.escape(query)}[a-zA-Z0-9_]*)"
        matches = self.search_code(pat, workspace_dir)
        results = []
        for m in matches:
            content = m.get("content", "")
            match = re.search(pat, content)
            sym_name = match.group(2) if match else query
            sym_kind = match.group(1) if match else "symbol"
            results.append({
                "name": sym_name,
                "label": sym_name,
                "kind": sym_kind,
                "type": sym_kind,
                "filepath": m.get("filepath", ""),
                "line": m.get("line", 1),
                "content": content,
                "provider": self.name,
            })
        return results

    def find_references(self, symbol_name: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        pat = rf"\b{re.escape(symbol_name)}\b"
        return self.search_code(pat, workspace_dir)

    def get_repo_map(self, workspace_dir: Path, **kwargs) -> Dict[str, Any]:
        # Fast regex discovery of all definitions
        symbols = self.search_code(r"^\s*(class|def)\s+[a-zA-Z0-9_]+", workspace_dir)
        return {
            "total_symbols": len(symbols),
            "sample_symbols": symbols[:20],
            "tier": self.tier.value,
            "provider": self.name,
        }


class TreeSitterProvider(CapabilityProvider):
    """
    Tier 4: Tree-sitter Structural Syntax Parser.
    Extracts class/function definitions and syntax AST nodes.
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name="tree-sitter", tier=CapabilityTier.TIER_4_TREE_SITTER, enabled=enabled)

    def is_available(self, workspace_dir: Path) -> bool:
        if not self.enabled:
            return False
        try:
            import tree_sitter  # noqa: F401
            return True
        except ImportError:
            return False

    def _parse_python_ast(self, filepath: Path, code: str) -> List[Dict[str, Any]]:
        symbols = []
        try:
            tree = ast.parse(code, filename=str(filepath))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append({
                        "name": node.name,
                        "kind": "function",
                        "type": "function",
                        "line": node.lineno,
                        "docstring": ast.get_docstring(node) or "",
                    })
                elif isinstance(node, ast.ClassDef):
                    symbols.append({
                        "name": node.name,
                        "kind": "class",
                        "type": "class",
                        "line": node.lineno,
                        "docstring": ast.get_docstring(node) or "",
                    })
        except Exception:
            pass
        return symbols

    def find_symbols(self, query: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        q_norm = query.lower().strip()
        results = []
        for root, _, files in os.walk(workspace_dir):
            for file in files:
                if file.endswith(".py"):
                    full_p = Path(root) / file
                    try:
                        content = full_p.read_text(encoding="utf-8", errors="replace")
                        rel_p = str(full_p.relative_to(workspace_dir)).replace("\\", "/")
                        ast_syms = self._parse_python_ast(full_p, content)
                        for s in ast_syms:
                            if q_norm in s["name"].lower():
                                results.append({
                                    "name": s["name"],
                                    "kind": s.get("kind", "symbol"),
                                    "type": s["type"],
                                    "filepath": rel_p,
                                    "line": s["line"],
                                    "docstring": s["docstring"],
                                    "provider": self.name,
                                })
                    except Exception:
                        continue
        return results

    def find_references(self, symbol_name: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        # Structural identifier reference matching
        results = []
        for root, _, files in os.walk(workspace_dir):
            for file in files:
                if file.endswith(".py"):
                    full_p = Path(root) / file
                    try:
                        content = full_p.read_text(encoding="utf-8", errors="replace")
                        rel_p = str(full_p.relative_to(workspace_dir)).replace("\\", "/")
                        tree = ast.parse(content, filename=str(full_p))
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Name) and node.id == symbol_name:
                                results.append({
                                    "symbol": symbol_name,
                                    "filepath": rel_p,
                                    "line": node.lineno,
                                    "provider": self.name,
                                })
                    except Exception:
                        continue
        return results

    def search_code(self, pattern: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        return self.find_symbols(pattern, workspace_dir, **kwargs)

    def get_repo_map(self, workspace_dir: Path, **kwargs) -> Dict[str, Any]:
        all_syms = []
        modules = set()
        for root, _, files in os.walk(workspace_dir):
            for file in files:
                if file.endswith(".py"):
                    full_p = Path(root) / file
                    try:
                        content = full_p.read_text(encoding="utf-8", errors="replace")
                        rel_p = str(full_p.relative_to(workspace_dir)).replace("\\", "/")
                        modules.add(rel_p)
                        syms = self._parse_python_ast(full_p, content)
                        for s in syms:
                            s["filepath"] = rel_p
                            all_syms.append(s)
                    except Exception:
                        continue
        return {
            "total_symbols": len(all_syms),
            "symbols": all_syms[:30],
            "modules": list(modules),
            "tier": self.tier.value,
            "provider": self.name,
        }


class FilesystemProvider(CapabilityProvider):
    """
    Tier 5: Raw Filesystem Provider (100% baseline availability).
    Linear file reading and directory traversal without external dependencies.
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name="filesystem", tier=CapabilityTier.TIER_5_FILESYSTEM, enabled=enabled)

    def is_available(self, workspace_dir: Path) -> bool:
        return workspace_dir.exists()

    def find_symbols(self, query: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        raw_matches = self.search_code(query, workspace_dir, **kwargs)
        results = []
        for m in raw_matches:
            results.append({
                "name": query,
                "label": query,
                "kind": "symbol",
                "type": "symbol",
                "filepath": m.get("filepath", ""),
                "line": m.get("line", 1),
                "content": m.get("content", ""),
                "provider": self.name,
            })
        return results

    def find_references(self, symbol_name: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        return self.search_code(symbol_name, workspace_dir, **kwargs)

    def search_code(self, pattern: str, workspace_dir: Path, **kwargs) -> List[Dict[str, Any]]:
        results = []
        pat_lower = pattern.lower().strip()
        for root, _, files in os.walk(workspace_dir):
            for f in files:
                full_p = Path(root) / f
                try:
                    rel_p = str(full_p.relative_to(workspace_dir)).replace("\\", "/")
                    content = full_p.read_text(encoding="utf-8", errors="replace")
                    for idx, line in enumerate(content.splitlines(), 1):
                        if pat_lower in line.lower():
                            results.append({
                                "filepath": rel_p,
                                "line": idx,
                                "content": line.strip(),
                                "provider": self.name,
                            })
                            if len(results) >= 50:
                                return results
                except Exception:
                    continue
        return results

    def get_repo_map(self, workspace_dir: Path, **kwargs) -> Dict[str, Any]:
        file_tree = []
        for root, dirs, files in os.walk(workspace_dir):
            rel_root = str(Path(root).relative_to(workspace_dir)).replace("\\", "/")
            if rel_root != ".":
                file_tree.append(f"{rel_root}/")
            for f in files:
                file_tree.append(f"{rel_root}/{f}" if rel_root != "." else f)
            if len(file_tree) >= 100:
                break
        return {
            "total_entries": len(file_tree),
            "files": file_tree,
            "tier": self.tier.value,
            "provider": self.name,
        }


class CapabilityExecutionResult:
    """Standard result envelope emitted by CapabilityFallbackEngine."""

    def __init__(
        self,
        success: bool,
        data: Any,
        active_tier: CapabilityTier,
        provider_name: str,
        degraded: bool = False,
        degraded_reason: Optional[str] = None,
        cascade_path: Optional[List[str]] = None,
        duration_ms: float = 0.0,
    ):
        self.success = success
        self.data = data
        self.active_tier = active_tier
        self.provider_name = provider_name
        self.degraded = degraded
        self.degraded_reason = degraded_reason
        self.cascade_path = cascade_path or []
        self.duration_ms = duration_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "active_tier": self.active_tier.value,
            "active_tier_label": self.active_tier.label,
            "provider": self.provider_name,
            "provider_name": self.provider_name,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "cascade_path": self.cascade_path,
            "duration_ms": round(self.duration_ms, 2),
        }


class CapabilityFallbackEngine:
    """
    Central Capability Fallback Engine managing multi-tier cascading resolution.
    Cascades down the fidelity ladder:
    Graft (Tier 1) -> CBM (Tier 2) -> ripgrep (Tier 3) -> tree-sitter (Tier 4) -> filesystem (Tier 5).
    """

    def __init__(
        self,
        workspace_dir: Optional[Path] = None,
        providers: Optional[List[CapabilityProvider]] = None,
    ):
        self.workspace_dir = Path(workspace_dir or Path.cwd()).resolve()
        self.providers: List[CapabilityProvider] = providers or [
            GraftProvider(),
            CBMProvider(),
            RipgrepProvider(),
            TreeSitterProvider(),
            FilesystemProvider(),
        ]
        # Sort providers by tier ascending (1 is highest fidelity)
        self.providers.sort(key=lambda p: p.tier.value)

    def get_provider(self, name: str) -> Optional[CapabilityProvider]:
        for p in self.providers:
            if p.name.lower() == name.lower():
                return p
        return None

    def execute(
        self,
        operation: str,
        query: str,
        workspace_dir: Optional[Path] = None,
        **kwargs,
    ) -> CapabilityExecutionResult:
        """
        Execute an operation against the capability ladder with automatic cascading.
        Operations: 'find_symbols', 'find_references', 'search_code', 'get_repo_map'.
        """
        target_ws = Path(workspace_dir or self.workspace_dir).resolve()
        t_start = time.time()
        cascade_path: List[str] = []
        last_error: Optional[str] = None
        degraded = False
        degraded_reason = None

        for provider in self.providers:
            cascade_path.append(provider.name)

            # Check availability
            if not provider.is_available(target_ws):
                logger.info(f"Capability provider '{provider.name}' (Tier {provider.tier.value}) is UNAVAILABLE. Cascading...")
                degraded = True
                if not degraded_reason:
                    degraded_reason = f"{provider.name} unavailable"
                continue

            # Attempt execution
            try:
                op_fn = getattr(provider, operation, None)
                if not op_fn:
                    raise AttributeError(f"Provider '{provider.name}' does not implement '{operation}'")

                result_data = op_fn(query, target_ws, **kwargs) if operation != "get_repo_map" else op_fn(target_ws, **kwargs)

                duration = (time.time() - t_start) * 1000.0
                return CapabilityExecutionResult(
                    success=True,
                    data=result_data,
                    active_tier=provider.tier,
                    provider_name=provider.name,
                    degraded=degraded,
                    degraded_reason=degraded_reason,
                    cascade_path=cascade_path,
                    duration_ms=duration,
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Provider '{provider.name}' failed during '{operation}': {e}. Cascading down..."
                )
                degraded = True
                if not degraded_reason:
                    degraded_reason = f"{provider.name} failed: {e}"

        # If all providers exhausted (should never happen because filesystem is baseline)
        duration = (time.time() - t_start) * 1000.0
        return CapabilityExecutionResult(
            success=False,
            data=None,
            active_tier=CapabilityTier.TIER_5_FILESYSTEM,
            provider_name="none",
            degraded=True,
            degraded_reason=f"All capability providers exhausted. Last error: {last_error}",
            cascade_path=cascade_path,
            duration_ms=duration,
        )
