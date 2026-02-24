"""
Pytest test suite for jira_ticket_creator.py.

Run: pytest tests.py -v
"""
import pandas as pd
import pytest
from unittest.mock import patch

from jira_ticket_creator import (
    filter_problematic_providers,
    _status_color,
    _is_default_row,
    select_providers,
    STATUS_DISPLAY_MAP,
    STATUS_COLORS,
    COL_PROVIDER_NAME,
    COL_PROVIDER_ID,
    COL_PERCENT_INGESTED,
    COL_VARIANCE,
    COL_ROLLING_AVG_LOCATIONS,
)

JIRA_URL = "https://conservice.atlassian.net"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_test_df():
    """Build a synthetic DataFrame covering all status display scenarios."""
    rows = [
        {
            COL_PROVIDER_NAME: "Test Provider - No Status",
            COL_PROVIDER_ID: 10001,
            COL_PERCENT_INGESTED: 0.15,
            COL_VARIANCE: -1200,
            COL_ROLLING_AVG_LOCATIONS: 200,
            "_dc_status_display": "",
            "_has_open_dc": False,
            "_related_items": [],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Backlog",
            COL_PROVIDER_ID: 10002,
            COL_PERCENT_INGESTED: 0.35,
            COL_VARIANCE: -2500,
            COL_ROLLING_AVG_LOCATIONS: 500,
            "_dc_status_display": "Backlog",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-100", "url": f"{JIRA_URL}/browse/DIT-100"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Development",
            COL_PROVIDER_ID: 10003,
            COL_PERCENT_INGESTED: 0.55,
            COL_VARIANCE: -3500,
            COL_ROLLING_AVG_LOCATIONS: 800,
            "_dc_status_display": "Development",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-101", "url": f"{JIRA_URL}/browse/DIT-101"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Testing",
            COL_PROVIDER_ID: 10004,
            COL_PERCENT_INGESTED: 0.45,
            COL_VARIANCE: -4200,
            COL_ROLLING_AVG_LOCATIONS: 1200,
            "_dc_status_display": "Testing",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-102", "url": f"{JIRA_URL}/browse/DIT-102"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Ops Review",
            COL_PROVIDER_ID: 10005,
            COL_PERCENT_INGESTED: 0.72,
            COL_VARIANCE: -5100,
            COL_ROLLING_AVG_LOCATIONS: 1500,
            "_dc_status_display": "Ops Review",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-103", "url": f"{JIRA_URL}/browse/DIT-103"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Completed",
            COL_PROVIDER_ID: 10006,
            COL_PERCENT_INGESTED: 0.83,
            COL_VARIANCE: -6800,
            COL_ROLLING_AVG_LOCATIONS: 2000,
            "_dc_status_display": "Completed 01/15",
            "_has_open_dc": False,
            "_related_items": [{"key": "DIT-104", "url": f"{JIRA_URL}/browse/DIT-104"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Complete",
            COL_PROVIDER_ID: 10007,
            COL_PERCENT_INGESTED: 0.91,
            COL_VARIANCE: -7500,
            COL_ROLLING_AVG_LOCATIONS: 2500,
            "_dc_status_display": "Complete",
            "_has_open_dc": False,
            "_related_items": [{"key": "DIT-105", "url": f"{JIRA_URL}/browse/DIT-105"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Cancelled",
            COL_PROVIDER_ID: 10008,
            COL_PERCENT_INGESTED: 0.68,
            COL_VARIANCE: -8200,
            COL_ROLLING_AVG_LOCATIONS: 2800,
            "_dc_status_display": "Cancelled",
            "_has_open_dc": False,
            "_related_items": [{"key": "DIT-106", "url": f"{JIRA_URL}/browse/DIT-106"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Multi Rel.",
            COL_PROVIDER_ID: 10009,
            COL_PERCENT_INGESTED: 0.95,
            COL_VARIANCE: -9500,
            COL_ROLLING_AVG_LOCATIONS: 3000,
            "_dc_status_display": "Development",
            "_has_open_dc": True,
            "_related_items": [
                {"key": "DIT-107", "url": f"{JIRA_URL}/browse/DIT-107"},
                {"key": "DIT-108", "url": f"{JIRA_URL}/browse/DIT-108"},
            ],
        },
    ]
    return pd.DataFrame(rows)


# ── STATUS_DISPLAY_MAP ────────────────────────────────────────────────────────

def test_status_display_map_testing():
    assert STATUS_DISPLAY_MAP["Code Review & Security"] == "Testing"


def test_status_display_map_ops_review():
    assert STATUS_DISPLAY_MAP["Merge"] == "Ops Review"


# ── _status_color ─────────────────────────────────────────────────────────────

def test_status_color_known():
    for status, expected_code in STATUS_COLORS.items():
        assert _status_color(status) == expected_code


def test_status_color_completed_prefix():
    assert _status_color("Completed 01/15") == STATUS_COLORS["Complete"]


def test_status_color_unknown():
    assert _status_color("Some Unknown Status") == ""


# ── _is_default_row ───────────────────────────────────────────────────────────

def test_is_default_excludes_open_dc():
    row = {"_has_open_dc": True, "_dc_status_display": "Development"}
    assert _is_default_row(row) is False


def test_is_default_excludes_completed_dc():
    row = {"_has_open_dc": False, "_dc_status_display": "Completed 01/15"}
    assert _is_default_row(row) is False


def test_is_default_includes_no_dc():
    row = {"_has_open_dc": False, "_dc_status_display": ""}
    assert _is_default_row(row) is True


def test_is_default_includes_cancelled():
    row = {"_has_open_dc": False, "_dc_status_display": "Cancelled"}
    assert _is_default_row(row) is True


# ── filter_problematic_providers ─────────────────────────────────────────────

def _base_row(**overrides):
    row = {
        COL_PROVIDER_NAME: "Provider A",
        COL_PROVIDER_ID: 99999,
        COL_PERCENT_INGESTED: 0.50,
        COL_VARIANCE: -2000,
        COL_ROLLING_AVG_LOCATIONS: 500,
    }
    row.update(overrides)
    return row


def test_filter_removes_below_min_pct():
    df = pd.DataFrame([
        _base_row(**{COL_PERCENT_INGESTED: 0.05}),  # below 10% — excluded
        _base_row(**{COL_PERCENT_INGESTED: 0.50}),  # above 10% — included
    ])
    result = filter_problematic_providers(df)
    assert len(result) == 1


def test_filter_removes_above_max_variance():
    df = pd.DataFrame([
        _base_row(**{COL_VARIANCE: -500}),   # above -1000 — excluded
        _base_row(**{COL_VARIANCE: -2000}),  # below -1000 — included
    ])
    result = filter_problematic_providers(df)
    assert len(result) == 1


# ── Table rendering ───────────────────────────────────────────────────────────

def test_table_renders_none_for_no_status(capsys):
    df = _build_test_df()
    with patch("builtins.input", return_value=""):
        select_providers(df)
    captured = capsys.readouterr().out
    # The "No Status" row (row 1) should show "None" in the status column area.
    assert "None" in captured


def test_table_shows_all_statuses(capsys):
    df = _build_test_df()
    with patch("builtins.input", return_value=""):
        select_providers(df)
    captured = capsys.readouterr().out
    for status in ("Backlog", "Development", "Testing", "Ops Review",
                   "Completed", "Complete", "Cancelled"):
        assert status in captured, f"Expected '{status}' in table output"
