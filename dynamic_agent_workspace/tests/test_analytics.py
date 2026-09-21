"""Tests for data_engine.analytics."""

import pytest
import math
from data_engine.analytics import (
    compute_mean,
    compute_median,
    compute_stddev,
    compute_percentile,
    detect_anomalies,
    compute_summary_statistics
)

def test_compute_mean():
    assert compute_mean([1, 2, 3, 4, 5]) == 3.0
    assert compute_mean([10.0, 20.0, 30.0]) == 20.0

def test_compute_mean_empty():
    assert math.isnan(compute_mean([]))

def test_compute_mean_invalid_type():
    with pytest.raises(TypeError, match="Expected a numeric type at index 0, got str"):
        compute_mean(["a", "b", "c"])

def test_compute_median_odd():
    assert compute_median([1, 3, 2]) == 2.0

def test_compute_median_even():
    assert compute_median([1, 2, 3, 4]) == 2.5

def test_compute_median_empty():
    assert math.isnan(compute_median([]))

def test_compute_median_invalid_type():
    with pytest.raises(TypeError, match="Expected a numeric type at index 0, got str"):
        compute_median(["a", "b", "c"])

def test_compute_stddev():
    # Population stddev for [1, 2, 3, 4, 5]
    # Mean = 3, variance = (4+1+0+1+4)/5 = 2, stddev = sqrt(2)
    assert compute_stddev([1, 2, 3, 4, 5]) == math.sqrt(2)

def test_compute_stddev_single_element():
    assert math.isnan(compute_stddev([5]))

def test_compute_stddev_empty():
    assert math.isnan(compute_stddev([]))

def test_compute_stddev_invalid_type():
    with pytest.raises(TypeError, match="Expected a numeric type at index 0, got str"):
        compute_stddev(["a", "b", "c"])

def test_compute_percentile():
    data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert compute_percentile(data, 50) == 5.5
    assert compute_percentile(data, 0) == 1.0
    assert compute_percentile(data, 100) == 10.0

def test_compute_percentile_invalid_range():
    with pytest.raises(ValueError, match="Percentile must be between 0 and 100"):
        compute_percentile([1, 2, 3], -1)
    with pytest.raises(ValueError, match="Percentile must be between 0 and 100"):
        compute_percentile([1, 2, 3], 101)

def test_compute_percentile_empty():
    assert math.isnan(compute_percentile([], 50))

def test_compute_percentile_invalid_type():
    with pytest.raises(TypeError, match="Expected a numeric type at index 0, got str"):
        compute_percentile(["a", "b", "c"], 50)

def test_detect_anomalies():
    # Data with a clear outlier
    data = [1, 2, 3, 4, 5, 100]
    anomalies = detect_anomalies(data, z_threshold=3.0)
    assert 5 in anomalies

def test_detect_anomalies_empty():
    assert detect_anomalies([]) == []

def test_detect_anomalies_no_outliers():
    data = [1, 2, 3, 4, 5]
    assert detect_anomalies(data, z_threshold=3.0) == []

def test_detect_anomalies_invalid_type():
    with pytest.raises(TypeError, match="Expected a numeric type at index 0, got str"):
        detect_anomalies(["a", "b", "c"])

def test_compute_summary_statistics():
    data = [1, 2, 3, 4, 5]
    stats = compute_summary_statistics(data)
    assert stats['mean'] == 3.0
    assert stats['median'] == 3.0
    assert stats['stddev'] == math.sqrt(2)
    assert stats['percentiles']['25'] == 2.0
    assert stats['percentiles']['50'] == 3.0
    assert stats['percentiles']['75'] == 4.0
    assert stats['anomalies'] == []

def test_compute_summary_statistics_empty():
    stats = compute_summary_statistics([])
    assert math.isnan(stats['mean'])
    assert math.isnan(stats['median'])
    assert math.isnan(stats['stddev'])
    assert math.isnan(stats['percentiles']['25'])
    assert math.isnan(stats['percentiles']['50'])
    assert math.isnan(stats['percentiles']['75'])
    assert stats['anomalies'] == []

def test_compute_summary_statistics_invalid_type():
    with pytest.raises(TypeError, match="Expected a numeric type at index 0, got str"):
        compute_summary_statistics(["a", "b", "c"])
