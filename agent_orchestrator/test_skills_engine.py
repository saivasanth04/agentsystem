"""
Test Suite for Dynamic Skills Architecture and Execution Engine.
"""
import os
import tempfile
import unittest
from pathlib import Path

from unittest.mock import MagicMock

from agent_orchestrator.registry.skill_registry import SkillRegistry, SkillManifest, SkillExecutionEngine
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.tools.unified_dispatcher import UnifiedToolDispatcher
from agent_orchestrator.agents.coder import CoderAgent


class TestSkillsEngine(unittest.TestCase):

    def setUp(self):
        self.registry = SkillRegistry()
        self.workspace = WorkspaceManager()
        self.builtin_tools = BuiltinToolRegistry(self.workspace, skill_registry=self.registry)
        self.dispatcher = UnifiedToolDispatcher(self.builtin_tools)

    def test_discovery_and_count(self):
        skills = self.registry.list_skills()
        self.assertGreaterEqual(len(skills), 30, f"Assertion failed: {len(skills)} skills")

    def test_domain_categorization(self):
        categories = self.registry.get_categories()
        expected_categories = {'python', 'react', 'database', 'git', 'security', 'code-review', 'architecture'}
        for ec in expected_categories:
            self.assertIn(ec, categories, f"Missing category {ec}")

        tdd = self.registry.get_skill('test-driven-development')
        self.assertIsNotNone(tdd)
        self.assertEqual(tdd.category, 'python')

        sec = self.registry.get_skill('security-and-hardening')
        self.assertIsNotNone(sec)
        self.assertEqual(sec.category, 'security')

        rev = self.registry.get_skill('code-review-and-quality')
        self.assertIsNotNone(rev)
        self.assertEqual(rev.category, 'code-review')

    def test_search_skills(self):
        matches = self.registry.search_skills('debugging')
        self.assertTrue(len(matches) > 0)
        self.assertTrue(any('debug' in m.name.lower() for m in matches))

        react_matches = self.registry.search_skills('react', category='react')
        self.assertTrue(len(react_matches) > 0)
        for m in react_matches:
            self.assertEqual(m.category, 'react')

    def test_load_skill_details(self):
        engine = self.registry.engine
        details = engine.load_skill_details('debugging-and-error-recovery')
        self.assertNotIn('error', details)
        self.assertEqual(details['name'], 'debugging-and-error-recovery')
        self.assertIn('instructions', details)
        self.assertTrue(len(details['instructions']) > 100)

    def test_read_reference_and_slicing(self):
        skill = self.registry.get_skill('constraint-driven-development')
        if skill and skill.references:
            ref_name = list(skill.references.keys())[0]
            res = self.registry.engine.read_reference('constraint-driven-development', ref_name)
            self.assertNotIn('error', res)
            self.assertIn('content', res)

            res_sliced = self.registry.engine.read_reference('constraint-driven-development', ref_name, start_line=1, end_line=5)
            self.assertNotIn('error', res_sliced)
            self.assertEqual(len(res_sliced['content'].splitlines()), 5)

    def test_script_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / 'test-skill'
            scripts_dir = skill_dir / 'scripts'
            scripts_dir.mkdir(parents=True)
            
            script_file = scripts_dir / 'verify.py'
            script_file.write_text('import sys\nprint("RUNNING_SCRIPT_OK")\n')

            skill_md = skill_dir / 'SKILL.md'
            skill_md.write_text('---\nname: test-skill\ndescription: Test skill for execution\ncategory: python\n---\n# Test\n')

            temp_reg = SkillRegistry(custom_skills_dir=Path(tmpdir))
            exec_res = temp_reg.engine.execute_script('test-skill', 'verify.py')
            self.assertTrue(exec_res.get('success'), f'Script execution failed: {exec_res}')
            self.assertIn('RUNNING_SCRIPT_OK', exec_res.get('stdout', ''))

    def test_builtin_tool_calling(self):
        search_res = self.builtin_tools._search_skills('security')
        self.assertTrue(search_res['success'])
        self.assertGreater(search_res['count'], 0)

        load_res = self.builtin_tools._load_skill('security-and-hardening')
        self.assertEqual(load_res['name'], 'security-and-hardening')

    def test_progressive_disclosure_prompt(self):
        coder = CoderAgent(llm=MagicMock())
        prompt = coder.build_system_prompt()
        self.assertIn('Available Skills Catalog', prompt)
        self.assertIn('load_skill(skill_name)', prompt)
        self.assertIn('search_skills', prompt)

    def test_dynamic_jit_discovery_and_semantic_scoring(self):
        # Test semantic TF-IDF discovery with relevance scores
        results = self.registry.discover("test driven development python unit tests", top_k=3)
        self.assertTrue(len(results) > 0)
        top_manifest, score = results[0]
        self.assertGreater(score, 0.0)
        self.assertTrue("test" in top_manifest.name or "tdd" in top_manifest.name)

    def test_jit_load_and_load_many(self):
        content = self.registry.load("test-driven-development")
        self.assertIsNotNone(content)
        self.assertIn("test-driven-development", content)

        multi_content = self.registry.load_many(["test-driven-development", "security-and-hardening"])
        self.assertIn("Active Dynamic Skills & Engineering Guidelines", multi_content)
        self.assertIn("test-driven-development", multi_content)
        self.assertIn("security-and-hardening", multi_content)

    def test_unload_cache(self):
        self.registry.load("test-driven-development")
        self.assertIn("test-driven-development", self.registry._loaded_cache)
        self.registry.unload("test-driven-development")
        self.assertNotIn("test-driven-development", self.registry._loaded_cache)

    def test_agent_decoupling_and_active_skills_injection(self):
        coder = CoderAgent(llm=MagicMock(), skill_registry=self.registry)
        # Verify no hardcoded default skills
        self.assertFalse(hasattr(coder, "default_skills") and len(coder.default_skills) > 0)

        # Injected prompt test
        injected_prompt = coder.build_system_prompt(active_skills=["test-driven-development"])
        self.assertIn("Active Dynamic Skills & Engineering Guidelines", injected_prompt)
        self.assertIn("test-driven-development", injected_prompt)


if __name__ == '__main__':
    unittest.main()

