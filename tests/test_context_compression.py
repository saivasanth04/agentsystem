"""
Comprehensive unit and integration tests for Context Compression, Deduplication,
Intelligent Observation Summarization, and Syntax-Safe Truncation (Issue #76).
"""
import json
import unittest

from agent_orchestrator.context.compressor import (
    CodeCompressor,
    JSONCompressor,
    LogCompressor,
    ContextCompressor,
    CompressionLevel,
)
from agent_orchestrator.context.deduplicator import ContentDeduplicator
from agent_orchestrator.context.budget_allocator import (
    ContextBudget,
    ContextSection,
    ContextAssembler,
)
from agent_orchestrator.runtime.react_loop import ContextCompactor


class TestCodeCompressor(unittest.TestCase):
    def test_strip_whitespace(self):
        raw = "def foo():   \n    x = 1   \n\n\n\n    return x   \n"
        cleaned = CodeCompressor.strip_whitespace(raw)
        self.assertTrue(all(not line.endswith(" ") for line in cleaned.splitlines()))
        self.assertNotIn("\n\n\n", cleaned)
        self.assertIn("def foo():", cleaned)

    def test_strip_comments(self):
        code = """#!/usr/bin/env python
# Top level file comment
def calculate(a: int, b: int) -> int:
    # inline note
    return a + b # return the sum
"""
        stripped = CodeCompressor.strip_comments(code)
        self.assertIn("#!/usr/bin/env python", stripped)
        self.assertNotIn("# Top level file comment", stripped)
        self.assertNotIn("# inline note", stripped)
        self.assertIn("return a + b", stripped)

    def test_fold_python_ast_skeleton(self):
        code = '''
class PaymentProcessor:
    """Handles payments for the platform."""
    gateway_url: str = "https://api.payment.com"

    def __init__(self, api_key: str):
        """Initialize payment processor."""
        self.api_key = api_key
        self.session = None

    async def process_transaction(self, amount: float, currency: str = "USD") -> bool:
        """Process charge with third-party gateway."""
        if amount <= 0:
            raise ValueError("Invalid amount")
        result = await self.gateway.charge(amount, currency)
        return result.success
'''
        folded = CodeCompressor.fold_python_ast(code, keep_docstrings=True)
        # Class and docstrings should be preserved
        self.assertIn("class PaymentProcessor:", folded)
        self.assertIn("Handles payments for the platform.", folded)
        self.assertIn("gateway_url: str = 'https://api.payment.com'", folded)
        # Function signatures and annotations must be preserved
        self.assertIn("def __init__(self, api_key: str):", folded)
        self.assertIn("async def process_transaction(self, amount: float,", folded)
        self.assertIn("currency: str", folded)
        self.assertIn("-> bool:", folded)
        # Function body should be folded to Ellipsis ...
        self.assertIn("...", folded)
        # Heavy internal implementation lines should NOT be in the skeleton
        self.assertNotIn("self.session = None", folded)
        self.assertNotIn("if amount <= 0:", folded)
        self.assertNotIn("await self.gateway.charge", folded)

    def test_fallback_folding(self):
        # Snippet with syntax fragment
        js_code = """function add(a, b) {\n    const sum = a + b;\n    return sum;\n}"""
        res = CodeCompressor.compress(js_code, language="javascript", level=CompressionLevel.SKELETON)
        self.assertIn("function add(a, b)", res)


class TestJSONCompressor(unittest.TestCase):
    def test_prune_structure_and_empty_fields(self):
        payload = {
            "task_id": "T-10",
            "objective": "Build cache",
            "empty_list": [],
            "empty_dict": {},
            "none_val": None,
            "checkpoints": [{"checkpoint_id": "ckpt-1", "file_hashes": {"a.py": "123"}}],
            "attempts": [{"attempt_id": "att-1", "logs": "very long logs"}],
            "valid_subtask": {
                "name": "Step 1",
                "status": "COMPLETED",
            }
        }
        compressed = JSONCompressor.compress(payload, max_tokens=1000)
        parsed = json.loads(compressed)

        self.assertEqual(parsed["task_id"], "T-10")
        self.assertEqual(parsed["valid_subtask"]["status"], "COMPLETED")
        # Empty and noise keys should be pruned
        self.assertNotIn("empty_list", parsed)
        self.assertNotIn("none_val", parsed)
        self.assertNotIn("checkpoints", parsed)
        self.assertNotIn("attempts", parsed)

    def test_list_capping_with_valid_sentinel(self):
        payload = {
            "items": [f"item_{i}" for i in range(25)]
        }
        compressed = JSONCompressor.compress(payload, max_tokens=50)
        parsed = json.loads(compressed)
        self.assertTrue(isinstance(parsed["items"], list))
        # Last item should be a valid omission sentinel
        last_elem = parsed["items"][-1]
        self.assertIn("items omitted", last_elem)


