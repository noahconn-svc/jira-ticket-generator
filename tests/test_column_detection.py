import pandas as pd
import pytest

from jira_ticket_creator import _detect_columns


def _df(*cols):
    return pd.DataFrame(columns=list(cols))


def test_exact_match():
    df = _df("AbbyyName", "AbbyyBCID", "PercentIngested", "CurrentVariance")
    cols = _detect_columns(df)
    assert cols["provider_name"] == "AbbyyName"
    assert cols["provider_id"] == "AbbyyBCID"
    assert cols["percent_ingested"] == "PercentIngested"
    assert cols["variance"] == "CurrentVariance"


def test_case_insensitive():
    df = _df("abbyyname", "abbyyBCID", "percentingested", "currentvariance")
    cols = _detect_columns(df)
    assert cols["provider_name"] == "abbyyname"
    assert cols["provider_id"] == "abbyyBCID"


def test_missing_column_raises():
    df = _df("AbbyyName", "AbbyyBCID", "PercentIngested")  # missing CurrentVariance
    with pytest.raises(ValueError, match="variance"):
        _detect_columns(df)


def test_whitespace_in_header():
    df = _df(" AbbyyName ", " AbbyyBCID ", " PercentIngested ", " CurrentVariance ")
    cols = _detect_columns(df)
    assert cols["provider_name"] == " AbbyyName "
    assert cols["variance"] == " CurrentVariance "
