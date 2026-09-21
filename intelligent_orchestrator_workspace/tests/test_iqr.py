"""Unit tests for IQR anomaly detection."""

import pytest
from data_engine.analytics import iqr_anomaly_detection


def test_iqr_anomaly_detection_normal_distribution():
    """Test IQR anomaly detection on a normal distribution without outliers."""
    data = [10, 12, 12, 13, 12, 11, 10, 11, 10, 10, 10, 10, 10, 10, 10]
    result = iqr_anomaly_detection(data, k=1.5)
    assert result == []  # No anomalies detected


def test_iqr_anomaly_detection_empty_list():
    """Test IQR anomaly detection on an empty list."""
    data = []
    result = iqr_anomaly_detection(data, k=1.5)
    assert result == []  # No anomalies detected


def test_iqr_anomaly_detection_with_outliers():
    """Test IQR anomaly detection on a list with clear outliers."""
    data = [10, 12, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 200]  # 200 is an outlier
    result = iqr_anomaly_detection(data, k=1.5)
    assert len(result) == 1  # One outlier detected
    assert data[result[0]] == 200  # Ensure the outlier is correctly identified


def test_iqr_anomaly_detection_with_multiple_outliers():
    """Test IQR anomaly detection on a list with multiple outliers."""
    data = [10, 12, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 100, 200]
    result = iqr_anomaly_detection(data, k=1.5)
    assert len(result) == 2  # Two outliers detected