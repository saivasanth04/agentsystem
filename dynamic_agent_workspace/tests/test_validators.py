"""Tests for data_engine.validators."""

import pytest
from data_engine.validators import (
    validate_numeric_range,
    validate_email,
    validate_missing_values,
    validate_schema,
    SchemaValidationError
)

def test_validate_numeric_range_valid():
    assert validate_numeric_range(10, min=0, max=100) is True
    assert validate_numeric_range(0, min=0, max=100) is True
    assert validate_numeric_range(100, min=0, max=100) is True
    assert validate_numeric_range(50.5, min=0, max=100) is True

def test_validate_numeric_range_invalid_type():
    with pytest.raises(ValueError, match="Expected a number, got str"):
        validate_numeric_range("abc")

def test_validate_numeric_range_below_min():
    with pytest.raises(ValueError, match="less than minimum"):
        validate_numeric_range(-10, min=0, max=100)

def test_validate_numeric_range_above_max():
    with pytest.raises(ValueError, match="greater than maximum"):
        validate_numeric_range(110, min=0, max=100)

def test_validate_email_valid():
    assert validate_email("test@example.com") is True
    assert validate_email("user.name+tag@domain.co.uk") is True

def test_validate_email_invalid_type():
    with pytest.raises(ValueError, match="Expected a string, got int"):
        validate_email(123)

def test_validate_email_invalid_format():
    with pytest.raises(ValueError, match="Invalid email format"):
        validate_email("invalid-email")
    with pytest.raises(ValueError, match="Invalid email format"):
        validate_email("test@.com")

def test_validate_missing_values_present():
    assert validate_missing_values(0) is True
    assert validate_missing_values("hello") is True
    assert validate_missing_values([1, 2]) is True
    assert validate_missing_values({"a": 1}) is True

def test_validate_missing_values_none():
    with pytest.raises(ValueError, match="Value cannot be None"):
        validate_missing_values(None)

def test_validate_missing_values_empty_string():
    with pytest.raises(ValueError, match="Value cannot be an empty string"):
        validate_missing_values("")
    with pytest.raises(ValueError, match="Value cannot be an empty string"):
        validate_missing_values("   ")

def test_validate_missing_values_empty_collection():
    with pytest.raises(ValueError, match="Value cannot be an empty list or dictionary"):
        validate_missing_values([])
    with pytest.raises(ValueError, match="Value cannot be an empty list or dictionary"):
        validate_missing_values({})

def test_validate_schema_valid():
    data = [
        {"name": "Alice", "age": 30, "email": "alice@example.com"},
        {"name": "Bob", "age": 25, "email": "bob@example.com"}
    ]
    schema = {
        "name": {"type": "missing"}, # Ensure name is present
        "age": {"type": "numeric_range", "min": 0, "max": 120},
        "email": {"type": "email"}
    }
    assert validate_schema(data, schema) == data

def test_validate_schema_single_dict():
    data = {"name": "Alice", "age": 30, "email": "alice@example.com"}
    schema = {
        "name": {"type": "missing"},
        "age": {"type": "numeric_range", "min": 0, "max": 120},
        "email": {"type": "email"}
    }
    assert validate_schema(data, schema) == [data]

def test_validate_schema_empty_data():
    data = []
    schema = {
        "name": {"type": "missing"},
        "age": {"type": "numeric_range", "min": 0, "max": 120},
        "email": {"type": "email"}
    }
    assert validate_schema(data, schema) == []

def test_validate_schema_invalid_row():
    data = [
        {"name": "Alice", "age": 30, "email": "alice@example.com"},
        {"name": "Bob", "age": 200, "email": "bob@invalid"}, # Invalid age and email
        {"name": "Charlie", "email": "charlie@example.com"} # Missing age
    ]
    schema = {
        "name": {"type": "missing"},
        "age": {"type": "numeric_range", "min": 0, "max": 120},
        "email": {"type": "email"}
    }
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_schema(data, schema)
    
    assert len(excinfo.value.errors) == 2
    assert excinfo.value.errors[0]['row_index'] == 1
    assert "Invalid age: Value 200 is greater than maximum 120" in excinfo.value.errors[0]['errors']
    assert "Invalid email: Invalid email format: bob@invalid" in excinfo.value.errors[0]['errors']
    assert excinfo.value.errors[1]['row_index'] == 2
    assert "Missing field: age" in excinfo.value.errors[1]['errors']

def test_validate_schema_missing_field_in_schema():
    data = [
        {"name": "Alice", "age": 30}
    ]
    schema = {
        "name": {"type": "missing"},
        "age": {"type": "numeric_range", "min": 0, "max": 120},
        "email": {"type": "email"} # Email is in schema but not in data
    }
    with pytest.raises(SchemaValidationError) as excinfo:
        validate_schema(data, schema)
    assert excinfo.value.errors[0]['row_index'] == 0
    assert "Missing field: email" in excinfo.value.errors[0]['errors']
