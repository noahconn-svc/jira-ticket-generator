import pandas as pd
import pytest


@pytest.fixture
def base_df():
    return pd.DataFrame({
        "AbbyyName":       ["Alpha Corp", "Beta Inc", "Gamma LLC"],
        "AbbyyBCID":       [100, 200, 300],
        "PercentIngested": [0.50, 0.05, 0.80],
        "CurrentVariance": [-2000.0, -3000.0, -500.0],
    })


@pytest.fixture
def base_cols():
    return {
        "provider_name":    "AbbyyName",
        "provider_id":      "AbbyyBCID",
        "percent_ingested": "PercentIngested",
        "variance":         "CurrentVariance",
    }
