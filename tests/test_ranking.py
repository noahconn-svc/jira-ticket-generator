import re
import pytest
from unittest.mock import patch, MagicMock

from jira_ticket_creator import rerank_backlog

_TITLE_RE = re.compile(r'\((\d+)\)\s*$')


# ── Regex tests ───────────────────────────────────────────────────────────────

def test_standard_title():
    m = _TITLE_RE.search("Alpha Corp (12345)")
    assert m and m.group(1) == "12345"


def test_trailing_whitespace():
    m = _TITLE_RE.search("Alpha Corp (12345)  ")
    assert m and m.group(1) == "12345"


def test_no_id_in_title():
    assert _TITLE_RE.search("Alpha Corp") is None


def test_non_numeric_id():
    assert _TITLE_RE.search("Alpha Corp (ABC)") is None


# ── Rerank ordering tests ─────────────────────────────────────────────────────

def _make_issue(key, pid):
    return {"key": key, "fields": {"summary": f"Provider ({pid})"}}


def test_worst_variance_ranked_first():
    issues = [
        _make_issue("ITDC-1", "100"),  # variance -500  (least bad)
        _make_issue("ITDC-2", "200"),  # variance -2000 (worst)
        _make_issue("ITDC-3", "300"),  # variance -1500 (middle)
    ]
    variance_by_id = {"100": -500.0, "200": -2000.0, "300": -1500.0}
    put_calls = []

    def fake_put(url, json, auth, headers):
        put_calls.append(json)
        r = MagicMock()
        r.status_code = 204
        return r

    with patch("jira_ticket_creator._jql_search", return_value=issues), \
         patch("requests.put", side_effect=fake_put):
        ranked = rerank_backlog(variance_by_id)

    # First element in ranked list should be the worst variance (ITDC-2)
    assert ranked[0][0] == "ITDC-2"
    assert ranked[1][0] == "ITDC-3"
    assert ranked[2][0] == "ITDC-1"


def test_single_issue_no_api_call():
    issues = [_make_issue("ITDC-1", "100")]
    variance_by_id = {"100": -2000.0}

    with patch("jira_ticket_creator._jql_search", return_value=issues), \
         patch("requests.put") as mock_put:
        ranked = rerank_backlog(variance_by_id)

    mock_put.assert_not_called()
    assert len(ranked) == 1


def test_no_backlog_returns_empty():
    with patch("jira_ticket_creator._jql_search", return_value=[]), \
         patch("requests.put") as mock_put:
        ranked = rerank_backlog({})

    mock_put.assert_not_called()
    assert ranked == []
