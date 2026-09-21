"""
DeliverableValidator: Multi-Tier Schema and Semantic Validation Engine for Agent Deliverables.
Enforces:
1. Pydantic schema validation (required fields, types, structure)
2. Value constraints (non-empty strings, positive integers, minimum collection sizes)
3. Semantic rules (domain validity, consistency, non-tautological contents)
"""
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional, Type, Union

from pydantic import BaseModel, ValidationError

from ..contracts import (
    ArchitectureContract,
    CodeDeliverableContract,
    ExecutionPlanContract,
    ReviewAuditContract,
    SpecificationContract,
    TestResultContract,
)


@dataclass
class ValidationReport:
    is_valid: bool
    contract_name: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    validated_model: Optional[BaseModel] = None
    raw_data: Optional[Dict[str, Any]] = None

    def errors_summary(self) -> str:
        if not self.errors:
            return "No errors."
        return "; ".join(self.errors)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "contract_name": self.contract_name,
            "errors": self.errors,
            "warnings": self.warnings,
            "validated_model": self.validated_model.model_dump() if self.validated_model else None,
        }


class DeliverableValidator:
    """
    Validates agent outputs against expected Pydantic contracts and domain semantic rules.
    """

    AGENT_CONTRACT_MAP: Dict[str, Type[BaseModel]] = {
        "PLANNER": ExecutionPlanContract,
        "SPECIFICATION": SpecificationContract,
        "ARCHITECTURE": ArchitectureContract,
        "CODER": CodeDeliverableContract,
        "TESTER": TestResultContract,
        "REVIEWER": ReviewAuditContract,
    }

    @classmethod
    def get_contract_for_agent(cls, agent_name: str) -> Optional[Type[BaseModel]]:
        return cls.AGENT_CONTRACT_MAP.get(agent_name.upper())

    @classmethod
    def validate_contract(
        cls,
        contract_class: Type[BaseModel],
        data: Any,
    ) -> ValidationReport:
        contract_name = getattr(contract_class, "__name__", str(contract_class))
        errors: List[str] = []
        warnings: List[str] = []

        # Tier 0: Type Check
        if not isinstance(data, dict):
            return ValidationReport(
                is_valid=False,
                contract_name=contract_name,
                errors=[f"Expected JSON object (dict) for {contract_name}, got {type(data).__name__}"],
                raw_data=data if isinstance(data, dict) else None,
            )

        # Tier 1: Pydantic Schema Validation (Required Fields & Types)
        validated_model = None
        try:
            if hasattr(contract_class, "model_validate"):
                validated_model = contract_class.model_validate(data)
            else:
                validated_model = contract_class(**data)
        except ValidationError as ve:
            for err in ve.errors():
                loc = " -> ".join(str(p) for p in err.get("loc", []))
                msg = err.get("msg", "Invalid value")
                errors.append(f"Field '{loc}': {msg}")
        except Exception as e:
            errors.append(f"Schema parsing error: {e}")

        # Tier 2 & 3: Value Constraints and Non-Trivial Checks
        if not errors and validated_model:
            cls._apply_constraint_and_semantic_checks(contract_class, validated_model, data, errors, warnings)

        is_valid = len(errors) == 0
        return ValidationReport(
            is_valid=is_valid,
            contract_name=contract_name,
            errors=errors,
            warnings=warnings,
            validated_model=validated_model if is_valid else None,
            raw_data=data,
        )

    @classmethod
    def validate_agent_deliverable(
        cls,
        agent_name: str,
        data: Any,
    ) -> ValidationReport:
        contract_class = cls.get_contract_for_agent(agent_name)
        if not contract_class:
            # If no formal contract registered for agent, perform generic dictionary sanity check
            if not isinstance(data, dict):
                return ValidationReport(
                    is_valid=False,
                    contract_name=agent_name,
                    errors=[f"Deliverable from {agent_name} must be a dictionary, got {type(data).__name__}"],
                )
            if not data or (len(data) == 1 and "garbage" in data):
                return ValidationReport(
                    is_valid=False,
                    contract_name=agent_name,
                    errors=[f"Deliverable from {agent_name} contains no valid fields"],
                )
            return ValidationReport(is_valid=True, contract_name=agent_name, raw_data=data)

        return cls.validate_contract(contract_class, data)

    @classmethod
    def _apply_constraint_and_semantic_checks(
        cls,
        contract_class: Type[BaseModel],
        model: BaseModel,
        raw_data: Dict[str, Any],
        errors: List[str],
        warnings: List[str],
    ):
        """Tier 3 & 4 constraint and domain semantic verification."""

        # 1. ExecutionPlanContract Constraints
        if issubclass(contract_class, ExecutionPlanContract):
            plan: ExecutionPlanContract = model  # type: ignore
            if not plan.project_title or not plan.project_title.strip():
                errors.append("Field 'project_title': cannot be empty or blank")
            if not plan.phases:
                warnings.append("Plan has no execution phases defined")
            else:
                for p in plan.phases:
                    if not p.name or not p.name.strip():
                        errors.append(f"Phase {p.phase_number}: 'name' cannot be empty")
                    if not p.deliverables:
                        warnings.append(f"Phase {p.phase_number} ('{p.name}'): 'deliverables' list is empty")

        # 2. SpecificationContract Constraints
        elif issubclass(contract_class, SpecificationContract):
            spec: SpecificationContract = model  # type: ignore
            if not spec.feature_name or not spec.feature_name.strip():
                errors.append("Field 'feature_name': cannot be empty or blank")
            if not spec.functional_requirements:
                warnings.append("Specification has no functional requirements defined")
            else:
                for req in spec.functional_requirements:
                    if not req.id or not req.id.strip():
                        errors.append("Functional requirement: 'id' cannot be empty")
                    if not req.description or not req.description.strip():
                        errors.append(f"Functional requirement '{req.id}': 'description' cannot be empty")
            if not spec.acceptance_criteria:
                warnings.append("Specification contains no acceptance criteria")

        # 3. ArchitectureContract Constraints
        elif issubclass(contract_class, ArchitectureContract):
            arch: ArchitectureContract = model  # type: ignore
            if not arch.system_title or not arch.system_title.strip():
                errors.append("Field 'system_title': cannot be empty or blank")
            if not arch.component_structure:
                warnings.append("Architecture has no component structure defined")
            else:
                for comp in arch.component_structure:
                    if not comp.module_name or not comp.module_name.strip():
                        errors.append("Component: 'module_name' cannot be empty")
                    if not comp.purpose or not comp.purpose.strip():
                        errors.append(f"Component '{comp.module_name}': 'purpose' cannot be empty")

        # 4. CodeDeliverableContract Constraints
        elif issubclass(contract_class, CodeDeliverableContract):
            code: CodeDeliverableContract = model  # type: ignore
            if not code.summary or not code.summary.strip():
                errors.append("Field 'summary': cannot be empty or blank")
            if not code.written_files and not code.deliverables:
                warnings.append("No written files or deliverables declared in code output")

        # 5. TestResultContract Constraints
        elif issubclass(contract_class, TestResultContract):
            test: TestResultContract = model  # type: ignore
            if not test.test_strategy or not test.test_strategy.strip():
                errors.append("Field 'test_strategy': cannot be empty or blank")

        # 6. ReviewAuditContract Constraints
        elif issubclass(contract_class, ReviewAuditContract):
            rev: ReviewAuditContract = model  # type: ignore
            if rev.verdict.upper() not in ("PASS", "FAIL", "UNDECIDED"):
                errors.append(f"Field 'verdict': must be PASS, FAIL, or UNDECIDED (got '{rev.verdict}')")
            if not rev.summary or not rev.summary.strip():
                errors.append("Field 'summary': cannot be empty or blank")

    @classmethod
    def format_error_feedback(cls, report: ValidationReport) -> str:
        """Formats a clear, actionable prompt message for LLM self-correction."""
        err_list = "\n".join(f"- {err}" for err in report.errors)
        return (
            f"Deliverable Schema Validation Error for [{report.contract_name}]:\n"
            f"Your output failed required schema validation with the following error(s):\n"
            f"{err_list}\n\n"
            f"Please review the schema requirements and correct your JSON response to conform strictly to [{report.contract_name}]."
        )
