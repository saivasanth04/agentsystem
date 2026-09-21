"""Pure computation of summary statistics (mean, median, stddev, percentile, anomalies)."""

import math
from typing import Any, Sequence, Dict, List


def _validate_numeric_sequence(values: Sequence[Any]) -> List[float]:
    """Helper to validate input and convert to list of floats."""
    if not values:
        return []
    numeric_values = []
    for i, value in enumerate(values):
        if not isinstance(value, (int, float)):
            raise TypeError(f"Expected a numeric type at index {i}, got {type(value).__name__}")
        numeric_values.append(float(value))
    return numeric_values


def compute_mean(values: Sequence[Any]) -> float:
    """Compute the arithmetic mean of a numeric sequence."""
    numeric_values = _validate_numeric_sequence(values)
    if not numeric_values:
        return float('nan')
    return sum(numeric_values) / len(numeric_values)


def compute_median(values: Sequence[Any]) -> float:
    """Compute the median of a numeric sequence."""
    numeric_values = _validate_numeric_sequence(values)
    if not numeric_values:
        return float('nan')
    sorted_values = sorted(numeric_values)
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_values[mid - 1] + sorted_values[mid]) / 2
    else:
        return sorted_values[mid]


def compute_stddev(values: Sequence[Any]) -> float:
    """Compute the population standard deviation of a numeric sequence."""
    numeric_values = _validate_numeric_sequence(values)
    n = len(numeric_values)
    if n < 2:
        return float('nan')
    mean = compute_mean(numeric_values)
    variance = sum((x - mean) ** 2 for x in numeric_values) / n
    return math.sqrt(variance)


def compute_percentile(values: Sequence[Any], percentile: float) -> float:
    """Compute an arbitrary percentile (0-100) of a numeric sequence."""
    if not 0 <= percentile <= 100:
        raise ValueError("Percentile must be between 0 and 100")
    numeric_values = _validate_numeric_sequence(values)
    if not numeric_values:
        return float('nan')
    
    sorted_values = sorted(numeric_values)
    n = len(sorted_values)
    k = (n - 1) * (percentile / 100.0)
    
    if k.is_integer():
        return sorted_values[int(k)]
    else:
        lower_idx = math.floor(k)
        upper_idx = math.ceil(k)
        weight = k - lower_idx
        return sorted_values[lower_idx] * (1 - weight) + sorted_values[upper_idx] * weight


def detect_anomalies(values: Sequence[Any], *, z_threshold: float = 3.0) -> List[int]:
    """Detect anomalies in a numeric sequence using the z-score method."""
    numeric_values = _validate_numeric_sequence(values)
    n = len(numeric_values)
    if n == 0:
        return []

    mean = compute_mean(numeric_values)
    stddev = compute_stddev(numeric_values)

    if stddev == 0:
        return []  # No anomalies if all values are the same

    anomalies = []
    for i, value in enumerate(numeric_values):
        z_score = abs((value - mean) / stddev)
        if z_score > z_threshold:
            anomalies.append(i)
    return anomalies


def iqr_anomaly_detection(data: Sequence[Any], k: float = 1.5) -> List[int]:
    """Detect anomalies using the Interquartile Range (IQR) method.
    
    Args:
        data: A sequence of numeric values.
        k: Multiplier for IQR to determine outlier thresholds.
            Default is 1.5.
    
    Returns:
        List of indices of values identified as outliers.
    """
    numeric_values = _validate_numeric_sequence(data)
    if not numeric_values:
        return []

    percentiles = {
        "25": compute_percentile(numeric_values, 25.0),
        "75": compute_percentile(numeric_values, 75.0)
    }
    q1, q3 = percentiles["25"], percentiles["75"]
    iqr = q3 - q1
    lower_bound = q1 - k * iqr
    upper_bound = q3 + k * iqr

    anomalies = []
    for i, value in enumerate(numeric_values):
        if value < lower_bound or value > upper_bound:
            anomalies.append(i)
    return anomalies



def compute_summary_statistics(values: Sequence[Any]) -> Dict[str, Any]:
    """Aggregate all core statistics into a single dictionary."""
    numeric_values = _validate_numeric_sequence(values)
    
    mean = compute_mean(numeric_values)
    median = compute_median(numeric_values)
    stddev = compute_stddev(numeric_values)
    
    percentiles = {
        "25": compute_percentile(numeric_values, 25.0),
        "50": compute_percentile(numeric_values, 50.0),
        "75": compute_percentile(numeric_values, 75.0)
    }
    
    anomalies = detect_anomalies(numeric_values)

    return {
        "mean": mean,
        "median": median,
        "stddev": stddev,
        "percentiles": percentiles,
        "anomalies": anomalies
    }
