"""
Typed Structured Output Pipeline for Autonomous Coding Agents.
Implements a 5-stage architecture:
1. Schema-constrained generation (optional response_format / json_schema)
2. Algorithmic dirty-JSON repair (handles trailing commas, single quotes, unescaped newlines)
3. Pydantic schema validation & type coercion
4. Corrective feedback retry loop
5. Explicit typed failure state (no silent corruption or masquerading success)
"""

from dataclasses import dataclass, field
import json
import logging
from typing import Any, Callable, Dict, Generic, List, Optional, Type, TypeVar, Union

from pydantic import BaseModel, ValidationError

from .json_repair import loads_repaired, repair_json

logger = logging.getLogger("agent_orchestrator.structured_output")

T = TypeVar("T")


class StructuredOutputError(Exception):
    """Raised when structured output generation, repair, and validation fail."""

    def __init__(
        self,
        message: str,
        raw_text: str = "",
        validation_errors: Optional[List[str]] = None,
        attempts: int = 1,
    ):
        super().__init__(message)
        self.raw_text = raw_text
        self.validation_errors = validation_errors or []
        self.attempts = attempts


@dataclass
class StructuredOutputResult(Generic[T]):
    """Formal result of a structured output execution."""
    success: bool
    data: Optional[T] = None
    raw_text: str = ""
    error: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)
    repair_applied: bool = False
    attempts: int = 1

    def unwrap(self) -> T:
        """Return data if successful, otherwise raise StructuredOutputError."""
        if not self.success:
            raise StructuredOutputError(
                self.error or "Structured output generation failed",
                raw_text=self.raw_text,
                validation_errors=self.validation_errors,
                attempts=self.attempts,
            )
        return self.data  # type: ignore


class StructuredOutputEngine:
    """
    Orchestrates the 5-stage structured output pipeline.
    """

    @staticmethod
    def get_json_schema(model_cls: Type[BaseModel]) -> Dict[str, Any]:
        """Extract JSON schema from a Pydantic model (v1 and v2 compatible)."""
        if hasattr(model_cls, "model_json_schema"):
            return model_cls.model_json_schema()
        elif hasattr(model_cls, "schema"):
            return model_cls.schema()
        return {}

    @classmethod
    def execute(
        cls,
        llm_caller: Callable[[List[Dict[str, Any]], Dict[str, Any]], str],
        messages: List[Dict[str, Any]],
        schema: Optional[Type[T]] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_retries: int = 2,
        response_format: Optional[Dict[str, Any]] = None,
        raise_on_failure: bool = False,
    ) -> StructuredOutputResult[T]:
        """
        Execute the 5-stage structured output pipeline.

        Args:
            llm_caller: Callable accepting (messages, kwargs) returning raw string response.
            messages: List of chat messages.
            schema: Optional Pydantic BaseModel class for validation.
            model: Optional model name.
            temperature: Sampling temperature.
            max_retries: Number of corrective feedback retries.
            response_format: Optional OpenAI-style response_format.
            raise_on_failure: If True, raises StructuredOutputError on terminal failure.
        """
        active_messages = list(messages)
        attempts = 0
        last_raw_text = ""
        last_error = ""
        last_validation_errors: List[str] = []
        repair_applied = False

        # Configure kwargs
        base_kwargs: Dict[str, Any] = {
            "model": model,
            "temperature": temperature,
        }
        if response_format:
            base_kwargs["response_format"] = response_format

        max_attempts = max(1, 1 + max_retries)

        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                raw_text = llm_caller(active_messages, base_kwargs)
            except Exception as call_err:
                last_error = f"LLM execution error: {str(call_err)}"
                logger.warning(f"[StructuredOutput] Attempt {attempt} LLM call failed: {last_error}")
                if attempt == max_attempts:
                    break
                continue

            last_raw_text = raw_text

            # Stage 2: Deserialization & Repair
            parsed_data: Any = None
            parse_error: Optional[str] = None
            try:
                parsed_data = json.loads(raw_text.strip())
            except Exception:
                try:
                    parsed_data = loads_repaired(raw_text)
                    repair_applied = True
                except Exception as r_err:
                    parse_error = f"JSON decode error: {str(r_err)}"

            if parse_error:
                last_error = parse_error
                last_validation_errors = [parse_error]
                logger.warning(f"[StructuredOutput] Attempt {attempt} JSON parse failed: {parse_error}")
                if attempt < max_attempts:
                    active_messages.append({"role": "assistant", "content": raw_text})
                    active_messages.append({
                        "role": "user",
                        "content": (
                            f"Your previous response could not be parsed as valid JSON.\n"
                            f"Error: {parse_error}\n"
                            f"Please provide ONLY valid JSON with no conversational text, preambles, or unescaped quotes."
                        ),
                    })
                    continue
                break

            # Stage 3: Pydantic Validation & Coercion
            if schema is not None and issubclass(schema, BaseModel):
                try:
                    if hasattr(schema, "model_validate"):
                        validated_model = schema.model_validate(parsed_data)
                    else:
                        validated_model = schema(**parsed_data)

                    return StructuredOutputResult(
                        success=True,
                        data=validated_model,
                        raw_text=raw_text,
                        repair_applied=repair_applied,
                        attempts=attempts,
                    )
                except ValidationError as ve:
                    err_lines = []
                    for err in ve.errors():
                        loc = " -> ".join(str(p) for p in err.get("loc", []))
                        msg = err.get("msg", "Invalid value")
                        err_lines.append(f"Field '{loc}': {msg}")
                    last_validation_errors = err_lines
                    last_error = f"Schema validation failed: {'; '.join(err_lines)}"
                    logger.warning(f"[StructuredOutput] Attempt {attempt} Pydantic validation failed: {last_error}")

                    if attempt < max_attempts:
                        schema_summary = cls.get_json_schema(schema)
                        required_fields = schema_summary.get("required", [])
                        active_messages.append({"role": "assistant", "content": raw_text})
                        active_messages.append({
                            "role": "user",
                            "content": (
                                f"Your previous response failed schema validation for {schema.__name__}:\n"
                                f"Errors:\n" + "\n".join(f"- {e}" for e in err_lines) + "\n"
                                f"Required fields: {required_fields}\n"
                                f"Please correct these errors and return ONLY the valid JSON object conforming to the schema."
                            ),
                        })
                        continue
                    break
                except Exception as other_val_err:
                    last_error = f"Schema instantiation error: {str(other_val_err)}"
                    last_validation_errors = [last_error]
                    if attempt < max_attempts:
                        active_messages.append({"role": "assistant", "content": raw_text})
                        active_messages.append({
                            "role": "user",
                            "content": f"Validation error: {last_error}. Please output valid JSON matching the required schema.",
                        })
                        continue
                    break
            else:
                # Untyped JSON requested (schema is None)
                return StructuredOutputResult(
                    success=True,
                    data=parsed_data,
                    raw_text=raw_text,
                    repair_applied=repair_applied,
                    attempts=attempts,
                )

        # Stage 5: Terminal Failure State
        failure_result = StructuredOutputResult(
            success=False,
            data=None,
            raw_text=last_raw_text,
            error=last_error or "Structured output generation failed",
            validation_errors=last_validation_errors,
            repair_applied=repair_applied,
            attempts=attempts,
        )

        if raise_on_failure:
            raise StructuredOutputError(
                failure_result.error or "Structured output generation failed",
                raw_text=last_raw_text,
                validation_errors=last_validation_errors,
                attempts=attempts,
            )

        return failure_result
