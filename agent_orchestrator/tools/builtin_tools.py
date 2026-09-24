"""
Pre-built and Custom Developer Tools with LangChain & StructuredTool implementations.
Includes ReadFileTool, WriteFileTool, ListDirectoryTool, CopyFileTool, DeleteFileTool,
TerminalExecutionTool, ASTSyntaxCheckerTool, RegexGrepTool, and StateTransitionLoggerTool.
"""
import ast
import fnmatch
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from .workspace import WorkspaceManager
from .code_graph import CodeGraphEngine
from ..codebase.architecture import ArchitectureAnalyzer
from ..codebase.semantic_index import SemanticCodeIndex
from ..codebase.cbm import CodebaseMemory
from ..registry.skill_registry import SkillRegistry
from ..runtime.messaging import MessageBus, MessageType, StructuredMessage
from .communication_tools import CommunicationToolRegistry
try:
    from ..security.sandbox import BaseExecutionSandbox, create_sandbox, SandboxResult
except (ImportError, ValueError):
    from agent_orchestrator.security.sandbox import BaseExecutionSandbox, create_sandbox, SandboxResult
try:
    from ..mcp.servers.git_server import GitMCPServer
except (ImportError, ValueError):
    from agent_orchestrator.mcp.servers.git_server import GitMCPServer


# --- Pydantic Argument Schemas ---
class SendAgentMessageInput(BaseModel):
    recipient: str = Field(description="Target agent name or task ID (e.g. 'CODER', 'T-01') or '*' for broadcast")
    content: str = Field(description="Message or finding content")
    message_type: Optional[str] = Field(default="TASK_RESULT", description="Message type: TASK_RESULT, ARTIFACT, OBSERVATION, FINDING, REQUEST, RESPONSE, BROADCAST")
    topic: Optional[str] = Field(default="general", description="Topic channel")
    payload: Optional[Dict[str, Any]] = Field(default=None, description="Optional structured payload data")


class QueryAgentInput(BaseModel):
    target_agent: str = Field(description="Target agent persona to consult (e.g. 'ARCHITECTURE', 'SPECIFICATION')")
    query: str = Field(description="Question or clarification request")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Relevant context snippets")


class PublishFindingInput(BaseModel):
    topic: str = Field(description="Topic channel name (e.g. 'security_boundaries', 'api_conventions')")
    title: str = Field(description="Summary title of the finding")
    details: Optional[Dict[str, Any]] = Field(default=None, description="Structured details or constraints")


class ReadInboxInput(BaseModel):
    recipient: Optional[str] = Field(default=None, description="Recipient inbox to read (agent name or task ID)")
    clear: Optional[bool] = Field(default=False, description="Whether to clear inbox after reading")


class ReadFileInput(BaseModel):
    filepath: str = Field(description="Relative path to file in workspace")
    start_line: Optional[int] = Field(default=None, description="Optional 1-indexed start line number")
    end_line: Optional[int] = Field(default=None, description="Optional 1-indexed end line number")


class WriteFileInput(BaseModel):
    filepath: str = Field(description="Relative path to file in workspace")
    content: str = Field(description="Full text content to write into the file")
    expected_version: Optional[str] = Field(default=None, description="Optional version tag, revision (e.g. 'v1', 'a3f10c9b'), or SHA-256 hash of file when read, for optimistic concurrency control (Compare-And-Swap)")
    expected_hash: Optional[str] = Field(default=None, description="Optional SHA-256 base hash of file for optimistic concurrency control (Compare-And-Swap)")
    operation_id: Optional[str] = Field(default=None, description="Optional unique idempotent operation ID")


class ReplaceFileContentInput(BaseModel):
    filepath: str = Field(description="Relative path to file in workspace")
    target_content: str = Field(description="Exact substring or lines to find and replace")
    replacement_content: str = Field(description="New content to replace target_content with")
    start_line: Optional[int] = Field(default=None, description="Optional 1-indexed start line number to constrain search")
    end_line: Optional[int] = Field(default=None, description="Optional 1-indexed end line number to constrain search")
    allow_multiple: Optional[bool] = Field(default=False, description="Whether to replace multiple occurrences")
    fuzzy: Optional[bool] = Field(default=True, description="Whether to tolerate minor whitespace or indentation variations")
    expected_version: Optional[str] = Field(default=None, description="Optional version tag, revision (e.g. 'v1', 'a3f10c9b'), or SHA-256 hash of file when read, for optimistic concurrency control (Compare-And-Swap)")
    expected_hash: Optional[str] = Field(default=None, description="Optional SHA-256 base hash of file for optimistic concurrency control (Compare-And-Swap)")
    operation_id: Optional[str] = Field(default=None, description="Optional unique idempotent operation ID")


class InsertLinesInput(BaseModel):
    filepath: str = Field(description="Relative path to file in workspace")
    line_number: int = Field(description="1-indexed line number where new lines should be inserted")
    content: str = Field(description="New content/lines to insert into the file")
    position: Optional[str] = Field(default="after", description="'after' to insert following line_number, or 'before' to prepend before line_number")
    expected_version: Optional[str] = Field(default=None, description="Optional version tag, revision (e.g. 'v1', 'a3f10c9b'), or SHA-256 hash of file when read, for optimistic concurrency control (Compare-And-Swap)")
    expected_hash: Optional[str] = Field(default=None, description="Optional SHA-256 base hash of file for optimistic concurrency control (Compare-And-Swap)")
    operation_id: Optional[str] = Field(default=None, description="Optional unique idempotent operation ID")


class DeleteLinesInput(BaseModel):
    filepath: str = Field(description="Relative path to file in workspace")
    start_line: int = Field(description="1-indexed starting line number of range to delete")
    end_line: int = Field(description="1-indexed ending line number of range to delete (inclusive)")
    expected_version: Optional[str] = Field(default=None, description="Optional version tag, revision (e.g. 'v1', 'a3f10c9b'), or SHA-256 hash of file when read, for optimistic concurrency control (Compare-And-Swap)")
    expected_hash: Optional[str] = Field(default=None, description="Optional SHA-256 base hash of file for optimistic concurrency control (Compare-And-Swap)")
    operation_id: Optional[str] = Field(default=None, description="Optional unique idempotent operation ID")


class ApplyDiffBlocksInput(BaseModel):
    filepath: str = Field(description="Relative path to file in workspace")
    diff_blocks: str = Field(description="One or more Aider-style <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE blocks")
    fuzzy: Optional[bool] = Field(default=True, description="Whether to tolerate minor whitespace or indentation variations")
    expected_version: Optional[str] = Field(default=None, description="Optional version tag, revision (e.g. 'v1', 'a3f10c9b'), or SHA-256 hash of file when read, for optimistic concurrency control (Compare-And-Swap)")
    expected_hash: Optional[str] = Field(default=None, description="Optional SHA-256 base hash of file for optimistic concurrency control (Compare-And-Swap)")
    operation_id: Optional[str] = Field(default=None, description="Optional unique idempotent operation ID")


class DeleteFileInput(BaseModel):
    filepath: str = Field(description="Relative path of file or directory in workspace to delete")
    expected_version: Optional[str] = Field(default=None, description="Optional expected version tag or SHA-256 for OCC")
    expected_hash: Optional[str] = Field(default=None, description="Optional expected SHA-256 hash for OCC")
    operation_id: Optional[str] = Field(default=None, description="Optional unique idempotent operation ID")


class RenameFileInput(BaseModel):
    old_filepath: str = Field(description="Current relative path of the file in workspace")
    new_filepath: str = Field(description="Target new relative path of the file in workspace")
    expected_version: Optional[str] = Field(default=None, description="Optional expected version tag or SHA-256 for OCC")
    expected_hash: Optional[str] = Field(default=None, description="Optional expected SHA-256 hash for OCC")
    overwrite: Optional[bool] = Field(default=False, description="Whether to overwrite if new_filepath already exists")
    operation_id: Optional[str] = Field(default=None, description="Optional unique idempotent operation ID")


class MoveFileInput(BaseModel):
    source_filepath: str = Field(description="Relative path of file to move")
    target_dir: str = Field(description="Relative directory path to move the file into")
    expected_version: Optional[str] = Field(default=None, description="Optional expected version tag or SHA-256 for OCC")
    expected_hash: Optional[str] = Field(default=None, description="Optional expected SHA-256 hash for OCC")
    overwrite: Optional[bool] = Field(default=False, description="Whether to overwrite if destination already exists")
    operation_id: Optional[str] = Field(default=None, description="Optional unique idempotent operation ID")


class ApplyPatchInput(BaseModel):
    patch_content: str = Field(description="Standard unified diff patch content (e.g. --- a/file +++ b/file @@ ... @@)")
    fuzz_factor: Optional[int] = Field(default=2, description="Line tolerance window when matching patch hunks")
    operation_id: Optional[str] = Field(default=None, description="Optional unique idempotent operation ID")


class ListDirectoryInput(BaseModel):
    path: Optional[str] = Field(default="", description="Subdirectory path relative to workspace root (empty for root)")


class FindSymbolInput(BaseModel):
    symbol_name: str = Field(description="Name or substring of class, function, or method to locate in codebase")


class FindReferencesInput(BaseModel):
    symbol_name: str = Field(description="Name of symbol to find all usages, callers, and imports across workspace")


class GetDependenciesInput(BaseModel):
    target: str = Field(description="Filepath (e.g. 'models/user.py') or Symbol name to find dependencies and dependents for")


class GetCodebaseMapInput(BaseModel):
    path: Optional[str] = Field(default="", description="Optional directory path filter (empty for entire codebase)")


class SemanticCodeSearchInput(BaseModel):
    query: str = Field(description="Natural language concept or query to search for in codebase (e.g. 'token bucket rate limiter', 'session authentication')")
    top_k: Optional[int] = Field(default=5, description="Number of top ranked code symbols to return")


class GetCallGraphInput(BaseModel):
    target: str = Field(description="Function or method name to inspect callers (who calls this) and callees (what this calls)")
    max_depth: Optional[int] = Field(default=2, description="Maximum traversal depth (default: 2)")


class GetImpactRadiusInput(BaseModel):
    target: str = Field(description="Filepath (e.g. 'core/engine.py') or symbol name to calculate downstream blast radius and affected files")
    max_depth: Optional[int] = Field(default=3, description="Maximum traversal depth (default: 3)")


class GetArchitectureSummaryInput(BaseModel):
    pass


class QueryCodebaseGraphInput(BaseModel):
    query: str = Field(description="Natural language query or concept to retrieve a focused sub-graph slice for (e.g. 'authentication tokens', 'rate limiter')")
    max_tokens: Optional[int] = Field(default=2000, description="Token ceiling for returned sub-graph slice")
    traversal: Optional[str] = Field(default="bfs", description="Traversal strategy: 'bfs' or 'dfs'")


class GetSymbolNeighborsInput(BaseModel):
    symbol_name: str = Field(description="Exact or qualified symbol name to inspect 1-2 hop neighborhood (callers, callees, classes)")
    depth: Optional[int] = Field(default=1, description="Hop depth to traverse (1 or 2)")


class GetArchitectureSliceInput(BaseModel):
    target_file: str = Field(description="Relative filepath in workspace to retrieve architectural layer, framework conventions, and entrypoint connections for")


class SyntaxCheckInput(BaseModel):
    filepath: str = Field(description="Relative path to Python file in workspace to validate syntax")


class GrepSearchInput(BaseModel):
    pattern: str = Field(description="Text or regex pattern to search for across workspace files")
    file_glob: Optional[str] = Field(default=None, description="Optional glob filter, e.g. '*.py'")


class TerminalExecutionInput(BaseModel):
    command: str = Field(description="Shell command to execute in workspace directory (e.g. pytest, unittest, npm test)")
    timeout: Optional[int] = Field(default=30, description="Timeout in seconds")


class SearchSkillsInput(BaseModel):
    query: str = Field(description="Query keyword, tag, or topic to search across available skills (e.g. 'react', 'debugging', 'owasp')")
    category: Optional[str] = Field(default=None, description="Optional domain category filter (python, react, database, git, security, code-review, architecture, deployment)")


class LoadSkillInput(BaseModel):
    skill_name: str = Field(description="Exact or fuzzy name of the skill to retrieve full guidelines, tools, and scripts for")


class ReadSkillReferenceInput(BaseModel):
    skill_name: str = Field(description="Name of the skill owning the reference document or rule")
    reference_name: str = Field(description="Name or filename of reference/rule document (e.g. 'floor-guard.md', 'candidates.md', 'rules')")
    start_line: Optional[int] = Field(default=None, description="Optional 1-indexed start line number")
    end_line: Optional[int] = Field(default=None, description="Optional 1-indexed end line number")


class ExecuteSkillScriptInput(BaseModel):
    skill_name: str = Field(description="Name of skill owning the executable script")
    script_name: str = Field(description="Filename of script to execute (e.g. 'budget-summary.mjs', 'idea-refine.sh')")
    args: Optional[List[str]] = Field(default=None, description="Optional command line arguments to pass to the script")


