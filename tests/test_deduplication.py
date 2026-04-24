import math
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

import jira_ticket_creator as jtc

COLS = {
    "provider_name":    "AbbyyName",
    "provider_id":      "AbbyyBCID",
    "percent_ingested": "PercentIngested",
    "variance":         "CurrentVariance",
}

_PATCHES = [
    "jira_ticket_creator.load_data",
    "jira_ticket_creator.get_existing_tickets",
    "jira_ticket_creator.create_jira_ticket",
    "jira_ticket_creator.update_jira_ticket",
    "jira_ticket_creator.rerank_backlog",
]


def _single_row_df(pid=100, variance=-2000.0, pct=0.50, name="Alpha Corp"):
    return pd.DataFrame({
        "AbbyyName":       [name],
        "AbbyyBCID":       [float(pid)],
        "PercentIngested": [pct],
        "CurrentVariance": [variance],
    })


def _run_with(existing, df=None):
    if df is None:
        df = _single_row_df()
    with patch("jira_ticket_creator.load_data", return_value=(df, COLS)), \
         patch("jira_ticket_creator.get_existing_tickets", return_value=existing), \
         patch("jira_ticket_creator.create_jira_ticket", return_value="ITDC-99") as mock_create, \
         patch("jira_ticket_creator.update_jira_ticket", return_value=True) as mock_update, \
         patch("jira_ticket_creator.rerank_backlog", return_value=[]):
        result = jtc.run()
    return result, mock_create, mock_update


def test_new_provider_creates_ticket():
    result, mock_create, mock_update = _run_with({})
    mock_create.assert_called_once()
    mock_update.assert_not_called()
    assert len(result["created_ticket_data"]) == 1


def test_todo_provider_updates_not_creates():
    existing = {"100": {"key": "ITDC-1", "status": "To Do"}}
    result, mock_create, mock_update = _run_with(existing)
    mock_create.assert_not_called()
    mock_update.assert_called_once()
    assert len(result["created_ticket_data"]) == 0


def test_in_progress_provider_skipped():
    existing = {"100": {"key": "ITDC-1", "status": "In Progress"}}
    _, mock_create, mock_update = _run_with(existing)
    mock_create.assert_not_called()
    mock_update.assert_not_called()


def test_transferred_to_dit_skipped():
    existing = {"100": {"key": "ITDC-1", "status": "Transferred to DIT"}}
    _, mock_create, mock_update = _run_with(existing)
    mock_create.assert_not_called()
    mock_update.assert_not_called()


def test_needs_follow_up_skipped():
    existing = {"100": {"key": "ITDC-1", "status": "Needs Follow Up"}}
    _, mock_create, mock_update = _run_with(existing)
    mock_create.assert_not_called()
    mock_update.assert_not_called()


def test_invalid_provider_id_skipped():
    df = pd.DataFrame({
        "AbbyyName":       ["Bad Row"],
        "AbbyyBCID":       [float("nan")],
        "PercentIngested": [0.50],
        "CurrentVariance": [-2000.0],
    })
    # filter_flagged won't include NaN rows in normal flow, but patch threshold to force inclusion
    with patch("jira_ticket_creator.load_data", return_value=(df, COLS)), \
         patch("jira_ticket_creator.get_existing_tickets", return_value={}), \
         patch("jira_ticket_creator.create_jira_ticket", return_value="ITDC-99") as mock_create, \
         patch("jira_ticket_creator.update_jira_ticket", return_value=True), \
         patch("jira_ticket_creator.rerank_backlog", return_value=[]), \
         patch("jira_ticket_creator.filter_flagged", return_value=df):  # force NaN row through
        result = jtc.run()
    mock_create.assert_not_called()  # NaN pid is caught and skipped


def test_errors_list_populated_on_create_failure():
    with patch("jira_ticket_creator.load_data", return_value=(_single_row_df(), COLS)), \
         patch("jira_ticket_creator.get_existing_tickets", return_value={}), \
         patch("jira_ticket_creator.create_jira_ticket", return_value=None), \
         patch("jira_ticket_creator.update_jira_ticket", return_value=True), \
         patch("jira_ticket_creator.rerank_backlog", return_value=[]):
        result = jtc.run()
    assert len(result["errors"]) == 1
    assert "Alpha Corp" in result["errors"][0]
