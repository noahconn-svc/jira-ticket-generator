import pandas as pd
import pytest

import jira_ticket_creator as jtc
from jira_ticket_creator import filter_flagged

COLS = {
    "provider_name":    "AbbyyName",
    "provider_id":      "AbbyyBCID",
    "percent_ingested": "PercentIngested",
    "variance":         "CurrentVariance",
}


def _row(variance, pct, name="Provider", pid=1):
    return pd.DataFrame({
        "AbbyyName":       [name],
        "AbbyyBCID":       [pid],
        "PercentIngested": [pct],
        "CurrentVariance": [float(variance)],
    })


def test_both_thresholds_met():
    df = _row(-2000, 0.50)
    result = filter_flagged(df, COLS)
    assert len(result) == 1


def test_variance_not_bad_enough():
    df = _row(-500, 0.50)
    result = filter_flagged(df, COLS)
    assert len(result) == 0


def test_pct_too_low():
    df = _row(-2000, 0.05)
    result = filter_flagged(df, COLS)
    assert len(result) == 0


def test_boundary_variance_excluded(monkeypatch):
    monkeypatch.setattr(jtc, "MAX_VARIANCE", -1500)
    df = _row(-1500, 0.50)  # exactly at boundary — strict < so excluded
    result = filter_flagged(df, COLS)
    assert len(result) == 0


def test_boundary_pct_included(monkeypatch):
    monkeypatch.setattr(jtc, "MIN_PERCENT_INGESTED", 0.10)
    df = _row(-2000, 0.10)  # exactly at boundary — >= so included
    result = filter_flagged(df, COLS)
    assert len(result) == 1


def test_sorted_worst_first():
    df = pd.DataFrame({
        "AbbyyName":       ["A", "B", "C"],
        "AbbyyBCID":       [1, 2, 3],
        "PercentIngested": [0.50, 0.60, 0.70],
        "CurrentVariance": [-1600.0, -2500.0, -1800.0],
    })
    result = filter_flagged(df, COLS)
    variances = list(result["CurrentVariance"])
    assert variances == sorted(variances)  # ascending (most negative first)
    assert variances[0] == -2500.0


def test_empty_result():
    df = _row(-100, 0.50)  # variance nowhere near threshold
    result = filter_flagged(df, COLS)
    assert len(result) == 0