class InstallSkillInput(BaseModel):
    skill_slug: str = Field(description="Skill name or slug from skills.sh ecosystem (e.g. 'github/code-quality')")


class CompleteTaskInput(BaseModel):
    summary: str = Field(description="Summary of task deliverables, actions performed, and status")
    deliverables: Optional[Dict[str, Any]] = Field(default=None, description="Key deliverables, generated files, or results")
    evidence_type: Optional[str] = Field(
        default="INSPECTION_CONFIRMED",
        description="Type of evidence justifying completion: 'TEST_PASS', 'INSPECTION_CONFIRMED', 'SYNTAX_VALID', 'INFORMATION_SATURATED', 'GOAL_SATISFIED_EARLY'",
    )
    proof_citation: Optional[str] = Field(
        default=None,
        description="Explicit citation of empirical proof (e.g. test command output, exit code 0, ast check, or file observation)",
    )
    confidence_score: Optional[float] = Field(
        default=1.0,
        description="Epistemic confidence that this task or goal is completely and correctly finished (0.0 - 1.0)",
    )
    redundant_tasks: Optional[List[str]] = Field(
        default=None,
        description="Optional list of downstream task IDs that are no longer necessary if GOAL_SATISFIED_EARLY",
    )


class GetWorkspaceChangesInput(BaseModel):
    filepath: Optional[str] = Field(default=None, description="Optional relative filepath to inspect changes for. If omitted, returns all workspace changes.")


class RollbackCheckpointInput(BaseModel):
    checkpoint_id: Optional[str] = Field(default=None, description="Specific checkpoint ID to roll back to. If omitted, rolls back to the most recent pre-task or baseline checkpoint.")
    reason: Optional[str] = Field(default="", description="Reason for rolling back changes")



class SpawnSubtasksInput(BaseModel):
    parent_task_id: str = Field(description="Task ID of current executing task (e.g. 'T-01')")
    subtasks: List[Dict[str, Any]] = Field(description="List of child subtasks with task_id, objective, capabilities, tools, inputs, outputs, acceptance_tests")


class StateTransitionLogInput(BaseModel):
    from_node: str = Field(description="Origin workflow node")
    to_node: str = Field(description="Target workflow node")
    iteration: int = Field(description="Current cycle/iteration count")
    details: str = Field(description="Reason or summary of transition")


class GitStatusInput(BaseModel):
    short: Optional[bool] = Field(default=True, description="Return short output format")


class GitDiffInput(BaseModel):
    target: Optional[str] = Field(default="HEAD", description="Branch, commit, or HEAD (default: HEAD)")
    staged: Optional[bool] = Field(default=False, description="Inspect staged changes only")
    file_path: Optional[str] = Field(default=None, description="Optional specific file path to diff")


class GitLogInput(BaseModel):
    max_count: Optional[int] = Field(default=5, description="Max number of commits to retrieve")
    file_path: Optional[str] = Field(default=None, description="Optional file path to inspect commit history for")


class GitShowInput(BaseModel):
    commit: Optional[str] = Field(default="HEAD", description="Commit hash or reference")


class GitBlameInput(BaseModel):
    file_path: str = Field(description="Relative path to file in workspace")
    start_line: Optional[int] = Field(default=None, description="Starting line number (1-based)")
    end_line: Optional[int] = Field(default=None, description="Ending line number (1-based)")


class GitBranchInput(BaseModel):
    name: Optional[str] = Field(default=None, description="Branch name to create or delete")
    delete: Optional[bool] = Field(default=False, description="Delete the specified branch")


class GitCheckoutInput(BaseModel):
    target: str = Field(description="Target branch name or commit hash")
    create_branch: Optional[bool] = Field(default=False, description="Create a new branch (-b)")


class GitCommitInput(BaseModel):
    message: str = Field(description="Commit message")
    add_all: Optional[bool] = Field(default=True, description="Stage all modified files (-A)")
    files: Optional[List[str]] = Field(default=None, description="Specific files to stage")


class GitRestoreInput(BaseModel):
    file_path: str = Field(description="Path to file to restore")
    staged: Optional[bool] = Field(default=False, description="Unstage changes from index (--staged)")


class GitPatchInput(BaseModel):
    action: str = Field(description="'export' (generate patch) or 'apply' (apply patch)")
    patch_content: Optional[str] = Field(default=None, description="Unified diff content to apply")


class GitInitInput(BaseModel):
    pass


class DetectProjectEnvironmentInput(BaseModel):
    path: Optional[str] = Field(default="", description="Optional subfolder path within workspace to inspect (default: workspace root)")


class StaticCodeCheckInput(BaseModel):
    filepath: Optional[str] = Field(default=None, description="Optional relative path of specific file to verify, or leave empty to verify all changed/workspace files.")


class InspectExistingTestsInput(BaseModel):
    path: Optional[str] = Field(default="", description="Optional subfolder path within workspace to inspect for existing tests (default: workspace root)")
    sample_files_count: Optional[int] = Field(default=2, description="Number of existing test files to sample for style and convention extraction")


class RunBuildPipelineInput(BaseModel):
    stages: Optional[List[str]] = Field(default=None, description="Optional list of pipeline stages to run (INSTALL_DEPENDENCIES, BUILD, COMPILE_TYPECHECK, LINT, UNIT_TEST, INTEGRATION_TEST)")
    fail_fast: Optional[bool] = Field(default=True, description="Whether to stop at first failing stage")


class RunStaticAnalysisInput(BaseModel):
    files: Optional[List[str]] = Field(default=None, description="Optional list of specific files to analyze (e.g. ['src/app.py']). If omitted, analyzes all workspace files.")
    tools: Optional[List[str]] = Field(default=None, description="Optional list of specific tools to run (e.g. ['ruff', 'mypy', 'pyright', 'eslint', 'tsc', 'semgrep', 'clippy', 'govet']).")
    categories: Optional[List[str]] = Field(default=None, description="Optional category filter: LINTER, TYPE_CHECKER, COMPILER, SAST.")


class VerifyGroundTruthInput(BaseModel):
    modified_files: Optional[List[str]] = Field(default=None, description="Optional list of modified files to evaluate against ground-truth gates.")


class SearchProjectMemoryInput(BaseModel):
    query: str = Field(description="Natural language or keyword search query for project memory")
    category: Optional[str] = Field(default=None, description="Optional category: ARCH_DECISION, BUG_PATTERN, CONVENTION, API_CONTRACT, GENERAL")
    top_k: Optional[int] = Field(default=5, description="Maximum number of memory items to return")


class RecordProjectMemoryInput(BaseModel):
    category: str = Field(description="Category: ARCH_DECISION, BUG_PATTERN, CONVENTION, API_CONTRACT, GENERAL")
    title: str = Field(description="Concise title for the memory item")
    content: str = Field(description="Detailed content or architectural decision explanation")
    tags: Optional[List[str]] = Field(default=None, description="Keywords or tags for retrieval")


class UpdateScratchpadInput(BaseModel):
    note: str = Field(description="Working note, hypothesis, or verified fact to append to working memory")


class RequestMoreEvidenceInput(BaseModel):
    reason: str = Field(description="Explanation of why this additional evidence is required before implementing code.")
    missing_symbols: Optional[List[str]] = Field(default=None, description="Optional list of class, function, or interface names missing from context.")
    missing_files: Optional[List[str]] = Field(default=None, description="Optional list of relative filepaths that need to be inspected.")
    query: Optional[str] = Field(default="", description="Optional conceptual search query for missing architectural or domain context.")


class SpawnSubagentInput(BaseModel):
    role: str = Field(description="Specialized role (e.g. 'RESEARCHER', 'TESTER', 'REFACTOR')")
    goal: str = Field(description="Specific sub-goal for this agent")
    capabilities: Optional[List[str]] = Field(default=None, description="Required capabilities")
    max_tokens: Optional[int] = Field(default=50000, description="Token limit quota for this subagent")


class DelegateSubtaskInput(BaseModel):
    subtask_objective: str = Field(description="Objective of the subtask to execute")
    target_role: Optional[str] = Field(default=None, description="Desired role of the worker agent")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Input data and context parameters")


class HandoffToAgentInput(BaseModel):
    target_role: str = Field(description="Role or instance ID of the recipient agent")
    reason: str = Field(description="Why handoff is being initiated")
    hypotheses: Optional[List[str]] = Field(default=None, description="Current working hypotheses")
    active_files: Optional[List[str]] = Field(default=None, description="Files currently being modified or inspected")


class DiscoverSwarmAgentsInput(BaseModel):
    capability: Optional[str] = Field(default=None, description="Optional capability filter")
    role: Optional[str] = Field(default=None, description="Optional role filter")


class PostToBlackboardInput(BaseModel):
    topic: str = Field(description="Topic category (e.g. 'architecture', 'findings')")
    key: str = Field(description="Lookup key")
    data: Any = Field(description="Data payload or summary")
    confidence: Optional[float] = Field(default=1.0, description="Confidence score from 0.0 to 1.0")


class ReadFromBlackboardInput(BaseModel):
    topic: str = Field(description="Topic category")
    key: str = Field(description="Lookup key")


class RequestConsensusInput(BaseModel):
    issue: str = Field(description="Description of the decision to make")
    options: List[str] = Field(description="List of options to vote on")
    mechanism: Optional[str] = Field(default="MAJORITY", description="Voting mechanism: MAJORITY, CONFIDENCE_WEIGHTED, UNANIMOUS")


class TerminateSubagentInput(BaseModel):
    subagent_id: str = Field(description="Instance ID of the subagent to terminate")
    reason: Optional[str] = Field(default=None, description="Reason for termination")


class QuerySpecificationInput(BaseModel):
    query: str = Field(description="Search keyword, concept, or requirement ID (e.g. 'FR-1', 'cache', 'authentication', 'timeout')")
    section: Optional[str] = Field(default=None, description="Optional section to restrict search: functional_requirements, edge_cases, acceptance_criteria, non_functional_requirements")


class QueryArchitectureInput(BaseModel):
    module_name: Optional[str] = Field(default=None, description="Optional module or component name to search (e.g. 'cache', 'storage', 'auth')")
    query: Optional[str] = Field(default=None, description="Optional search term for class, function, or design pattern")


class GetTaskArtifactInput(BaseModel):
    task_id: str = Field(description="ID of the predecessor task whose deliverable/artifact is requested (e.g. 'T-01')")


class GenerateEditPlanInput(BaseModel):
    task_id: Optional[str] = Field(default=None, description="Task ID to generate edit plan for (e.g. 'T-01')")
    objective: Optional[str] = Field(default=None, description="Task objective or goal summary")
    inputs: Optional[List[str]] = Field(default=None, description="List of input files/modules")
    outputs: Optional[List[str]] = Field(default=None, description="List of output files/symbols (e.g. ['src/models.py:User'])")
    acceptance_tests: Optional[List[str]] = Field(default=None, description="List of acceptance test commands")


class GetEditPlanInput(BaseModel):
    task_id: Optional[str] = Field(default=None, description="Task ID to retrieve edit plan for")


class UpdateEditPlanStepInput(BaseModel):
    step_id: str = Field(description="Step ID to update (e.g. 'step-1')")
    status: str = Field(description="New status: PENDING, IN_PROGRESS, COMPLETED, SKIPPED, FAILED")
    error: Optional[str] = Field(default=None, description="Optional error message if step failed")


