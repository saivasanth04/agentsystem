import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_orchestrator.tools.workspace import WorkspaceManager
from agent_orchestrator.tools.builtin_tools import BuiltinToolRegistry
from agent_orchestrator.runtime.idempotency import ConcurrencyConflictError


class TestVersionAwareEditing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceManager(self.temp_dir)
        self.registry = BuiltinToolRegistry(workspace=self.workspace)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_read_file_header_and_version_metadata(self):
        """read_file should prepend human-readable version header to output, while keeping raw content intact."""
        test_file = "app.py"
        self.workspace.write_file(test_file, "def hello():\n    return 'world'\n")

        # Call read_file tool
        res = self.registry.call_tool("read_file", {"filepath": test_file})
        self.assertTrue(res.get("success"), res.get("error"))

        # Raw content is untouched
        self.assertEqual(res["content"], "def hello():\n    return 'world'\n")

        # Output contains version header
        self.assertIn("[File: app.py | Version:", res["output"])
        self.assertIn("Total Lines: 2", res["output"])
        self.assertTrue(res["output"].endswith("def hello():\n    return 'world'\n"))

        # Structured metadata
        self.assertIn("file_hash", res)
        self.assertIn("version", res)
        self.assertIn("revision", res)
        self.assertIn("version_tag", res)
        self.assertEqual(res["revision"], 1)
        self.assertEqual(res["version"], res["file_hash"][:12])
        self.assertTrue(res["version_tag"].startswith("v1-"))

    def test_read_file_slice_header(self):
        """read_file with slicing should include line range in version header."""
        test_file = "lines.txt"
        self.workspace.write_file(test_file, "line1\nline2\nline3\nline4\nline5\n")

        res = self.registry.call_tool("read_file", {"filepath": test_file, "start_line": 2, "end_line": 4})
        self.assertTrue(res.get("success"))
        self.assertIn("Viewing Lines 2-4", res["output"])
        self.assertIn("2: line2", res["output"])
        self.assertIn("4: line4", res["output"])
        self.assertEqual(res["total_lines"], 5)

    def test_write_file_with_valid_expected_versions(self):
        """write_file should succeed when expected_version matches full hash, prefix, revision, or tag."""
        test_file = "service.py"
        self.workspace.write_file(test_file, "class Service:\n    pass\n")

        read_res = self.registry.call_tool("read_file", {"filepath": test_file})
        full_hash = read_res["file_hash"]
        short_ver = read_res["version"]
        rev_str = str(read_res["revision"])  # "1"
        tag = read_res["version_tag"]       # "v1-..."

        # 1. Update with short prefix version
        res1 = self.registry.call_tool("write_file", {
            "filepath": test_file,
            "content": "class Service:\n    version = 1\n",
            "expected_version": short_ver,
        })
        self.assertTrue(res1.get("success"), res1.get("error"))
        self.assertEqual(self.workspace.get_file_revision(test_file), 2)

        # Read revision 2
        read_res2 = self.registry.call_tool("read_file", {"filepath": test_file})
        self.assertEqual(read_res2["revision"], 2)

        # 2. Update with revision string "v2"
        res2 = self.registry.call_tool("write_file", {
            "filepath": test_file,
            "content": "class Service:\n    version = 2\n",
            "expected_version": "v2",
        })
        self.assertTrue(res2.get("success"), res2.get("error"))
        self.assertEqual(self.workspace.get_file_revision(test_file), 3)

        # 3. Update with full SHA-256
        read_res3 = self.registry.call_tool("read_file", {"filepath": test_file})
        res3 = self.registry.call_tool("write_file", {
            "filepath": test_file,
            "content": "class Service:\n    version = 3\n",
            "expected_version": read_res3["file_hash"],
        })
        self.assertTrue(res3.get("success"), res3.get("error"))

    def test_replace_file_content_with_expected_version(self):
        """replace_file_content should accept expected_version and reject stale version."""
        test_file = "config.py"
        self.workspace.write_file(test_file, "DEBUG = False\nPORT = 8080\n")

        read_res = self.registry.call_tool("read_file", {"filepath": test_file})
        v1_hash = read_res["version"]

        # Valid replace
        res1 = self.registry.call_tool("replace_file_content", {
            "filepath": test_file,
            "target_content": "DEBUG = False",
            "replacement_content": "DEBUG = True",
            "expected_version": v1_hash,
        })
        self.assertTrue(res1.get("success"), res1.get("error"))
        self.assertEqual(self.workspace.read_file(test_file), "DEBUG = True\nPORT = 8080\n")

        # Stale replace attempt with v1_hash should fail because file is now at v2
        res2 = self.registry.call_tool("replace_file_content", {
            "filepath": test_file,
            "target_content": "PORT = 8080",
            "replacement_content": "PORT = 9000",
            "expected_version": v1_hash,
        })
        self.assertFalse(res2.get("success"))
        self.assertIn("Concurrency conflict", res2.get("error", ""))
        self.assertIn("Please call read_file", res2.get("error", ""))

    def test_concurrency_conflict_direct_workspace(self):
        """Workspace methods raise ConcurrencyConflictError on stale expected_version."""
        test_file = "data.txt"
        self.workspace.write_file(test_file, "initial data")

        # Someone else modifies the file
        self.workspace.write_file(test_file, "updated data by peer")

        with self.assertRaises(ConcurrencyConflictError) as ctx:
            self.workspace.write_file(
                test_file,
                "conflict data",
                expected_version="v1",
            )
        self.assertIn("Concurrency conflict on 'data.txt'", str(ctx.exception))

    def test_insert_and_delete_lines_with_expected_version(self):
        """insert_lines and delete_lines validate expected_version properly."""
        test_file = "list.py"
        self.workspace.write_file(test_file, "item1\nitem2\nitem3\n")

        read_res = self.registry.call_tool("read_file", {"filepath": test_file})
        v1_tag = read_res["version_tag"]

        # Insert lines with valid expected_version
        ins_res = self.registry.call_tool("insert_lines", {
            "filepath": test_file,
            "line_number": 2,
            "content": "item1.5",
            "position": "after",
            "expected_version": v1_tag,
        })
        self.assertTrue(ins_res.get("success"), ins_res.get("error"))

        # Try delete_lines with old v1_tag -> should fail
        del_res = self.registry.call_tool("delete_lines", {
            "filepath": test_file,
            "start_line": 1,
            "end_line": 1,
            "expected_version": v1_tag,
        })
        self.assertFalse(del_res.get("success"))
        self.assertIn("Concurrency conflict", del_res.get("error", ""))

        # Fetch latest version and delete lines
        read_res2 = self.registry.call_tool("read_file", {"filepath": test_file})
        del_res2 = self.registry.call_tool("delete_lines", {
            "filepath": test_file,
            "start_line": 1,
            "end_line": 1,
            "expected_version": read_res2["version"],
        })
        self.assertTrue(del_res2.get("success"), del_res2.get("error"))

    def test_react_loop_explicit_version_and_recovery(self):
        """ReAct loop allows agent to pass expected_version, detects stale conflict, and recovers after re-reading."""
        from unittest.mock import MagicMock
        from agent_orchestrator.runtime.react_loop import ReActLoop

        test_file = "workflow.py"
        self.workspace.write_file(test_file, "STATUS = 'pending'\n")

        mock_llm = MagicMock()
        turn_counter = 0

        def fake_chat_json(messages, **kwargs):
            nonlocal turn_counter
            turn_counter += 1
            if turn_counter == 1:
                # Turn 1: Read file
                return {
                    "tool_call": {
                        "name": "read_file",
                        "arguments": {"filepath": test_file}
                    }
                }
            elif turn_counter == 2:
                # Outside change happens right before turn 2!
                self.workspace.write_file(test_file, "STATUS = 'running'\n")
                # Agent attempts edit with stale version "v1"
                return {
                    "tool_call": {
                        "name": "replace_file_content",
                        "arguments": {
                            "filepath": test_file,
                            "target_content": "STATUS = 'pending'",
                            "replacement_content": "STATUS = 'completed'",
                            "expected_version": "v1",
                        }
                    }
                }
            elif turn_counter == 3:
                # Turn 3: Agent re-reads to resolve conflict
                return {
                    "tool_call": {
                        "name": "read_file",
                        "arguments": {"filepath": test_file}
                    }
                }
            elif turn_counter == 4:
                # Turn 4: Agent updates with fresh revision "v2"
                return {
                    "tool_call": {
                        "name": "replace_file_content",
                        "arguments": {
                            "filepath": test_file,
                            "target_content": "STATUS = 'running'",
                            "replacement_content": "STATUS = 'completed'",
                            "expected_version": "v2",
                        }
                    }
                }
            else:
                return {"final_output": {"status": "success"}}

        mock_llm.chat_json.side_effect = fake_chat_json

        loop = ReActLoop(tool_registry=self.registry, llm=mock_llm)
        result = loop.execute(
            task_prompt="Update status in workflow.py",
            max_turns=5,
            workspace=self.workspace,
            task_id="t-version-aware",
        )

        history = result.get("history_events") or []
        replace_events = [e for e in history if e.get("tool") == "replace_file_content"]
        self.assertEqual(len(replace_events), 2)

        # First edit attempt failed with ConcurrencyConflict
        first_res = replace_events[0].get("result") or {}
        self.assertFalse(first_res.get("success", True))
        self.assertIn("Concurrency conflict", first_res.get("error", ""))

        # Second edit attempt succeeded with fresh version
        second_res = replace_events[1].get("result") or {}
        self.assertTrue(second_res.get("success", False))
        self.assertEqual(self.workspace.read_file(test_file), "STATUS = 'completed'\n")


if __name__ == "__main__":
    unittest.main()
