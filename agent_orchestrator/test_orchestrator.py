"""
Comprehensive Unit & Integration Test Suite for LangGraph Multi-Agent Runtime, Ready Tools, Skills, and MCPs.
"""
import unittest
from unittest.mock import MagicMock
from pathlib import Path
import tempfile
import shutil

from agent_orchestrator.config import OrchestratorConfig
from agent_orchestrator.state import OrchestratorState, ReviewVerdict, TaskStatus
from agent_orchestrator.orchestrator import TaskOrchestrator
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.mcp_client import MCPClientAdapter
from agent_orchestrator.registry.skill_registry import SkillRegistry


class TestUnifiedLangGraphOrchestrator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(root_dir=Path(self.temp_dir))
        self.cfg = OrchestratorConfig(
            max_replan_iterations=2,
            workspace_dir=Path(self.temp_dir)
        )
        self.mock_llm = MagicMock()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_understand_and_decompose(self):
        def mock_chat_json(messages, model=None, temperature=0.2):
            content = messages[0]["content"] if messages else ""
            if "Decomposer" in content or "DAG Planner" in content:
                return [
                    {"step_id": 1, "required_capabilities": ["planning"], "subtask_name": "Plan"},
                    {"step_id": 2, "required_capabilities": ["spec-writing"], "subtask_name": "Spec"},
                    {"step_id": 3, "required_capabilities": ["software-architecture"], "subtask_name": "Arch"},
                ]
            return {"proposed_endpoints": ["/cache"], "services": ["CacheService"]}

        self.mock_llm.chat_json.side_effect = mock_chat_json
        orchestrator = TaskOrchestrator(cfg=self.cfg, llm=self.mock_llm, workspace=self.workspace)
        state = OrchestratorState(user_request="Build an in-memory cache with TTL and LRU")

        understanding = orchestrator.understand_task(state)
        self.assertIn("Build an in-memory cache", understanding["core_goal"])

        decomposition = orchestrator.decompose_task(state)
        self.assertEqual(len(decomposition), 3)
        self.assertEqual(decomposition[0]["subtask_name"], "Plan")

    def test_agent_selection(self):
        orchestrator = TaskOrchestrator(cfg=self.cfg, llm=self.mock_llm, workspace=self.workspace)
        planner = orchestrator.select_agent("PLANNER")
        self.assertEqual(planner.name, "PLANNER")
        coder = orchestrator.select_agent("CODER")
        self.assertEqual(coder.name, "CODER")
        tester = orchestrator.select_agent("TESTER")
        self.assertEqual(tester.name, "TESTER")
        reviewer = orchestrator.select_agent("REVIEWER")
        self.assertEqual(reviewer.name, "REVIEWER")

    def test_ready_to_use_langchain_tools(self):
        tools = BuiltinToolRegistry(self.workspace)
        
        # Test write_file tool
        w_res = tools.call_tool("write_file", {"filepath": "test_script.py", "content": "print('hello from langchain tools')\n"})
        self.assertTrue(w_res.get("success"))

        # Test read_file tool
        r_res = tools.call_tool("read_file", {"filepath": "test_script.py"})
        self.assertTrue(r_res.get("success"))
        self.assertIn("hello from langchain tools", r_res.get("output", ""))

        # Test syntax_check tool
        s_res = tools.call_tool("syntax_check", {"filepath": "test_script.py"})
        self.assertTrue(s_res.get("success"))

        # Test shell tool
        cmd_res = tools.call_tool("run_command", {"command": "python test_script.py"})
        self.assertTrue(cmd_res.get("success"))

    def test_skill_registry_and_skills_sh(self):
        skill_reg = SkillRegistry()
        self.assertIsNotNone(skill_reg.get_skill("code-review-and-quality"))
        self.assertIsNotNone(skill_reg.get_skill("test-driven-development"))
        self.assertIsNotNone(skill_reg.get_skill("planning-and-task-breakdown"))

        matched = skill_reg.find_best_skills_for_task("Implement a Python class with test driven development")
        self.assertTrue(any(s.name == "test-driven-development" for s in matched))

    def test_mcp_client_adapter(self):
        mcp_adapter = MCPClientAdapter()
        mcp_adapter.register_mcp_tool(
            server_name="claude-flow",
            tool_name="terminal_execute",
            description="Run command in Claude Flow",
            input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
            handler=lambda args: {"output": f"Executed {args.get('command')}"}
        )
        schemas = mcp_adapter.get_tool_schemas()
        self.assertGreaterEqual(len(schemas), 1)
        res = mcp_adapter.call_tool("mcp_claude-flow_terminal_execute", {"command": "echo test"})
        self.assertTrue(res.get("success"))

    def test_langgraph_replan_cycle_on_reviewer_fail(self):
        plan_data = {"project_title": "Cache System", "phases": []}
        spec_data = {"feature_name": "Cache", "functional_requirements": []}
        arch_data = {"system_title": "Cache Arch", "component_structure": []}
        
        coder_data_1 = {
            "summary": "Initial code",
            "files": [{"filepath": "cache.py", "content": "def get_value(): return None"}]
        }
        tester_data_1 = {
            "test_strategy": "Unit tests",
            "test_files": [{"filepath": "test_cache.py", "content": "import unittest\nfrom cache import get_value\nclass TestC(unittest.TestCase):\n    def test_v(self): self.assertIsNotNone(get_value())"}]
        }
        reviewer_data_1 = {
            "verdict": "FAIL",
            "score_out_of_100": 40,
            "summary": "Test failed because get_value returns None",
            "issues": [{"severity": "CRITICAL", "component": "cache.py", "description": "get_value returns None"}],
            "target_agent_for_fix": "CODER",
            "remediation_plan": ["Update get_value to return valid string"]
        }
        diag_data = {
            "root_cause_summary": "get_value returns None instead of string",
            "failure_type": "ASSERTION_FAILURE",
            "affected_files": ["cache.py"],
            "suggested_remediation": ["Update get_value to return valid string"],
            "target_agent": "CODER",
        }

        coder_data_2 = {
            "summary": "Fixed code",
            "files": [{"filepath": "cache.py", "content": "def get_value(): return 'data'"}]
        }
        tester_data_2 = {
            "test_strategy": "Unit tests",
            "test_files": [{"filepath": "test_cache.py", "content": "import unittest\nfrom cache import get_value\nclass TestC(unittest.TestCase):\n    def test_v(self): self.assertEqual(get_value(), 'data')\nif __name__ == '__main__': unittest.main()"}]
        }
        reviewer_data_2 = {
            "verdict": "PASS",
            "score_out_of_100": 98,
            "summary": "All tests pass and requirements met.",
            "issues": [],
            "target_agent_for_fix": "CODER",
            "remediation_plan": []
        }

        call_counts = {"coder": 0, "tester": 0, "reviewer": 0}

        def mock_chat_json(messages, model=None, temperature=0.2):
            msg_str = str(messages)
            if "Multi-Domain" in msg_str or "multi-domain analysis" in msg_str.lower() or "domain analysis" in msg_str.lower():
                return {"core_goal": "Build a reliable caching system"}
            if "DAG Planner" in msg_str or "Task Decomposer" in msg_str:
                return [
                    {"task_id": "T-1", "required_capabilities": ["planning"], "objective": "Plan", "dependencies": []},
                    {"task_id": "T-2", "required_capabilities": ["spec-writing"], "objective": "Spec", "dependencies": ["T-1"]},
                    {"task_id": "T-3", "required_capabilities": ["software-architecture"], "objective": "Arch", "dependencies": ["T-2"]},
                    {"task_id": "T-4", "required_capabilities": ["code-generation"], "objective": "Code", "dependencies": ["T-3"]},
                    {"task_id": "T-5", "required_capabilities": ["testing"], "objective": "Test", "dependencies": ["T-4"]},
                ]
            sys_msg = messages[0].get("content", "") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else ""
            if "Root Cause Failure Diagnostician" in sys_msg or "Transaction Safety Auditor" in sys_msg:
                return diag_data
            if "You are the REVIEWER" in sys_msg or "Review Instructions" in sys_msg or "Conducts multi-axis code reviews" in sys_msg:
                call_counts["reviewer"] += 1
                return reviewer_data_2 if call_counts["reviewer"] > 1 else reviewer_data_1
            if "You are the TESTER" in sys_msg or "automated test generation" in sys_msg or "Authors comprehensive test suites" in msg_str:
                call_counts["tester"] += 1
                return tester_data_2 if call_counts["tester"] > 1 else tester_data_1
            if "You are the CODER" in sys_msg or "Role: Responsible for writing clean" in sys_msg or "Generates, inspects, and refactors working production code" in sys_msg:
                call_counts["coder"] += 1
                return coder_data_2 if call_counts["coder"] > 1 else coder_data_1
            if "You are the PLANNER" in sys_msg or "Role: Creates structured execution roadmaps" in sys_msg or "Role: Creates structured execution roadmaps" in msg_str:
                return plan_data
            if "You are the SPECIFICATION" in sys_msg or "Role: Formulates requirements" in sys_msg or "Role: Formulates requirements" in msg_str:
                return spec_data
            if "You are the ARCHITECTURE" in sys_msg or "Role: Designs modular system" in sys_msg or "Role: Designs modular system" in msg_str:
                return arch_data
            return {"summary": "Execution completed", "status": "COMPLETED"}

        self.mock_llm.chat_json.side_effect = mock_chat_json

        orchestrator = TaskOrchestrator(cfg=self.cfg, llm=self.mock_llm, workspace=self.workspace)
        state = orchestrator.run("Build a reliable caching system")

        self.assertEqual(state.status, TaskStatus.COMPLETED)
        self.assertEqual(state.verdict, ReviewVerdict.PASS)
        self.assertEqual(state.current_iteration, 1)
        self.assertEqual(len(state.replan_history), 1)
        self.assertIn("failure", state.replan_history[0].trigger_reason.lower())
        self.assertGreater(len(state.messages), 0, "State messages should be preserved across workflow")

    def test_regex_grep_tool_with_glob_and_regex(self):
        tools = BuiltinToolRegistry(self.workspace)
        tools.call_tool("write_file", {"filepath": "pkg/a.py", "content": "def calculate_sum(x, y):\n    return x + y\n"})
        tools.call_tool("write_file", {"filepath": "pkg/b.txt", "content": "calculate_sum in text file\n"})

        # Test regex pattern with glob filter
        res = tools.call_tool("regex_grep", {"pattern": r"def\s+calc.*\(", "file_glob": "*.py"})
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("count"), 1)
        self.assertEqual(res["matches"][0]["file"], "pkg/a.py")


if __name__ == "__main__":
    unittest.main()
