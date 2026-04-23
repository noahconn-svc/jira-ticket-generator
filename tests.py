"""
Pytest test suite for jira_ticket_creator.py.

Run: pytest tests.py -v
"""
import re

import pandas as pd
import pytest
from unittest.mock import patch

import jira_ticket_creator as jtc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sample_df(**overrides):
    """One-row DataFrame with default values that pass both thresholds."""
    data = {
        "Abbyy Name": ["Provider A"],
        "Abbyy/BC ID": [12345],
        "% of Locactions Ingested MTD of Rolling 3 Month Avg": [0.50],
        "Current Month Expected Variance (Based on 3 Mos Avg)": [-2000],
        "Rolling 3 Month Avg # of Locations Ingested": [500],
    }
    for k, v in overrides.items():
        data[k] = [v]
    return pd.DataFrame(data)


def _cols(df=None):
    return jtc._detect_columns(df if df is not None else _sample_df())


# ── filter_flagged: percent_ingested threshold ────────────────────────────────

def test_filter_includes_provider_meeting_both_thresholds():
    result = jtc.filter_flagged(_sample_df(), _cols())
    assert len(result) == 1


def test_filter_excludes_below_min_percent_ingested():
    df = _sample_df(**{"% of Locactions Ingested MTD of Rolling 3 Month Avg": 0.05})
    assert len(jtc.filter_flagged(df, _cols(df))) == 0


def test_filter_includes_at_min_percent_ingested_boundary():
    # Exactly at 0.10 should be included (condition is >=)
    df = _sample_df(**{"% of Locactions Ingested MTD of Rolling 3 Month Avg": 0.10})
    assert len(jtc.filter_flagged(df, _cols(df))) == 1


def test_filter_excludes_zero_percent_ingested():
    df = _sample_df(**{"% of Locactions Ingested MTD of Rolling 3 Month Avg": 0.0})
    assert len(jtc.filter_flagged(df, _cols(df))) == 0


# ── filter_flagged: variance threshold ───────────────────────────────────────

def test_filter_excludes_variance_above_threshold():
    df = _sample_df(**{"Current Month Expected Variance (Based on 3 Mos Avg)": -500})
    assert len(jtc.filter_flagged(df, _cols(df))) == 0


def test_filter_excludes_variance_at_boundary():
    # Exactly -1000 should be excluded (condition is strictly <)
    df = _sample_df(**{"Current Month Expected Variance (Based on 3 Mos Avg)": -1000})
    assert len(jtc.filter_flagged(df, _cols(df))) == 0


def test_filter_includes_variance_just_below_threshold():
    df = _sample_df(**{"Current Month Expected Variance (Based on 3 Mos Avg)": -1001})
    assert len(jtc.filter_flagged(df, _cols(df))) == 1


def test_filter_excludes_positive_variance():
    df = _sample_df(**{"Current Month Expected Variance (Based on 3 Mos Avg)": 500})
    assert len(jtc.filter_flagged(df, _cols(df))) == 0


# ── filter_flagged: sort order ────────────────────────────────────────────────

def test_filter_sorted_worst_variance_first():
    df = pd.DataFrame({
        "Abbyy Name": ["A", "B", "C"],
        "Abbyy/BC ID": [1, 2, 3],
        "% of Locactions Ingested MTD of Rolling 3 Month Avg": [0.5, 0.5, 0.5],
        "Current Month Expected Variance (Based on 3 Mos Avg)": [-1500, -3000, -2000],
        "Rolling 3 Month Avg # of Locations Ingested": [100, 200, 300],
    })
    cols = jtc._detect_columns(df)
    result = jtc.filter_flagged(df, cols)
    variances = list(result[cols["variance"]])
    assert variances == sorted(variances)  # ascending (most negative = worst = first)
    assert variances[0] == -3000


def test_filter_excludes_rows_failing_either_threshold():
    df = pd.DataFrame({
        "Abbyy Name": ["pass-both", "fail-pct", "fail-var", "fail-both"],
        "Abbyy/BC ID": [1, 2, 3, 4],
        "% of Locactions Ingested MTD of Rolling 3 Month Avg": [0.5, 0.05, 0.5, 0.05],
        "Current Month Expected Variance (Based on 3 Mos Avg)": [-2000, -2000, -500, -500],
        "Rolling 3 Month Avg # of Locations Ingested": [100, 100, 100, 100],
    })
    cols = jtc._detect_columns(df)
    result = jtc.filter_flagged(df, cols)
    assert len(result) == 1
    assert result.iloc[0][cols["provider_name"]] == "pass-both"