class BuiltinToolRegistry:

    """
    Registry organizing tools by agent roles.
    """

    def __init__(
        self,
        workspace: WorkspaceManager,
        skill_registry: Optional[SkillRegistry] = None,
        message_bus: Optional[MessageBus] = None,
        agent_registry: Any = None,
        llm: Any = None,
        sandbox: Optional[BaseExecutionSandbox] = None,
        checkpoint_manager: Optional[Any] = None,
        swarm_coordinator: Optional[Any] = None,
    ):
        self.workspace = workspace
        self.root_dir = str(self.workspace.root_dir)
        self.sandbox = sandbox or create_sandbox(self.workspace.root_dir)
        self.skill_registry = skill_registry or SkillRegistry()
        self.message_bus = message_bus
        self.agent_registry = agent_registry
        self.llm = llm
        self.transition_logs: List[Dict[str, Any]] = []
        self.code_graph = CodeGraphEngine(self.workspace.root_dir)
        self.semantic_index = SemanticCodeIndex(code_graph=self.code_graph)
        self.arch_analyzer = ArchitectureAnalyzer(workspace_dir=self.workspace.root_dir, code_graph=self.code_graph)
        self.cbm = CodebaseMemory(
            workspace_dir=self.workspace.root_dir,
            code_graph=self.code_graph,
            semantic_index=self.semantic_index,
            arch_analyzer=self.arch_analyzer,
        )

        # Wire reactive incremental indexing on file change
        def _on_workspace_file_changed(rel_path: str, change_type: str):
            try:
                if change_type == "DELETED":
                    self.code_graph.delete_file(rel_path)
                    self.semantic_index.delete_file(rel_path)
                    self.cbm.delete_file(rel_path)
                else:
                    self.code_graph.reindex_file(rel_path)
                    new_syms = self.code_graph.file_to_symbols.get(rel_path, [])
                    self.semantic_index.reindex_file(rel_path, new_syms)
                    self.cbm.update_file(rel_path)
            except Exception:
                pass
        self.workspace.register_file_change_listener(_on_workspace_file_changed)
        self.checkpoint_manager = checkpoint_manager
        if not self.checkpoint_manager:
            try:
                from ..persistence.checkpoint_manager import WorkspaceCheckpointManager
                self.checkpoint_manager = WorkspaceCheckpointManager(workspace_dir=self.workspace.root_dir)
            except Exception:
                self.checkpoint_manager = None
        self.orchestrator_state: Optional[Any] = None
        self._current_edit_plan: Optional[Any] = None


        # Communication tools
        self.comm_tools = CommunicationToolRegistry(
            message_bus=self.message_bus,
            agent_registry=self.agent_registry,
            llm=self.llm,
            workspace=self.workspace,
        ) if self.message_bus else None

        # Swarm Coordinator & Swarm Tools
        self.swarm_coordinator = swarm_coordinator
        if self.swarm_coordinator:
            from .swarm_tools import SwarmToolRegistry
            self.swarm_tools = SwarmToolRegistry(coordinator=self.swarm_coordinator)
        else:
            self.swarm_tools = None

        self.spawn_subagent_tool = StructuredTool.from_function(
            func=self._spawn_subagent,
            name="spawn_subagent",
            description="Dynamically spawn a new specialized subagent with a scoped goal and token limit.",
            args_schema=SpawnSubagentInput,
        )
        self.delegate_subtask_tool = StructuredTool.from_function(
            func=self._delegate_subtask,
            name="delegate_subtask",
            description="Delegate a subtask to a specialized peer or dynamically spawned child agent.",
            args_schema=DelegateSubtaskInput,
        )
        self.handoff_to_agent_tool = StructuredTool.from_function(
            func=self._handoff_to_agent,
            name="handoff_to_agent",
            description="Execute a stateful handoff to another agent, preserving hypotheses, active files, and diffs.",
            args_schema=HandoffToAgentInput,
        )
        self.discover_swarm_agents_tool = StructuredTool.from_function(
            func=self._discover_swarm_agents,
            name="discover_swarm_agents",
            description="Discover active swarm agents by capability or role.",
            args_schema=DiscoverSwarmAgentsInput,
        )
        self.post_to_blackboard_tool = StructuredTool.from_function(
            func=self._post_to_blackboard,
            name="post_to_blackboard",
            description="Publish an intermediate observation, finding, or deliverable to the shared blackboard.",
            args_schema=PostToBlackboardInput,
        )
        self.read_from_blackboard_tool = StructuredTool.from_function(
            func=self._read_from_blackboard,
            name="read_from_blackboard",
            description="Read a specific observation or finding from the shared blackboard.",
            args_schema=ReadFromBlackboardInput,
        )
        self.request_consensus_tool = StructuredTool.from_function(
            func=self._request_consensus,
            name="request_consensus",
            description="Initiate a swarm consensus vote on an issue or design decision.",
            args_schema=RequestConsensusInput,
        )
        self.terminate_subagent_tool = StructuredTool.from_function(
            func=self._terminate_subagent,
            name="terminate_subagent",
            description="Terminate an active subagent and all its child processes.",
            args_schema=TerminateSubagentInput,
        )

        # On-Demand State Retrieval Tools (Context Isolation)
        self.query_specification_tool = StructuredTool.from_function(
            func=self._query_specification,
            name="query_specification",
            description="Searches active project specifications for functional requirements, schemas, edge cases, and acceptance criteria on demand.",
            args_schema=QuerySpecificationInput,
        )
        self.query_architecture_tool = StructuredTool.from_function(
            func=self._query_architecture,
            name="query_architecture",
            description="Searches active project architecture blueprint for component structures, class signatures, and file layouts on demand.",
            args_schema=QueryArchitectureInput,
        )
        self.get_task_artifact_tool = StructuredTool.from_function(
            func=self._get_task_artifact,
            name="get_task_artifact",
            description="Retrieves the deliverable, summary, or outputs of a specific predecessor task on demand.",
            args_schema=GetTaskArtifactInput,
        )
        self.generate_edit_plan_tool = StructuredTool.from_function(
            func=self._generate_edit_plan,
            name="generate_edit_plan",
            description="Generates or refines a structured mutation plan (target files, symbols, edit actions, dependencies, and invariants).",
            args_schema=GenerateEditPlanInput,
        )
        self.get_edit_plan_tool = StructuredTool.from_function(
            func=self._get_edit_plan,
            name="get_edit_plan",
            description="Retrieves the active structured edit plan and completion status of mutation steps.",
            args_schema=GetEditPlanInput,
        )
        self.update_edit_plan_step_tool = StructuredTool.from_function(
            func=self._update_edit_plan_step,
            name="update_edit_plan_step",
            description="Updates the execution status (PENDING, IN_PROGRESS, COMPLETED, FAILED) of a specific mutation step.",
            args_schema=UpdateEditPlanStepInput,
        )

        # 1. Custom & Structured Tools for granular coding workflows
        self.rollback_tool = StructuredTool.from_function(
            func=self._rollback_to_checkpoint,
            name="rollback_to_checkpoint",
            description="Rolls back workspace files to a snapshot checkpoint, restoring modified files and deleting newly created files.",
            args_schema=RollbackCheckpointInput,
        )
        self.read_file_tool = StructuredTool.from_function(
            func=self._read_file,
            name="read_file",
            description="Reads file contents from workspace with optional start_line and end_line slice window.",
            args_schema=ReadFileInput,
        )
        self.write_file_tool = StructuredTool.from_function(
            func=self._write_file,
            name="write_file",
            description="Writes full content to a file in the workspace, creating parent directories if needed.",
            args_schema=WriteFileInput,
        )
        self.replace_content_tool = StructuredTool.from_function(
            func=self._replace_file_content,
            name="replace_file_content",
            description="Replaces target_content substring with replacement_content in a file without rewriting the entire file.",
            args_schema=ReplaceFileContentInput,
        )
        self.insert_lines_tool = StructuredTool.from_function(
            func=self._insert_lines,
            name="insert_lines",
            description="Inserts content before or after a specific line number in an existing file.",
            args_schema=InsertLinesInput,
        )
        self.delete_lines_tool = StructuredTool.from_function(
            func=self._delete_lines,
            name="delete_lines",
            description="Deletes a range of lines [start_line, end_line] from an existing file.",
            args_schema=DeleteLinesInput,
        )
        self.apply_diff_blocks_tool = StructuredTool.from_function(
            func=self._apply_diff_blocks,
            name="apply_diff_blocks",
            description="Applies one or more Aider-style <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE blocks to an existing file.",
            args_schema=ApplyDiffBlocksInput,
        )
        self.delete_file_tool = StructuredTool.from_function(
            func=self._delete_file,
            name="delete_file",
            description="Deletes a file or directory safely from the workspace with OCC versioning.",
            args_schema=DeleteFileInput,
        )
        self.rename_file_tool = StructuredTool.from_function(
            func=self._rename_file,
            name="rename_file",
            description="Renames or moves a file from old_filepath to new_filepath safely in workspace.",
            args_schema=RenameFileInput,
        )
        self.move_file_tool = StructuredTool.from_function(
            func=self._move_file,
            name="move_file",
            description="Moves a source file into a target directory safely in workspace.",
            args_schema=MoveFileInput,
        )
        self.apply_patch_tool = StructuredTool.from_function(
            func=self._apply_patch,
            name="apply_patch",
            description="Applies standard unified diff patch (--- a/file +++ b/file @@ ... @@) across files in the workspace atomically.",
            args_schema=ApplyPatchInput,
        )
        self.list_dir_tool = StructuredTool.from_function(
            func=self._list_directory,
            name="list_directory",
            description="Lists files and subdirectories in the workspace.",
            args_schema=ListDirectoryInput,
        )
        self.terminal_tool = StructuredTool.from_function(
            func=self._run_terminal_command,
            name="terminal_execute",
            description="Executes a test runner, compiler, or build command in the workspace.",
            args_schema=TerminalExecutionInput,
        )
        self.syntax_checker_tool = StructuredTool.from_function(
            func=self._syntax_check,
            name="ast_syntax_check",
            description="Validates Python AST syntax (fail fast) before execution.",
            args_schema=SyntaxCheckInput,
        )
        self.regex_grep_tool = StructuredTool.from_function(
            func=self._grep_search,
            name="regex_grep",
            description="Searches function definitions, imports, or patterns across workspace files.",
            args_schema=GrepSearchInput,
        )
        self.find_symbol_tool = StructuredTool.from_function(
            func=self._find_symbol,
            name="find_symbol",
            description="Searches AST symbols (classes, functions, methods) with line bounds, parameters, and docstrings.",
            args_schema=FindSymbolInput,
        )
        self.find_references_tool = StructuredTool.from_function(
            func=self._find_references,
            name="find_references",
            description="Finds all files, line numbers, and callers where a symbol is imported or used.",
            args_schema=FindReferencesInput,
        )
        self.get_dependencies_tool = StructuredTool.from_function(
            func=self._get_dependencies,
            name="get_dependencies",
            description="Finds upstream imports/dependencies and downstream dependent files for a module or symbol.",
            args_schema=GetDependenciesInput,
        )
        self.get_codebase_map_tool = StructuredTool.from_function(
            func=self._get_codebase_map,
            name="get_codebase_map",
            description="Generates a compact hierarchical symbol outline map of classes and functions in the project.",
            args_schema=GetCodebaseMapInput,
        )
        self.semantic_code_search_tool = StructuredTool.from_function(
            func=self._semantic_code_search,
            name="semantic_code_search",
            description="Searches code symbols, methods, and classes using semantic/BM25 concept matching.",
            args_schema=SemanticCodeSearchInput,
        )
        self.get_call_graph_tool = StructuredTool.from_function(
            func=self._get_call_graph,
            name="get_call_graph",
            description="Traverses callers (upstream) and callees (downstream) for a symbol or function with depth traversal.",
            args_schema=GetCallGraphInput,
        )
        self.get_impact_radius_tool = StructuredTool.from_function(
            func=self._get_impact_radius,
            name="get_impact_radius",
            description="Calculates the blast radius / impact analysis if a file or symbol is modified, identifying affected files and callers.",
            args_schema=GetImpactRadiusInput,
        )
        self.get_architecture_summary_tool = StructuredTool.from_function(
            func=self._get_architecture_summary,
            name="get_architecture_summary",
            description="Retrieves the detected frameworks, architectural layers, entrypoints, and core classes.",
            args_schema=GetArchitectureSummaryInput,
        )
        self.query_codebase_graph_tool = StructuredTool.from_function(
            func=self._query_codebase_graph,
            name="query_codebase_graph",
            description="Queries Codebase Memory (CBM) to retrieve a token-bounded sub-graph of related symbols, interfaces, and dependencies using GraphRAG.",
            args_schema=QueryCodebaseGraphInput,
        )
        self.get_symbol_neighbors_tool = StructuredTool.from_function(
            func=self._get_symbol_neighbors,
            name="get_symbol_neighbors",
            description="Retrieves upstream callers and downstream callees (1-2 hops) for a symbol directly from Codebase Memory.",
            args_schema=GetSymbolNeighborsInput,
        )
        self.get_architecture_slice_tool = StructuredTool.from_function(
            func=self._get_architecture_slice,
            name="get_architecture_slice",
            description="Retrieves the architectural slice, layer boundaries, and framework conventions for a specific target file.",
            args_schema=GetArchitectureSliceInput,
        )
        self.request_more_evidence_tool = StructuredTool.from_function(
            func=self._request_more_evidence,
            name="request_more_evidence",
            description="Requests missing symbols, file contents, or conceptual architecture evidence before writing code.",
            args_schema=RequestMoreEvidenceInput,
        )
        self.complete_task_tool = StructuredTool.from_function(
            func=self._complete_task,
            name="complete_task",
            description="Call this tool when you have finished the task and verified results.",
            args_schema=CompleteTaskInput,
        )
        self.get_workspace_changes_tool = StructuredTool.from_function(
            func=self._get_workspace_changes,
            name="get_workspace_changes",
            description="Inspects file modifications, cryptographic hashes (before/after SHA-256), unified diffs, lines added/removed, and AST symbol changes in the workspace.",
            args_schema=GetWorkspaceChangesInput,
        )
        self.state_logger_tool = StructuredTool.from_function(
            func=self._log_state_transition,
            name="state_transition_logger",
            description="Persists transition events, cycle counts, and checkpoint metadata.",
            args_schema=StateTransitionLogInput,
        )
        self.search_skills_tool = StructuredTool.from_function(
            func=self._search_skills,
            name="search_skills",
            description="Searches the Skill Registry for domain practices, tools, or handbooks by query or category.",
            args_schema=SearchSkillsInput,
        )
        self.load_skill_tool = StructuredTool.from_function(
            func=self._load_skill,
            name="load_skill",
            description="Retrieves the full instructions, guidelines, scripts, and references for a specific skill from skills.sh.",
            args_schema=LoadSkillInput,
        )
        self.read_skill_ref_tool = StructuredTool.from_function(
            func=self._read_skill_reference,
            name="read_skill_reference",
            description="Reads reference material or rules associated with a skill (e.g. implementation.md, candidates.md).",
            args_schema=ReadSkillReferenceInput,
        )
        self.execute_skill_script_tool = StructuredTool.from_function(
            func=self._execute_skill_script,
            name="execute_skill_script",
            description="Executes an automation or verification script provided by a skill package.",
            args_schema=ExecuteSkillScriptInput,
        )
        self.install_skill_tool = StructuredTool.from_function(
            func=self._install_skill,
            name="install_skill",
            description="Installs a skill from the skills.sh ecosystem or registers a new skill.",
            args_schema=InstallSkillInput,
        )
        self.spawn_subtasks_tool = StructuredTool.from_function(
            func=self._spawn_subtasks,
            name="spawn_subtasks",
            description="Dynamically spawns child subtasks and grafts them into the active execution DAG.",
            args_schema=SpawnSubtasksInput,
        )

        # Optional communication tools
        if self.comm_tools:
            self.send_msg_tool = StructuredTool.from_function(
                func=self.comm_tools.send_agent_message,
                name="send_agent_message",
                description="Send a structured message, artifact, or deliverable to another agent or task ID.",
                args_schema=SendAgentMessageInput,
            )
            self.query_agent_tool = StructuredTool.from_function(
                func=self.comm_tools.query_agent,
                name="query_agent",
                description="Synchronously consult another specialized persona mid-flight and receive an answer.",
                args_schema=QueryAgentInput,
            )
            self.publish_finding_tool = StructuredTool.from_function(
                func=self.comm_tools.publish_finding,
                name="publish_finding",
                description="Publish an architectural constraint, security boundary, or finding to a topic channel.",
                args_schema=PublishFindingInput,
            )
            self.read_inbox_tool = StructuredTool.from_function(
                func=self.comm_tools.read_inbox,
                name="read_inbox",
                description="Read pending messages and requests delivered to this agent or subtask.",
                args_schema=ReadInboxInput,
            )

        # Git tools
        self.git_server = GitMCPServer(repo_dir=self.workspace.root_dir, sandbox=self.sandbox)
        self.git_status_tool = StructuredTool.from_function(
            func=self.git_server.git_status,
            name="git_status",
            description="Returns Git working tree status (staged, unstaged, untracked files).",
            args_schema=GitStatusInput,
        )
        self.git_diff_tool = StructuredTool.from_function(
            func=self.git_server.git_diff,
            name="git_diff",
            description="Generates Git diff of unstaged/staged changes or against a target branch/commit.",
            args_schema=GitDiffInput,
        )
        self.git_log_tool = StructuredTool.from_function(
            func=self.git_server.git_log,
            name="git_log",
            description="Returns recent Git commit history log.",
            args_schema=GitLogInput,
        )
        self.git_show_tool = StructuredTool.from_function(
            func=self.git_server.git_show,
            name="git_show",
            description="Inspects metadata, message, and diff of a specific Git commit.",
            args_schema=GitShowInput,
        )
        self.git_blame_tool = StructuredTool.from_function(
            func=self.git_server.git_blame,
            name="git_blame",
            description="Inspects line-by-line commit authorship and provenance for a file in Git.",
            args_schema=GitBlameInput,
        )
        self.git_branch_tool = StructuredTool.from_function(
            func=self.git_server.git_branch,
            name="git_branch",
            description="Lists existing Git branches, creates a new branch, or deletes a branch.",
            args_schema=GitBranchInput,
        )
        self.git_checkout_tool = StructuredTool.from_function(
            func=self.git_server.git_checkout,
            name="git_checkout",
            description="Switches Git branches or creates and switches to a new branch.",
            args_schema=GitCheckoutInput,
        )
        self.git_commit_tool = StructuredTool.from_function(
            func=self.git_server.git_commit,
            name="git_commit",
            description="Stages modified files and creates a new Git commit with a descriptive message.",
            args_schema=GitCommitInput,
        )
        self.git_restore_tool = StructuredTool.from_function(
            func=self.git_server.git_restore,
            name="git_restore",
            description="Restores/discards modifications to working tree files or unstages files.",
            args_schema=GitRestoreInput,
        )
        self.git_patch_tool = StructuredTool.from_function(
            func=self.git_server.git_patch,
            name="git_patch",
            description="Exports a unified diff patch or applies a unified diff patch to the workspace.",
            args_schema=GitPatchInput,
        )
        self.git_init_tool = StructuredTool.from_function(
            func=self.git_server.git_init,
            name="git_init",
            description="Initializes a new Git repository in the workspace with safe default configuration.",
            args_schema=GitInitInput,
        )
        self.detect_env_tool = StructuredTool.from_function(
            func=self._detect_project_environment,
            name="detect_project_environment",
            description="Detects project programming language, framework, build tool, test runner, test commands, and source/test conventions.",
            args_schema=DetectProjectEnvironmentInput,
        )
        self.static_code_check_tool = StructuredTool.from_function(
            func=self._run_static_code_check,
            name="static_code_check",
            description="Runs independent multi-tier static verification (AST/syntax, compiler/typecheck, linter, and runtime import smoke checks) on files or the entire workspace.",
            args_schema=StaticCodeCheckInput,
        )
        self.inspect_tests_tool = StructuredTool.from_function(
            func=self._inspect_existing_tests,
            name="inspect_existing_tests",
            description="Inspects repository for existing test suites, test frameworks (pytest, unittest, vitest, jest), conventions, fixtures, mocks, and CI test commands.",
            args_schema=InspectExistingTestsInput,
        )
        self.run_build_pipeline_tool = StructuredTool.from_function(
            func=self._run_build_pipeline,
            name="run_build_pipeline",
            description="Executes staged build verification pipeline (INSTALL_DEPENDENCIES, BUILD, COMPILE_TYPECHECK, LINT, UNIT_TEST, INTEGRATION_TEST) with fail-fast diagnostics.",
            args_schema=RunBuildPipelineInput,
        )
        self.run_static_analysis_tool = StructuredTool.from_function(
            func=self._run_static_analysis,
            name="run_static_analysis",
            description="Runs polyglot static analysis (ruff, mypy, pyright, eslint, tsc, semgrep, clippy, govet) with structured LSP diagnostics.",
            args_schema=RunStaticAnalysisInput,
        )
        self.verify_ground_truth_tool = StructuredTool.from_function(
            func=self._verify_ground_truth,
            name="verify_ground_truth",
            description="Evaluates deterministic ground-truth verification matrix (static analysis, build pipeline, baseline regressions, diff coverage, spec traceability, anti-tautology).",
            args_schema=VerifyGroundTruthInput,
        )
        self.search_memory_tool = StructuredTool.from_function(
            func=self._search_project_memory,
            name="search_project_memory",
            description="Searches long-term project memory for past architectural decisions, bug patterns, conventions, or API contracts.",
            args_schema=SearchProjectMemoryInput,
        )
        self.record_memory_tool = StructuredTool.from_function(
            func=self._record_project_memory,
            name="record_project_memory",
            description="Persists an architectural decision, bug pattern, convention, or API contract into long-term project memory.",
            args_schema=RecordProjectMemoryInput,
        )
        self.update_scratchpad_tool = StructuredTool.from_function(
            func=self._update_scratchpad,
            name="update_scratchpad",
            description="Appends a working finding, hypothesis, or note to the active working memory scratchpad.",
            args_schema=UpdateScratchpadInput,
        )

        self._tools = {
            "read_file": self.read_file_tool,
            "write_file": self.write_file_tool,
            "replace_file_content": self.replace_content_tool,
            "edit_file": self.replace_content_tool,
            "insert_lines": self.insert_lines_tool,
            "delete_lines": self.delete_lines_tool,
            "delete_file": self.delete_file_tool,
            "remove_file": self.delete_file_tool,
            "rename_file": self.rename_file_tool,
            "move_file": self.move_file_tool,
            "apply_patch": self.apply_patch_tool,
            "apply_diff_blocks": self.apply_diff_blocks_tool,
            "diff_blocks": self.apply_diff_blocks_tool,
            "list_directory": self.list_dir_tool,
            "list_files": self.list_dir_tool,
            "run_command": self.terminal_tool,
            "terminal_execute": self.terminal_tool,
            "syntax_check": self.syntax_checker_tool,
            "ast_syntax_check": self.syntax_checker_tool,
            "grep_search": self.regex_grep_tool,
            "regex_grep": self.regex_grep_tool,
            "find_symbol": self.find_symbol_tool,
            "find_references": self.find_references_tool,
            "get_dependencies": self.get_dependencies_tool,
            "get_codebase_map": self.get_codebase_map_tool,
            "codebase_map": self.get_codebase_map_tool,
            "search_skills": self.search_skills_tool,
            "load_skill": self.load_skill_tool,
            "read_skill_reference": self.read_skill_ref_tool,
            "execute_skill_script": self.execute_skill_script_tool,
            "install_skill": self.install_skill_tool,
            "spawn_subtasks": self.spawn_subtasks_tool,
            "complete_task": self.complete_task_tool,
            "get_workspace_changes": self.get_workspace_changes_tool,
            "get_changes": self.get_workspace_changes_tool,
            "log_transition": self.state_logger_tool,
            "state_transition_logger": self.state_logger_tool,
            "git_status": self.git_status_tool,
            "git_diff": self.git_diff_tool,
            "git_log": self.git_log_tool,
            "git_show": self.git_show_tool,
            "git_blame": self.git_blame_tool,
            "git_branch": self.git_branch_tool,
            "git_checkout": self.git_checkout_tool,
            "git_commit": self.git_commit_tool,
            "git_restore": self.git_restore_tool,
            "git_patch": self.git_patch_tool,
            "git_init": self.git_init_tool,
            "rollback_to_checkpoint": self.rollback_tool,
            "rollback_workspace": self.rollback_tool,
            "detect_project_environment": self.detect_env_tool,
            "detect_environment": self.detect_env_tool,
            "static_code_check": self.static_code_check_tool,
            "static_verification": self.static_code_check_tool,
            "code_check": self.static_code_check_tool,
            "inspect_existing_tests": self.inspect_tests_tool,
            "inspect_tests": self.inspect_tests_tool,
            "test_inspection": self.inspect_tests_tool,
            "run_build_pipeline": self.run_build_pipeline_tool,
            "build_pipeline": self.run_build_pipeline_tool,
            "build_verification": self.run_build_pipeline_tool,
            "run_static_analysis": self.run_static_analysis_tool,
            "static_analysis": self.run_static_analysis_tool,
            "lint": self.run_static_analysis_tool,
            "sast_scan": self.run_static_analysis_tool,
            "verify_ground_truth": self.verify_ground_truth_tool,
            "ground_truth_verification": self.verify_ground_truth_tool,
            "ground_truth": self.verify_ground_truth_tool,
            "search_project_memory": self.search_memory_tool,
            "search_memory": self.search_memory_tool,
            "record_project_memory": self.record_memory_tool,
            "record_memory": self.record_memory_tool,
            "update_scratchpad": self.update_scratchpad_tool,
            "query_codebase_graph": self.query_codebase_graph_tool,
            "query_graph": self.query_codebase_graph_tool,
            "get_symbol_neighbors": self.get_symbol_neighbors_tool,
            "symbol_neighbors": self.get_symbol_neighbors_tool,
            "get_architecture_slice": self.get_architecture_slice_tool,
            "architecture_slice": self.get_architecture_slice_tool,
            "query_specification": self.query_specification_tool,
            "query_spec": self.query_specification_tool,
            "query_architecture": self.query_architecture_tool,
            "query_arch": self.query_architecture_tool,
            "get_task_artifact": self.get_task_artifact_tool,
            "task_artifact": self.get_task_artifact_tool,
            "generate_edit_plan": self.generate_edit_plan_tool,
            "create_edit_plan": self.generate_edit_plan_tool,
            "get_edit_plan": self.get_edit_plan_tool,
            "edit_plan": self.get_edit_plan_tool,
            "update_edit_plan_step": self.update_edit_plan_step_tool,
            "update_edit_step": self.update_edit_plan_step_tool,
        }


        if self.comm_tools:
            self._tools["send_agent_message"] = self.send_msg_tool
            self._tools["query_agent"] = self.query_agent_tool
            self._tools["publish_finding"] = self.publish_finding_tool
            self._tools["read_inbox"] = self.read_inbox_tool

        # Register Swarm Tools
        self._tools["spawn_subagent"] = self.spawn_subagent_tool
        self._tools["delegate_subtask"] = self.delegate_subtask_tool
        self._tools["handoff_to_agent"] = self.handoff_to_agent_tool
        self._tools["discover_swarm_agents"] = self.discover_swarm_agents_tool
        self._tools["post_to_blackboard"] = self.post_to_blackboard_tool
        self._tools["read_from_blackboard"] = self.read_from_blackboard_tool
        self._tools["request_consensus"] = self.request_consensus_tool
        self._tools["terminate_subagent"] = self.terminate_subagent_tool

        # Wire core ToolRegistry engine (Issue #70)
        from .registry import ToolRegistry
        self.registry = ToolRegistry()
        self._init_tool_registry()

    def _init_tool_registry(self):
        """Populates self.registry with all native tools, aliases, and health checks."""
        unique_tools = {}
        tool_aliases = {}
        for alias, tool_inst in self._tools.items():
            t_name = getattr(tool_inst, "name", str(alias))
            if t_name not in unique_tools:
                unique_tools[t_name] = tool_inst
                tool_aliases[t_name] = []
            if alias.lower() != t_name.lower():
                tool_aliases[t_name].append(alias)

        for t_name, tool_inst in unique_tools.items():
            h_fn = None
            if t_name in ("read_file", "write_file", "list_directory", "replace_file_content", "insert_lines", "delete_lines", "apply_diff_blocks"):
                h_fn = lambda: (self.workspace.root_dir.exists(), f"Workspace directory exists: {self.workspace.root_dir}")
            elif t_name in ("terminal_execute", "run_command", "ast_syntax_check"):
                h_fn = lambda: (self.sandbox is not None, "Sandbox runtime attached")
            elif t_name.startswith("git_"):
                h_fn = lambda: (self.git_server is not None, "GitMCPServer attached")
            elif t_name in ("query_codebase_graph", "get_symbol_neighbors", "get_architecture_slice"):
                h_fn = lambda: (self.cbm is not None, "CodebaseMemory graph active")
            elif "skill" in t_name:
                h_fn = lambda: (self.skill_registry is not None, "SkillRegistry active")
            from ..security.trust_boundaries import ToolProvenance
            prov = ToolProvenance.WORKSPACE_DATA
            if t_name in (
                "query_specification", "query_spec",
                "query_architecture", "query_arch",
                "get_task_artifact", "task_artifact",
                "complete_task", "rollback_to_checkpoint",
                "execute_rollback", "log_state_transition",
                "state_transition_logger", "send_agent_message",
                "query_agent", "publish_finding", "read_inbox",
            ):
                prov = ToolProvenance.INTERNAL_CONTROL
            elif t_name in ("terminal_execute", "run_command", "ast_syntax_check", "static_code_check", "execute_skill_script"):
                prov = ToolProvenance.EXECUTION_ENVIRONMENT
            elif t_name.startswith("mcp_") or t_name.startswith("external_"):
                prov = ToolProvenance.EXTERNAL_MCP

            self.registry.register(
                tool=tool_inst,
                name=t_name,
                aliases=tool_aliases.get(t_name, []),
                health_check_fn=h_fn,
                provenance=prov,
            )

    def register(self, tool: Any, **kwargs) -> Any:
        """Dynamically registers a tool in both self.registry and self._tools."""
        entry = self.registry.register(tool, **kwargs)
        self._tools[entry.name] = entry.tool_instance
        self._tools[entry.name.lower()] = entry.tool_instance
        return entry

    def unregister(self, name: str) -> bool:
        """Deregisters a tool by name or alias from self.registry and self._tools."""
        res = self.registry.unregister(name)
        norm = name.lower().strip()
        if norm in self._tools:
            del self._tools[norm]
        keys_to_del = [k for k, v in self._tools.items() if getattr(v, "name", "").lower() == norm]
        for k in keys_to_del:
            self._tools.pop(k, None)
        return res

    def discover(self, query: str = "", tags: Optional[List[str]] = None, category: Optional[str] = None, agent_role: Optional[str] = None, top_k: int = 5) -> List[Any]:
        """Discovers tools semantically or by tag matching."""
        return self.registry.discover(query=query, tags=tags, category=category, agent_role=agent_role, top_k=top_k)

    def get(self, name: str) -> Any:
        """Retrieves a tool entry, auto-syncing from _tools if directly assigned."""
        if not name:
            return None
        entry = self.registry.get(name)
        if entry:
            return entry
        norm = name.lower().strip()
        if norm in self._tools:
            tool_inst = self._tools[norm]
            return self.registry.register(tool_inst, name=getattr(tool_inst, "name", norm))
        for k, tool_inst in self._tools.items():
            if getattr(tool_inst, "name", "").lower() == norm:
                return self.registry.register(tool_inst, name=getattr(tool_inst, "name", k))

        # Strip prefixes (mcp_, builtin__, native_, <server>__)
        bare = norm
        if "__" in bare:
            bare = bare.split("__")[-1]
        prefixes = ("mcp_", "builtin_", "native_", "mcp-server-filesystem_", "mcp-server-git_", "mcp-server-terminal_", "mcp-server-memory_", "filesystem_", "git_", "terminal_", "memory_")
        changed = True
        while changed:
            changed = False
            if bare in self._tools:
                break
            for pfx in prefixes:
                if bare.startswith(pfx) and len(bare) > len(pfx) and bare not in self._tools:
                    bare = bare[len(pfx):]
                    changed = True
                    break

        if bare in self._tools:
            tool_inst = self._tools[bare]
            return self.registry.register(tool_inst, name=getattr(tool_inst, "name", bare))
        for k, tool_inst in self._tools.items():
            if getattr(tool_inst, "name", "").lower() == bare:
                return self.registry.register(tool_inst, name=getattr(tool_inst, "name", k))
        return None

    def get_schema(self, name: str) -> Optional[Dict[str, Any]]:
        """Returns OpenAI function schema for an individual tool."""
        entry = self.get(name)
        return entry.get_schema() if entry else None

    def authorize(self, name: str, args: Dict[str, Any], agent_role: Optional[Any] = None, task_permissions: Optional[Any] = None) -> Any:
        """Evaluates whether tool execution is authorized under policy."""
        self.get(name)
        return self.registry.authorize(name, args, agent_role=agent_role, task_permissions=task_permissions, workspace=self.workspace)

    def execute(
        self,
        name: str,
        args: Dict[str, Any],
        caller_role: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None,
        task_permissions: Optional[Any] = None,
    ) -> Any:
        """Executes a tool through the standardized pipeline."""
        self.get(name)
        return self.registry.execute(
            name,
            args,
            caller_role=caller_role,
            task_permissions=task_permissions,
            workspace=self.workspace,
            context=context,
        )

    def health_check(self, name: Optional[str] = None) -> Dict[str, Any]:
        """Runs active liveness and health diagnostics on registered tools."""
        return self.registry.health_check(name=name)

    def get_all_tools(self) -> List[Any]:
        """Returns all unique registered structured tools."""
        unique_tools = []
        seen = set()
        for tool in self._tools.values():
            if tool.name not in seen:
                seen.add(tool.name)
                unique_tools.append(tool)
        return unique_tools

    def get_tools(self) -> List[Any]:
        """Alias for get_all_tools()."""
        return self.get_all_tools()

    def list_tools(self) -> Dict[str, Any]:
        """Returns dictionary of unique registered tools."""
        unique_tools = {}
        for tool in self._tools.values():
            name = getattr(tool, "name", str(tool))
            if name not in unique_tools:
                unique_tools[name] = tool
        return unique_tools

    def resolve_tools_for_capabilities(self, capability_ids: List[str]) -> List[Any]:
        """Resolves tool instances for the given list of capability IDs."""
        from ..capabilities.model import default_capability_registry
        tool_names = default_capability_registry.resolve_tools(capability_ids)
        resolved = []
        seen = set()
        for t_name in tool_names:
            t_inst = self._tools.get(t_name) or self._tools.get(t_name.lower())
            if t_inst and t_inst.name not in seen:
                seen.add(t_inst.name)
                resolved.append(t_inst)
        if self.complete_task_tool.name not in seen:
            resolved.append(self.complete_task_tool)
        return resolved

    def get_tools_for_agent(self, agent_name: str) -> List[Any]:
        """Returns the specific tools allowed for a given agent."""
        agent_clean = agent_name.upper().strip()

        # 1. Dynamic Agent Manifest / Capability Resolution
        if self.agent_registry:
            manifest = self.agent_registry.get(agent_name)
            if manifest and agent_clean not in (
                "TASKORCHESTRATOR", "PLANNER", "SPECIFICATION", "ARCHITECTURE", "CODER", "TESTER", "REVIEWER"
            ):
                effective_names = manifest.get_effective_tools()
                if effective_names:
                    resolved = []
                    seen = set()
                    for t_name in effective_names:
                        t_inst = self._tools.get(t_name) or self._tools.get(t_name.lower())
                        if t_inst and t_inst.name not in seen:
                            seen.add(t_inst.name)
                            resolved.append(t_inst)
                    if self.complete_task_tool.name not in seen:
                        resolved.append(self.complete_task_tool)
                    return resolved

        # 2. Check if agent_name refers directly to a registered capability
        from ..capabilities.model import default_capability_registry
        if default_capability_registry.get(agent_name):
            return self.resolve_tools_for_capabilities([agent_name])

        readonly_skill_tools = [
            self.search_skills_tool,
            self.load_skill_tool,
            self.read_skill_ref_tool,
        ]
        common_skill_tools = [
            self.search_skills_tool,
            self.load_skill_tool,
            self.read_skill_ref_tool,
            self.execute_skill_script_tool,
        ]
        swarm_tools_list = [
            self.spawn_subagent_tool,
            self.delegate_subtask_tool,
            self.handoff_to_agent_tool,
            self.discover_swarm_agents_tool,
            self.post_to_blackboard_tool,
            self.read_from_blackboard_tool,
            self.request_consensus_tool,
            self.terminate_subagent_tool,
        ]
        common_skill_tools.extend(swarm_tools_list)
        readonly_skill_tools.extend(swarm_tools_list)
        if self.comm_tools:
            comm_list = [
                self.send_msg_tool,
                self.query_agent_tool,
                self.publish_finding_tool,
                self.read_inbox_tool,
            ]
            common_skill_tools.extend(comm_list)
            readonly_skill_tools.extend(comm_list)

        if agent_clean == "TASKORCHESTRATOR":
            return [self.state_logger_tool, self.complete_task_tool, self.get_codebase_map_tool, self.detect_env_tool, self.get_architecture_summary_tool, self.query_codebase_graph_tool, self.get_architecture_slice_tool] + readonly_skill_tools
        elif agent_clean == "PLANNER":
            return [
                self.read_file_tool,
                self.list_dir_tool,
                self.regex_grep_tool,
                self.find_symbol_tool,
                self.find_references_tool,
                self.get_dependencies_tool,
                self.semantic_code_search_tool,
                self.get_call_graph_tool,
                self.get_impact_radius_tool,
                self.get_architecture_summary_tool,
                self.get_codebase_map_tool,
                self.query_codebase_graph_tool,
                self.get_symbol_neighbors_tool,
                self.get_architecture_slice_tool,
                self.request_more_evidence_tool,
                self.detect_env_tool,
                self.inspect_tests_tool,
                self.run_build_pipeline_tool,
                self.run_static_analysis_tool,
                self.static_code_check_tool,
                self.verify_ground_truth_tool,
                self.get_workspace_changes_tool,
                self.rollback_tool,
                self.git_status_tool,
                self.git_log_tool,
                self.git_branch_tool,
                self.complete_task_tool,
            ] + readonly_skill_tools
        elif agent_clean == "SPECIFICATION":
            return [
                self.read_file_tool,
                self.write_file_tool,
                self.list_dir_tool,
                self.find_symbol_tool,
                self.get_dependencies_tool,
                self.semantic_code_search_tool,
                self.get_architecture_summary_tool,
                self.get_codebase_map_tool,
                self.query_codebase_graph_tool,
                self.get_symbol_neighbors_tool,
                self.get_architecture_slice_tool,
                self.request_more_evidence_tool,
                self.detect_env_tool,
                self.complete_task_tool,
            ] + readonly_skill_tools
        elif agent_clean == "ARCHITECTURE":
            return [
                self.list_dir_tool,
                self.read_file_tool,
                self.write_file_tool,
                self.find_symbol_tool,
                self.find_references_tool,
                self.get_dependencies_tool,
                self.semantic_code_search_tool,
                self.get_call_graph_tool,
                self.get_impact_radius_tool,
                self.get_architecture_summary_tool,
                self.get_codebase_map_tool,
                self.query_codebase_graph_tool,
                self.get_symbol_neighbors_tool,
                self.get_architecture_slice_tool,
                self.request_more_evidence_tool,
                self.detect_env_tool,
                self.complete_task_tool,
            ] + readonly_skill_tools
        elif agent_clean == "CODER":
            return [
                self.read_file_tool,
                self.write_file_tool,
                self.replace_content_tool,
                self.insert_lines_tool,
                self.delete_lines_tool,
                self.delete_file_tool,
                self.rename_file_tool,
                self.move_file_tool,
                self.apply_patch_tool,
                self.apply_diff_blocks_tool,
                self.list_dir_tool,
                self.syntax_checker_tool,
                self.static_code_check_tool,
                self.run_static_analysis_tool,
                self.verify_ground_truth_tool,
                self.regex_grep_tool,
                self.find_symbol_tool,
                self.find_references_tool,
                self.get_dependencies_tool,
                self.semantic_code_search_tool,
                self.get_call_graph_tool,
                self.get_impact_radius_tool,
                self.get_architecture_summary_tool,
                self.get_codebase_map_tool,
                self.query_codebase_graph_tool,
                self.get_symbol_neighbors_tool,
                self.get_architecture_slice_tool,
                self.request_more_evidence_tool,
                self.detect_env_tool,
                self.inspect_tests_tool,
                self.run_build_pipeline_tool,
                self.get_workspace_changes_tool,
                self.rollback_tool,
                self.terminal_tool,
                self.git_status_tool,
                self.git_diff_tool,
                self.git_commit_tool,
                self.git_restore_tool,
                self.git_patch_tool,
                self.query_specification_tool,
                self.query_architecture_tool,
                self.get_task_artifact_tool,
                self.generate_edit_plan_tool,
                self.get_edit_plan_tool,
                self.update_edit_plan_step_tool,
                self.complete_task_tool,
            ] + common_skill_tools
        elif agent_clean == "TESTER":
            return [
                self.read_file_tool,
                self.write_file_tool,
                self.replace_content_tool,
                self.insert_lines_tool,
                self.delete_lines_tool,
                self.delete_file_tool,
                self.rename_file_tool,
                self.move_file_tool,
                self.apply_patch_tool,
                self.list_dir_tool,
                self.terminal_tool,
                self.detect_env_tool,
                self.inspect_tests_tool,
                self.run_build_pipeline_tool,
                self.run_static_analysis_tool,
                self.verify_ground_truth_tool,
                self.syntax_checker_tool,
                self.static_code_check_tool,
                self.find_symbol_tool,
                self.find_references_tool,
                self.get_dependencies_tool,
                self.semantic_code_search_tool,
                self.get_call_graph_tool,
                self.get_impact_radius_tool,
                self.get_architecture_summary_tool,
                self.get_codebase_map_tool,
                self.query_codebase_graph_tool,
                self.get_symbol_neighbors_tool,
                self.get_architecture_slice_tool,
                self.request_more_evidence_tool,
                self.get_workspace_changes_tool,
                self.query_specification_tool,
                self.query_architecture_tool,
                self.get_task_artifact_tool,
                self.generate_edit_plan_tool,
                self.get_edit_plan_tool,
                self.update_edit_plan_step_tool,
                self.complete_task_tool,
            ] + common_skill_tools
        elif agent_clean == "REVIEWER":
            return [
                self.read_file_tool,
                self.list_dir_tool,
                self.regex_grep_tool,
                self.find_symbol_tool,
                self.find_references_tool,
                self.get_dependencies_tool,
                self.semantic_code_search_tool,
                self.get_call_graph_tool,
                self.get_impact_radius_tool,
                self.get_architecture_summary_tool,
                self.get_codebase_map_tool,
                self.query_codebase_graph_tool,
                self.get_symbol_neighbors_tool,
                self.get_architecture_slice_tool,
                self.detect_env_tool,
                self.inspect_tests_tool,
                self.run_build_pipeline_tool,
                self.run_static_analysis_tool,
                self.static_code_check_tool,
                self.verify_ground_truth_tool,
                self.get_workspace_changes_tool,
                self.rollback_tool,
                self.git_status_tool,
                self.git_diff_tool,

                self.git_log_tool,
                self.git_blame_tool,
                self.git_show_tool,
                self.query_specification_tool,
                self.query_architecture_tool,
                self.get_task_artifact_tool,
                self.complete_task_tool,
            ] + readonly_skill_tools

        return list(self._tools.values())

    def get_schemas(self, agent_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns schemas for tools, optionally filtered by agent role."""
        if agent_name:
            return self.get_schemas_for_agent(agent_name)
        seen = set()
        schemas = []
        for name, tool in self._tools.items():
            if tool.name in seen:
                continue
            seen.add(tool.name)
            args_schema = (
                tool.args_schema.model_json_schema()
                if tool.args_schema and hasattr(tool.args_schema, "model_json_schema")
                else (tool.args_schema.schema() if tool.args_schema else {"type": "object", "properties": {}})
            )
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": args_schema.get("properties", {}),
                        "required": args_schema.get("required", []),
                    },
                },
            })
        return schemas

    def get_schemas_for_agent(self, agent_name: str) -> List[Dict[str, Any]]:
        tools = self.get_tools_for_agent(agent_name)
        schemas = []
        for tool in tools:
            args_schema = (
                tool.args_schema.model_json_schema()
                if tool.args_schema and hasattr(tool.args_schema, "model_json_schema")
                else (tool.args_schema.schema() if tool.args_schema else {"type": "object", "properties": {}})
            )
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": args_schema.get("properties", {}),
                        "required": args_schema.get("required", []),
                    },
                },
            })
        return schemas

    def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        caller_role: Optional[str] = None,
        agent_name: Optional[str] = None,
        task_permissions: Optional[Any] = None,
        **kwargs: Any,
    ) -> Any:
        arguments = arguments if isinstance(arguments, dict) else {}
        eff_role = caller_role or agent_name or kwargs.get("agent_role")
        eff_perms = task_permissions or kwargs.get("permissions") or kwargs.get("task_permissions")
        exec_res = self.execute(name, arguments, caller_role=eff_role, task_permissions=eff_perms)
        if exec_res.success:
            if isinstance(exec_res.data, dict):
                res = dict(exec_res.data)
            else:
                res = {"output": exec_res.output, "success": True}
            if exec_res.provenance:
                res["_provenance"] = exec_res.provenance
            return res
        else:
            res = {"error": exec_res.error, "success": False, "output": exec_res.error}
            if exec_res.metadata and "suggested_action" in exec_res.metadata:
                res["suggested_action"] = exec_res.metadata["suggested_action"]
            if exec_res.provenance:
                res["_provenance"] = exec_res.provenance
            return res

    def _read_file(self, filepath: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> Dict[str, Any]:
        try:
            content = self.workspace.read_file(filepath)
        except Exception as e:
            return {"error": str(e), "success": False, "output": ""}

        if content is None:
            return {"error": f"File '{filepath}' not found.", "success": False, "output": ""}

        from .change_tracker import compute_sha256
        file_hash = compute_sha256(content)
        version_str = file_hash[:12]
        rev = getattr(self.workspace, "get_file_revision", lambda p: 1)(filepath)
        version_tag = f"v{rev}-{version_str[:8]}"

        from ..security.secrets import secret_manager
        content = secret_manager.redact_text(content)

        lines = content.splitlines()
        total_lines = len(lines)

        if start_line is not None or end_line is not None:
            s = max(1, start_line or 1)
            e = min(total_lines, end_line or total_lines)
            sliced_lines = lines[s - 1 : e]
            numbered = [f"{i}: {line}" for i, line in enumerate(sliced_lines, start=s)]
            text = "\n".join(numbered)
            header = f"[File: {filepath} | Version: {version_str} ({version_tag}) | Total Lines: {total_lines} | Viewing Lines {s}-{e}]\n"
            full_output = header + text
            return {
                "filepath": filepath,
                "content": text,
                "output": full_output,
                "header": header.strip(),
                "total_lines": total_lines,
                "start_line": s,
                "end_line": e,
                "file_hash": file_hash,
                "version": version_str,
                "revision": rev,
                "version_tag": version_tag,
                "success": True,
            }

        header = f"[File: {filepath} | Version: {version_str} ({version_tag}) | Total Lines: {total_lines}]\n"
        full_output = header + content
        return {
            "filepath": filepath,
            "content": content,
            "output": full_output,
            "header": header.strip(),
            "total_lines": total_lines,
            "file_hash": file_hash,
            "version": version_str,
            "revision": rev,
            "version_tag": version_tag,
            "success": True,
        }

    def _write_file(
        self,
        filepath: str,
        content: str,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            from .change_tracker import compute_sha256
            before_content = self.workspace.read_file(filepath)
            before_hash = compute_sha256(before_content) if before_content is not None else None
            target_hash = compute_sha256(content)
            is_no_op = (before_content is not None and before_hash == target_hash)

            self.workspace.write_file(
                filepath,
                content,
                expected_hash=expected_hash,
                operation_id=operation_id,
                expected_version=expected_version,
            )
            return {
                "filepath": filepath,
                "bytes_written": len(content.encode("utf-8")),
                "no_op": is_no_op,
                "already_applied": is_no_op,
                "file_hash": target_hash,
                "version": target_hash[:12],
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _replace_file_content(
        self,
        filepath: str,
        target_content: str,
        replacement_content: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        allow_multiple: bool = False,
        fuzzy: bool = True,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self.workspace.replace_file_content(
                rel_path=filepath,
                target_content=target_content,
                replacement_content=replacement_content,
                start_line=start_line,
                end_line=end_line,
                allow_multiple=allow_multiple,
                fuzzy=fuzzy,
                expected_hash=expected_hash,
                operation_id=operation_id,
                expected_version=expected_version,
            )
        except Exception as e:
            return {"error": str(e), "success": False}

    def _insert_lines(
        self,
        filepath: str,
        line_number: int,
        content: str,
        position: str = "after",
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self.workspace.insert_lines(
                rel_path=filepath,
                line_number=line_number,
                content=content,
                position=position,
                expected_hash=expected_hash,
                operation_id=operation_id,
                expected_version=expected_version,
            )
        except Exception as e:
            return {"error": str(e), "success": False}

    def _delete_lines(
        self,
        filepath: str,
        start_line: int,
        end_line: int,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self.workspace.delete_lines(
                rel_path=filepath,
                start_line=start_line,
                end_line=end_line,
                expected_hash=expected_hash,
                operation_id=operation_id,
                expected_version=expected_version,
            )
        except Exception as e:
            return {"error": str(e), "success": False}

    def _apply_diff_blocks(
        self,
        filepath: str,
        diff_blocks: str,
        fuzzy: bool = True,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self.workspace.apply_diff_blocks(
                rel_path=filepath,
                diff_blocks=diff_blocks,
                fuzzy=fuzzy,
                expected_hash=expected_hash,
                operation_id=operation_id,
                expected_version=expected_version,
            )
        except Exception as e:
            return {"error": str(e), "success": False}

    def _delete_file(
        self,
        filepath: str,
        expected_version: Optional[str] = None,
        expected_hash: Optional[str] = None,
        operation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            res = self.workspace.delete_file(
                rel_path=filepath,
                expected_version=expected_version,
                expected_hash=expected_hash,
                operation_id=operation_id,
            )
            return {
                "filepath": filepath,
                "deleted": res,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _rename_file(
        self,
        old_filepath: str,
        new_filepath: str,
        expected_version: Optional[str] = None,
        expected_hash: Optional[str] = None,
        overwrite: bool = False,
        operation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            res = self.workspace.rename_file(
                old_path=old_filepath,
                new_path=new_filepath,
                expected_version=expected_version,
                expected_hash=expected_hash,
                overwrite=overwrite,
                operation_id=operation_id,
            )
            return {
                "old_filepath": old_filepath,
                "new_filepath": new_filepath,
                "renamed": res,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _move_file(
        self,
        source_filepath: str,
        target_dir: str,
        expected_version: Optional[str] = None,
        expected_hash: Optional[str] = None,
        overwrite: bool = False,
        operation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            new_rel_path = self.workspace.move_file(
                source_path=source_filepath,
                target_dir=target_dir,
                expected_version=expected_version,
                expected_hash=expected_hash,
                overwrite=overwrite,
                operation_id=operation_id,
            )
            return {
                "source_filepath": source_filepath,
                "target_dir": target_dir,
                "new_filepath": new_rel_path,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _apply_patch(
        self,
        patch_content: str,
        fuzz_factor: int = 2,
        operation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self.workspace.apply_patch(
                patch_content=patch_content,
                fuzz_factor=fuzz_factor,
                operation_id=operation_id,
            )
        except Exception as e:
            return {"error": str(e), "success": False}

    def _generate_edit_plan(
        self,
        task_id: Optional[str] = None,
        objective: Optional[str] = None,
        inputs: Optional[List[str]] = None,
        outputs: Optional[List[str]] = None,
        acceptance_tests: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        try:
            from ..runtime.edit_plan import EditPlanManager
            task_info = {
                "task_id": task_id or "T-01",
                "objective": objective or "",
                "inputs": inputs or [],
                "outputs": outputs or [],
                "acceptance_tests": acceptance_tests or [],
            }
            spec = getattr(self.orchestrator_state, "specification_output", None) if self.orchestrator_state else None
            arch = getattr(self.orchestrator_state, "architecture_output", None) if self.orchestrator_state else None

            plan = EditPlanManager.generate_plan(
                task_info=task_info,
                workspace=self.workspace,
                code_graph=getattr(self, "code_graph", None),
                architecture=arch,
                specification=spec,
            )
            if not hasattr(self, "_active_edit_plans"):
                self._active_edit_plans = {}
            self._active_edit_plans[plan.task_id or "default"] = plan
            return {
                "plan": plan.model_dump() if hasattr(plan, "model_dump") else plan.dict(),
                "prompt_context": EditPlanManager.to_prompt_context(plan),
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _get_edit_plan(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            from ..runtime.edit_plan import EditPlanManager
            plans = getattr(self, "_active_edit_plans", {})
            target_id = task_id or "default"
            plan = plans.get(target_id)
            if not plan and plans:
                plan = next(iter(plans.values()))
            if not plan:
                plan = EditPlanManager.generate_plan(
                    task_info={"task_id": target_id, "objective": "Active Task"},
                    workspace=self.workspace,
                    code_graph=getattr(self, "code_graph", None),
                )
                if not hasattr(self, "_active_edit_plans"):
                    self._active_edit_plans = {}
                self._active_edit_plans[target_id] = plan

            return {
                "plan": plan.model_dump() if hasattr(plan, "model_dump") else plan.dict(),
                "prompt_context": EditPlanManager.to_prompt_context(plan),
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _update_edit_plan_step(
        self,
        step_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            from ..runtime.edit_plan import EditPlanManager
            plans = getattr(self, "_active_edit_plans", {})
            updated = False
            for plan in plans.values():
                for step in plan.edit_sequence:
                    if step.step_id == step_id:
                        EditPlanManager.update_step_status(plan, step_id, status, error=error)
                        updated = True
                        break
                if updated:
                    break
            return {
                "step_id": step_id,
                "status": status,
                "updated": updated,
                "success": True,
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _list_directory(self, path: str = "") -> Dict[str, Any]:
        all_files = self.workspace.list_files()
        clean_prefix = path.replace("\\", "/").strip("/ ")
        if clean_prefix:
            filtered = [f for f in all_files if f.startswith(clean_prefix)]
        else:
            filtered = all_files
        return {
            "path": path or ".",
            "files": filtered,
            "total_files": len(filtered),
            "success": True,
        }

    def _complete_task(
        self,
        summary: str,
        deliverables: Optional[Dict[str, Any]] = None,
        evidence_type: Optional[str] = "INSPECTION_CONFIRMED",
        proof_citation: Optional[str] = None,
        confidence_score: Optional[float] = 1.0,
        redundant_tasks: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        try:
            from ..runtime.evidence_stopping import EvidenceRecord, EvidenceType
            ev_rec = EvidenceRecord(
                evidence_type=EvidenceType(evidence_type) if evidence_type in EvidenceType.__members__ else EvidenceType.INSPECTION_CONFIRMED,
                proof_citation=proof_citation or "",
                confidence_score=float(confidence_score if confidence_score is not None else 1.0),
                redundant_tasks=redundant_tasks or [],
            )
            ev_dict = ev_rec.to_dict()
        except Exception:
            ev_dict = {
                "evidence_type": evidence_type or "INSPECTION_CONFIRMED",
                "proof_citation": proof_citation or "",
                "confidence_score": float(confidence_score if confidence_score is not None else 1.0),
                "redundant_tasks": redundant_tasks or [],
            }

        wm = getattr(self, "working_memory", None)
        if wm and hasattr(wm, "update_scratchpad") and proof_citation:
            wm.update_scratchpad(f"Task completed [{ev_dict['evidence_type']}]: {summary}. Proof: {proof_citation}")

        return {
            "status": "COMPLETED",
            "summary": summary,
            "deliverables": deliverables or {},
            "evidence": ev_dict,
            "evidence_type": ev_dict["evidence_type"],
            "proof_citation": ev_dict["proof_citation"],
            "confidence_score": ev_dict["confidence_score"],
            "redundant_tasks": ev_dict["redundant_tasks"],
            "success": True,
        }

    def _get_workspace_changes(self, filepath: Optional[str] = None) -> Dict[str, Any]:
        """Returns the change manifest or specific file changes in the workspace."""
        if hasattr(self.workspace, "get_change_manifest"):
            manifest = self.workspace.get_change_manifest()
            m_dict = manifest.to_dict() if hasattr(manifest, "to_dict") else manifest
        else:
            m_dict = {"summary": "No change journal available.", "file_changes": {}}

        if filepath:
            f_changes = m_dict.get("file_changes", {})
            norm_path = filepath.replace("\\", "/").strip("/")
            file_rec = f_changes.get(norm_path)
            if not file_rec:
                for k, v in f_changes.items():
                    if k == norm_path or k.endswith("/" + norm_path):
                        file_rec = v
                        break
            return {
                "filepath": filepath,
                "found": bool(file_rec),
                "change": file_rec or "No recorded changes for this file.",
                "success": True,
            }

        return {
            "summary": m_dict.get("summary", ""),
            "created_files": m_dict.get("created_files", []),
            "modified_files": m_dict.get("modified_files", []),
            "deleted_files": m_dict.get("deleted_files", []),
            "total_lines_added": m_dict.get("total_lines_added", 0),
            "total_lines_removed": m_dict.get("total_lines_removed", 0),
            "file_changes": m_dict.get("file_changes", {}),
            "success": True,
        }

    def _run_terminal_command(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        result = self.sandbox.run_command(command, timeout=timeout, cwd=self.root_dir)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.success,
        }

    def _syntax_check(self, filepath: str) -> Dict[str, Any]:
        try:
            content = self.workspace.read_file(filepath)
        except Exception as e:
            return {"valid": False, "error": str(e), "success": False}

        if content is None:
            return {"valid": False, "error": f"File '{filepath}' not found.", "success": False}
        try:
            ast.parse(content, filename=filepath)
            return {"valid": True, "message": f"Syntax valid for {filepath}", "success": True}
        except SyntaxError as se:
            return {"valid": False, "error": str(se), "line": se.lineno, "success": False}

    def _grep_search(self, pattern: str, file_glob: Optional[str] = None) -> Dict[str, Any]:
        matches = []
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = re.compile(re.escape(pattern))

        for rel_path in self.workspace.list_files():
            if file_glob and not fnmatch.fnmatch(rel_path, file_glob) and not fnmatch.fnmatch(Path(rel_path).name, file_glob):
                continue

            content = self.workspace.read_file(rel_path)
            if content:
                from ..security.secrets import secret_manager
                for idx, line in enumerate(content.splitlines(), start=1):
                    if regex.search(line):
                        clean_line = secret_manager.redact_text(line)
                        matches.append({"file": rel_path, "line": idx, "content": clean_line})
                        if len(matches) >= 50:
                            break
            if len(matches) >= 50:
                break
        return {"pattern": pattern, "file_glob": file_glob, "matches": matches, "count": len(matches), "success": True}

    def _ensure_indexed(self) -> None:
        """Cold-start index guarantee: builds in-memory graphs on first access if empty."""
        if not self.code_graph.file_to_symbols:
            self.code_graph.build_index()
            self.semantic_index.build_index()
            self.cbm.build_memory()

    def _find_symbol(self, symbol_name: str) -> Dict[str, Any]:
        self._ensure_indexed()
        res = self.code_graph.find_symbol(symbol_name)
        if not res.get("found") and not res.get("exact_match"):
            res["reformulation_hint"] = (
                f"No symbol named '{symbol_name}' found. Try semantic_code_search with concepts, "
                f"or check if this symbol needs to be newly created."
            )
        return res

    def _find_references(self, symbol_name: str) -> Dict[str, Any]:
        self._ensure_indexed()
        return self.code_graph.find_references(symbol_name)

    def _get_dependencies(self, target: str) -> Dict[str, Any]:
        self._ensure_indexed()
        return self.code_graph.get_dependencies(target)

    def _get_codebase_map(self, path: str = "") -> Dict[str, Any]:
        self._ensure_indexed()
        outline = self.code_graph.get_codebase_map(path)
        return {"codebase_map": outline, "output": outline, "success": True}

    def _semantic_code_search(self, query: str, top_k: int = 5) -> Dict[str, Any]:
        self._ensure_indexed()
        results = self.semantic_index.search(query=query, top_k=top_k)
        res = {"query": query, "results": results, "count": len(results), "success": True}
        if not results:
            res["reformulation_hint"] = (
                f"No code matches for '{query}'. Try splitting camelCase/snake_case terms, "
                f"searching for base class names, or using query_codebase_graph."
            )
        return res

    def _get_call_graph(self, target: str, max_depth: int = 2) -> Dict[str, Any]:
        self._ensure_indexed()
        return self.code_graph.get_call_graph(target=target, max_depth=max_depth)

    def _get_impact_radius(self, target: str, max_depth: int = 3) -> Dict[str, Any]:
        self._ensure_indexed()
        return self.code_graph.get_impact_radius(target=target, max_depth=max_depth)

    def _get_architecture_summary(self) -> Dict[str, Any]:
        self._ensure_indexed()
        summary = self.arch_analyzer.analyze()
        return {
            "summary": summary.to_dict(),
            "markdown": summary.to_markdown(),
            "output": summary.to_markdown(),
            "success": True,
        }

    def _query_codebase_graph(self, query: str, max_tokens: int = 2000, traversal: str = "bfs") -> Dict[str, Any]:
        self._ensure_indexed()
        res = self.cbm.query_graph(query=query, max_tokens=max_tokens, traversal=traversal)
        if not res.get("found") and not res.get("nodes"):
            res["reformulation_hint"] = (
                f"No connected sub-graph found for '{query}'. Try broader keywords or search with get_architecture_summary."
            )
        return res

    def _get_symbol_neighbors(self, symbol_name: str, depth: int = 1) -> Dict[str, Any]:
        self._ensure_indexed()
        return self.cbm.get_symbol_neighbors(symbol_name=symbol_name, depth=depth)

    def _get_architecture_slice(self, target_file: str) -> Dict[str, Any]:
        self._ensure_indexed()
        return self.cbm.get_architecture_slice(target_file=target_file)

    def _request_more_evidence(
        self,
        reason: str,
        missing_symbols: Optional[List[str]] = None,
        missing_files: Optional[List[str]] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Dispatches targeted multi-source evidence retrieval for missing symbols, files, or concepts.
        """
        self._ensure_indexed()
        resolved_symbols = []
        unresolved_symbols = []
        file_contents = {}
        missing_file_errors = {}

        if missing_symbols:
            for sym_name in missing_symbols:
                sym_res = self.code_graph.find_symbol(sym_name)
                if sym_res.get("found") or sym_res.get("exact_match"):
                    resolved_symbols.append({
                        "name": sym_name,
                        "symbols": sym_res.get("symbols", [])
                    })
                else:
                    unresolved_symbols.append(sym_name)

        if missing_files:
            for fp in missing_files:
                read_res = self._read_file(filepath=fp)
                if read_res.get("success"):
                    file_contents[fp] = read_res.get("content", "")
                else:
                    missing_file_errors[fp] = read_res.get("error", "File not found")

        conceptual_evidence = None
        if query:
            conceptual_evidence = self.cbm.query_graph(query=query, max_tokens=1500)

        wm = getattr(self, "working_memory", None)
        if wm and hasattr(wm, "update_scratchpad"):
            note = f"Evidence request: {reason}. Resolved: {[s['name'] for s in resolved_symbols]}. Unresolved: {unresolved_symbols}"
            wm.update_scratchpad(note)

        return {
            "success": True,
            "reason": reason,
            "resolved_symbols": resolved_symbols,
            "unresolved_symbols": unresolved_symbols,
            "file_contents": file_contents,
            "missing_file_errors": missing_file_errors,
            "conceptual_evidence": conceptual_evidence,
            "guidance": (
                "For unresolved symbols, they do not currently exist in the workspace and should be created as new components, "
                "or consulted via query_agent." if unresolved_symbols else "All requested evidence resolved successfully."
            ),
        }

    def _log_state_transition(self, from_node: str, to_node: str, iteration: int, details: str) -> Dict[str, Any]:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "from_node": from_node,
            "to_node": to_node,
            "iteration": iteration,
            "details": details,
        }
        self.transition_logs.append(entry)
        return {"status": "logged", "entry": entry, "success": True}

    def _search_skills(self, query: str, category: Optional[str] = None) -> Dict[str, Any]:
        matches = self.skill_registry.search_skills(query=query, category=category)
        return {
            "query": query,
            "category": category,
            "count": len(matches),
            "skills": [m.to_dict() for m in matches[:10]],
            "success": True,
        }

    def _load_skill(self, skill_name: str) -> Dict[str, Any]:
        return self.skill_registry.engine.load_skill_details(skill_name)

    def _read_skill_reference(
        self,
        skill_name: str,
        reference_name: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self.skill_registry.engine.read_reference(
            skill_name=skill_name,
            reference_name=reference_name,
            start_line=start_line,
            end_line=end_line,
        )

    def _execute_skill_script(
        self,
        skill_name: str,
        script_name: str,
        args: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self.skill_registry.engine.execute_script(
            skill_name=skill_name,
            script_name=script_name,
            args=args,
        )

    def _install_skill(self, skill_slug: str) -> Dict[str, Any]:
        success = self.skill_registry.install_skill_from_skills_sh(skill_slug)
        return {
            "skill_slug": skill_slug,
            "installed": success,
            "success": success,
            "message": f"Skill '{skill_slug}' installation {'succeeded' if success else 'failed'}.",
        }

    def _spawn_subtasks(self, parent_task_id: str, subtasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "status": "SPAWNED",
            "parent_task_id": parent_task_id,
            "count": len(subtasks),
            "subtasks": subtasks,
            "success": True,
        }

    def _rollback_to_checkpoint(self, checkpoint_id: Optional[str] = None, reason: str = "") -> Dict[str, Any]:
        """Rolls back workspace files to a specific or most recent snapshot checkpoint."""
        if not self.checkpoint_manager:
            return {"success": False, "error": "No checkpoint manager available for rollback."}

        target_id = checkpoint_id
        if not target_id:
            snaps = self.checkpoint_manager.list_snapshots()
            if not snaps:
                return {"success": False, "error": "No snapshot checkpoints found to roll back to."}
            target_id = snaps[-1]

        try:
            res = self.checkpoint_manager.rollback_to_checkpoint(target_id, self.workspace)
            if hasattr(self.workspace, "clear_change_manifest"):
                self.workspace.clear_change_manifest()
            res["reason"] = reason
            return res
        except Exception as e:
            return {"success": False, "error": str(e), "checkpoint_id": target_id}

    def _detect_project_environment(self, path: str = "") -> Dict[str, Any]:
        """Detects workspace programming language, framework, build tool, and test conventions."""
        try:
            from ..runtime.project_detector import ProjectEnvironmentDetector
        except (ImportError, ValueError):
            from agent_orchestrator.runtime.project_detector import ProjectEnvironmentDetector

        target = os.path.join(self.root_dir, path) if path else self.root_dir
        env = ProjectEnvironmentDetector.detect(target)
        result = env.to_dict()
        result["success"] = True
        return result

    def _run_static_code_check(self, filepath: Optional[str] = None) -> Dict[str, Any]:
        """Runs multi-tier static verification (syntax, typecheck/compiler, linter, import smoke) on workspace or specific file."""
        try:
            from ..runtime.static_verifier import StaticVerificationEngine
        except (ImportError, ValueError):
            from agent_orchestrator.runtime.static_verifier import StaticVerificationEngine

        target_files = [filepath] if filepath else None
        engine = StaticVerificationEngine(workspace_dir=self.root_dir, sandbox=self.sandbox)
        report = engine.verify(files=target_files)
        res = report.to_dict()
        res["success"] = report.passed
        return res

    def _inspect_existing_tests(self, path: str = "", sample_files_count: int = 2) -> Dict[str, Any]:
        """Inspects workspace for existing test suites, framework, conventions, fixtures, mocks, and CI commands."""
        try:
            from ..runtime.test_detector import ExistingTestDetector
        except (ImportError, ValueError):
            from agent_orchestrator.runtime.test_detector import ExistingTestDetector

        target = os.path.join(self.root_dir, path) if path else self.root_dir
        context = ExistingTestDetector.scan(target, max_samples=sample_files_count)
        res = context.to_dict()
        res["success"] = True
        res["prompt_context"] = context.to_prompt_context()
        return res

    def _run_build_pipeline(self, stages: Optional[List[str]] = None, fail_fast: bool = True) -> Dict[str, Any]:
        """Executes staged build verification pipeline (install, build, compile/typecheck, lint, test)."""
        try:
            from ..runtime.build_pipeline import BuildVerificationPipeline
        except (ImportError, ValueError):
            from agent_orchestrator.runtime.build_pipeline import BuildVerificationPipeline

        pipeline = BuildVerificationPipeline(workspace=self.workspace, sandbox=self.sandbox)
        report = pipeline.execute(stages=stages, fail_fast=fail_fast)
        res = report.to_dict()
        res["success"] = report.passed
        res["summary"] = report.summary()
        return res

    def _run_static_analysis(
        self,
        files: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Executes targeted polyglot static analysis and returns structured LSP diagnostics."""
        try:
            from ..runtime.static_analyzer import StaticAnalyzer
        except (ImportError, ValueError):
            from agent_orchestrator.runtime.static_analyzer import StaticAnalyzer

        analyzer = StaticAnalyzer(workspace=self.workspace, sandbox=self.sandbox)
        report = analyzer.run_analysis(files=files, tools=tools, categories=categories)
        res = report.to_dict()
        res["success"] = report.passed
        res["summary"] = report.summary()
        return res

    def _verify_ground_truth(self, modified_files: Optional[List[str]] = None) -> Dict[str, Any]:
        """Evaluates deterministic ground-truth verification matrix across 6 independent gates."""
        try:
            from ..runtime.verification_matrix import GroundTruthVerificationMatrix
        except (ImportError, ValueError):
            from agent_orchestrator.runtime.verification_matrix import GroundTruthVerificationMatrix

        matrix = GroundTruthVerificationMatrix(workspace=self.workspace, sandbox=self.sandbox)
        report = matrix.evaluate(state={}, modified_files=modified_files)
        res = report.to_dict()
        res["success"] = report.passed
        res["summary"] = report.summary()
        return res

    def _search_project_memory(self, query: str, category: Optional[str] = None, top_k: int = 5) -> Dict[str, Any]:
        """Searches long-term project memory for past architectural decisions, bug patterns, or conventions."""
        try:
            from ..memory.long_term_memory import long_term_memory
            results = long_term_memory.search(query=query, category=category, top_k=top_k)
            return {"success": True, "query": query, "count": len(results), "results": results}
        except Exception as e:
            return {"success": False, "error": str(e), "results": []}

    def _record_project_memory(self, category: str, title: str, content: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """Stores an architectural decision, bug pattern, convention, or API contract in long-term project memory."""
        try:
            from ..memory.long_term_memory import long_term_memory
            mem_id = long_term_memory.store(category=category, title=title, content=content, tags=tags)
            return {"success": True, "memory_id": mem_id, "title": title}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _update_scratchpad(self, note: str) -> Dict[str, Any]:
        """Appends a working note or hypothesis to working memory."""
        try:
            wm = getattr(self, "working_memory", None)
            if wm:
                wm.update_scratchpad(note)
            return {"success": True, "note_recorded": note}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Swarm Tool Handlers
    def _spawn_subagent(
        self,
        role: str,
        goal: str,
        capabilities: Optional[List[str]] = None,
        max_tokens: Optional[int] = 50000,
    ) -> Dict[str, Any]:
        if not self.swarm_tools:
            return {"error": "Swarm coordinator is not configured.", "status": "FAILED"}
        return self.swarm_tools.spawn_subagent(
            role=role,
            goal=goal,
            capabilities=capabilities,
            max_tokens=max_tokens or 50000,
        )

    def _delegate_subtask(
        self,
        subtask_objective: str,
        target_role: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.swarm_tools:
            return {"error": "Swarm coordinator is not configured.", "status": "FAILED"}
        return self.swarm_tools.delegate_subtask(
            subtask_objective=subtask_objective,
            target_role=target_role,
            context=context,
        )

    def _handoff_to_agent(
        self,
        target_role: str,
        reason: str,
        hypotheses: Optional[List[str]] = None,
        active_files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if not self.swarm_tools:
            return {"error": "Swarm coordinator is not configured.", "status": "FAILED"}
        return self.swarm_tools.handoff_to_agent(
            target_role=target_role,
            reason=reason,
            hypotheses=hypotheses,
            active_files=active_files,
        )

    def _discover_swarm_agents(
        self,
        capability: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.swarm_tools:
            return {"count": 0, "agents": []}
        return self.swarm_tools.discover_swarm_agents(
            capability=capability,
            role=role,
        )

    def _post_to_blackboard(
        self,
        topic: str,
        key: str,
        data: Any,
        confidence: Optional[float] = 1.0,
    ) -> Dict[str, Any]:
        if not self.swarm_tools:
            return {"error": "Swarm coordinator is not configured.", "status": "FAILED"}
        return self.swarm_tools.post_to_blackboard(
            topic=topic,
            key=key,
            data=data,
            confidence=confidence if confidence is not None else 1.0,
        )

    def _read_from_blackboard(
        self,
        topic: str,
        key: str,
    ) -> Dict[str, Any]:
        if not self.swarm_tools:
            return {"error": "Swarm coordinator is not configured.", "status": "NOT_FOUND"}
        return self.swarm_tools.read_from_blackboard(
            topic=topic,
            key=key,
        )

    def _request_consensus(
        self,
        issue: str,
        options: List[str],
        mechanism: Optional[str] = "MAJORITY",
    ) -> Dict[str, Any]:
        if not self.swarm_tools:
            return {"error": "Swarm coordinator is not configured.", "status": "FAILED"}
        return self.swarm_tools.request_consensus(
            issue=issue,
            options=options,
            mechanism=mechanism or "MAJORITY",
        )

    def _terminate_subagent(
        self,
        subagent_id: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.swarm_tools:
            return {"error": "Swarm coordinator is not configured.", "status": "FAILED"}
        return self.swarm_tools.terminate_subagent(
            subagent_id=subagent_id,
            reason=reason,
        )

    def set_orchestrator_state(self, state: Any) -> None:
        """Attaches active orchestrator state for on-demand retrieval tools."""
        self.orchestrator_state = state

    def _query_specification(self, query: str, section: Optional[str] = None) -> Dict[str, Any]:
        from ..context.isolation import ContextIsolationEngine
        spec = getattr(self.orchestrator_state, "specification_output", None)
        if not spec and isinstance(self.orchestrator_state, dict):
            spec = self.orchestrator_state.get("specification_output")
        return ContextIsolationEngine.query_specification(spec, query=query, section=section)

    def _query_architecture(self, module_name: Optional[str] = None, query: Optional[str] = None) -> Dict[str, Any]:
        from ..context.isolation import ContextIsolationEngine
        arch = getattr(self.orchestrator_state, "architecture_output", None)
        if not arch and isinstance(self.orchestrator_state, dict):
            arch = self.orchestrator_state.get("architecture_output")
        return ContextIsolationEngine.query_architecture(arch, module_name=module_name, query=query)

    def _get_task_artifact(self, task_id: str) -> Dict[str, Any]:
        from ..context.isolation import ContextIsolationEngine
        return ContextIsolationEngine.get_task_artifact(self.orchestrator_state, task_id=task_id)

    def set_current_edit_plan(self, plan: Any) -> None:
        """Sets the active structured edit plan for the current task/agent turn."""
        self._current_edit_plan = plan

    def _generate_edit_plan(
        self,
        task_id: Optional[str] = None,
        objective: Optional[str] = None,
        inputs: Optional[List[str]] = None,
        outputs: Optional[List[str]] = None,
        acceptance_tests: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from ..runtime.edit_plan import EditPlanManager
        task_info = {
            "task_id": task_id or "T-01",
            "objective": objective or "",
            "inputs": inputs or [],
            "outputs": outputs or [],
            "acceptance_tests": acceptance_tests or [],
        }
        arch = getattr(self.orchestrator_state, "architecture_output", None) if self.orchestrator_state else None
        spec = getattr(self.orchestrator_state, "specification_output", None) if self.orchestrator_state else None
        plan = EditPlanManager.generate_plan(
            task_info=task_info,
            workspace=self.workspace,
            architecture=arch,
            specification=spec,
        )
        self._current_edit_plan = plan
        plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else (plan.model_dump() if hasattr(plan, "model_dump") else plan.dict())
        return {
            "status": "SUCCESS",
            "plan": plan_dict,
            "formatted_context": EditPlanManager.to_prompt_context(plan),
        }

    def _get_edit_plan(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        if not self._current_edit_plan:
            return {"status": "NOT_FOUND", "message": "No active edit plan configured."}
        from ..runtime.edit_plan import EditPlanManager
        plan = self._current_edit_plan
        plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else (plan.model_dump() if hasattr(plan, "model_dump") else plan.dict())
        return {
            "status": "SUCCESS",
            "plan": plan_dict,
            "formatted_context": EditPlanManager.to_prompt_context(plan),
        }

    def _update_edit_plan_step(
        self,
        step_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self._current_edit_plan:
            return {"status": "ERROR", "message": "No active edit plan configured to update."}
        from ..runtime.edit_plan import EditPlanManager
        EditPlanManager.update_step_status(
            self._current_edit_plan,
            step_id=step_id,
            status=status,
            error=error,
        )
        return {
            "status": "SUCCESS",
            "step_id": step_id,
            "new_status": status,
            "message": f"Step '{step_id}' updated to '{status}'.",
        }

    async def call_tool_async(self, tool_name: str, arguments: Dict[str, Any], **kwargs: Any) -> Any:
        """
        Asynchronously invokes a registered builtin tool without blocking the event loop.
        """
        import asyncio
        return await asyncio.to_thread(self.call_tool, tool_name, arguments, **kwargs)

    async def execute_tool_async(self, tool_name: str, arguments: Dict[str, Any], **kwargs: Any) -> Any:
        """Alias for call_tool_async."""
        return await self.call_tool_async(tool_name, arguments, **kwargs)

    def close(self) -> None:
        """Closes code_graph and other resource handles."""
        if hasattr(self, "code_graph") and hasattr(self.code_graph, "close"):
            try:
                self.code_graph.close()
            except Exception:
                pass


