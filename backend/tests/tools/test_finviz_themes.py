
import pytest
import pandas as pd
import json
import os
from tools.organize_finviz_themes import organize_information, align_with_spreadsheet

# Mock data
MOCK_JSON = {
    "label": "Test Map",
    "nodes": [
        {
            "label": "TECH",
            "nodes": [
                {
                    "label": "Software",
                    "tickers": ["MSFT", "ORCL"]
                }
            ]
        }
    ]
}

@pytest.fixture
def sample_json_file(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    f = d / "test_themes.json"
    f.write_text(json.dumps(MOCK_JSON))
    return str(f)

def test_organize_information(sample_json_file):
    df = organize_information(sample_json_file)
    assert len(df) == 1
    assert df.iloc[0]['Industry (Finviz)'] == 'TECH'
    assert df.iloc[0]['Theme (Finviz)'] == 'Software'
    assert df.iloc[0]['Ticker_List'] == 'MSFT,ORCL'
    assert df.iloc[0]['Ticker_Count'] == 2

def test_align_with_spreadsheet(sample_json_file):
    df_info = organize_information(sample_json_file)
    df_aligned = align_with_spreadsheet(df_info)
    
    assert len(df_aligned) == 1
    assert df_aligned.iloc[0]['category'] == 'テーマ'
    assert df_aligned.iloc[0]['Exchange'] == 'VIRTUAL'
    assert df_aligned.iloc[0]['theme_type'] == 'virtual'
    assert df_aligned.iloc[0]['Name'] == 'Software'
    assert "_SOFT_" in df_aligned.iloc[0]['Ticker']
