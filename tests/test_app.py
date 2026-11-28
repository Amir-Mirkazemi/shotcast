"""Smoke test: the Streamlit app renders every page against a built warehouse without errors."""

from pathlib import Path

import pytest

streamlit_testing = pytest.importorskip("streamlit.testing.v1")

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "warehouse" / "shotcast.duckdb"

pytestmark = pytest.mark.skipif(not WAREHOUSE.exists(), reason="warehouse not built (run `make all`)")


@pytest.mark.parametrize("page", ["Chance creators", "Teams", "Data health"])
def test_page_renders(page):
    at = streamlit_testing.AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception
