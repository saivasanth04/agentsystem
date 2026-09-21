"""
Codebase Graph Engine: Resolved Call Graphs, Transitive Dependencies, and Impact Radius.
Provides high-precision multi-hop symbol resolution, caller/callee trees, and change blast radius analysis.
"""
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .cache import IncrementalCodeCache
from .parser import ParserRegistry
from .symbols import CallEdge, DependencyNode, ReferenceEdge, SymbolNode, is_module_import_match


IGNORE_DIRS = {
    "__pycache__", ".git", ".pytest_cache", "node_modules", ".venv", "venv",
    ".gemini", ".idea", ".vscode", "dist", "build", ".mypy_cache"
}


class CodebaseGraph:
    """
    AST-based Relational Code Knowledge Graph with Incremental Indexing.
    Enables resolved call graphs, transitive dependency resolution, and blast-radius impact analysis.
    """

    def __init__(
        self,
        workspace_dir: Path,
        parser_registry: Optional[ParserRegistry] = None,
        cache: Optional[IncrementalCodeCache] = None,
        cache_db_path: Optional[Path] = None,
    ):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.parser_registry = parser_registry or ParserRegistry()
        if cache is not None:
            self.cache = cache
        elif cache_db_path is not None:
            self.cache = IncrementalCodeCache(db_path=cache_db_path)
        else:
            default_db = self.workspace_dir / ".cache" / "codebase_index.db"
            self.cache = IncrementalCodeCache(db_path=default_db)

        # Symbol & Reference Indices
        self.symbols: Dict[str, List[SymbolNode]] = {}  # lowercase_name -> list of SymbolNode
        self.qualified_symbols: Dict[str, SymbolNode] = {}  # qualified_name -> SymbolNode
        self.file_to_symbols: Dict[str, List[SymbolNode]] = {}  # filepath -> list of SymbolNode
        self.references: Dict[str, List[ReferenceEdge]] = {}  # lowercase_name -> list of ReferenceEdge
        self.file_imports: Dict[str, Set[str]] = {}  # filepath -> set of imported names
        self.call_graph: Dict[str, List[CallEdge]] = {}  # caller_symbol -> list of CallEdge
        self.reverse_call_graph: Dict[str, List[CallEdge]] = {}  # callee_symbol -> list of CallEdge

        self.build_index()

    def build_index(self, files: Optional[List[str]] = None) -> Dict[str, Any]:
        """Scans workspace, parses symbols using polyglot parser with incremental cache, and builds graph edges."""
        if files is not None:
            indexed_count = 0
            for f_str in files:
                syms = self.reindex_file(f_str)
                if syms is not None:
                    indexed_count += 1
            return {
                "indexed_files": indexed_count,
                "total_symbols": sum(len(s) for s in self.file_to_symbols.values()),
                "total_calls": sum(len(edges) for edges in self.call_graph.values()),
                "success": True,
            }

        # Discover all eligible files
        target_files: List[Path] = []
        for p in self.workspace_dir.rglob("*"):
            if p.is_file() and not any(part in IGNORE_DIRS for part in p.parts):
                if self.parser_registry.is_supported(p.name):
                    target_files.append(p)

        active_rel_paths = {str(p.relative_to(self.workspace_dir)).replace("\\", "/") for p in target_files}

        # Prune deleted files from cache and in-memory structures
        self.cache.prune_deleted_files(active_rel_paths)
        deleted_in_memory = set(self.file_to_symbols.keys()) - active_rel_paths
        for d_fp in deleted_in_memory:
            self._remove_file_from_in_memory(d_fp)

        indexed_files = 0
        total_symbols = 0

        # Parse symbols and references with cache acceleration
        for file_path in target_files:
            rel_path = str(file_path.relative_to(self.workspace_dir)).replace("\\", "/")
            parser = self.parser_registry.get_parser(rel_path)
            if not parser:
                continue

            try:
                mtime = file_path.stat().st_mtime
                cached_meta = self.cache.get_file_metadata(rel_path)
                syms = None
                refs = None
                imps = None

                # Fast check: mtime matches cached mtime
                if cached_meta and cached_meta[0] == mtime:
                    cached_data = self.cache.get_cached_file(rel_path)
                    if cached_data:
                        syms, refs, imps = cached_data

                # Hash check if mtime changed or cache miss
                if syms is None:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    sha256 = self.cache.compute_sha256(content)
                    if cached_meta and cached_meta[1] == sha256:
                        cached_data = self.cache.get_cached_file(rel_path)
                        if cached_data:
                            syms, refs, imps = cached_data

                    if syms is None:
                        syms, refs, imps = parser.parse(content, rel_path)
                        self.cache.save_file(rel_path, mtime, sha256, syms, refs, imps)

                if rel_path in self.file_to_symbols:
                    self._remove_file_from_in_memory(rel_path)

                self.file_to_symbols[rel_path] = syms
                self.file_imports[rel_path] = imps
                indexed_files += 1
                total_symbols += len(syms)

                for sym in syms:
                    self.symbols.setdefault(sym.name.lower(), []).append(sym)
                    self.qualified_symbols[sym.qualified_name] = sym

                for ref in refs:
                    self.references.setdefault(ref.symbol_name.lower(), []).append(ref)

            except Exception:
                continue

        # Build Resolved Call Graph & Reverse Call Graph
        self._resolve_call_graph()

        return {
            "indexed_files": indexed_files,
            "total_symbols": total_symbols,
            "total_calls": sum(len(edges) for edges in self.call_graph.values()),
            "success": True,
        }

    def _remove_file_from_in_memory(self, rel_path: str) -> None:
        """Cleans up in-memory symbols, references, and imports for a file without touching SQLite."""
        old_syms = self.file_to_symbols.pop(rel_path, [])
        for sym in old_syms:
            sym_key = sym.name.lower()
            if sym_key in self.symbols:
                self.symbols[sym_key] = [s for s in self.symbols[sym_key] if s.filepath != rel_path]
                if not self.symbols[sym_key]:
                    del self.symbols[sym_key]
            self.qualified_symbols.pop(sym.qualified_name, None)
            self.call_graph.pop(sym.qualified_name, None)
            self.reverse_call_graph.pop(sym.qualified_name, None)

        self.file_imports.pop(rel_path, None)

        # Remove references originating from rel_path
        for sym_key, ref_list in list(self.references.items()):
            remaining = [r for r in ref_list if r.filepath != rel_path]
            if remaining:
                self.references[sym_key] = remaining
            else:
                self.references.pop(sym_key, None)

        # Clean edges in call_graph pointing to or from rel_path
        for caller_sym, edges in list(self.call_graph.items()):
            self.call_graph[caller_sym] = [e for e in edges if e.caller_filepath != rel_path and e.target_filepath != rel_path]
            if not self.call_graph[caller_sym]:
                del self.call_graph[caller_sym]

        for callee_sym, edges in list(self.reverse_call_graph.items()):
            self.reverse_call_graph[callee_sym] = [e for e in edges if e.caller_filepath != rel_path and e.target_filepath != rel_path]
            if not self.reverse_call_graph[callee_sym]:
                del self.reverse_call_graph[callee_sym]

    def invalidate_file(self, filepath: str) -> None:
        """Removes a file from in-memory indices and from the SQLite cache."""
        rel_path = str(filepath).replace("\\", "/").strip("/")
        self._remove_file_from_in_memory(rel_path)
        self.cache.delete_file(rel_path)

    def delete_file(self, filepath: str) -> None:
        """Removes a file upon deletion and re-resolves incident call graph edges."""
        rel_path = str(filepath).replace("\\", "/").strip("/")
        self.invalidate_file(rel_path)
        self._incremental_resolve_call_graph([rel_path])

    def reindex_file(self, filepath: str, content: Optional[str] = None) -> List[SymbolNode]:
        """Re-indexes a single file in-place and updates the cache and incident call graph edges."""
        rel_path = str(filepath).replace("\\", "/").strip("/")
        target_path = (self.workspace_dir / rel_path).resolve()

        if not target_path.exists() and content is None:
            self.delete_file(rel_path)
            return []

        parser = self.parser_registry.get_parser(rel_path)
        if not parser:
            return []

        try:
            if content is None:
                content = target_path.read_text(encoding="utf-8", errors="replace")
            mtime = target_path.stat().st_mtime if target_path.exists() else 0.0
            sha256 = self.cache.compute_sha256(content)

            syms, refs, imps = parser.parse(content, rel_path)
            self.cache.save_file(rel_path, mtime, sha256, syms, refs, imps)

            self._remove_file_from_in_memory(rel_path)

            self.file_to_symbols[rel_path] = syms
            self.file_imports[rel_path] = imps

            for sym in syms:
                self.symbols.setdefault(sym.name.lower(), []).append(sym)
                self.qualified_symbols[sym.qualified_name] = sym

            for ref in refs:
                self.references.setdefault(ref.symbol_name.lower(), []).append(ref)

            self._incremental_resolve_call_graph([rel_path])
            return syms
        except Exception:
            return []

    def _incremental_resolve_call_graph(self, affected_files: List[str]) -> None:
        """
        Re-resolves call graph edges only for callers in affected_files and files that
        import or call symbols from affected_files.
        """
        affected_set = {f.replace("\\", "/").strip("/") for f in affected_files}
        if not affected_set:
            return

        files_to_re_resolve: Set[str] = set(affected_set)

        for aff_fp in affected_set:
            for fp, imps in self.file_imports.items():
                if any(is_module_import_match(imp, aff_fp, fp) for imp in imps if imp):
                    files_to_re_resolve.add(fp)

        # For files to re-resolve, clear their caller entries in self.call_graph
        # and their appearances as callers in self.reverse_call_graph
        for fp in files_to_re_resolve:
            syms_in_file = self.file_to_symbols.get(fp, [])
            for sym in syms_in_file:
                old_edges = self.call_graph.pop(sym.qualified_name, [])
                for old_edge in old_edges:
                    if old_edge.callee_symbol and old_edge.callee_symbol in self.reverse_call_graph:
                        self.reverse_call_graph[old_edge.callee_symbol] = [
                            e for e in self.reverse_call_graph[old_edge.callee_symbol]
                            if e.caller_symbol != sym.qualified_name
                        ]
                        if not self.reverse_call_graph[old_edge.callee_symbol]:
                            del self.reverse_call_graph[old_edge.callee_symbol]

        # Re-resolve edges for symbols in files_to_re_resolve
        for filepath in files_to_re_resolve:
            syms = self.file_to_symbols.get(filepath, [])
            file_imp_set = self.file_imports.get(filepath, set())

            for caller in syms:
                if not caller.calls:
                    continue

                for callee_raw in caller.calls:
                    callee_name = callee_raw.split(".")[-1]
                    callee_candidates = self.symbols.get(callee_name.lower(), [])

                    resolved_target: Optional[SymbolNode] = None

                    # Strategy A: Same file local call
                    for cand in callee_candidates:
                        if cand.filepath == filepath:
                            if caller.parent and cand.parent == caller.parent:
                                resolved_target = cand
                                break
                            elif not cand.parent:
                                resolved_target = cand
                                break

                    # Strategy B: Imported call from another file
                    if not resolved_target:
                        for cand in callee_candidates:
                            mod_name = cand.filepath.replace("/", ".").replace("\\", ".").replace(".py", "")
                            if cand.name in file_imp_set or any(imp in mod_name for imp in file_imp_set):
                                resolved_target = cand
                                break

                    # Strategy C: Unique candidate in entire workspace
                    if not resolved_target and len(callee_candidates) == 1:
                        resolved_target = callee_candidates[0]

                    edge = CallEdge(
                        caller_symbol=caller.qualified_name,
                        callee_name=callee_raw,
                        caller_filepath=filepath,
                        line=caller.start_line,
                        callee_symbol=resolved_target.qualified_name if resolved_target else None,
                        target_filepath=resolved_target.filepath if resolved_target else None,
                        resolved=bool(resolved_target),
                    )

                    self.call_graph.setdefault(caller.qualified_name, []).append(edge)
                    if resolved_target:
                        self.reverse_call_graph.setdefault(resolved_target.qualified_name, []).append(edge)

    def _resolve_call_graph(self) -> None:
        """Resolves raw function/method calls to specific SymbolNodes across the codebase."""
        self.call_graph.clear()
        self.reverse_call_graph.clear()

        for filepath, syms in self.file_to_symbols.items():
            file_imp_set = self.file_imports.get(filepath, set())

            for caller in syms:
                if not caller.calls:
                    continue

                for callee_raw in caller.calls:
                    callee_name = callee_raw.split(".")[-1]  # e.g., 'helper' from 'self.helper' or 'mod.helper'
                    callee_candidates = self.symbols.get(callee_name.lower(), [])

                    resolved_target: Optional[SymbolNode] = None

                    # Strategy A: Same file local call
                    for cand in callee_candidates:
                        if cand.filepath == filepath:
                            if caller.parent and cand.parent == caller.parent:
                                resolved_target = cand
                                break
                            elif not cand.parent:
                                resolved_target = cand
                                break

                    # Strategy B: Imported call from another file
                    if not resolved_target:
                        for cand in callee_candidates:
                            mod_name = cand.filepath.replace("/", ".").replace("\\", ".").replace(".py", "")
                            if cand.name in file_imp_set or any(imp in mod_name for imp in file_imp_set):
                                resolved_target = cand
                                break

                    # Strategy C: Unique candidate in entire workspace
                    if not resolved_target and len(callee_candidates) == 1:
                        resolved_target = callee_candidates[0]

                    edge = CallEdge(
                        caller_symbol=caller.qualified_name,
                        callee_name=callee_raw,
                        caller_filepath=filepath,
                        line=caller.start_line,
                        callee_symbol=resolved_target.qualified_name if resolved_target else None,
                        target_filepath=resolved_target.filepath if resolved_target else None,
                        resolved=bool(resolved_target),
                    )

                    self.call_graph.setdefault(caller.qualified_name, []).append(edge)
                    if resolved_target:
                        self.reverse_call_graph.setdefault(resolved_target.qualified_name, []).append(edge)

    def find_symbol(self, symbol_name: str) -> Dict[str, Any]:
        """Locates definition locations, line ranges, signatures, and docstrings for a symbol."""
        q_lower = symbol_name.lower().strip()
        exact_matches = []
        fuzzy_matches = []

        for name_key, sym_list in self.symbols.items():
            if name_key == q_lower:
                exact_matches.extend([s.to_dict() for s in sym_list])
            elif q_lower in name_key:
                fuzzy_matches.extend([s.to_dict() for s in sym_list])

        results = exact_matches if exact_matches else fuzzy_matches[:20]
        return {
            "query": symbol_name,
            "total_matches": len(results),
            "exact_match": bool(exact_matches),
            "symbols": results,
            "success": True,
        }

    def find_references(self, symbol_name: str) -> Dict[str, Any]:
        """Finds all files and line locations where the symbol is imported, called, or used."""
        q_lower = symbol_name.lower().strip()
        refs = self.references.get(q_lower, [])

        return {
            "query": symbol_name,
            "total_references": len(refs),
            "references": [r.to_dict() for r in refs[:50]],
            "success": True,
        }

    def get_dependencies(self, target: str) -> Dict[str, Any]:
        """
        Retrieves upstream dependencies (what target depends on) and downstream dependents (what uses target).
        Target can be a file path or a symbol name. Backward-compatible with CodeGraphEngine.
        """
        clean_target = target.strip().replace("\\", "/")

        # Case 1: Target is a file path
        if clean_target in self.file_imports or any(clean_target in f for f in self.file_to_symbols):
            matched_file = next((f for f in self.file_to_symbols if clean_target in f), clean_target)
            direct_imports = sorted(list(self.file_imports.get(matched_file, [])))
            symbols_defined = [s.to_dict() for s in self.file_to_symbols.get(matched_file, [])]
            defined_sym_names = {s["name"].lower() for s in symbols_defined if "name" in s}

            # Find files that import this file or its symbols
            dependents = []
            for other_file, imports in self.file_imports.items():
                if other_file == matched_file:
                    continue
                if (
                    any(is_module_import_match(imp, matched_file, other_file) for imp in imports if imp)
                    or any(s in [i.lower() for i in imports] for s in defined_sym_names)
                ):
                    dependents.append(other_file)

            return {
                "target_type": "file",
                "filepath": matched_file,
                "imports": direct_imports,
                "defined_symbols": symbols_defined,
                "dependent_files": sorted(dependents),
                "success": True,
            }

        # Case 2: Target is a symbol
        sym_res = self.find_symbol(target)
        ref_res = self.find_references(target)

        return {
            "target_type": "symbol",
            "symbol": target,
            "definitions": sym_res.get("symbols", []),
            "references_and_callers": ref_res.get("references", []),
            "success": True,
        }

    def get_call_graph(self, target: str, max_depth: int = 2) -> Dict[str, Any]:
        """
        Traverses callers (upstream) and callees (downstream) for a symbol up to max_depth.
        """
        sym_res = self.find_symbol(target)
        sym_list = sym_res.get("symbols", [])
        if not sym_list:
            return {"target": target, "found": False, "callers": [], "callees": [], "success": False}

        primary_sym = sym_list[0]["qualified_name"]

        # 1. Downstream Callees (what does primary_sym call?)
        callees: List[Dict[str, Any]] = []
        visited_callees: Set[str] = {primary_sym}
        queue: deque = deque([(primary_sym, 1)])

        while queue:
            curr_sym, depth = queue.popleft()
            if depth > max_depth:
                continue

            for edge in self.call_graph.get(curr_sym, []):
                callees.append({
                    "caller": curr_sym,
                    "callee": edge.callee_symbol or edge.callee_name,
                    "target_filepath": edge.target_filepath,
                    "line": edge.line,
                    "depth": depth,
                    "resolved": edge.resolved,
                })
                if edge.callee_symbol and edge.callee_symbol not in visited_callees:
                    visited_callees.add(edge.callee_symbol)
                    queue.append((edge.callee_symbol, depth + 1))

        # 2. Upstream Callers (who calls primary_sym?)
        callers: List[Dict[str, Any]] = []
        visited_callers: Set[str] = {primary_sym}
        queue = deque([(primary_sym, 1)])

        while queue:
            curr_sym, depth = queue.popleft()
            if depth > max_depth:
                continue

            for edge in self.reverse_call_graph.get(curr_sym, []):
                callers.append({
                    "caller": edge.caller_symbol,
                    "callee": curr_sym,
                    "caller_filepath": edge.caller_filepath,
                    "line": edge.line,
                    "depth": depth,
                })
                if edge.caller_symbol and edge.caller_symbol not in visited_callers:
                    visited_callers.add(edge.caller_symbol)
                    queue.append((edge.caller_symbol, depth + 1))

        return {
            "target": target,
            "qualified_name": primary_sym,
            "found": True,
            "callers": callers,
            "callees": callees,
            "total_callers": len(callers),
            "total_callees": len(callees),
            "success": True,
        }

    def get_impact_radius(self, target: str, max_depth: int = 3) -> Dict[str, Any]:
        """
        Calculates the blast radius / impact analysis if target (file or symbol) is modified.
        Traverses both the transitive import dependency graph and the call hierarchy graph.
        """
        clean_target = target.strip().replace("\\", "/")
        affected_files: Set[str] = set()
        affected_symbols: Set[str] = set()
        visited_nodes: Set[str] = set()

        # Identify starting files and symbols
        initial_files: Set[str] = set()
        initial_symbols: Set[str] = set()

        if any(clean_target in f for f in self.file_to_symbols):
            matched = next(f for f in self.file_to_symbols if clean_target in f)
            initial_files.add(matched)
            affected_files.add(matched)
            for s in self.file_to_symbols.get(matched, []):
                initial_symbols.add(s.qualified_name)
        else:
            sym_res = self.find_symbol(clean_target)
            for s in sym_res.get("symbols", []):
                initial_symbols.add(s["qualified_name"])
                initial_files.add(s["filepath"])
                affected_files.add(s["filepath"])

        # 1. Transitive File Dependencies (Downstream consumers of these files)
        file_queue: deque = deque([(f, 1) for f in initial_files])
        visited_files: Set[str] = set(initial_files)
        while file_queue:
            curr_file, depth = file_queue.popleft()
            if depth > max_depth:
                continue

            deps = self.get_dependencies(curr_file)
            for df in deps.get("dependent_files", []):
                affected_files.add(df)
                if df not in visited_files:
                    visited_files.add(df)
                    file_queue.append((df, depth + 1))

        # 2. Transitive Caller Graph (Functions that call these symbols)
        sym_queue: deque = deque([(s, 1) for s in initial_symbols])
        visited_syms: Set[str] = set(initial_symbols)
        while sym_queue:
            curr_sym, depth = sym_queue.popleft()
            if depth > max_depth:
                continue

            for edge in self.reverse_call_graph.get(curr_sym, []):
                if edge.caller_symbol:
                    affected_symbols.add(edge.caller_symbol)
                if edge.caller_filepath:
                    affected_files.add(edge.caller_filepath)
                if edge.caller_symbol and edge.caller_symbol not in visited_syms:
                    visited_syms.add(edge.caller_symbol)
                    sym_queue.append((edge.caller_symbol, depth + 1))

        # 3. Categorize affected files (Implementation vs Test Suites)
        test_files = [f for f in affected_files if "test" in f.lower()]
        impl_files = [f for f in affected_files if "test" not in f.lower()]

        # Blast Radius Score (0 - 100)
        total_files = len(self.file_to_symbols) or 1
        blast_score = min(100.0, round((len(affected_files) / total_files) * 100.0 + len(affected_symbols) * 2.0, 1))

        return {
            "target": target,
            "blast_score": blast_score,
            "risk_level": "CRITICAL" if blast_score > 50 else ("HIGH" if blast_score > 25 else ("MEDIUM" if blast_score > 10 else "LOW")),
            "affected_files": sorted(list(affected_files)),
            "affected_symbols": sorted(list(affected_symbols)),
            "implementation_files_count": len(impl_files),
            "test_files_to_verify": sorted(test_files),
            "success": True,
        }

    def get_codebase_map(self, path: Optional[str] = None) -> str:
        """
        Generates a token-compact symbol outline of the codebase for efficient agent context.
        Backward-compatible with CodeGraphEngine.
        """
        lines = ["# Codebase Symbol Outline Map:"]
        filter_prefix = path.strip().replace("\\", "/") if path else ""

        for filepath in sorted(self.file_to_symbols.keys()):
            if filter_prefix and not filepath.startswith(filter_prefix):
                continue

            symbols = self.file_to_symbols[filepath]
            lines.append(f"\n📁 {filepath}:")
            if not symbols:
                lines.append("  (No top-level classes/functions)")
                continue

            for sym in symbols:
                indent = "    " if sym.parent else "  "
                kind_tag = f"[{sym.kind}]"
                doc = f" - {sym.docstring}" if sym.docstring else ""
                lines.append(f"{indent}• {kind_tag} {sym.name} (L{sym.start_line}-L{sym.end_line}){doc}")

        return "\n".join(lines)

    def close(self) -> None:
        """Closes cache database connections."""
        if hasattr(self, "cache") and self.cache:
            try:
                self.cache.close()
            except Exception:
                pass
