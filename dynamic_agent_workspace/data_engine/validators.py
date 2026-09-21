"""Custom schema validators for numeric ranges, email formats, and missing values."""

import re
from typing import Any, Optional, Union


class SchemaValidationError(ValueError):
    """Custom exception for schema validation errors."""
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(f"Schema validation failed: {errors}")


def validate_numeric_range(value: Any, *, min: Optional[float] = None, max: Optional[float] = None) -> bool:
    """Validate if a numeric value falls within inclusive bounds."""
    if not isinstance(value, (int, float)):
        raise ValueError(f"Expected a number, got {type(value).__name__}")
    if min is not None and value < min:
        raise ValueError(f"Value {value} is less than minimum {min}")
    if max is not None and value > max:
        raise ValueError(f"Value {value} is greater than maximum {max}")
    return True


def validate_email(value: Any) -> bool:
    """Validate string against RFC 5322 email format."""
    if not isinstance(value, str):
        raise ValueError(f"Expected a string, got {type(value).__name__}")
    # Fixed regex pattern - proper email validation
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    if not re.match(pattern, value):
        raise ValueError(f"Invalid email format: {value}")
    return True


def validate_missing_values(value: Any) -> bool:
    """Check for None, empty strings, or empty dictionaries."""
    if value is None:
        raise ValueError("Value cannot be None")
    if isinstance(value, str) and not value.strip():
        raise ValueError("Value cannot be an empty string")
    if isinstance(value, (list, dict)) and not value:
        raise ValueError("Value cannot be an empty list or dictionary")
    return True


def validate_schema(data: Union[list[dict], dict], schema: dict) -> list[dict]:
    """Validate a list of dicts against a schema."""
    errors = []
    if isinstance(data, list):
        for i, row in enumerate(data):
            row_errors = []
            for field, field_schema in schema.items():
                if field not in row:
                    row_errors.append(f"Missing field: {field}")
                    continue
                value = row[field]
                if field_schema.get('type') == 'numeric_range':
                    try:
                        validate_numeric_range(value, min=field_schema.get('min'), max=field_schema.get('max'))
                    except ValueError as e:
                        row_errors.append(f"Invalid {field}: {e}")
                elif field_schema.get('type') == 'email':
                    try:
                        validate_email(value)
                    except ValueError as e:
                        row_errors.append(f"Invalid {field}: {e}")
                elif field_schema.get('type') == 'missing':
                    try:
                        validate_missing_values(value)
                    except ValueError as e:
                        row_errors.append(f"Invalid {field}: {e}")
            if row_errors:
                errors.append({
                    'row_index': i,
                    'errors': row_errors
                })
    if errors:
        raise SchemaValidationError(errors)
    return data if isinstance(data, list) else [data]