"""Tests for data_engine.exporter."""

import pytest
import json
import csv
import os
import tempfile
from data_engine.exporter import (
    export_to_json,
    export_to_csv,
    export_data
)

def test_export_to_json():
    data = {"name": "Alice", "age": 30}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        filepath = f.name
    try:
        export_to_json(data, filepath)
        with open(filepath, 'r') as f:
            loaded = json.load(f)
        assert loaded == data
    finally:
        os.unlink(filepath)

def test_export_to_json_with_indent():
    data = {"name": "Alice", "age": 30}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        filepath = f.name
    try:
        export_to_json(data, filepath, indent=4)
        with open(filepath, 'r') as f:
            content = f.read()
        assert '    ' in content  # 4-space indent
    finally:
        os.unlink(filepath)

def test_export_to_json_directory_traversal():
    with pytest.raises(ValueError, match="Filepath contains directory traversal"):
        export_to_json({}, "../test.json")

def test_export_to_csv():
    data = [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": 25}
    ]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        filepath = f.name
    try:
        export_to_csv(data, filepath)
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]['name'] == 'Alice'
        assert rows[1]['name'] == 'Bob'
    finally:
        os.unlink(filepath)

def test_export_to_csv_custom_delimiter():
    data = [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": 25}
    ]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        filepath = f.name
    try:
        export_to_csv(data, filepath, delimiter=';')
        with open(filepath, 'r') as f:
            content = f.read()
        assert ';' in content
    finally:
        os.unlink(filepath)

def test_export_to_csv_inconsistent_keys():
    data = [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "city": "NYC"}  # Different keys
    ]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        filepath = f.name
    try:
        with pytest.raises(ValueError, match="Inconsistent keys in data rows"):
            export_to_csv(data, filepath)
        # Verify no file was created
        assert not os.path.exists(filepath)
    finally:
        if os.path.exists(filepath):
            os.unlink(filepath)

def test_export_to_csv_empty_data():
    with pytest.raises(ValueError, match="No data to export"):
        export_to_csv([], "test.csv")

def test_export_to_csv_directory_traversal():
    with pytest.raises(ValueError, match="Filepath contains directory traversal"):
        export_to_csv([{"a": 1}], "../test.csv")

def test_export_data_json():
    data = {"name": "Alice", "age": 30}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        filepath = f.name
    try:
        export_data(data, filepath, format="json")
        with open(filepath, 'r') as f:
            loaded = json.load(f)
        assert loaded == data
    finally:
        os.unlink(filepath)

def test_export_data_csv():
    data = [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": 25}
    ]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        filepath = f.name
    try:
        export_data(data, filepath, format="csv")
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
    finally:
        os.unlink(filepath)

def test_export_data_unsupported_format():
    with pytest.raises(ValueError, match="Unsupported format: xml"):
        export_data({}, "test.xml", format="xml")

def test_export_data_csv_requires_list_of_dicts():
    with pytest.raises(ValueError, match="CSV export requires a list of dictionaries"):
        export_data({"name": "Alice"}, "test.csv", format="csv")
    with pytest.raises(ValueError, match="CSV export requires a list of dictionaries"):
        export_data([{"name": "Alice"}, "not a dict"], "test.csv", format="csv")

def test_export_data_directory_traversal():
    with pytest.raises(ValueError, match="Filepath contains directory traversal"):
        export_data({}, "../test.json", format="json")
    with pytest.raises(ValueError, match="Filepath contains directory traversal"):
        export_data([{"a": 1}], "../test.csv", format="csv")