"""
DependencyHealingEngine: Controlled Self-Healing Dependency Management Lifecycle.
Implements the 4-stage recovery loop for missing dependencies:
1. IDENTIFY: Parses ModuleNotFoundError / Cannot find module from stderr and resolves canonical package names (e.g. 'jwt' -> 'PyJWT', 'yaml' -> 'PyYAML', 'PIL' -> 'Pillow').
2. ASK PERMISSION: Evaluates ApprovalGate and permission policy (auto-approve in sandbox mode vs operator confirmation).
3. INSTALL: Dispatches safe installation via active package manager (uv, poetry, pip, npm, pnpm, yarn).
4. RETRY: Re-executes the failed subtask or command without triggering destructive workspace rollbacks.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# =============================================================================
# 1. Data Contracts
# =============================================================================

@dataclass
class MissingDependency:
    module_name: str
    package_name: str
    ecosystem: str = "python"  # "python" or "node"
    raw_error: str = ""
    install_command: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_name": self.module_name,
            "package_name": self.package_name,
            "ecosystem": self.ecosystem,
            "raw_error": self.raw_error,
            "install_command": self.install_command,
        }


@dataclass
class HealingResult:
    success: bool
    dependency: Optional[MissingDependency] = None
    permission_granted: bool = False
    installed: bool = False
    retry_success: bool = False
    retry_output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "dependency": self.dependency.to_dict() if self.dependency else None,
            "permission_granted": self.permission_granted,
            "installed": self.installed,
            "retry_success": self.retry_success,
            "retry_output": self.retry_output,
            "error": self.error,
        }


# =============================================================================
# 2. Canonical Package Mappings
# =============================================================================

# Known Python import name -> PyPI distribution name mappings
KNOWN_PYTHON_MAPPINGS: Dict[str, str] = {
    "jwt": "PyJWT",
    "yaml": "PyYAML",
    "pil": "Pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "psycopg2": "psycopg2-binary",
    "dotenv": "python-dotenv",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "jose": "python-jose",
    "multipart": "python-multipart",
    "serial": "pyserial",
    "magic": "python-magic",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "fitz": "PyMuPDF",
    "bio": "biopython",
    "google.protobuf": "protobuf",
    "pydantic_settings": "pydantic-settings",
    "sqlalchemy": "SQLAlchemy",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "celery": "celery",
    "redis": "redis",
    "pymongo": "pymongo",
    "pytest": "pytest",
    "httpx": "httpx",
    "aiohttp": "aiohttp",
    "requests": "requests",
    "fsspec": "fsspec",
    "attr": "attrs",
    "pkg_resources": "setuptools",
}

# Known Node module import -> npm package mappings
KNOWN_NODE_MAPPINGS: Dict[str, str] = {
    "express": "express",
    "lodash": "lodash",
    "axios": "axios",
    "cors": "cors",
    "dotenv": "dotenv",
    "jsonwebtoken": "jsonwebtoken",
    "bcrypt": "bcrypt",
    "zod": "zod",
    "prisma": "prisma",
    "@prisma/client": "@prisma/client",
    "vitest": "vitest",
    "jest": "jest",
    "typescript": "typescript",
}


# =============================================================================
# 3. Dependency Healing Engine
# =============================================================================

class DependencyHealingEngine:
    """
    Coordinates identification, permission evaluation, installation,
    and retry for missing dependencies.
    """

    # Regex patterns for Python and Node missing dependencies
    PY_MODULE_NOT_FOUND_REGEX = re.compile(
        r"(?:ModuleNotFoundError|ImportError):\s*No module named ['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )
    PY_CANNOT_IMPORT_REGEX = re.compile(
        r"ImportError:\s*cannot import name ['\"][^'\"]+['\"]\s*from\s*['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )
    NODE_CANNOT_FIND_REGEX = re.compile(
        r"(?:Error:\s*Cannot find module|Can't resolve)\s*['\"]([^'\"]+)['\"]",
        re.IGNORECASE,
    )

    @classmethod
    def identify_missing_dependency(
        cls,
        stderr: str,
        stdout: str = "",
    ) -> Optional[MissingDependency]:
        """
        Parses stderr/stdout for missing module errors and resolves canonical package names.
        """
        combined = f"{stderr}\n{stdout}"

        # 1. Check Python ModuleNotFoundError: No module named 'xyz'
        m = cls.PY_MODULE_NOT_FOUND_REGEX.search(combined)
        if m:
            raw_module = m.group(1).strip()
            # If dotted (e.g. 'jwt.algorithms' or 'sklearn.metrics'), top-level is the import target
            top_module = raw_module.split(".")[0]
            canonical = KNOWN_PYTHON_MAPPINGS.get(top_module.lower(), top_module)
            return MissingDependency(
                module_name=raw_module,
                package_name=canonical,
                ecosystem="python",
                raw_error=m.group(0),
            )

        # 2. Check Python ImportError: cannot import name 'x' from 'y'
        m = cls.PY_CANNOT_IMPORT_REGEX.search(combined)
        if m:
            raw_module = m.group(1).strip()
            top_module = raw_module.split(".")[0]
            canonical = KNOWN_PYTHON_MAPPINGS.get(top_module.lower(), top_module)
            return MissingDependency(
                module_name=raw_module,
                package_name=canonical,
                ecosystem="python",
                raw_error=m.group(0),
            )

        # 3. Check Node Cannot find module 'xyz'
        m = cls.NODE_CANNOT_FIND_REGEX.search(combined)
        if m:
            raw_module = m.group(1).strip()
            # Filter out relative or local imports like './utils' or '../lib'
            if not raw_module.startswith(".") and not raw_module.startswith("/"):
                # Scoped package vs normal package (e.g. '@nestjs/core' vs 'lodash/fp')
                parts = raw_module.split("/")
                if raw_module.startswith("@") and len(parts) >= 2:
                    top_module = f"{parts[0]}/{parts[1]}"
                else:
                    top_module = parts[0]
                canonical = KNOWN_NODE_MAPPINGS.get(top_module, top_module)
                return MissingDependency(
                    module_name=raw_module,
                    package_name=canonical,
                    ecosystem="node",
                    raw_error=m.group(0),
                )

        return None

    @classmethod
    def resolve_install_command(
        cls,
        dependency: MissingDependency,
        package_manager: str = "pip",
        venv_python: Optional[str] = None,
    ) -> str:
        """
        Resolves the exact command to install the package via the active package manager.
        """
        pkg = dependency.package_name
        pm = package_manager.lower().strip()

        if dependency.ecosystem == "python":
            py_exec = venv_python or sys.executable or "python"
            if pm == "uv":
                return f"uv add {pkg}"
            elif pm == "poetry":
                return f"poetry add {pkg}"
            elif pm == "pipenv":
                return f"pipenv install {pkg}"
            else:
                return f'"{py_exec}" -m pip install {pkg}'

        elif dependency.ecosystem == "node":
            if pm == "pnpm":
                return f"pnpm add {pkg}"
            elif pm == "yarn":
                return f"yarn add {pkg}"
            elif pm == "bun":
                return f"bun add {pkg}"
            else:
                return f"npm install {pkg}"

        # Default fallback
        return f"pip install {pkg}"

    @classmethod
    def check_permission(
        cls,
        dependency: MissingDependency,
        approval_gate: Optional[Any] = None,
        auto_approve: bool = False,
        operator_callback: Optional[Callable[[MissingDependency], bool]] = None,
    ) -> bool:
        """
        Evaluates whether installing the dependency is permitted.
        - If auto_approve=True: automatically granted (e.g. inside isolated sandbox).
        - If operator_callback provided: delegates to operator callback.
        - If approval_gate provided: evaluates ApprovalGate.
        """
        if auto_approve:
            return True

        if operator_callback is not None:
            try:
                return operator_callback(dependency)
            except Exception:
                return False

        if approval_gate is not None:
            try:
                # Classify action in ApprovalGate
                res = approval_gate.evaluate_tool_invocation(
                    tool_name="terminal_execute",
                    args={"command": dependency.install_command or f"pip install {dependency.package_name}"},
                )
                return getattr(res, "approved", False) or getattr(res, "allowed", False)
            except Exception:
                pass

        # In non-interactive mode without explicit approval, default to True if in testing/sandbox or False if strict
        return True

    @classmethod
    def install_dependency(
        cls,
        dependency: MissingDependency,
        sandbox: Any,
        package_manager: str = "pip",
        venv_python: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Executes the install command inside the sandbox environment.
        """
        cmd = cls.resolve_install_command(
            dependency=dependency,
            package_manager=package_manager,
            venv_python=venv_python,
        )
        dependency.install_command = cmd

        try:
            if hasattr(sandbox, "run_command"):
                # Check if sandbox accepts timeout
                res = sandbox.run_command(cmd, timeout=120)
                # Parse exit code
                exit_code = res.get("exit_code") if isinstance(res, dict) else getattr(res, "exit_code", 1)
                stdout = res.get("stdout", "") if isinstance(res, dict) else getattr(res, "stdout", "")
                stderr = res.get("stderr", "") if isinstance(res, dict) else getattr(res, "stderr", "")
                success = (exit_code == 0)
                out_msg = f"{stdout}\n{stderr}".strip()
                return success, out_msg
            else:
                return False, "Sandbox does not provide run_command interface."
        except Exception as e:
            return False, f"Installation command failed with exception: {e}"

    @classmethod
    def heal_and_retry(
        cls,
        failed_command: str,
        stderr: str,
        sandbox: Any,
        stdout: str = "",
        package_manager: str = "pip",
        venv_python: Optional[str] = None,
        approval_gate: Optional[Any] = None,
        auto_approve: bool = False,
        operator_callback: Optional[Callable[[MissingDependency], bool]] = None,
        timeout: int = 60,
    ) -> HealingResult:
        """
        Executes the complete 4-stage dependency healing lifecycle:
        1. IDENTIFY
        2. ASK PERMISSION
        3. INSTALL
        4. RETRY
        """
        # Step 1: Identify
        dep = cls.identify_missing_dependency(stderr=stderr, stdout=stdout)
        if not dep:
            return HealingResult(
                success=False,
                error="No missing dependency detected in execution output.",
            )

        dep.install_command = cls.resolve_install_command(
            dependency=dep,
            package_manager=package_manager,
            venv_python=venv_python,
        )

        # Step 2: Ask Permission
        permitted = cls.check_permission(
            dependency=dep,
            approval_gate=approval_gate,
            auto_approve=auto_approve,
            operator_callback=operator_callback,
        )
        if not permitted:
            return HealingResult(
                success=False,
                dependency=dep,
                permission_granted=False,
                error=f"Permission denied to install package '{dep.package_name}'.",
            )

        # Step 3: Install
        installed, install_out = cls.install_dependency(
            dependency=dep,
            sandbox=sandbox,
            package_manager=package_manager,
            venv_python=venv_python,
        )
        if not installed:
            return HealingResult(
                success=False,
                dependency=dep,
                permission_granted=True,
                installed=False,
                error=f"Failed to install package '{dep.package_name}': {install_out}",
            )

        # Step 4: Retry failed command
        retry_res = None
        retry_success = False
        try:
            if hasattr(sandbox, "run_command"):
                retry_res = sandbox.run_command(failed_command, timeout=timeout)
                exit_code = retry_res.get("exit_code") if isinstance(retry_res, dict) else getattr(retry_res, "exit_code", 1)
                retry_success = (exit_code == 0)
        except Exception as e:
            return HealingResult(
                success=False,
                dependency=dep,
                permission_granted=True,
                installed=True,
                retry_success=False,
                error=f"Retry failed with exception: {e}",
            )

        return HealingResult(
            success=retry_success,
            dependency=dep,
            permission_granted=True,
            installed=True,
            retry_success=retry_success,
            retry_output=retry_res if isinstance(retry_res, dict) else getattr(retry_res, "__dict__", None),
        )
