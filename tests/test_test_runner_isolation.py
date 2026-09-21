"""
Tests for Test Runner Isolation (Issue #83).
Verifies that test execution runs in an isolated context (shadow workspace or snapshot rollback),
preventing untrusted or rogue test code from mutating, polluting, or corrupting the authoritative workspace.
"""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import pytest

from agent_orchestrator.security.test_isolation import (
    TestIsolationEngine,
    TestIsolationMode,
    TestIsolationPolicy,
    IsolatedExecutionResult,
)
from agent_orchestrator.security.sandbox import (
    LocalProcessSandbox,
    SandboxPolicy,
    create_sandbox,
)
from agent_orchestrator.runtime.build_pipeline import (
    BuildVerificationPipeline,
    PipelineStage,
)
from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.executor import CodeExecutor


@pytest.fixture
def temp_workspace():
    """Creates a temporary workspace with source code and test files."""
    tmp_dir = tempfile.mkdtemp(prefix="test_iso_ws_")
    ws_path = Path(tmp_dir)

    src_dir = ws_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    main_file = src_dir / "calculator.py"
    main_file.write_text(
        "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n",
        encoding="utf-8",
    )

    tests_dir = ws_path / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    test_file = tests_dir / "test_calc.py"
    test_file.write_text(
        "import unittest\nfrom src.calculator import add\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )

    yield ws_path
    shutil.rmtree(tmp_dir, ignore_errors=True)


class TestShadowWorkspaceIsolation:
    """Test SHADOW_WORKSPACE mode isolation engine."""

    def test_shadow_workspace_prevents_source_deletion(self, temp_workspace):
        # A test script that attempts to delete src/calculator.py and root files
        destructive_test = temp_workspace / "tests" / "test_destructive.py"
        destructive_test.write_text(
            "import unittest\nimport os\nfrom pathlib import Path\n\n"
            "class AttackTestCase(unittest.TestCase):\n"
            "    def test_destructive_payload(self):\n"
            "        p = Path('src/calculator.py')\n"
            "        if p.exists():\n"
            "            p.unlink()\n"
            "        print('ATTEMPTED_DELETE')\n",
            encoding="utf-8",
        )

        cmd = f'"{sys.executable}" -m unittest tests/test_destructive.py'
        res = TestIsolationEngine.execute_isolated(
            command=cmd,
            workspace_dir=temp_workspace,
            mode=TestIsolationMode.SHADOW_WORKSPACE,
        )

        assert res.success is True
        assert "ATTEMPTED_DELETE" in res.stdout or "Ran 1 test" in res.stderr

        # Crucial assertion: Authoritative workspace file MUST still exist intact!
        authoritative_file = temp_workspace / "src" / "calculator.py"
        assert authoritative_file.exists(), "Authoritative file was deleted despite test isolation!"
        assert "def add(a, b):" in authoritative_file.read_text(encoding="utf-8")

    def test_shadow_workspace_prevents_workspace_pollution(self, temp_workspace):
        # A test script that creates unwanted artifacts and side effects in the workspace
        polluting_test = temp_workspace / "tests" / "test_polluter.py"
        polluting_test.write_text(
            "import unittest\nimport os\n\n"
            "class PolluterTestCase(unittest.TestCase):\n"
            "    def test_pollute(self):\n"
            "        with open('pollution_artifact.db', 'w') as f:\n"
            "            f.write('polluted data')\n"
            "        with open('src/rogue_backdoor.py', 'w') as f:\n"
            "            f.write('# rogue')\n"
            "        print('POLLUTION_DONE')\n",
            encoding="utf-8",
        )

        cmd = f'"{sys.executable}" -m unittest tests/test_polluter.py'
        res = TestIsolationEngine.execute_isolated(
            command=cmd,
            workspace_dir=temp_workspace,
            mode=TestIsolationMode.SHADOW_WORKSPACE,
        )

        assert res.success is True
        assert not (temp_workspace / "pollution_artifact.db").exists(), "Pollution artifact leaked to workspace!"
        assert not (temp_workspace / "src" / "rogue_backdoor.py").exists(), "Rogue file leaked to workspace!"

    def test_shadow_workspace_captures_test_failure_accurately(self, temp_workspace):
        failing_test = temp_workspace / "tests" / "test_failing.py"
        failing_test.write_text(
            "import unittest\n\n"
            "class TestFail(unittest.TestCase):\n"
            "    def test_boom(self):\n"
            "        self.assertEqual(1, 2, 'Intentional failure')\n",
            encoding="utf-8",
        )

        cmd = f'"{sys.executable}" -m unittest tests/test_failing.py'
        res = TestIsolationEngine.execute_isolated(
            command=cmd,
            workspace_dir=temp_workspace,
            mode=TestIsolationMode.SHADOW_WORKSPACE,
        )

        assert res.success is False
        assert res.exit_code != 0
        assert "Intentional failure" in res.stderr or "AssertionError" in res.stderr


class TestSnapshotRollbackIsolation:
    """Test SNAPSHOT_ROLLBACK mode isolation engine."""

    def test_snapshot_rollback_restores_mutated_files(self, temp_workspace):
        orig_content = (temp_workspace / "src" / "calculator.py").read_text(encoding="utf-8")

        # Mutator test script
        mutator_test = temp_workspace / "tests" / "test_mutator.py"
        mutator_test.write_text(
            "import unittest\nimport os\n\n"
            "class MutatorTestCase(unittest.TestCase):\n"
            "    def test_mutate(self):\n"
            "        with open('src/calculator.py', 'w') as f:\n"
            "            f.write('# CORRUPTED CONTENT')\n"
            "        with open('test_temporary.db', 'w') as f:\n"
            "            f.write('db artifact')\n"
            "        print('MUTATED')\n",
            encoding="utf-8",
        )

        cmd = f'"{sys.executable}" -m unittest tests/test_mutator.py'
        res = TestIsolationEngine.execute_isolated(
            command=cmd,
            workspace_dir=temp_workspace,
            mode=TestIsolationMode.SNAPSHOT_ROLLBACK,
        )

        assert res.success is True
        # Verify mutated file was automatically rolled back to pristine content
        current_content = (temp_workspace / "src" / "calculator.py").read_text(encoding="utf-8")
        assert current_content == orig_content
        # Verify pollution file was deleted
        assert not (temp_workspace / "test_temporary.db").exists()
        # Verify mutation records
        assert len(res.mutations) > 0

    def test_snapshot_rollback_restores_deleted_files(self, temp_workspace):
        orig_content = (temp_workspace / "src" / "calculator.py").read_text(encoding="utf-8")

        deleter_test = temp_workspace / "tests" / "test_deleter.py"
        deleter_test.write_text(
            "import unittest\nimport os\n\n"
            "class DeleterTestCase(unittest.TestCase):\n"
            "    def test_delete(self):\n"
            "        if os.path.exists('src/calculator.py'):\n"
            "            os.remove('src/calculator.py')\n"
            "        print('DELETED')\n",
            encoding="utf-8",
        )

        cmd = f'"{sys.executable}" -m unittest tests/test_deleter.py'
        res = TestIsolationEngine.execute_isolated(
            command=cmd,
            workspace_dir=temp_workspace,
            mode=TestIsolationMode.SNAPSHOT_ROLLBACK,
        )

        assert res.success is True
        assert (temp_workspace / "src" / "calculator.py").exists()
        assert (temp_workspace / "src" / "calculator.py").read_text(encoding="utf-8") == orig_content


class TestSandboxAndPipelineIntegration:
    """Test LocalProcessSandbox and BuildVerificationPipeline test runner isolation."""

    def test_sandbox_run_tests_uses_isolation(self, temp_workspace):
        policy = SandboxPolicy(isolate_tests=True)
        sandbox = LocalProcessSandbox(working_dir=temp_workspace, policy=policy)

        # Destructive test
        destructive_test = temp_workspace / "tests" / "test_attack.py"
        destructive_test.write_text(
            "import unittest\nimport os\n\n"
            "class AttackTestCase(unittest.TestCase):\n"
            "    def test_attack(self):\n"
            "        if os.path.exists('src/calculator.py'):\n"
            "            os.remove('src/calculator.py')\n",
            encoding="utf-8",
        )

        cmd = f'"{sys.executable}" -m unittest tests/test_attack.py'
        res = sandbox.run_tests(command=cmd, cwd=temp_workspace)

        assert res.success is True
        assert (temp_workspace / "src" / "calculator.py").exists(), "Sandbox run_tests failed to isolate workspace!"

    def test_code_executor_run_tests_uses_isolation(self, temp_workspace):
        executor = CodeExecutor(working_dir=temp_workspace)
        calc_file = temp_workspace / "src" / "calculator.py"
        assert calc_file.exists()

        res = executor.run_tests(timeout=30)
        assert res["success"] is True
        assert calc_file.exists()
        assert calc_file.exists()

    def test_build_pipeline_isolates_unit_test_stage(self, temp_workspace):
        ws_mgr = WorkspaceManager(root_dir=str(temp_workspace))
        pipeline = BuildVerificationPipeline(workspace=ws_mgr)

        # Destructive test command
        cmd = f'"{sys.executable}" -c "import os; os.remove(\'src/calculator.py\') if os.path.exists(\'src/calculator.py\') else None"'
        report = pipeline.execute(
            stages=[PipelineStage.UNIT_TEST],
            custom_commands={PipelineStage.UNIT_TEST: cmd},
        )

        assert report.passed is True
        # Crucial check: calculator.py MUST NOT be deleted
        assert (temp_workspace / "src" / "calculator.py").exists(), "BuildVerificationPipeline UNIT_TEST altered workspace!"
