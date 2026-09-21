"""Serialization of results to external file formats."""

import json
import csv
import os
from typing import Any, Sequence, Dict, Union


def _validate_filepath(filepath: str) -> None:
    """Validate that the filepath is safe and within the working directory."""
    if '..' in filepath:
        raise ValueError("Filepath contains directory traversal")
    
    # Ensure the directory exists
    dirname = os.path.dirname(filepath)
    if dirname and not os.path.exists(dirname):
        os.makedirs(dirname)


def export_to_json(data: Any, filepath: str, *, indent: int = 2) -> None:
    """Serialize a Python object to a JSON file."""
    _validate_filepath(filepath)
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=indent)
    except IOError as e:
        raise IOError(f"Failed to write JSON file: {e}")


def export_to_csv(data: Sequence[Dict[str, Any]], filepath: str, *, delimiter: str = ",") -> None:
    """Serialize a list of dicts to a CSV file with a header row."""
    _validate_filepath(filepath)
    if not data:
        raise ValueError("No data to export")
    
    # Check for consistent keys
    keys = set(data[0].keys())
    for row in data:
        if set(row.keys()) != keys:
            raise ValueError("Inconsistent keys in data rows")
    
    try:
        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=keys, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(data)
    except IOError as e:
        raise IOError(f"Failed to write CSV file: {e}")


def export_data(data: Any, filepath: str, *, format: str = "json") -> None:
    """Convenient wrapper to export data to either JSON or CSV based on a format flag."""
    if format == "json":
        export_to_json(data, filepath)
    elif format == "csv":
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError("CSV export requires a list of dictionaries")
        export_to_csv(data, filepath)
    else:
        raise ValueError(f"Unsupported format: {format}")