# ── _detect_columns ───────────────────────────────────────────────────────────

def test_detect_columns_exact_match():
    cols = _cols()
    assert cols["provider_name"] == "Abbyy Name"
    assert cols["provider_id"] == "Abbyy/BC ID"
    assert cols["percent_ingested"] == "% of Locactions Ingested MTD of Rolling 3 Month Avg"
    assert cols["variance"] == "Current Month Expected Variance (Based on 3 Mos Avg)"
    assert cols["rolling_avg"] == "Rolling 3 Month Avg # of Locations Ingested"


def test_detect_columns_typo_corrected_percent():
    df = _sample_df().rename(columns={
        "% of Locactions Ingested MTD of Rolling 3 Month Avg":
        "% of Locations Ingested MTD of Rolling 3 Month Avg"
    })
    cols = jtc._detect_columns(df)
    assert cols["percent_ingested"] == "% of Locations Ingested MTD of Rolling 3 Month Avg"


def test_detect_columns_case_insensitive():
    df = _sample_df().rename(columns={"Abbyy Name": "abbyy name"})
    cols = jtc._detect_columns(df)
    assert cols["provider_name"] == "abbyy name"


def test_detect_columns_raises_on_missing_column():
    df = pd.DataFrame({"Irrelevant Column": [1, 2, 3]})
    with pytest.raises(ValueError, match="Could not find column 'provider_name'"):
        jtc._detect_columns(df)


def test_detect_columns_raises_lists_available_columns():
    df = pd.DataFrame({"Wrong": [1]})
    with pytest.raises(ValueError, match="Available:"):
        jtc._detect_columns(df)


# ── Provider ID regex (used for backlog re-ranking and deduplication) ─────────

_PID = re.compile(r'\((\d+)\)\s*$')


def test_pid_regex_standard_title():
    m = _PID.search("Some Provider Name (12345)")
    assert m is not None and m.group(1) == "12345"


def test_pid_regex_trailing_whitespace():
    m = _PID.search("Some Provider Name (12345)   ")
    assert m is not None and m.group(1) == "12345"


def test_pid_regex_no_match_without_parens():
    assert _PID.search("Some Provider Name 12345") is None


def test_pid_regex_no_match_non_numeric():
    assert _PID.search("Some Provider Name (ABC)") is None


def test_pid_regex_no_match_id_mid_title():
    assert _PID.search("Some (12345) Provider Name") is None


def test_pid_regex_no_match_empty_parens():
    assert _PID.search("Some Provider Name ()") is None


# ── preflight_checks ──────────────────────────────────────────────────────────

def test_preflight_fails_on_missing_api_token():
    with patch.object(jtc, "JIRA_API_TOKEN", None):
        errors = jtc.preflight_checks()
    assert any("JIRA_API_TOKEN" in e for e in errors)


def test_preflight_fails_on_missing_data_file():
    with patch.object(jtc, "DATA_FILE_PATH", "/nonexistent/path/variance.csv"):
        errors = jtc.preflight_checks()
    assert any("Data file not found" in e for e in errors)


def test_preflight_fails_on_bad_columns(tmp_path):
    csv_path = tmp_path / "variance.csv"
    csv_path.write_text("Wrong Column A,Wrong Column B\n1,2\n")
    with (
        patch.object(jtc, "JIRA_API_TOKEN", "fake-token"),
        patch.object(jtc, "DATA_FILE_PATH", str(csv_path)),
    ):
        errors = jtc.preflight_checks()
    assert any("column" in e.lower() for e in errors)


def test_preflight_passes_with_valid_setup(tmp_path):
    csv_path = tmp_path / "variance.csv"
    csv_path.write_text(
        "Abbyy Name,Abbyy/BC ID,"
        "% of Locactions Ingested MTD of Rolling 3 Month Avg,"
        "Current Month Expected Variance (Based on 3 Mos Avg),"
        "Rolling 3 Month Avg # of Locations Ingested\n"
        "Provider A,12345,0.5,-2000,500\n"
    )
    with (
        patch.object(jtc, "JIRA_API_TOKEN", "fake-token"),
        patch.object(jtc, "DATA_FILE_PATH", str(csv_path)),
    ):
        errors = jtc.preflight_checks()
    assert errors == []


def test_preflight_reports_multiple_errors():
    with (
        patch.object(jtc, "JIRA_API_TOKEN", None),
        patch.object(jtc, "DATA_FILE_PATH", "/nonexistent/path.csv"),
    ):
        errors = jtc.preflight_checks()
    assert len(errors) >= 2
