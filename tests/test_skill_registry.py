"""
Unit tests for Issue #71: Skill Registry.
Validates the 5 core primitives on SkillRegistry:
1. discover() - Semantic & metadata multi-filter search
2. load() - Structured loading returning string-compatible LoadedSkillBundle
3. validate() - Integrity checking (SemVer, disk file existence, dependency validity, cycle detection)
4. version() & list_versions() - SemVer comparison and constraint satisfaction (^, ~, >=, <=, ==)
5. resolve_dependencies() - Cycle-detecting topological dependency resolution
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from agent_orchestrator.registry.agent_registry import AgentManifest
from agent_orchestrator.registry.skill_registry import (
    CircularDependencyError,
    LoadedSkillBundle,
    MissingDependencyError,
    SemVer,
    SkillManifest,
    SkillRegistry,
    SkillValidationReport,
)
from agent_orchestrator.tools.registry import ToolRegistry


class TestSemVer(unittest.TestCase):
    """Tests semantic version parsing, comparison, and constraint evaluation."""

    def test_parse_valid_versions(self):
        v1 = SemVer.parse("1.2.3")
        self.assertEqual((v1.major, v1.minor, v1.patch), (1, 2, 3))
        self.assertEqual(str(v1), "1.2.3")

        v2 = SemVer.parse("v2.0.1-alpha.1+build100")
        self.assertEqual((v2.major, v2.minor, v2.patch), (2, 0, 1))
        self.assertEqual(v2.prerelease, "alpha.1")
        self.assertEqual(v2.build, "build100")

        v_loose = SemVer.parse("3.1")
        self.assertEqual((v_loose.major, v_loose.minor, v_loose.patch), (3, 1, 0))

    def test_parse_invalid_versions(self):
        with self.assertRaises(ValueError):
            SemVer.parse("not-a-version")
        with self.assertRaises(ValueError):
            SemVer.parse("")

    def test_comparisons(self):
        v1 = SemVer.parse("1.0.0")
        v2 = SemVer.parse("1.1.0")
        v3 = SemVer.parse("2.0.0")
        v_pre = SemVer.parse("1.0.0-beta")

        self.assertTrue(v1 < v2)
        self.assertTrue(v2 < v3)
        self.assertTrue(v3 > v1)
        self.assertTrue(v1 <= v1)
        self.assertTrue(v1 == SemVer.parse("1.0.0"))
        # In SemVer 2.0, prerelease has lower precedence
        self.assertTrue(v_pre < v1)

    def test_constraint_satisfaction(self):
        v = SemVer.parse("1.2.3")

        # Wildcard / Empty
        self.assertTrue(v.satisfies("*"))
        self.assertTrue(v.satisfies(""))

        # Exact
        self.assertTrue(v.satisfies("1.2.3"))
        self.assertTrue(v.satisfies("==1.2.3"))
        self.assertFalse(v.satisfies("==1.2.4"))

        # Inequalities
        self.assertTrue(v.satisfies(">=1.0.0"))
        self.assertTrue(v.satisfies("<=1.2.3"))
        self.assertTrue(v.satisfies("<2.0.0"))
        self.assertTrue(v.satisfies(">1.2.0"))
        self.assertTrue(v.satisfies("!=1.0.0"))
        self.assertFalse(v.satisfies(">=2.0.0"))

        # Caret (same major)
        self.assertTrue(v.satisfies("^1.0.0"))
        self.assertTrue(v.satisfies("^1.2.0"))
        self.assertFalse(v.satisfies("^1.3.0"))
        self.assertFalse(v.satisfies("^2.0.0"))

        # Tilde (same minor)
        self.assertTrue(v.satisfies("~1.2.0"))
        self.assertFalse(v.satisfies("~1.1.0"))
        self.assertFalse(v.satisfies("~1.3.0"))

        # Compound constraints
        self.assertTrue(v.satisfies(">=1.0.0, <2.0.0"))
        self.assertFalse(v.satisfies(">=1.0.0, <1.2.0"))


class TestSkillRegistryPrimitives(unittest.TestCase):
    """Tests the 5 core primitives on SkillRegistry."""

    def setUp(self):
        self.registry = SkillRegistry()

    def test_version_and_list_versions(self):
        s1 = SkillManifest(
            name="test-versioned-skill",
            description="A versioned skill",
            version="1.0.0",
        )
        s2 = SkillManifest(
            name="test-versioned-skill",
            description="A versioned skill v2",
            version="2.1.0",
        )
        self.registry.register_skill(s1)
        self.registry.register_skill(s2)

        # Active version should be latest (2.1.0)
        self.assertEqual(self.registry.version("test-versioned-skill"), "2.1.0")

        versions = self.registry.list_versions("test-versioned-skill")
        self.assertEqual(versions, ["1.0.0", "2.1.0"])

        # Query with constraint
        v1_match = self.registry.get_skill("test-versioned-skill", version="^1.0.0")
        self.assertIsNotNone(v1_match)
        self.assertEqual(v1_match.version, "1.0.0")

        v2_match = self.registry.get_skill("test-versioned-skill", version=">=2.0.0")
        self.assertIsNotNone(v2_match)
        self.assertEqual(v2_match.version, "2.1.0")

    def test_resolve_dependencies_linear(self):
        """Tests linear chain: A depends on B, B depends on C."""
        c = SkillManifest(name="skill-c", description="Base skill C", version="1.0.0")
        b = SkillManifest(name="skill-b", description="Intermediate B", version="1.0.0", dependencies=["skill-c"])
        a = SkillManifest(name="skill-a", description="Top A", version="1.0.0", dependencies=["skill-b"])

        self.registry.register_skill(c)
        self.registry.register_skill(b)
        self.registry.register_skill(a)

        order = self.registry.resolve_dependencies("skill-a")
        self.assertEqual(order, ["skill-c", "skill-b", "skill-a"])

        manifest_order = self.registry.resolve_dependency_manifests("skill-a")
        self.assertEqual([m.name for m in manifest_order], ["skill-c", "skill-b", "skill-a"])

    def test_resolve_dependencies_diamond(self):
        """Tests diamond DAG: Root -> [A, B], A -> Common, B -> Common."""
        common = SkillManifest(name="common-dep", description="Common base", version="1.0.0")
        a = SkillManifest(name="branch-a", description="Branch A", version="1.0.0", dependencies=["common-dep"])
        b = SkillManifest(name="branch-b", description="Branch B", version="1.0.0", dependencies=["common-dep"])
        root = SkillManifest(name="root-skill", description="Root", version="1.0.0", dependencies=["branch-a", "branch-b"])

        self.registry.register_skill(common)
        self.registry.register_skill(a)
        self.registry.register_skill(b)
        self.registry.register_skill(root)

        order = self.registry.resolve_dependencies("root-skill")
        # common-dep must appear exactly once, before branch-a and branch-b
        self.assertEqual(order[0], "common-dep")
        self.assertEqual(order.count("common-dep"), 1)
        self.assertEqual(order[-1], "root-skill")
        self.assertIn("branch-a", order[1:3])
        self.assertIn("branch-b", order[1:3])

    def test_resolve_dependencies_circular_detection(self):
        """Tests circular dependency detection: Cyc1 -> Cyc2 -> Cyc1."""
        c1 = SkillManifest(name="cycle-1", description="Cycle 1", version="1.0.0", dependencies=["cycle-2"])
        c2 = SkillManifest(name="cycle-2", description="Cycle 2", version="1.0.0", dependencies=["cycle-1"])

        self.registry.register_skill(c1)
        self.registry.register_skill(c2)

        with self.assertRaises(CircularDependencyError) as ctx:
            self.registry.resolve_dependencies("cycle-1")
        self.assertIn("Circular dependency detected", str(ctx.exception))

    def test_resolve_dependencies_missing_dependency(self):
        """Tests missing dependency error."""
        broken = SkillManifest(name="broken-skill", description="Has nonexistent dep", version="1.0.0", dependencies=["nonexistent-dep"])
        self.registry.register_skill(broken)

        with self.assertRaises(MissingDependencyError) as ctx:
            self.registry.resolve_dependencies("broken-skill")
        self.assertIn("nonexistent-dep", str(ctx.exception))

    def test_validate_valid_skill(self):
        """Tests validation of a healthy skill."""
        skill = SkillManifest(
            name="healthy-skill",
            description="A perfectly valid skill",
            version="1.2.0",
            category="python",
            required_tools=["read_file"],
        )
        self.registry.register_skill(skill)

        report = self.registry.validate(skill)
        self.assertTrue(report.is_valid)
        self.assertTrue(report.semver_valid)
        self.assertTrue(report.manifest_valid)
        self.assertTrue(report.files_exist)
        self.assertTrue(report.dependencies_resolved)
        self.assertEqual(len(report.errors), 0)

    def test_validate_invalid_semver_and_empty_name(self):
        """Tests validation flags invalid SemVer and empty name."""
        bad_manifest = SkillManifest(
            name="",
            description="Bad version skill",
            version="invalid-version-string",
        )
        report = self.registry.validate(bad_manifest)
        self.assertFalse(report.is_valid)
        self.assertFalse(report.semver_valid)
        self.assertFalse(report.manifest_valid)
        self.assertTrue(any("SemVer" in e for e in report.errors))
        self.assertTrue(any("name" in e for e in report.errors))

    def test_validate_missing_asset_files(self):
        """Tests validation catches missing script or reference files on disk."""
        skill = SkillManifest(
            name="missing-files-skill",
            description="Points to phantom assets",
            version="1.0.0",
            scripts={"run.py": "C:/phantom/nonexistent/script.py"},
            references={"guide.md": "C:/phantom/nonexistent/guide.md"},
        )
        report = self.registry.validate(skill)
        self.assertFalse(report.is_valid)
        self.assertFalse(report.files_exist)
        self.assertGreaterEqual(len(report.missing_files), 2)
        self.assertTrue(any("Missing asset files" in e for e in report.errors))

    def test_validate_missing_and_circular_dependencies(self):
        """Tests validation detects broken dependencies and cycle errors."""
        cyc_a = SkillManifest(name="v-cyc-a", description="Cycle A", version="1.0.0", dependencies=["v-cyc-b"])
        cyc_b = SkillManifest(name="v-cyc-b", description="Cycle B", version="1.0.0", dependencies=["v-cyc-a"])
        self.registry.register_skill(cyc_a)
        self.registry.register_skill(cyc_b)

        report_a = self.registry.validate(cyc_a)
        self.assertFalse(report_a.is_valid)
        self.assertFalse(report_a.dependencies_resolved)
        self.assertTrue(any("Circular dependency" in e for e in report_a.errors))

    def test_validate_tool_registry_integration(self):
        """Tests tool availability checking with ToolRegistry."""
        mock_tools = ToolRegistry()
        mock_tools.register(tool=lambda: None, name="existing_tool")

        skill = SkillManifest(
            name="tool-using-skill",
            description="Uses tools",
            version="1.0.0",
            required_tools=["existing_tool", "unregistered_tool"],
        )
        self.registry.register_skill(skill)

        report = self.registry.validate(skill, tool_registry=mock_tools)
        self.assertIn("unregistered_tool", report.missing_tools)
        self.assertTrue(any("Required tools not found" in w for w in report.warnings))

    def test_discover_multi_filter(self):
        """Tests rich discover() with query, category, tags, required_tools, and version_constraint."""
        s1 = SkillManifest(
            name="react-fast-ui",
            description="Fast React components and animations",
            category="react",
            version="2.0.0",
            tags=["frontend", "react", "components"],
            required_tools=["read_file", "render_preview"],
        )
        s2 = SkillManifest(
            name="react-legacy-form",
            description="Legacy React forms and validations",
            category="react",
            version="0.9.0",
            tags=["frontend", "react", "forms"],
            required_tools=["read_file"],
        )
        self.registry.register_skill(s1)
        self.registry.register_skill(s2)

        # Filter by category and version constraint
        v2_results = self.registry.discover(
            category="react",
            version_constraint=">=2.0.0",
        )
        self.assertTrue(any(m.name == "react-fast-ui" for m, _ in v2_results))
        self.assertFalse(any(m.name == "react-legacy-form" for m, _ in v2_results))

        # Filter by required_tools
        tool_results = self.registry.discover(required_tools=["render_preview"])
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(tool_results[0][0].name, "react-fast-ui")

        # Filter by tag
        tag_results = self.registry.discover(tags=["forms"])
        self.assertEqual(len(tag_results), 1)
        self.assertEqual(tag_results[0][0].name, "react-legacy-form")

    def test_load_loaded_skill_bundle_backward_compatibility(self):
        """Tests load() returns LoadedSkillBundle which behaves 100% as a string and exposes bundle metadata."""
        dep = SkillManifest(
            name="base-guideline",
            description="Base guidelines",
            version="1.0.0",
            rules={"rule_clean_code.md": "path/to/rule"},
        )
        skill = SkillManifest(
            name="advanced-coding",
            description="Advanced coding skill",
            version="1.5.0",
            system_instructions="Follow strict TDD principles at all times.",
            dependencies=["base-guideline"],
        )
        self.registry.register_skill(dep)
        self.registry.register_skill(skill)

        bundle = self.registry.load("advanced-coding")
        self.assertIsNotNone(bundle)

        # Subclass check: bundle must evaluate as a string
        self.assertIsInstance(bundle, str)
        self.assertIsInstance(bundle, LoadedSkillBundle)

        # String operations
        self.assertTrue("advanced-coding" in bundle)
        self.assertTrue("Follow strict TDD principles" in bundle)
        self.assertIn("base-guideline", bundle)
        self.assertTrue(bundle.startswith("### [Skill: advanced-coding"))

        # Bundle structured properties
        self.assertEqual(bundle.manifest.name, "advanced-coding")
        self.assertEqual(len(bundle.dependencies), 1)
        self.assertEqual(bundle.dependencies[0].name, "base-guideline")
        self.assertIn("rule_clean_code.md", bundle.rules)

        bundle_dict = bundle.to_dict()
        self.assertEqual(bundle_dict["name"], "advanced-coding")
        self.assertIn("base-guideline", bundle_dict["dependencies"])

    def test_agent_manifest_transitive_dependency_resolution(self):
        """Tests AgentManifest.get_effective_skills() resolves dependencies transitively using SkillRegistry."""
        base = SkillManifest(name="low-level-logging", description="Base logging", version="1.0.0")
        mid = SkillManifest(name="audit-pipeline", description="Audit pipeline", version="1.0.0", dependencies=["low-level-logging"])
        top = SkillManifest(name="security-auditor", description="Top auditor", version="1.0.0", dependencies=["audit-pipeline"])

        self.registry.register_skill(base)
        self.registry.register_skill(mid)
        self.registry.register_skill(top)

        agent = AgentManifest(
            name="AUDITOR",
            role_description="Security and compliance auditor",
            skills=["security-auditor"],
        )

        # Without skill_registry: direct skills only
        direct_only = agent.get_effective_skills()
        self.assertEqual(direct_only, ["security-auditor"])

        # With skill_registry: transitive dependencies resolved
        effective = agent.get_effective_skills(skill_registry=self.registry)
        self.assertIn("low-level-logging", effective)
        self.assertIn("audit-pipeline", effective)
        self.assertIn("security-auditor", effective)
        # Verify topological order
        self.assertLess(effective.index("low-level-logging"), effective.index("audit-pipeline"))
        self.assertLess(effective.index("audit-pipeline"), effective.index("security-auditor"))


if __name__ == "__main__":
    unittest.main()
