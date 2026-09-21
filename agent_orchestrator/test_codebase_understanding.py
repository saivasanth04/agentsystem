"""
Comprehensive Test Suite for Codebase Understanding Layer (Issue #37).
Validates polyglot parsing, resolved call/dependency graphs, blast radius, architecture discovery,
semantic/BM25 indexing, PageRank repo map, incremental caching, and backward compatibility.
"""
from pathlib import Path
import tempfile
import unittest

from agent_orchestrator.codebase.symbols import SymbolNode, ReferenceEdge, SymbolKind, ReferenceKind
from agent_orchestrator.codebase.parser import PythonASTParser, PolyglotRegexParser, ParserRegistry
from agent_orchestrator.codebase.graph import CodebaseGraph
from agent_orchestrator.codebase.architecture import ArchitectureAnalyzer, ArchitectureSummary
from agent_orchestrator.codebase.semantic_index import SemanticCodeIndex
from agent_orchestrator.codebase.repo_map import PageRankRepoMap
from agent_orchestrator.codebase.cache import IncrementalCodeCache
from agent_orchestrator.tools.code_graph import CodeGraphEngine
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.workspace import WorkspaceManager


class TestCodebaseUnderstanding(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self.temp_dir.name)
        self.workspace = WorkspaceManager(root_dir=self.workspace_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_python_ast_parser_symbols_and_imports(self):
        code = '''
"""Module docstring."""
import os
from math import sqrt as square_root

@decorator_one
class Calculator(BaseClass):
    """Calculator service."""
    
    @property
    def version(self) -> str:
        return "1.0"

    async def compute(self, x: int, y: int = 10) -> float:
        """Compute calculation."""
        res = square_root(x)
        return self.helper(res, y)

    def helper(self, val: float, mult: int) -> float:
        return val * mult
'''
        parser = PythonASTParser()
        symbols, references, imports = parser.parse(code, "calc.py")

        # Symbols
        sym_names = {s.name: s for s in symbols}
        self.assertIn("Calculator", sym_names)
        self.assertIn("version", sym_names)
        self.assertIn("compute", sym_names)
        self.assertIn("helper", sym_names)

        calc = sym_names["Calculator"]
        self.assertEqual(calc.kind, "class")
        self.assertEqual(calc.bases, ["BaseClass"])
        self.assertEqual(calc.decorators, ["decorator_one"])

        compute = sym_names["compute"]
        self.assertEqual(compute.kind, "method")
        self.assertEqual(compute.parent, "Calculator")
        self.assertIn("self", compute.parameters[0])
        self.assertEqual(compute.return_type, "float")
        self.assertIn("square_root", compute.calls)
        self.assertIn("helper", compute.calls)

        # Imports
        self.assertIn("os", imports)
        self.assertIn("math", imports)
        self.assertIn("square_root", imports)

        # References
        ref_symbols = {r.symbol_name for r in references}
        self.assertIn("BaseClass", ref_symbols)
        self.assertIn("square_root", ref_symbols)

    def test_polyglot_regex_parser(self):
        parser = PolyglotRegexParser()

        # 1. TypeScript
        ts_code = '''
import { Component, OnInit } from '@angular/core';
import axios from 'axios';

export class UserService extends BaseService {
  constructor() {}
}

export interface IUser {
  id: string;
  name: string;
}

export async function fetchUser(id: string): Promise<IUser> {
  return null;
}

const formatName = (user: IUser) => user.name;
'''
        ts_syms, ts_refs, ts_imps = parser.parse(ts_code, "user.service.ts")
        ts_names = {s.name: s for s in ts_syms}
        self.assertIn("UserService", ts_names)
        self.assertEqual(ts_names["UserService"].kind, "class")
        self.assertEqual(ts_names["UserService"].bases, ["BaseService"])
        self.assertIn("IUser", ts_names)
        self.assertEqual(ts_names["IUser"].kind, "interface")
        self.assertIn("fetchUser", ts_names)
        self.assertIn("formatName", ts_names)
        self.assertIn("@angular/core", ts_imps)
        self.assertIn("axios", ts_imps)

        # 2. Go
        go_code = '''
package main

import (
    "fmt"
    "net/http"
)

type Server struct {
    port int
}

type Router interface {
    Route()
}

func (s *Server) Start() error {
    return nil
}

func main() {
    fmt.Println("Running")
}
'''
        go_syms, go_refs, go_imps = parser.parse(go_code, "main.go")
        go_names = {s.name: s for s in go_syms}
        self.assertIn("Server", go_names)
        self.assertEqual(go_names["Server"].kind, "class")
        self.assertIn("Router", go_names)
        self.assertEqual(go_names["Router"].kind, "interface")
        self.assertIn("Start", go_names)
        self.assertEqual(go_names["Start"].kind, "method")
        self.assertEqual(go_names["Start"].parent, "Server")
        self.assertIn("main", go_names)

    def test_resolved_call_graph(self):
        # Create helper.py
        helper_path = self.workspace_dir / "helper.py"
        helper_path.write_text('''
def hash_string(val: str) -> str:
    """Hash a string value."""
    return val + "_hashed"
''', encoding="utf-8")

        # Create service.py
        service_path = self.workspace_dir / "service.py"
        service_path.write_text('''
from helper import hash_string

class Worker:
    def process(self, data: str) -> str:
        res = self.internal_step(data)
        return hash_string(res)

    def internal_step(self, item: str) -> str:
        return item.strip()
''', encoding="utf-8")

        graph = CodebaseGraph(self.workspace_dir)
        call_res = graph.get_call_graph("process")

        self.assertTrue(call_res["found"])
        self.assertGreaterEqual(call_res["total_callees"], 2)

        callees = [c["callee"] for c in call_res["callees"]]
        self.assertTrue(any("internal_step" in c for c in callees))
        self.assertTrue(any("hash_string" in c for c in callees))

        # Test reverse caller lookup
        rev_res = graph.get_call_graph("hash_string")
        self.assertTrue(rev_res["found"])
        self.assertGreaterEqual(rev_res["total_callers"], 1)
        callers = [c["caller"] for c in rev_res["callers"]]
        self.assertTrue(any("process" in c for c in callers))

    def test_transitive_dependency_graph_and_cycles(self):
        # Create A -> B -> C -> A (cycle)
        (self.workspace_dir / "module_c.py").write_text("import module_a\nC = 3", encoding="utf-8")
        (self.workspace_dir / "module_b.py").write_text("import module_c\nB = 2", encoding="utf-8")
        (self.workspace_dir / "module_a.py").write_text("import module_b\nA = 1", encoding="utf-8")

        graph = CodebaseGraph(self.workspace_dir)
        deps_a = graph.get_dependencies("module_a.py")

        self.assertTrue(deps_a["success"])
        self.assertIn("module_b", deps_a["imports"])
        self.assertIn("module_c.py", deps_a["dependent_files"])

    def test_impact_radius_calculation(self):
        (self.workspace_dir / "models.py").write_text('''
class UserModel:
    def __init__(self, name: str):
        self.name = name
''', encoding="utf-8")

        (self.workspace_dir / "service.py").write_text('''
from models import UserModel

class UserService:
    def get_user(self) -> UserModel:
        return UserModel("Alice")
''', encoding="utf-8")

        (self.workspace_dir / "test_service.py").write_text('''
from service import UserService

def test_user():
    s = UserService()
    assert s.get_user().name == "Alice"
''', encoding="utf-8")

        graph = CodebaseGraph(self.workspace_dir)
        impact = graph.get_impact_radius("models.py", max_depth=3)

        self.assertTrue(impact["success"])
        self.assertIn("models.py", impact["affected_files"])
        self.assertIn("service.py", impact["affected_files"])
        self.assertIn("test_service.py", impact["affected_files"])
        self.assertIn("test_service.py", impact["test_files_to_verify"])
        self.assertGreater(impact["blast_score"], 0.0)

    def test_architecture_analyzer(self):
        # Create a mock web app
        (self.workspace_dir / "main.py").write_text('''
from fastapi import FastAPI
from routes import api_router

app = FastAPI()
app.include_router(api_router)
''', encoding="utf-8")

        (self.workspace_dir / "routes.py").write_text('''
from fastapi import APIRouter

api_router = APIRouter()

@api_router.get("/users")
def list_users():
    return []
''', encoding="utf-8")

        (self.workspace_dir / "test_api.py").write_text('''
import pytest

def test_list():
    assert True
''', encoding="utf-8")

        analyzer = ArchitectureAnalyzer(self.workspace_dir)
        summary = analyzer.analyze()

        self.assertIn("main.py", summary.entrypoints)
        self.assertIn("FastAPI", summary.detected_frameworks)
        self.assertIn("Pytest", summary.test_frameworks)
        self.assertIn("tests", summary.layers)
        self.assertIn("test_api.py", summary.layers["tests"])
        self.assertTrue(any("list_users" in r for r in summary.api_routes))

        # Markdown output verification
        md = summary.to_markdown()
        self.assertIn("Architectural Summary", md)
        self.assertIn("FastAPI", md)
        self.assertIn("main.py", md)

    def test_semantic_code_index_bm25(self):
        (self.workspace_dir / "limiter.py").write_text('''
class TokenBucketRateLimiter:
    """Enforces token bucket traffic rate limiting and request throttling."""
    def consume_token(self, count: int = 1) -> bool:
        """Consumes a capacity token if available."""
        return True
''', encoding="utf-8")

        (self.workspace_dir / "auth.py").write_text('''
class SessionAuthenticator:
    """Validates user sessions, bearer tokens, and JWT credentials."""
    def verify_token(self, token_str: str) -> bool:
        """Verifies cryptographic token signature."""
        return True
''', encoding="utf-8")

        graph = CodebaseGraph(self.workspace_dir)
        index = SemanticCodeIndex(graph)

        # Test tokenization
        tokens = SemanticCodeIndex.tokenize("TokenBucketRateLimiter")
        self.assertIn("token", tokens)
        self.assertIn("bucket", tokens)
        self.assertIn("rate", tokens)
        self.assertIn("limiter", tokens)

        # Search for rate limiting
        results = index.search("token bucket rate limiting", top_k=2)
        self.assertGreater(len(results), 0)
        top_match = results[0]
        self.assertEqual(top_match["symbol_name"], "TokenBucketRateLimiter")
        self.assertGreater(top_match["score"], 0.0)

        # Search for session authentication
        auth_results = index.search("JWT bearer authentication credentials", top_k=2)
        self.assertGreater(len(auth_results), 0)
        self.assertEqual(auth_results[0]["symbol_name"], "SessionAuthenticator")

    def test_pagerank_repo_map_budget(self):
        (self.workspace_dir / "core.py").write_text("class CoreEngine:\n    def run(self): pass", encoding="utf-8")
        (self.workspace_dir / "client.py").write_text("from core import CoreEngine\nclass Client:\n    pass", encoding="utf-8")

        graph = CodebaseGraph(self.workspace_dir)
        repo_map = PageRankRepoMap(graph)
        scores = repo_map.compute_pagerank()

        self.assertGreater(len(scores), 0)
        total_score = sum(scores.values())
        self.assertAlmostEqual(total_score, 1.0, places=3)

        outline = repo_map.generate_repo_map(max_tokens=500)
        self.assertIn("Centrality-Ranked Codebase Map", outline)
        self.assertIn("core.py", outline)

    def test_incremental_code_cache(self):
        cache = IncrementalCodeCache(Path(":memory:"))
        sym = SymbolNode(name="TestClass", kind="class", filepath="test.py", start_line=1, end_line=10)
        ref = ReferenceEdge(symbol_name="TestClass", filepath="test.py", line=5, kind="import")
        imports = {"math", "os"}

        cache.save_file("test.py", mtime=123.45, sha256="abc123hash", symbols=[sym], references=[ref], imports=imports)

        # Check not changed
        self.assertFalse(cache.is_file_changed("test.py", current_mtime=123.45, current_sha256="abc123hash"))
        # Check changed when hash differs
        self.assertTrue(cache.is_file_changed("test.py", current_mtime=124.0, current_sha256="different_hash"))

        # Retrieve cached data
        cached = cache.get_cached_file("test.py")
        self.assertIsNotNone(cached)
        syms_out, refs_out, imps_out = cached
        self.assertEqual(len(syms_out), 1)
        self.assertEqual(syms_out[0].name, "TestClass")
        self.assertEqual(len(refs_out), 1)
        self.assertIn("math", imps_out)
        cache.close()

    def test_builtin_tools_codebase_understanding(self):
        (self.workspace_dir / "app.py").write_text('''
class Application:
    """Core application entrypoint."""
    def start(self):
        pass
''', encoding="utf-8")

        registry = BuiltinToolRegistry(workspace=self.workspace)

        # 1. semantic_code_search
        sem_res = registry._semantic_code_search(query="application entrypoint", top_k=2)
        self.assertTrue(sem_res["success"])
        self.assertGreater(sem_res["count"], 0)

        # 2. get_call_graph
        cg_res = registry._get_call_graph(target="start", max_depth=2)
        self.assertTrue(cg_res["success"])

        # 3. get_impact_radius
        imp_res = registry._get_impact_radius(target="app.py", max_depth=2)
        self.assertTrue(imp_res["success"])
        self.assertIn("app.py", imp_res["affected_files"])

        # 4. get_architecture_summary
        arch_res = registry._get_architecture_summary()
        self.assertTrue(arch_res["success"])
        self.assertIn("summary", arch_res)
        self.assertIn("markdown", arch_res)

    def test_backward_compatibility_code_graph_engine(self):
        (self.workspace_dir / "service.py").write_text('''
class DataService:
    """Handles data processing."""
    def process(self):
        return True
''', encoding="utf-8")

        engine = CodeGraphEngine(self.workspace_dir)

        sym_res = engine.find_symbol("DataService")
        self.assertTrue(sym_res["success"])
        self.assertEqual(sym_res["symbols"][0]["name"], "DataService")

        deps = engine.get_dependencies("service.py")
        self.assertTrue(deps["success"])

        cmap = engine.get_codebase_map()
        self.assertIn("DataService", cmap)


if __name__ == "__main__":
    unittest.main()