class TestLogCompressor(unittest.TestCase):
    def test_error_trace_preservation(self):
        log = """=== test session starts ===
platform win32 -- Python 3.13
test_1.py ... ok
test_2.py ... ok
test_3.py ... ok
test_4.py ... ok
test_5.py ... ok
test_6.py ... ok
test_7.py ... ok
test_8.py ... ok
test_9.py ... ok
test_10.py ... ok
Traceback (most recent call last):
  File "test_11.py", line 42, in test_broken
    assert result == 42
AssertionError: Expected 42, got 0
test_12.py ... ok
test_13.py ... ok
test_14.py ... ok
test_15.py ... ok
=== 1 failed, 14 passed in 2.5s ===
"""
        compressed = LogCompressor.compress(log, max_lines=12)
        # Passing lines should be collapsed
        self.assertIn("collapsed", compressed)
        # Error stack trace must be preserved
        self.assertIn("Traceback (most recent call last):", compressed)
        self.assertIn("AssertionError: Expected 42, got 0", compressed)
        self.assertIn("=== 1 failed, 14 passed", compressed)


class TestContentDeduplicator(unittest.TestCase):
    def test_cross_section_file_deduplication(self):
        dedup = ContentDeduplicator()

        # Section 1: Focal files (higher priority)
        focal_content = "// File: src/user.py\nclass User:\n    def get_id(self): return self.id"
        sec1_clean = dedup.deduplicate_section_content("focal_files", focal_content)
        self.assertIn("class User:", sec1_clean)

        # Section 2: Interface contracts (lower priority) referencing same file
        interface_content = "// Module Interface: src/user.py\n  • class User\n  • def get_id(self)"
        sec2_clean = dedup.deduplicate_section_content("interface_signatures", interface_content)

        # Body should be suppressed and replaced with reference
        self.assertIn("Already provided in focal targets above", sec2_clean)
        self.assertNotIn("• class User", sec2_clean)

    def test_fact_deduplication(self):
        dedup = ContentDeduplicator()
        text = """• Task [T-1] 'Plan' completed successfully.
• Task [T-2] 'Spec' completed successfully.
• Task [T-1] 'Plan' completed successfully."""
        clean = dedup.deduplicate_section_content("working_memory", text)
        self.assertEqual(clean.count("Task [T-1] 'Plan' completed successfully."), 1)


class TestContextAssemblerCompression(unittest.TestCase):
    def test_progressive_compression_under_tight_budget(self):
        budget = ContextBudget(total_budget=300)
        assembler = ContextAssembler(budget)

        code_content = """
class DataPipeline:
    \"\"\"Processes streaming data.\"\"\"
    def step_one(self, data):
        # heavy step 1 processing
        x = [d * 2 for d in data]
        return x

    def step_two(self, data):
        # heavy step 2 processing
        y = [d + 10 for d in data]
        return y
"""
        sec_essential = ContextSection(name="system", title="System", content="Execute plan.", priority=1, max_tokens=100, is_essential=True)
        sec_code = ContextSection(name="code", title="Pipeline Code", content=code_content, priority=2, max_tokens=150)

        assembled = assembler.assemble([sec_essential, sec_code])

        # Essential section must remain
        self.assertIn("Execute plan.", assembled)
        # Code section should be compressed (AST skeleton or whitespace pruned)
        self.assertIn("class DataPipeline", assembled)
        self.assertTrue("..." in assembled or "def step_one" in assembled)


class TestContextCompactorUpgrades(unittest.TestCase):
    def test_semantic_summary_with_legacy_header(self):
        file_read_output = json.dumps({
            "filepath": "services/auth_service.py",
            "total_lines": 140,
            "output": "class AuthService:\n    def authenticate(self, token):\n        pass\n" + ("#" * 500),
            "success": True,
        })
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Initial prompt"},
            {"role": "assistant", "content": "Reading file..."},
            {"role": "tool", "name": "read_file", "content": file_read_output},
            {"role": "assistant", "content": "File analyzed."},
            {"role": "user", "content": "Next turn."},
            {"role": "assistant", "content": "Action 2."},
            {"role": "user", "content": "User prompt."},
            {"role": "assistant", "content": "Final turn."},
        ]

        compacted, was_compacted = ContextCompactor.compact(messages, max_tokens=100)
        self.assertTrue(was_compacted)

        tool_msg = compacted[3]
        # Legacy header must be present for backward compatibility
        self.assertIn("[Observation compacted: read_file produced", tool_msg["content"])
        self.assertIn("Full result omitted to preserve context window.]", tool_msg["content"])
        # Upgraded semantic extraction
        self.assertIn("services/auth_service.py", tool_msg["content"])
        self.assertIn("AuthService", tool_msg["content"])
        self.assertIn("Status: SUCCESS", tool_msg["content"])


if __name__ == "__main__":
    unittest.main()
